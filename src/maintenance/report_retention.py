"""报告库保留策略：识别并删除过期的完整报告单元。

报告单元由 Markdown 原文与可选的同名结构快照组成。只处理本服务已知的目录层级
与文件名，未知文件、校验失败的快照和链接一律保留，避免误删用户资料。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

CATEGORIES = ("stock", "index", "gold")
MONTH_PATTERN = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])")
# 股票为「代码_时间」，指数与黄金另带 32 位随机后缀。
NAME_PATTERN = re.compile(
    r"^(?P<symbol>.+)_(?P<date>\d{8})_(?P<time>\d{6})(?:_[0-9a-f]{32})?\.md$"
)
LEGACY_SYMBOL_PATTERN = re.compile(r"(?:\d{6}|\d{5}\.HK)")
INDEX_SYMBOL_PATTERN = re.compile(r"[A-Za-z0-9~][A-Za-z0-9._~-]*")
SNAPSHOT_SUFFIX = ".report.json"
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
# 保留期是固定策略，不提供配置项；到期判断按报告生成时间计算。
RETENTION_DAYS = 7


@dataclass
class SweepResult:
    """一次扫描的可核对结果；失败只记录，不中断其余报告处理。"""

    scanned: int = 0
    deleted: int = 0
    orphans: int = 0
    freed_bytes: int = 0
    skipped: int = 0
    failures: int = 0

    def summary(self) -> dict[str, int]:
        return {
            "scanned": self.scanned,
            "deleted": self.deleted,
            "orphans": self.orphans,
            "freed_bytes": self.freed_bytes,
            "skipped": self.skipped,
            "failures": self.failures,
        }


def sweep(
    reports_root: Path,
    *,
    enabled: bool,
    now: float | None = None,
) -> SweepResult:
    """删除超过固定保留期的报告单元；关闭时不做任何文件操作。"""
    result = SweepResult()
    if not enabled:
        return result
    root = Path(reports_root)
    if not root.is_dir():
        return result
    cutoff = RETENTION_DAYS * 86400
    current = time.time() if now is None else now
    for month in _month_directories(root):
        _sweep_month(month, current, cutoff, result)
        _remove_if_empty(month)
        parent = month.parent
        if parent != root and parent.name not in CATEGORIES:
            _remove_if_empty(parent)
    if result.deleted or result.orphans:
        logger.info(
            "报告清理完成：删除 %s 份报告、%s 个孤立快照，释放 %.1f MiB",
            result.deleted,
            result.orphans,
            result.freed_bytes / 1024**2,
        )
    if result.skipped:
        logger.debug("报告清理保留 %s 个无法验证的报告单元", result.skipped)
    if result.failures:
        logger.warning("报告清理有 %s 个文件未能删除，将在下轮重试", result.failures)
    return result


def _month_directories(root: Path):
    for category in CATEGORIES:
        category_dir = root / category
        if category_dir.is_symlink() or not category_dir.is_dir():
            continue
        for symbol in _child_directories(category_dir):
            if not _symbol_allowed(category, symbol.name):
                continue
            yield from _months(symbol)
    # 旧版个股报告直接放在报告根目录下，保持只读识别以便到期回收。
    for symbol in _child_directories(root):
        if symbol.name in CATEGORIES or not LEGACY_SYMBOL_PATTERN.fullmatch(symbol.name):
            continue
        yield from _months(symbol)


def _child_directories(parent: Path):
    try:
        entries = sorted(parent.iterdir(), key=lambda item: item.name)
    except OSError:
        return
    for entry in entries:
        if entry.is_symlink() or not entry.is_dir():
            continue
        yield entry


def _months(symbol_dir: Path):
    for month in _child_directories(symbol_dir):
        if MONTH_PATTERN.fullmatch(month.name):
            yield month


def _symbol_allowed(category: str, name: str) -> bool:
    if category == "gold":
        return name == "GOLD"
    if category == "stock":
        return LEGACY_SYMBOL_PATTERN.fullmatch(name) is not None
    return INDEX_SYMBOL_PATTERN.fullmatch(name) is not None


def _sweep_month(month: Path, now: float, cutoff: float, result: SweepResult) -> None:
    try:
        entries = sorted(month.iterdir(), key=lambda item: item.name)
    except OSError:
        return
    for path in entries:
        if path.suffix == ".md" and not path.is_symlink() and path.is_file():
            _consider_unit(month, path, now, cutoff, result)
    # 上一轮可能只删掉了原文或快照；回收到期孤立快照，避免永久残留。
    for path in entries:
        if path.name.endswith(SNAPSHOT_SUFFIX) and not path.is_symlink() and path.is_file():
            markdown = month / (path.name[: -len(SNAPSHOT_SUFFIX)] + ".md")
            if not markdown.exists():
                _consider_orphan(month, path, now, cutoff, result)


def _consider_unit(
    month: Path,
    markdown: Path,
    now: float,
    cutoff: float,
    result: SweepResult,
) -> None:
    match = NAME_PATTERN.fullmatch(markdown.name)
    if match is None:
        return
    if match.group("symbol") != month.parent.name:
        return
    date = match.group("date")
    if f"{date[:4]}-{date[4:6]}" != month.name:
        return
    stamp = _timestamp(date, match.group("time"))
    if stamp is None:
        return
    result.scanned += 1
    if now - stamp <= cutoff:
        return
    snapshot = markdown.with_suffix(SNAPSHOT_SUFFIX)
    if snapshot.exists() and (
        snapshot.is_symlink() or not _snapshot_matches(markdown, snapshot)
    ):
        result.skipped += 1
        logger.debug("报告快照无法验证，保留报告：%s", markdown.name)
        return
    size = _file_size(markdown)
    if snapshot.exists():
        size += _file_size(snapshot)
    _delete_unit(markdown, snapshot, size, result)


def _consider_orphan(
    month: Path,
    snapshot: Path,
    now: float,
    cutoff: float,
    result: SweepResult,
) -> None:
    stem = snapshot.name[: -len(SNAPSHOT_SUFFIX)]
    markdown_name = stem + ".md"
    match = NAME_PATTERN.fullmatch(markdown_name)
    if match is None or match.group("symbol") != month.parent.name:
        return
    date = match.group("date")
    if f"{date[:4]}-{date[4:6]}" != month.name:
        return
    stamp = _timestamp(date, match.group("time"))
    if stamp is None or now - stamp <= cutoff:
        return
    size = _file_size(snapshot)
    try:
        snapshot.unlink()
    except OSError as exc:
        result.failures += 1
        logger.warning("孤立快照删除失败：%s（%s）", snapshot.name, type(exc).__name__)
        return
    result.orphans += 1
    result.freed_bytes += size


def _snapshot_matches(markdown: Path, snapshot: Path) -> bool:
    """快照摘要与原文一致才整组删除，损坏或未知结构一律保留。"""
    try:
        if snapshot.stat().st_size > MAX_SNAPSHOT_BYTES:
            return False
        wrapper = json.loads(snapshot.read_text(encoding="utf-8"))
        if (
            not isinstance(wrapper, dict)
            or type(wrapper.get("schema_version")) is not int
            or wrapper["schema_version"] != 1
        ):
            return False
        digest = hashlib.sha256(markdown.read_bytes()).hexdigest()
        return wrapper.get("markdown_sha256") == digest
    except (OSError, ValueError, TypeError):
        return False


def _delete_unit(
    markdown: Path,
    snapshot: Path,
    size: int,
    result: SweepResult,
) -> None:
    # 先删快照再删原文：原文删除失败时下一轮按孤立快照回收。
    try:
        if snapshot.is_file():
            snapshot.unlink()
        markdown.unlink()
    except OSError as exc:
        result.failures += 1
        logger.warning("报告文件删除失败：%s（%s）", markdown.name, type(exc).__name__)
        return
    result.deleted += 1
    result.freed_bytes += size


def _timestamp(date: str, clock: str) -> float | None:
    try:
        return datetime.strptime(date + clock, "%Y%m%d%H%M%S").astimezone().timestamp()
    except ValueError:
        return None


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _remove_if_empty(directory: Path) -> None:
    try:
        if directory.is_symlink() or not directory.is_dir():
            return
        if not any(directory.iterdir()):
            directory.rmdir()
    except OSError:
        return
