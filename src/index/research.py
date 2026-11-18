"""从已计算事实生成确定性指数研判。"""
import math
from typing import Any

from data.schemas import AnalysisResult, IndexAnalysisContext, IndexReport
from index.profile import IndexProfile
from report.research import _safe
from utils.numbers import is_finite_number

_DIMENSIONS = {
    "technical": ("index_technical", "技术面", "比较收盘与均线，结合趋势强度、波动和价格位置。"),
    "valuation": ("index_valuation", "估值面", "仅在历史分位有效时讨论采样窗口内的位置。"),
    "capital": ("index_capital_flow", "资金面", "使用本次可用的资金背景指标。"),
    "macro": ("index_macro", "宏观面", "按所属市场和指标日期解读金融背景。"),
    "sentiment": ("index_sentiment", "新闻与事件", "市场新闻不等于指数专属事件，未作情绪评分。"),
    "strategy": ("index_strategy", "策略专项", "按官方权重覆盖率和实际样本解读。"),
}
_STATUS = {"ok": "available", "partial": "partial", "unavailable": "unavailable", "not_applicable": "not_applicable"}


def json_facts(value: Any) -> Any:
    """保留可序列化证据，非有限数字视为缺失。"""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: json_facts(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_facts(item) for item in value]
    return value


def _positioning(ctx: IndexAnalysisContext, report: IndexReport, profile: IndexProfile) -> list[dict[str, str]]:
    performance = (report.section_performance or {}).get("metrics", {})
    technical = report.section_technical.get("metrics", {})
    etf = ctx.requested_instrument or {}
    symbol = etf.get("symbol")
    index = f"{report.name}（{report.code}）"
    instrument = f"{index}；输入 ETF {etf.get('name') or ''}（{symbol}）" if symbol else index
    start = technical.get("sample_start") or performance.get("start_date")
    end = technical.get("sample_end") or performance.get("end_date") or report.overview.get("price_date")
    sample = technical.get("sample_count") or performance.get("sample_count")
    actual_range = f"{start} 至 {end}，{sample} 个交易日" if start and end and sample else "数据区间未取得"
    basis = performance.get("return_basis") or {
        "price": "价格指数，不含分红",
        "total_return": "全收益指数",
        "net_total_return": "净总收益指数",
    }.get(ctx.target.return_type, "收益口径未核验")
    val = ctx.valuation_data
    if val and val.pe_snapshot is not None:
        valuation = val.pe_snapshot_basis or "官方快照，未声明 TTM"
    elif val and val.pe_ttm is not None:
        valuation = val.pe_basis or "滚动市盈率"
    else:
        valuation = (report.section_valuation.get("summary") or "估值口径未取得") if profile.valuation_kind != "unsupported" else "指数官方估值来源尚未核实"
    return [
        {"label": "市场/风格", "value": profile.label, "note": ""},
        {"label": "指数/ETF", "value": instrument, "note": "ETF 与指数表现分别计算。" if symbol else "分析指数本身。"},
        {"label": "收益口径", "value": basis, "note": ""},
        {"label": "数据区间", "value": actual_range, "note": ""},
        {"label": "估值口径", "value": valuation, "note": ""},
    ]


def _technical_text(section: dict) -> str:
    metrics = section.get("metrics", {})
    close = metrics.get("latest_close")
    if not is_finite_number(close):
        return section.get("summary") or "行情不可用。"
    parts = [f"收盘 {close:.2f} 点"]
    for days in (5, 20, 60):
        average = metrics.get(f"ma_{days}")
        if is_finite_number(average):
            relation = "高于" if close > average else "低于" if close < average else "等于"
            parts.append(f"{relation} MA{days}（{average:.2f}）")
    for key, label, unit in (
        ("adx_14", "ADX", ""), ("atr_20", "ATR", " 点"),
        ("price_position_pct", "样本区间价格位置", "%"),
    ):
        value = metrics.get(key)
        if is_finite_number(value):
            parts.append(f"{label} {value:.2f}{unit}")
    parts.extend(str(note).rstrip("。") for note in metrics.get("notes", []))
    return "；".join(parts) + "。"


def build_index_research(
    ctx: IndexAnalysisContext, report: IndexReport, results: list[AnalysisResult], profile: IndexProfile,
) -> dict[str, Any]:
    """报告章节提供展示事实，历史表现仅作为独立证据。"""
    analysis = {result.dimension: result for result in results}
    dimensions, risks, gaps = [], [], []
    positioning = _positioning(ctx, report, profile)
    quality = {row["key"]: row["status"] for row in report.data_quality.get("dimensions", [])}
    evidence: dict[str, Any] = {"index_positioning": positioning}
    for name in profile.dimensions:
        key, label, rule = _DIMENSIONS[name]
        section = getattr(report, f"section_{name}") or {}
        collected = ctx.collection_records.get(key)
        status = _STATUS.get(quality.get(name, section.get("status", "")), "unavailable")
        summary = _technical_text(section) if name == "technical" else section.get("summary", "")
        reason = collected.error_message if collected else ""
        if name == "valuation" and profile.valuation_kind == "unsupported":
            status, reason = "not_supported", "指数官方估值来源尚未核实。"
        elif name == "macro" and profile.macro_kind == "none":
            status, reason = "not_supported", "尚未适配当地宏观来源。"
        elif name == "sentiment" and profile.news_kind == "none":
            status, reason = "not_collected", "本次未采集可核查的市场新闻。"
        if status != "available":
            reason = reason or summary or f"{label}数据未取得。"
        text = reason if status in {"not_supported", "not_collected"} else summary or reason
        if name in {"sentiment", "capital"} and (
            name == "sentiment" or profile.capital_kind == "southbound_background"
        ):
            scope = rule if name == "sentiment" else "港股通资金是市场背景，不代表指数资金流。"
            if scope.rstrip("。") not in text and "市场新闻，不等于指数专属事件" not in text:
                text = text.rstrip("。") + "。" + scope
        if status in {"not_collected", "not_supported"}:
            gaps.append(f"{label}：{reason}")
        evidence[key] = {"text": text, "status": status, "reason": reason,
                         "metrics": json_facts(analysis[key].metrics) if key in analysis else {}}
        dimensions.append({"key": key, "label": label, "text": text, "rule": rule,
                           "status": status, "reason": reason, "evidence_ids": [key]})
        if name in {"macro", "sentiment", "strategy"} and status != "available":
            risks.append({"id": f"{name}.coverage", "text": reason, "evidence_ids": [key]})

    performance = report.section_performance or {}
    returns = performance.get("metrics", {})
    if performance:
        evidence["index_performance"] = json_facts({"text": performance.get("summary", ""), "metrics": returns})
    tech = report.section_technical
    tech_notes = "；".join([tech.get("summary", ""), *tech.get("metrics", {}).get("notes", [])])
    if "OHLC" in tech_notes:
        risks.append({"id": "technical.incomplete_ohlc", "text": "OHLC 数据不完整时，趋势强度、波动及价格位置的解读受限。",
                      "evidence_ids": ["index_technical"]})
    if performance.get("status") != "ok" or any(
        "历史不足" in note for window in returns.get("windows", []) for note in window.get("notes", [])
    ):
        risks.append({"id": "performance.history", "text": "历史覆盖不足或存在缺口，区间统计的可比性受限。",
                      "evidence_ids": ["index_performance"]})
    if returns.get("benchmark_note") or (profile.key in {"a-sector", "a-strategy"} and not returns.get("benchmark_sample_count")):
        risks.append({"id": "performance.benchmark", "text": "基准共同交易日不足时，无法比较同日收益。",
                      "evidence_ids": ["index_performance"]})
    valuation = ctx.valuation_data
    if valuation is None or not valuation.valuation_valid or valuation.pe_percentile is None:
        reason = "仅有估值快照，无法判断长期估值分位。" if valuation and valuation.pe_snapshot is not None else "缺少有效估值分位，无法判断历史高低估。"
        risks.append({"id": "valuation.percentile", "text": reason, "evidence_ids": ["index_valuation"]})
    if profile.capital_kind == "southbound_background":
        risks.append({"id": "capital.background", "text": "港股通资金仅反映市场背景。", "evidence_ids": ["index_capital_flow"]})
    if "sentiment" in profile.dimensions and evidence["index_sentiment"]["status"] in {"available", "partial"}:
        risks.append({"id": "sentiment.background", "text": "市场新闻不能说明指数专属事件。", "evidence_ids": ["index_sentiment"]})
    if profile.key == "overseas":
        gaps.extend(("宏观面：尚未适配当地宏观来源。", "新闻与事件：本次未采集可核查的市场新闻。"))
    return {"version": 2, "market": profile.market, "style": profile.style, "profile": profile.key,
            "positioning": positioning, "dimensions": dimensions, "evidence": evidence,
            "risk_candidates": risks, "model": None, "limitation": " ".join([profile.limitation, *gaps])}


def render_index_research(research: dict[str, Any]) -> str:
    """以与 Web 相同的顺序生成综合研判 Markdown。"""
    lines = ["### 指数定位", ""]
    for row in research["positioning"]:
        value = f"{row['value']} {row['note']}".strip()
        lines.append(f"- **{row['label']}**：{_safe(value)}")
    model = research.get("model")
    if model:
        lines.extend(("", "### 核心结论", ""))
        for key, label in (("overall", "整体判断"), ("support", "主要支撑"), ("question", "主要疑问")):
            lines.append(f"- **{label}**：{_safe(model['conclusion'][key])}")
    lines.extend(("", "### 维度解读", ""))
    lines.extend(f"- **{row['label']}**：{_safe(row['text'])}" for row in research["dimensions"])
    if model:
        lines.extend(("", "### 风险提示", ""))
        lines.extend(f"- {_safe(risk['text'])}" for risk in model["risks"])
        if not model["risks"]:
            lines.append("本次未形成有充分依据的独立风险提示。")
    lines.extend(("", "### 解读限制", "", _safe(research["limitation"])))
    return "\n".join(lines)
