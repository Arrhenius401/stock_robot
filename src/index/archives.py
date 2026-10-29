"""指数Markdown与结构快照的摘要绑定存档。"""
import hashlib
import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path

from data.schemas import IndexReport

logger = logging.getLogger(__name__)


def write_snapshot(report: IndexReport, path: Path, markdown: str) -> None:
    payload = report.model_dump(mode="json")
    wrapper = {"schema_version": 1, "markdown_sha256": hashlib.sha256(markdown.encode()).hexdigest(), "payload": payload}
    snapshot = path.with_suffix(".report.json")
    from report.formatter import _atomic_write
    _atomic_write(snapshot, json.dumps(wrapper, ensure_ascii=False, allow_nan=False).encode("utf-8"))


def update_index_report(report: IndexReport, path: Path, expected_token: str) -> None:
    """只更新绑定的指数存档，失败恢复原Markdown与结构快照。"""
    from index.build_single import render_index_report_markdown
    from report.formatter import _atomic_write

    if path.suffix != ".md" or len(path.parents) < 4 or path.parents[2].name != "index" or path.parent.parent.name != report.code:
        raise ValueError("指数报告路径无效")
    root = path.parents[3].resolve()
    snapshot = path.with_suffix(".report.json")
    if not path.resolve().is_relative_to(root) or snapshot.is_symlink():
        raise ValueError("指数报告路径越界")
    old_markdown = path.read_bytes()
    previous = load_index_snapshot(path, old_markdown.decode("utf-8"), report.code)
    # 同一冻结报告上次已写盘而会话写入失败时，允许幂等完成，不再覆盖或调用模型。
    if report.commentary_status == "ready" and report.commentary_retry_id is None and previous == report.model_dump(mode="json"):
        return
    if previous is None or previous.get("commentary_retry_id") != expected_token:
        raise ValueError("原存档已被其他批次替换，禁止覆盖")
    old_snapshot = snapshot.read_bytes() if snapshot.exists() else None
    markdown = render_index_report_markdown(report)
    try:
        write_snapshot(report, path, markdown)
        _atomic_write(path, markdown.encode("utf-8"))
    except (OSError, ValueError, TypeError):
        _atomic_write(path, old_markdown)
        if old_snapshot is not None:
            _atomic_write(snapshot, old_snapshot)
        elif snapshot.exists():
            snapshot.unlink()
        raise


def save_index_report(report: IndexReport, output_dir: Path | None = None) -> Path:
    from index.build_single import render_index_report_markdown
    from index.commentary import commentary_store
    from report.formatter import _atomic_write

    markdown = render_index_report_markdown(report)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", report.code):
        raise ValueError("指数代码不适合存档")
    root = output_dir or Path("reports")
    now = datetime.now().astimezone()
    directory = root / "index" / report.code / now.strftime("%Y-%m")
    if not directory.resolve().is_relative_to(root.resolve()):
        raise ValueError("指数报告路径越界")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{report.code}_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex}.md"
    _atomic_write(path, markdown.encode("utf-8"))
    snapshot_written = True
    try:
        write_snapshot(report, path, markdown)
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("指数结构快照保存失败，保留Markdown: %s", exc)
        snapshot_written = False
    record = commentary_store.get(report.commentary_retry_id or "")
    if record and snapshot_written:
        record.output_path = path.resolve()
    return path


def load_index_snapshot(path: Path, markdown: str, symbol: str) -> dict | None:
    try:
        snapshot = path.with_suffix(".report.json")
        if snapshot.is_symlink():
            raise ValueError("快照符号链接不允许")
        wrapper = json.loads(snapshot.read_text(encoding="utf-8"))
        if not isinstance(wrapper, dict) or type(wrapper.get("schema_version")) is not int or wrapper["schema_version"] != 1:
            raise ValueError("指数快照包装无效")
        if wrapper.get("markdown_sha256") != hashlib.sha256(markdown.encode()).hexdigest():
            raise ValueError("指数Markdown摘要不匹配")
        payload = wrapper.get("payload")
        if not isinstance(payload, dict) or payload.get("code") != symbol or payload.get("report_version") != 2:
            raise ValueError("指数代码或版本不匹配")
        json.dumps(payload, allow_nan=False)
        return IndexReport.model_validate(payload).model_dump(mode="json")
    except (OSError, ValueError, TypeError) as exc:
        logger.debug("指数快照不可用，保留Markdown: %s", exc)
        return None
