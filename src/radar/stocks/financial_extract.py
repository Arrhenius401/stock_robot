"""从年报主要会计数据表保守提取待审财务建议。"""
from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal, localcontext
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pypdf import PdfReader

logger = logging.getLogger(__name__)
_NUMBER = r"[-+]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?"
_LABELS = {
    "revenue": "营业收入",
    "parent_net_profit": "归属于上市公司股东的净利润",
    "deducted_parent_net_profit": "归属于上市公司股东的扣除非经常性损益的净利润",
    "operating_cash_flow": "经营活动产生的现金流量净额",
}


def _base(symbol: str, url: str, disclosed: str) -> dict[str, Any]:
    return {"symbol": symbol, "source_url": url, "disclosed_on": disclosed,
            "status": "pending_verification", "reasons": [], "financial_proposals": [],
            "requires_review": True, "financial_verified": False, "rule_enabled": False,
            "comparison_requires_review": False}


def _amount(value: str, unit: str) -> str:
    with localcontext() as context:
        context.prec = max(50, len(value) + 15)
        number = Decimal(value.replace(",", "")) * {"元": 1, "万元": 10000, "百万元": 1000000}[unit]
    result = format(number, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def extract_annual_text(pages: list[str], *, symbol: str, source_url: str,
                        disclosed_on: str, as_of: date) -> dict[str, Any]:
    """只解析明确的连续年度数据表；所有建议仍须核对原文。"""
    result = _base(symbol, source_url, disclosed_on)
    selected_pages = pages[:20]
    result["text_sha256"] = hashlib.sha256("\f".join(selected_pages).encode()).hexdigest()
    header = re.sub(r"\s+", "", "".join(selected_pages[:2]))
    try:
        parsed = urlsplit(source_url)
        if not re.fullmatch(r"\d{6}", symbol) or parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("代码或来源 URL 无效")
        disclosed = date.fromisoformat(disclosed_on)
        if disclosed > as_of:
            raise ValueError("披露日期晚于研究截至日期")
        codes = re.findall(r"(?:公司代码|证券代码)[：:]?(\d{6})", header)
        years = set(re.findall(r"(20\d{2})年年度报告", header))
        if not codes or any(code != symbol for code in codes) or len(years) != 1:
            raise ValueError("公司身份或年度报告标题缺失、冲突")
        year = int(next(iter(years)))
        if year >= as_of.year or disclosed.year <= year:
            raise ValueError("报告年度尚未完成或披露年度不合理")
        if re.search(r"(?:更正|修订|取消|英文)", header[:500]):
            raise ValueError("修订等特殊版本待人工确认")
    except ValueError as exc:
        result["reasons"].append(str(exc))
        return result
    proposals: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for page_number, page in enumerate(selected_pages, 1):
        # 仅独立章节标题或表头是候选；说明段和季度表提及不算年度表。
        anchors = list(re.finditer(r"(?m)^[ \t]*(?:[（(][一二三四五六七八九十]+[)）][ \t]*)?主要会计数据(?=[ \t\r\n]|20\d{2})", page))
        if not anchors:
            continue
        # 章节标题与表头可重复；已出现金额行后再有表头才是真正的多表。
        amount_before_heading = any(
            re.search(r"\s*".join(map(re.escape, label)) + r"\s*[-+]?\d",
                      page[anchors[0].start():anchor.start()])
            for anchor in anchors[1:] for label in _LABELS.values()
        )
        if amount_before_heading:
            result["reasons"].append(f"第{page_number}页多个主要会计数据表，报表范围待核验")
            continue
        original_section = page[anchors[0].start():]
        section = re.sub(r"\s+", "", original_section)
        section = re.split(r"(?:主要财务指标|归属于上市公司股东的净资产|总资产)", section)[0]
        unit_match = re.search(r"单位[：:](百万元|万元|元)币种[：:]人民币", section)
        table = re.search(str(year) + r"年" + str(year - 1) + r"年(.*?)" + str(year - 2) + r"年", section)
        if not unit_match or not table or "本期比上年" not in table[1]:
            result["reasons"].append(f"第{page_number}页币种、单位或连续年度表头不支持")
            continue
        unit = unit_match[1]
        adjusted = "调整" in section[:section.find("营业收入")] if "营业收入" in section else True
        result["comparison_requires_review"] |= adjusted
        for metric, label in _LABELS.items():
            spaced_label = r"\s*".join(map(re.escape, label))
            original_section = re.split(r"(?:主要财务指标|归属于上市公司股\s*东的净资产|总资产)", original_section)[0]
            original_matches = list(re.finditer(spaced_label + r"\s*([^\r\n]+)", original_section))
            for original in original_matches:
                raw_line = original[1].strip()
                if not re.fullmatch(r"[-+0-9,.%\s]+", raw_line):
                    result["reasons"].append(f"第{page_number}页{label}金额行不支持")
                    continue
                # 空白分词后逐个完整消费，不能跳过缺失符号、脚注或坏千分组。
                tokens = raw_line.split()
                delta_index = len(tokens) - 2 if adjusted else 2
                valid_tokens = all(re.fullmatch(_NUMBER + (r"%?" if index == delta_index else ""), token)
                                   for index, token in enumerate(tokens))
                if not valid_tokens or (adjusted and not 5 <= len(tokens) <= 8) or (not adjusted and len(tokens) != 4):
                    result["reasons"].append(f"第{page_number}页{label}金额列不支持")
                    continue
                values = [(year, tokens[0])] if adjusted else [(year, tokens[0]), (year - 1, tokens[1]), (year - 2, tokens[3])]
                for fiscal_year, token in values:
                    proposal = {"symbol": symbol, "fiscal_year": fiscal_year, "metric": metric,
                                "amount_yuan": _amount(token, unit), "source_unit": unit,
                                "currency": "CNY", "scope": "reported_parent" if "parent" in metric else "consolidated",
                                "source_url": source_url, "disclosed_on": disclosed_on,
                                "page_number": page_number, "evidence_excerpt": original[0],
                                "raw_comparison_values": tokens[1:] if adjusted else [],
                                "requires_review": True, "financial_verified": False,
                                "rule_enabled": False, "status": "matched_template"}
                    proposals.setdefault((metric, fiscal_year), []).append(proposal)
    for key, candidates in proposals.items():
        if len({item["amount_yuan"] for item in candidates}) != 1:
            result["reasons"].append(f"指标{key[0]}年度{key[1]}金额冲突")
        else:
            result["financial_proposals"].append(candidates[0])
    current = {item["metric"] for item in result["financial_proposals"] if item["fiscal_year"] == year}
    if current == set(_LABELS) and not result["reasons"]:
        result["status"] = "matched_template"
    elif not result["reasons"]:
        result["reasons"].append("主要会计数据缺失或不支持的文本结构")
    return result


def extract_archived_financials(archive_result: dict[str, Any], archive_dir: Path,
                                as_of: date, *, text_extractor: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    """校验归档后限制页数解析；单份 PDF 故障不影响其他公司。"""
    output: dict[str, Any] = {"items": [], "financial_proposals": [], "requires_review": True,
                             "financial_verified": False, "rule_enabled": False}
    items = archive_result.get("items", [])
    if not isinstance(items, list):
        raise TypeError("归档 items 必须为列表")
    root = archive_dir.resolve()
    for item in items:
        if not isinstance(item, dict):
            output["items"].append({**_base("", "", ""), "reasons": ["归档行格式无效"]})
            continue
        symbol, url, disclosed = (str(item.get(key, "")) for key in ("symbol", "source_url", "disclosed_on"))
        result = _base(symbol, url, disclosed)
        try:
            if item.get("discovery_status") == "pending_review":
                raise ValueError("公告发现结果尚待人工确认")
            name = hashlib.sha256(url.encode()).hexdigest() + ".pdf"
            path = root / name
            if item.get("file") != name or path.is_symlink() or path.resolve().parent != root:
                raise ValueError("文件名或路径不安全")
            if item.get("status") not in {"downloaded", "cached"}:
                raise ValueError("文件尚未成功归档")
            if path.stat().st_size > 25 * 1024 * 1024:
                raise ValueError("PDF 超过25MiB预算")
            # 有界读取也覆盖 stat 之后文件增长的窗口，不一次载入超预算文件。
            with path.open("rb") as stream:
                payload = stream.read(25 * 1024 * 1024 + 1)
            if len(payload) > 25 * 1024 * 1024:
                raise ValueError("PDF 超过25MiB预算")
            if not payload.startswith(b"%PDF-") or hashlib.sha256(payload).hexdigest() != item.get("sha256"):
                raise ValueError("PDF 文件头或 SHA 校验失败")
            if len(payload) != item.get("bytes"):
                raise ValueError("PDF 长度校验失败")
            reader = PdfReader(BytesIO(payload))
            if len(reader.pages) > 1000:
                raise ValueError("PDF 超过1000页预算")
            pages = [reader.pages[index].extract_text() or "" for index in range(min(20, len(reader.pages)))]
            extractor = text_extractor or extract_annual_text
            result = extractor(pages, symbol=symbol, source_url=url, disclosed_on=disclosed, as_of=as_of)
            result["file_sha256"] = item["sha256"]
            result["page_count"] = len(reader.pages)
            result["parsed_page_count"] = len(pages)
        except Exception as exc:  # noqa: BLE001 第三方 PDF 和归档文件隔离边界
            logger.warning("财务报告提取失败 %s: %s", symbol, exc)
            result["reasons"].append(str(exc))
        if text_extractor is not None:
            result["discovery_period_end"] = item.get("period_end")
            result["discovery_period_months"] = item.get("period_months")
            result["discovery_fiscal_year"] = item.get("fiscal_year")
            known_period, parsed_period = item.get("period_end"), result.get("period_end")
            if known_period is not None and parsed_period is not None and known_period != parsed_period:
                result["status"] = "pending_verification"
                result["financial_proposals"] = []
                result["reasons"].append("公告发现期间与PDF标题期间不一致，保留较新报告线索待核验")
        output["items"].append(result)
        output["financial_proposals"].extend(result["financial_proposals"])
    return output
