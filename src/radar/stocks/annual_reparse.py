"""只读已提交年度归档，离线重建待审财务资料。"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

from radar.stocks import financial_extract
from radar.stocks.annual_batch import VERSION as SOURCE_VERSION
from radar.stocks.annual_batch import (
    _bytes,
    _check_symbols,
    _limited,
    _roster,
    _save,
    _sha,
    _validate_archive,
    _workspace,
)
from radar.stocks.dividend_review import build_review_packet
from radar.stocks.financial_extract import extract_archived_financials
from stock_robot.dividend_materials import prepare_candidates

logger = logging.getLogger(__name__)
VERSION = "stock-annual-reparse-v1"


def _safe_tree(root: Path) -> None:
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("工作目录及父目录不能含符号链接")
    if root.exists() and any(path.is_symlink() for path in root.rglob("*")):
        raise ValueError("工作目录不能含符号链接")


def _read_result(root: Path, company: dict[str, str]) -> tuple[dict[str, Any], str]:
    payload = (root / "result.json").read_bytes()
    digest = _sha(payload)
    checkpoint = json.loads((root / "checkpoint.json").read_text(encoding="utf-8"))
    if checkpoint != {"symbol": company["symbol"], "sha256": digest}:
        raise ValueError("公司断点摘要不一致")
    result = json.loads(payload)
    if not isinstance(result, dict) or any(result.get(key) != company[key] for key in ("symbol", "name")):
        raise ValueError("结果公司代码或名称与清单不一致")
    _check_symbols(result, company["symbol"])
    return result, digest


def _reparse(source: dict[str, Any], company: dict[str, str], directory: Path,
             as_of: date, years: list[int], extract: Callable[..., Any]) -> dict[str, Any]:
    archived = deepcopy(source["archive"])
    extraction = extract(archived, directory / "archive", as_of)
    _check_symbols(extraction, company["symbol"])
    for item in extraction.get("items", []):
        item["financial_proposals"] = [row for row in item.get("financial_proposals", []) if row.get("fiscal_year") in years]
    extraction["financial_proposals"] = [row for row in extraction.get("financial_proposals", []) if row.get("fiscal_year") in years]
    packet = build_review_packet([company], {"items": []}, prepare_candidates([company]),
                                 {"items": []}, as_of, financial_extraction=extraction)
    discovery = source["discovery"]
    selected = [row for row in discovery.get("documents", []) if row.get("fiscal_year") in years]
    queried = discovery.get("companies", [])
    query_complete = len(queried) == 1 and queried[0].get("status") == "query_scope_complete"
    archive_items = archived.get("items", [])
    parsed = extraction.get("items", [])
    selected_urls = sorted(row.get("source_url", "") for row in selected)
    archive_urls = sorted(row.get("source_url", "") for row in archive_items)
    parsed_urls = sorted(row.get("source_url", "") for row in parsed)
    found_years = {row.get("fiscal_year") for row in selected if row.get("discovery_status") != "pending_review"}
    unresolved = deepcopy(source.get("unresolved", []))
    ambiguous = any(row.get("discovery_status") == "pending_review" for row in selected)
    archive_complete = (archive_urls == selected_urls
                        and all(row.get("status") in {"downloaded", "cached"} for row in archive_items))
    extraction_complete = parsed_urls == selected_urls and all(row.get("status") == "matched_template" for row in parsed)
    stopped = bool(source.get("source_stopped")) or _limited(discovery) or _limited(archived)
    complete = (query_complete and archive_complete and extraction_complete and set(years) <= found_years
                and not unresolved and not ambiguous and not stopped)
    return {**company, "status": "completed" if complete else "partial", "discovery": discovery,
            "archive": archived, "extraction": extraction, "packet": packet, "unresolved": unresolved,
            "excluded": source.get("excluded", []), "source_stopped": stopped,
            "financial_verified": False, "formal_screening_enabled": False, "members": 0}


def run_annual_reparse(source_workspace: Path, workspace: Path, as_of: date, *,
                       on_packet: Callable[..., Any] | None = None,
                       extract: Callable[..., Any] = extract_archived_financials) -> dict[str, Any]:
    """源任务可继续采集；本次只处理已有完整断点的公司。"""
    if type(as_of) is not date or as_of.year < 4:
        raise ValueError("截止日须为可覆盖此前三个完整年度的日期")
    source, root = Path(source_workspace).absolute(), Path(workspace).absolute()
    _safe_tree(source)
    _safe_tree(root)
    source, root = source.resolve(), root.resolve()
    if source == root or source in root.parents or root in source.parents:
        raise ValueError("源与目标工作目录不能重叠或嵌套")
    manifest_payload = (source / "manifest.json").read_bytes()
    manifest: Any = json.loads(manifest_payload)
    if not isinstance(manifest, dict):
        raise TypeError("源清单必须为对象")
    companies: Any = manifest.get("companies")
    roster = _roster(companies)
    if (manifest.get("version") != SOURCE_VERSION or manifest.get("as_of") != as_of.isoformat()
            or manifest.get("companies") != roster or manifest.get("input_sha256") != _sha(_bytes(roster))):
        raise ValueError("源清单版本、日期或公司摘要不一致")
    parser_path = Path(financial_extract.__file__)
    binding = {"version": VERSION, "as_of": as_of.isoformat(), "companies": roster,
               "source_workspace": str(source), "source_manifest_sha256": _sha(manifest_payload),
               "extractor_sha256": _sha(parser_path.read_bytes())}
    _workspace(root, binding)
    years = list(range(as_of.year - 3, as_of.year))
    rows: list[dict[str, Any]] = [{**company, "status": "pending", "reused": False} for company in roster]
    summary: dict[str, Any] = {**binding, "years": years, "companies": rows,
                               "financial_verified": False, "formal_screening_enabled": False, "members": 0}

    def save_summary() -> None:
        for status in ("pending", "completed", "partial", "failed"):
            summary[status] = sum(row["status"] == status for row in rows)
        summary["processed"] = len(rows) - summary["pending"]
        summary["reused"] = sum(row["reused"] for row in rows)
        summary["import_failed"] = sum(row.get("import_status") == "failed" for row in rows)
        for key in ("financial_proposals", "reports_matched", "reports_pending"):
            summary[key] = sum(row.get(key, 0) for row in rows)
        summary["companies_with_proposals"] = sum(row.get("financial_proposals", 0) > 0 for row in rows)
        summary["status"] = "completed" if summary["completed"] == len(rows) else "partial"
        _save(root / "summary.json", summary)

    save_summary()
    for company, row in zip(roster, rows, strict=True):
        original = source / "companies" / company["symbol"]
        target = root / "companies" / company["symbol"]
        if not (original / "checkpoint.json").exists():
            row["reason"] = "源任务尚无完整断点，本次保持待处理"
            save_summary()
            continue
        try:
            source_result, source_digest = _read_result(original, company)
            _validate_archive(source_result["archive"], original / "archive")
            if (target / "checkpoint.json").exists():
                result, digest = _read_result(target, company)
                if result.get("source_result_sha256") != source_digest:
                    raise ValueError("目标绑定的源结果摘要已变化，拒绝复用")
                if result.get("archive") != source_result["archive"]:
                    raise ValueError("目标归档与源结果不一致")
                row["reused"] = True
            else:
                if (target / "result.json").exists():
                    raise ValueError("目标结果缺少断点，拒绝覆盖")
                result = _reparse(source_result, company, original, as_of, years, extract)
                result["source_result_sha256"] = source_digest
                target.mkdir(parents=True, exist_ok=True)
                digest = _save(target / "result.json", result)
                _save(target / "checkpoint.json", {"symbol": company["symbol"], "sha256": digest})
            row.update(status=result["status"], sha256=digest,
                       result_file=str((target / "result.json").relative_to(root)),
                       financial_proposals=result["packet"]["summary"].get("financial_proposals", 0))
            parsed = result["extraction"].get("items", [])
            row["reports_matched"] = sum(item.get("status") == "matched_template" for item in parsed)
            row["reports_pending"] = sum(item.get("status") != "matched_template" for item in parsed)
        except Exception as exc:  # noqa: BLE001 单公司离线资料与执行隔离边界
            logger.warning("年度归档重解析失败 %s: %s", company["symbol"], exc)
            row.update(status="failed", error=str(exc))
            save_summary()
            continue
        save_summary()
        if on_packet is not None:
            try:
                on_packet(result["packet"])
            except Exception as exc:
                logger.warning("年度重解析资料导入失败 %s: %s", company["symbol"], exc)
                row.update(import_status="failed", import_error=str(exc))
                save_summary()
                raise RuntimeError(f"{company['symbol']}重解析断点已保存，导入失败，可重试：{exc}") from exc
            row["import_status"] = "completed"
            save_summary()
    return summary
