"""按公司串联年度财务资料采集，断点不代表审核或准入。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from radar.stocks.announcement_discovery import discover_announcements
from radar.stocks.dividend_review import build_review_packet
from radar.stocks.financial_extract import extract_archived_financials
from radar.stocks.source_archive import collect_documents
from stock_robot.dividend_materials import DEFAULT_HOSTS, prepare_candidates

logger = logging.getLogger(__name__)
VERSION = "stock-annual-batch-v1"


def _bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2).encode("utf-8")


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _save(path: Path, value: Any) -> str:
    payload = _bytes(value)
    descriptor, temporary = tempfile.mkstemp(prefix=".batch-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(3):
            try:
                os.replace(temporary, path)
                break
            except PermissionError as exc:
                if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 2:
                    raise
                logger.debug("摘要文件暂时无法替换，第%s次重试：%s", attempt + 1, path)
                time.sleep((0.05, 0.2)[attempt])
    finally:
        Path(temporary).unlink(missing_ok=True)
    return _sha(payload)


def _roster(companies: list[dict[str, Any]]) -> list[dict[str, str]]:
    if not isinstance(companies, list) or not companies:
        raise ValueError("公司输入必须为非空列表")
    result: dict[str, dict[str, str]] = {}
    for company in companies:
        if not isinstance(company, dict):
            raise TypeError("公司输入必须为对象")
        symbol, name = company.get("symbol"), company.get("name")
        if not isinstance(symbol, str) or not re.fullmatch(r"(?:6\d{5}|[03]\d{5})", symbol):
            raise ValueError("公司代码必须为精确六位沪深A股代码")
        if not isinstance(name, str) or not name.strip() or name != name.strip() or any(ord(c) < 32 for c in name):
            raise ValueError("公司名称必须为无首尾空白的非空文本")
        row = {"symbol": symbol, "name": name}
        if symbol in result and result[symbol] != row:
            raise ValueError(f"公司代码名称冲突：{symbol}")
        result[symbol] = row
    return [result[symbol] for symbol in sorted(result)]


def _check_symbols(value: Any, symbol: str) -> None:
    # 递归检查原始结果和建议，避免跨公司资料进入单公司断点。
    if isinstance(value, dict):
        if "symbol" in value and value["symbol"] != symbol:
            raise ValueError(f"采集结果包含跨公司代码：{value['symbol']}")
        for child in value.values():
            _check_symbols(child, symbol)
    elif isinstance(value, list):
        for child in value:
            _check_symbols(child, symbol)


def _validate_archive(archived: dict[str, Any], directory: Path) -> None:
    """成功归档必须仍有绑定原始URL、长度和摘要的本地PDF。"""
    items = archived.get("items", [])
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError("归档items必须为对象列表")
    root = directory.resolve()
    for item in items:
        if item.get("status") not in {"downloaded", "cached"}:
            continue
        url = item.get("source_url")
        if not isinstance(url, str) or not url:
            raise ValueError("成功归档PDF缺少来源URL")
        name = _sha(url.encode("utf-8")) + ".pdf"
        path = directory / name
        if item.get("file") != name or path.is_symlink() or path.resolve().parent != root:
            raise ValueError("归档PDF文件名或路径与来源URL不符")
        if (type(item.get("bytes")) is not int or item["bytes"] <= 0
                or not isinstance(item.get("sha256"), str)
                or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None):
            raise ValueError("归档PDF长度或SHA256元数据无效")
        digest, size = hashlib.sha256(), 0
        try:
            with path.open("rb") as stream:
                if stream.read(5) != b"%PDF-":
                    raise ValueError("归档PDF文件头不符")
                stream.seek(0)
                for chunk in iter(lambda: stream.read(65536), b""):
                    digest.update(chunk)
                    size += len(chunk)
        except OSError as exc:
            raise ValueError(f"归档PDF缺失或不可读取：{name}") from exc
        if size != item["bytes"] or digest.hexdigest() != item["sha256"]:
            raise ValueError("归档PDF真实长度或SHA256校验失败，拒绝复用")


def _limited(value: Any) -> bool:
    if isinstance(value, dict):
        error = value.get("error", "")
        return (bool(value.get("source_stopped"))
                or (isinstance(error, str) and re.search(r"\b429\b", error) is not None)
                or any(_limited(child) for child in value.values() if isinstance(child, (dict, list))))
    if isinstance(value, list):
        return any(_limited(child) for child in value)
    return False


def _workspace(root: Path, binding: dict[str, Any]) -> None:
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("工作目录及其父目录不能是符号链接")
    root.mkdir(parents=True, exist_ok=True)
    if any(path.is_symlink() for path in root.rglob("*")):
        raise ValueError("工作目录中不能含符号链接")
    manifest = root / "manifest.json"
    if manifest.exists():
        if json.loads(manifest.read_text(encoding="utf-8")) != binding:
            raise ValueError("工作目录清单与公司输入、日期或版本不一致")
    elif any(root.iterdir()):
        raise ValueError("拒绝使用未绑定的非空工作目录")
    else:
        _save(manifest, binding)


def _collect(company: dict[str, str], root: Path, as_of: date, years: list[int],
             discover: Callable[..., Any], archive: Callable[..., Any], extract: Callable[..., Any]) -> dict[str, Any]:
    symbol = company["symbol"]
    discovery = discover([company], as_of, root / "discovery", mode="annual_report")
    _check_symbols(discovery, symbol)
    documents = discovery.get("documents", [])
    if not isinstance(documents, list) or any(not isinstance(row, dict) or row.get("symbol") != symbol for row in documents):
        raise ValueError("公告发现结果缺少精确公司归属")
    selected, excluded, unresolved = [], [], []
    for document in documents:
        year = document.get("fiscal_year")
        if type(year) is not int:
            unresolved.append({"reason": "财政年度未知，未自动归档或提取", "document": document})
        elif year not in years:
            excluded.append({"reason": "不在此前三个完整财政年度", "document": document})
        else:
            selected.append(document)
            if document.get("discovery_status") == "pending_review":
                unresolved.append({"reason": "公告标题待人工确认", "document": document})
    stopped = _limited(discovery)
    archived = {"items": []} if stopped else archive(selected, root / "archive", allowed_hosts=set(DEFAULT_HOSTS))
    _check_symbols(archived, symbol)
    _validate_archive(archived, root / "archive")
    stopped = stopped or _limited(archived)
    extraction = extract(archived, root / "archive", as_of)
    _check_symbols(extraction, symbol)
    # 比较列可能带出更早年度，审核建议同样限制在本批次三年窗口。
    for item in extraction.get("items", []):
        item["financial_proposals"] = [row for row in item.get("financial_proposals", []) if row.get("fiscal_year") in years]
    extraction["financial_proposals"] = [row for row in extraction.get("financial_proposals", []) if row.get("fiscal_year") in years]
    packet = build_review_packet([company], {"items": []}, prepare_candidates([company]),
                                 {"items": []}, as_of, financial_extraction=extraction)
    queried = discovery.get("companies", [])
    query_complete = len(queried) == 1 and queried[0].get("status") == "query_scope_complete"
    archived_items = archived.get("items", [])
    archive_complete = len(archived_items) == len(selected) and all(row.get("status") in {"cached", "downloaded"} for row in archived_items)
    found_years = {row.get("fiscal_year") for row in selected if row.get("discovery_status") != "pending_review"}
    parsed = extraction.get("items", [])
    extraction_complete = len(parsed) == len(selected) and all(row.get("status") == "matched_template" for row in parsed)
    complete = query_complete and archive_complete and extraction_complete and set(years) <= found_years and not unresolved and not stopped
    return {"symbol": symbol, "name": company["name"], "status": "completed" if complete else "partial",
            "discovery": discovery, "archive": archived, "extraction": extraction, "packet": packet,
            "unresolved": unresolved, "excluded": excluded, "source_stopped": stopped,
            "formal_screening_enabled": False, "members": 0, "financial_verified": False}


def run_annual_batch(companies: list[dict[str, Any]], workspace: Path, as_of: date, *,
                     on_packet: Callable[..., Any] | None = None, discover: Callable[..., Any] = discover_announcements,
                     archive: Callable[..., Any] = collect_documents,
                     extract: Callable[..., Any] = extract_archived_financials) -> dict[str, Any]:
    """逐公司保存可恢复资料包；回调失败保留采集断点并抛出以便重试导入。"""
    roster = _roster(companies)
    if type(as_of) is not date or as_of.year < 4:
        raise ValueError("截止日须为可覆盖此前三个完整年度的日期")
    root = Path(workspace).absolute()
    binding = {"version": VERSION, "as_of": as_of.isoformat(), "input_sha256": _sha(_bytes(roster)), "companies": roster}
    _workspace(root, binding)
    years = list(range(as_of.year - 3, as_of.year))
    rows: list[dict[str, Any]] = [{**company, "status": "pending", "reused": False} for company in roster]
    summary: dict[str, Any] = {**binding, "years": years, "companies": rows,
                               "source_stopped": False, "formal_screening_enabled": False,
                               "members": 0, "financial_verified": False, "warnings": []}

    def save_summary() -> None:
        for status in ("completed", "partial", "failed", "pending"):
            summary[status] = sum(row["status"] == status for row in rows)
        summary["processed"] = len(rows) - summary["pending"]
        summary["reused"] = sum(row["reused"] for row in rows)
        summary["import_failed"] = sum(row.get("import_status") == "failed" for row in rows)
        for key in ("proposals", "financial_proposals"):
            summary[key] = sum(row.get(key, 0) for row in rows)
        summary["status"] = "completed" if summary["completed"] == len(rows) else "partial"
        _save(root / "summary.json", summary)

    save_summary()
    for row, company in zip(rows, roster, strict=True):
        company_root = root / "companies" / company["symbol"]
        checkpoint_path, result_path = company_root / "checkpoint.json", company_root / "result.json"
        if checkpoint_path.exists():
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            payload = result_path.read_bytes()
            if checkpoint != {"symbol": company["symbol"], "sha256": _sha(payload)}:
                raise ValueError(f"{company['symbol']}断点摘要不一致，拒绝覆盖缓存")
            result = json.loads(payload)
            _check_symbols(result, company["symbol"])
            _validate_archive(result["archive"], company_root / "archive")
            row["reused"] = True
            if result.get("status") == "partial":
                row["reason"] = "复用已保存的部分资料，待审内容不自动覆盖；如需重采请使用新工作目录"
        elif summary["source_stopped"]:
            row["reason"] = "来源已停止，未发起采集；下次运行可继续"
            save_summary()
            continue
        else:
            if result_path.exists():
                raise ValueError(f"{company['symbol']}结果缺少摘要断点，拒绝覆盖")
            company_root.mkdir(parents=True, exist_ok=True)
            try:
                result = _collect(company, company_root, as_of, years, discover, archive, extract)
            except Exception as exc:  # noqa: BLE001 单公司采集隔离边界
                logger.warning("年度资料采集失败 %s: %s", company["symbol"], exc)
                row.update(status="failed", error=str(exc))
                if _limited({"error": str(exc)}):
                    summary.update(source_stopped=True, stop_reason="采集异常报告429，停止整个剩余批次")
                save_summary()
                continue
            digest = _save(result_path, result)
            _save(checkpoint_path, {"symbol": company["symbol"], "sha256": digest})
        row.update(status=result["status"], result_file=str(result_path.relative_to(root)), sha256=_sha(result_path.read_bytes()))
        for key in ("proposals", "financial_proposals"):
            row[key] = result["packet"]["summary"].get(key, 0)
        if result["source_stopped"] and not row["reused"]:
            summary.update(source_stopped=True, stop_reason="发现或PDF来源限流，保守停止整个剩余批次")
        save_summary()
        if on_packet is not None:
            try:
                on_packet(result["packet"])
            except Exception as exc:  # 外部资料导入隔离边界，记录后重新抛出，不吞异常
                logger.warning("年度资料导入失败 %s: %s", company["symbol"], exc)
                row.update(import_status="failed", import_error=str(exc))
                save_summary()
                raise RuntimeError(f"{company['symbol']}采集断点已保存，审核包导入失败，可重试：{exc}") from exc
            row["import_status"] = "completed"
            save_summary()
    cache_bytes = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
    summary["cache_bytes"] = cache_bytes
    if cache_bytes > 1024 ** 3:
        summary["warnings"].append("批次缓存超过1GiB软上限，仅提醒，未自动删除")
    save_summary()
    return summary
