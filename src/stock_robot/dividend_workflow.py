"""串联官方分红资料研究，保留分阶段产物与明确审核边界。"""

import hashlib
import logging
import time
from datetime import date
from pathlib import Path
from typing import Any, cast

import click

from radar.stocks import dividend_extract
from radar.stocks.announcement_discovery import discover_announcements
from radar.stocks.dividend_extract import extract_archived_documents
from radar.stocks.dividend_review import build_review_packet
from radar.stocks.source_archive import collect_documents
from stock_robot.dividend_documents import _cutoff
from stock_robot.dividend_materials import DEFAULT_HOSTS, _read, prepare_candidates
from stock_robot.dividend_research import _digest, run_batch
from stock_robot.dividend_research import _write_json as _atomic_write

logger = logging.getLogger(__name__)
WORKFLOW_VERSION = "dividend-workflow-v1"


def _write_json(path: Path, value: Any) -> None:
    """仅重试短暂文件占用；持续拒绝不修改权限，交由阶段失败记录处理。"""
    for attempt in range(3):
        try:
            _atomic_write(path, value)
            return
        except PermissionError:
            if attempt == 2:
                raise
            logger.debug("工作流文件暂时无法替换，重试：%s", path, exc_info=True)
            time.sleep(0.05 * (attempt + 1))


def _outside(path: Path, root: Path) -> None:
    resolved = path.resolve()
    if resolved == root or root in resolved.parents:
        raise ValueError("公司输入及证据必须位于工作目录之外")


def _extraction_key(archived: dict[str, Any], root: Path, as_of: date) -> str | None:
    """缓存依据实际原文，不把归档摘要中的校验和当作文件核验。"""
    if root.is_symlink():
        return None
    files = []
    for item in archived.get("items", []):
        if item.get("status") not in {"downloaded", "cached"}:
            continue
        name = item.get("file")
        if not isinstance(name, str) or Path(name).name != name:
            return None
        url = item.get("source_url")
        if isinstance(url, str) and name != hashlib.sha256(url.encode("utf-8")).hexdigest() + ".pdf":
            return None
        path = root / name
        try:
            if path.is_symlink() or path.resolve().parent != root.resolve():
                return None
            if path.stat().st_size > 25 * 1024 * 1024:
                return None
            raw = path.read_bytes()
        except OSError:
            return None
        checksum = hashlib.sha256(raw).hexdigest()
        if (checksum != item.get("sha256") or not raw.startswith(b"%PDF-")
                or ("bytes" in item and item["bytes"] != len(raw))):
            return None
        files.append({"file": name, "sha256": checksum})
    stable_archived = [{**{key: value for key, value in item.items() if key not in {"attempted_at", "status"}},
                        "archive_state": "available" if item.get("status") in {"downloaded", "cached"} else item.get("status")}
                       for item in archived.get("items", [])]
    parser = Path(dividend_extract.__file__).read_bytes()
    return _digest({"version": WORKFLOW_VERSION, "parser_sha256": hashlib.sha256(parser).hexdigest(),
                    "as_of": as_of.isoformat(), "archived": stable_archived, "files": files})


def _cached_extraction(root: Path, key: str | None) -> dict[str, Any] | None:
    if key is None:
        return None
    try:
        metadata, _ = _read(root / "extraction-cache.json")
        result, source = _read(root / "extracted.json")
        if (isinstance(metadata, dict) and isinstance(result, dict)
                and metadata.get("key") == key and metadata.get("sha256") == source["sha256"]):
            return result
    except (OSError, ValueError, TypeError):
        logger.debug("字段建议缓存缺失或损坏，重新提取")
    return None


def run_workflow(input_path: Path, workspace: Path, as_of: date,
                 evidence_path: Path | None = None, refresh: bool = False) -> dict[str, Any]:
    """复用采集与核验缓存，机器建议只进入审核包，不自动成为证据。"""
    if as_of.year < 4:
        raise ValueError("截止日不足以覆盖此前三个完整年度")
    root = workspace.resolve()
    _outside(input_path, root)
    if evidence_path is not None:
        _outside(evidence_path, root)
    payload, source = _read(input_path)
    companies = payload.get("companies") if isinstance(payload, dict) else payload
    normalized = None
    evidence_source = None
    if evidence_path is not None:
        supplied, evidence_source = _read(evidence_path)
        if not isinstance(supplied, dict) or not isinstance(supplied.get("companies"), list):
            raise ValueError("证据JSON必须包含companies列表")
        normalized = supplied["companies"]
    # 所有输入先校验；拒绝操作不得破坏已有研究产物。
    prepared = prepare_candidates(companies, evidence=normalized)
    for company in prepared["companies"]:
        for key in ("required_share_classes", "profits", "coverage", "events"):
            if not isinstance(company[key], list):
                raise TypeError(f"{company['symbol']}规范证据{key}必须是列表")
    companies = cast(list[dict[str, Any]], companies)
    binding = {"version": WORKFLOW_VERSION, "as_of": as_of.isoformat(), "input_sha256": source["sha256"]}
    manifest = root / "manifest.json"
    if manifest.exists():
        existing, _ = _read(manifest)
        if existing != binding:
            raise ValueError("工作目录绑定了其他公司输入、版本或截止日，请使用新目录")
    elif root.exists() and any(root.iterdir()):
        raise ValueError("非空工作目录没有有效绑定，请使用新目录")
    root.mkdir(parents=True, exist_ok=True)
    _write_json(manifest, binding)
    state: dict[str, Any] = {**binding, "input_source": source, "evidence_source": evidence_source,
                            "research_only": True, "rule_enabled": False, "financial_verified": False,
                            "status": "running", "stages": {}, "errors": []}
    _write_json(root / "summary.json", state)

    def stage(name: str, output: str, operation: Any, fallback: Any) -> dict[str, Any]:
        try:
            state["active_stage"] = name
            result = operation()
            _write_json(root / output, result)
            state["stages"][name] = {"status": "completed", "file": output}
        except Exception as exc:  # noqa: BLE001, RUF100 工作流阶段隔离，记录异常后保留其他公司资料
            logger.exception("分红工作流阶段失败：%s", name)
            state["errors"].append({"stage": name, "message": str(exc)})
            state["status"] = "partial"
            state["stages"][name] = {"status": "failed", "file": output}
            _write_json(root / "summary.json", state)
            result = fallback()
            _write_json(root / output, result)
            state["stages"][name] = {"status": "failed", "file": output}
        _write_json(root / "summary.json", state)
        return result

    try:
        state["active_stage"] = "discovery"
        discovery = stage("discovery", "discovery.json",
                          lambda: discover_announcements(companies, as_of, root / "query-cache", refresh=refresh),
                          lambda: {"companies": [{"symbol": row["symbol"], "status": "failed"} for row in companies],
                                   "documents": [], "rule_enabled": False})
        documents = discovery.get("documents", [])
        archived = stage("archive", "archived.json",
                         lambda: collect_documents(documents, root / "pdf-archive", allowed_hosts=set(DEFAULT_HOSTS), refresh=refresh),
                         lambda: {"items": [{**row, "status": "failed"} for row in documents], "rule_enabled": False})
        state["active_stage"] = "extraction_cache"
        cache_key = _extraction_key(archived, root / "pdf-archive", as_of)
        cached = None if refresh else _cached_extraction(root, cache_key)
        extraction = stage("extraction", "extracted.json",
                           lambda: cached if cached is not None else extract_archived_documents(archived, root / "pdf-archive", as_of),
                           lambda: {"items": [{"symbol": row["symbol"], "status": "pending_verification",
                                               "event_proposals": [], "reasons": ["字段提取阶段失败"]} for row in companies],
                                    "event_proposals": [], "rule_enabled": False})
        state["stages"]["extraction"]["cache_reused"] = cached is not None
        if cache_key is not None and state["stages"]["extraction"]["status"] == "completed":
            _, trace = _read(root / "extracted.json")
            _write_json(root / "extraction-cache.json", {"key": cache_key, "sha256": trace["sha256"]})
        else:
            (root / "extraction-cache.json").unlink(missing_ok=True)
        state["active_stage"] = "preparation"
        _write_json(root / "prepared.json", {**prepared, "input_source": source, "evidence_source": evidence_source})
        state["stages"]["preparation"] = {"status": "completed", "file": "prepared.json"}
        research = stage("research", "research.json",
                         lambda: run_batch(prepared["companies"], as_of, root / "research-cache"),
                         lambda: {"items": [{"symbol": row["symbol"], "status": "pending_verification",
                                              "reasons": ["研究阶段失败"]} for row in companies], "rule_enabled": False})
        stage("review", "review-packet.json",
              lambda: build_review_packet(companies, extraction, prepared, research, as_of),
              lambda: {"companies": [{"symbol": row["symbol"], "proposals": [], "research_status": "pending_verification",
                                       "unresolved": ["审核资料包阶段失败"]} for row in companies],
                       "requires_review": True, "rule_enabled": False})
        queries = discovery.get("companies", [])
        archive_items = archived.get("items", [])
        extract_items = extraction.get("items", [])
        research_items = research.get("items", [])
        complete = sum(row.get("status") == "complete" for row in research_items)
        state["summary"] = {"companies": len(companies),
                            "query_scope_complete": sum(row.get("status") == "query_scope_complete" for row in queries),
                            "archive_available": sum(row.get("status") in {"downloaded", "cached"} for row in archive_items),
                            "archive_failed": sum(row.get("status") not in {"downloaded", "cached"} for row in archive_items),
                            "templates_matched": sum(row.get("status") == "matched_template" for row in extract_items),
                            "templates_pending": sum(row.get("status") != "matched_template" for row in extract_items),
                            "research_complete": complete, "research_pending": len(companies) - complete}
        state["status"] = "partial" if (state["errors"] or state["summary"]["query_scope_complete"] != len(companies)
                                        or state["summary"]["archive_failed"] or state["summary"]["templates_pending"]
                                        or state["summary"]["research_pending"]) else "complete"
        state.pop("active_stage", None)
        _write_json(root / "summary.json", state)
        return state
    except Exception as exc:  # noqa: BLE001, RUF100 工作流执行隔离，保存失败检查点后重新抛出
        logger.exception("分红工作流未完成：%s", state.get("active_stage", "checkpoint"))
        state["status"] = "failed"
        state["errors"].append({"stage": state.get("active_stage", "checkpoint"), "message": str(exc)})
        try:
            _write_json(root / "summary.json", state)
        except OSError:
            logger.exception("工作流失败摘要无法保存，请检查磁盘或目录权限")
        raise


@click.command("dividend-workflow")
@click.option("--input", "input_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--workspace", type=click.Path(file_okay=False, path_type=Path), required=True)
@click.option("--as-of", required=True, help="研究截止日，YYYY-MM-DD")
@click.option("--evidence", "evidence_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--refresh", is_flag=True)
def dividend_workflow(input_path: Path, workspace: Path, as_of: str,
                      evidence_path: Path | None, refresh: bool) -> None:
    """批量生成官方原文、字段建议及审核包；不发布股票池成员。"""
    try:
        result = run_workflow(input_path, workspace, _cutoff(as_of), evidence_path, refresh)
    except (OSError, ValueError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc
    summary = result["summary"]
    click.echo(f"研究产物已保存：{summary['companies']}家公司，完整{summary['research_complete']}家，"
               f"待核验{summary['research_pending']}家，阶段异常{len(result['errors'])}项；正式筛选未启用。")
