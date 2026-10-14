"""整理分红待审核资料，机器建议与规范证据保持独立。"""

import hashlib
import json
import re
from copy import deepcopy
from datetime import date
from typing import Any

REVIEW_PACKET_VERSION = "dividend-review-v1"


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _valid_hash(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _index(rows: Any, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        raise TypeError(f"{label}必须是公司列表")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("symbol"), str) or not row["symbol"].strip():
            raise ValueError(f"{label}缺少公司代码")
        if row["symbol"] in result:
            raise ValueError(f"{label}公司代码重复：{row['symbol']}")
        result[row["symbol"]] = row
    return result


def build_review_packet(companies: list[dict[str, Any]], extraction: dict[str, Any],
                        prepared: dict[str, Any], research: dict[str, Any],
                        as_of: date) -> dict[str, Any]:
    """保留每家公司和失败原文；不将模板命中转换成审核或完整性结论。"""
    if as_of.year < 4:
        raise ValueError("截止日不足以覆盖此前三个完整年度")
    roster = _index(companies, "公司输入")
    normalized = _index(prepared.get("companies", []), "规范证据")
    results = _index(research.get("items", []), "研究结果")
    if (set(normalized) | set(results)) - set(roster):
        raise ValueError("规范证据或研究结果包含公司输入之外的代码")
    items = extraction.get("items", [])
    if not isinstance(items, list):
        raise TypeError("提取items必须是列表")
    proposals: dict[str, dict[str, dict[str, Any]]] = {symbol: {} for symbol in roster}
    documents: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in roster}
    diagnostics: list[str] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("symbol"), str) or item["symbol"] not in roster:
            diagnostics.append("提取项缺少有效公司归属或位于公司输入之外，未纳入建议")
            continue
        symbol = item["symbol"]
        raw_origin = {key: deepcopy(item.get(key)) for key in
                      ("source_url", "disclosed_on", "file_sha256", "text_sha256", "status", "reasons")}
        try:
            # 非有限原始值保留成文本诊断，不能破坏其他公司的资料包。
            origin = json.loads(json.dumps(raw_origin, ensure_ascii=False, default=str), parse_constant=str)
        except (TypeError, ValueError):
            origin = {"status": "pending_verification", "reasons": ["原文追踪字段格式无效"]}
        documents[symbol].append(origin)
        candidates = item.get("event_proposals", [])
        if not isinstance(candidates, list):
            diagnostics.append(f"{symbol}公告建议列表格式无效")
            continue
        hashes_valid = all(_valid_hash(raw_origin[key]) for key in ("file_sha256", "text_sha256"))
        reasons = raw_origin.get("reasons")
        origin_valid = (hashes_valid and isinstance(raw_origin.get("status"), str)
                        and raw_origin["status"] in {"matched_template", "pending_verification"}
                        and isinstance(reasons, list) and all(isinstance(reason, str) for reason in reasons))
        if candidates and not origin_valid:
            diagnostics.append(f"{symbol}建议缺少有效原文摘要、提取状态或追踪字段，未纳入建议")
            continue
        for candidate in candidates:
            if (not isinstance(candidate, dict) or candidate.get("symbol") != symbol
                    or candidate.get("source_url") != item.get("source_url")
                    or candidate.get("disclosed_on") != item.get("disclosed_on")):
                diagnostics.append(f"{symbol}建议的公司、来源或披露日与原文提取项不符，未纳入建议")
                continue
            # 身份绑定字段内容和原文摘要；排序与输入位置不影响身份。
            identity = {"symbol": symbol, "proposal": candidate,
                        "file_sha256": origin["file_sha256"], "text_sha256": origin["text_sha256"]}
            try:
                identifier = _hash(identity)
            except (TypeError, ValueError):
                diagnostics.append(f"{symbol}建议存在非JSON值或非有限金额，未纳入建议")
                continue
            fields = deepcopy(candidate)
            fields.update(requires_review=True, financial_verified=False, rule_enabled=False)
            proposals[symbol][identifier] = {
                "proposal_id": identifier, "fields": fields, "origin": deepcopy(origin),
                "requires_review": True, "financial_verified": False,
            }
    years = list(range(as_of.year - 3, as_of.year))
    output: list[dict[str, Any]] = []
    for symbol, company in roster.items():
        evidence = deepcopy(normalized.get(symbol, {}))
        evidence.setdefault("symbol", symbol)
        for key in ("required_share_classes", "profits", "coverage", "events"):
            evidence.setdefault(key, [])
            if not isinstance(evidence[key], list):
                raise TypeError(f"{symbol}规范证据{key}必须是列表")
        result = deepcopy(results.get(symbol, {}))
        status = result.get("status", "pending_verification")
        reasons = result.get("reasons", [])
        if not isinstance(reasons, list) or any(not isinstance(reason, str) for reason in reasons):
            raise TypeError(f"{symbol}研究未解决事项必须是字符串列表")
        unresolved = list(reasons)
        if symbol not in results:
            unresolved.append("缺少该公司的支付率研究结果")
        if not evidence["required_share_classes"]:
            unresolved.append("全部股类范围尚未核实")
        checks = []
        for year in years:
            counts = {
                key: sum(isinstance(row, dict) and row.get("year" if key != "events" else "fiscal_year") == year
                         for row in evidence[key])
                for key in ("profits", "coverage", "events")
            }
            checks.append({"year": year, "evidence_row_counts": counts,
                           "presence_only": True, "financial_verified": False})
            if not counts["profits"]:
                unresolved.append(f"缺少{year}年普通股归母利润证据")
            if not counts["coverage"]:
                unresolved.append(f"缺少{year}年分红完整性证据（不能以关键词查询完成代替）")
        output.append({
            "symbol": symbol, "name": company.get("name", company.get("current_name", symbol)),
            "years": years, "annual_checks": checks, "documents": documents[symbol],
            "proposals": [proposals[symbol][key] for key in sorted(proposals[symbol])],
            "evidence": evidence, "research_status": status, "research": result,
            "unresolved": list(dict.fromkeys(unresolved)), "requires_review": True,
        })
    packet = {
        "packet_version": REVIEW_PACKET_VERSION, "as_of": as_of.isoformat(), "companies": output,
        "diagnostics": diagnostics, "requires_review": True, "financial_verified": False,
        "rule_enabled": False, "research_only": True,
        "summary": {"companies": len(output), "proposals": sum(len(row["proposals"]) for row in output),
                    "research_complete": sum(row["research_status"] == "complete" for row in output)},
    }
    packet["revision_sha256"] = _hash(packet)
    return packet
