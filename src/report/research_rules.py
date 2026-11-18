"""程序生成的五维解读规则。"""
from typing import cast

from report.capability import DimensionState
from report.formatter import format_metric_value, metric_display_name
from report.market_profile import MarketProfile
from utils.numbers import is_finite_number, is_positive_finite, is_valid_percentile


def _position(value: float) -> str:
    return "较低" if value <= 25 else "较高" if value >= 75 else "中等"


def _peer_metrics(comparison: dict, section: str) -> dict[str, dict]:
    rows = comparison.get(section, {}).get("metrics", [])
    return {r["key"]: r for r in rows if isinstance(r, dict) and r.get("key")
            and r.get("status", "ok") == "ok" and is_finite_number(r.get("sample_count"))
            and r["sample_count"] >= 5 and is_valid_percentile(r.get("percentile"))}


def _financial(m: dict) -> tuple[str, str]:
    profit = m.get("net_profit")
    if is_finite_number(profit) and cast(float, profit) < 0:
        return "当前报告期存在亏损，盈利恢复仍需后续经营证据支持。", "financial.loss"
    trends = "；".join(str(x) for x in m.get("operating_trends", []))
    if "净利润增长" in trends and "经营现金流" in trends and "降至" in trends:
        return "利润增长与经营现金流下降并存，盈利增长的现金支持存在分歧。", "financial.divergence"
    cash_ratio = m.get("cash_profit_ratio")
    if is_finite_number(cash_ratio):
        if cast(float, cash_ratio) < 0:
            return "利润为正但经营现金流为负，利润的现金支持不足。", "financial.divergence"
        if cast(float, cash_ratio) >= 1:
            return "利润为正且经营现金流覆盖净利润，现金流对本期利润形成支持。", "financial.cash_support"
        return "利润为正但经营现金流低于净利润，现金回收与资金占用仍需结合披露理解。", "financial.cash_partial"
    rev, growth = m.get("revenue_growth_yoy"), m.get("profit_growth_yoy")
    if is_finite_number(rev) and is_finite_number(growth):
        rev, growth = cast(float, rev), cast(float, growth)
        if rev > 0 and growth < 0:
            return "收入增长而利润下降，盈利转化承压。", "financial.divergence"
        if rev > 0 and growth > 0:
            return "收入与利润均同比增长，现金流和负债情况仍需结合原始证据理解。", "financial.growth"
        if rev < 0 and growth < 0:
            return "收入与利润同比下降，经营表现承压。", "financial.decline"
    roe = m.get("roe")
    if is_finite_number(roe):
        return ("资本回报为正" if cast(float, roe) > 0 else "资本回报非正") + "，仅凭该指标不足以判断整体财务健康。", "financial.roe"
    return "可用财务证据有限，暂不足以判断整体财务健康。", "financial.insufficient"


def _technical(m: dict) -> tuple[str, str]:
    values = [m.get(k) for k in ("ma_5", "ma_20", "ma_60")]
    if not all(is_positive_finite(v) for v in values):
        return "完整均线证据不足，暂不判断整体技术趋势。", "technical.insufficient"
    a, b, c = [cast(float, v) for v in values]
    trend = 1 if a > b > c else -1 if a < b < c else 0
    dif, dea = m.get("macd_dif"), m.get("macd_dea")
    macd = (1 if cast(float, dif) > cast(float, dea) else -1 if cast(float, dif) < cast(float, dea) else 0) if all(is_finite_number(v) for v in (dif, dea)) else None
    prefix = "均线呈多头排列" if trend == 1 else "均线呈空头排列" if trend == -1 else "均线未形成一致排列"
    if macd is not None and macd != 0 and (trend == 0 or macd != trend):
        return prefix + "，与MACD方向存在分歧。", "technical.divergence"
    return prefix + ("，MACD方向一致。" if macd else "，其他技术证据有限。"), "technical.trend"


def _valuation(m: dict, comparison: dict) -> tuple[str, str]:
    parts = []
    for key, pct_key, label in (("pe_ttm", "pe_percentile", "PE"), ("pb", "pb_percentile", "PB")):
        allowed = m.get("valuation_applicability", {}).get(key, {}).get("applicable", True)
        pct = m.get(pct_key)
        if allowed and is_positive_finite(m.get(key)) and is_valid_percentile(pct):
            parts.append(f"{label}处于自身采样区间{_position(cast(float, pct))}位置")
    peers = _peer_metrics(comparison, "valuation")
    for key, label in (("pe_ttm", "PE"), ("pb", "PB")):
        row = peers.get(key)
        if row and is_positive_finite(row.get("target")):
            parts.append(f"{label}同行位置{_position(row['percentile'])}")
    if not parts:
        return "有效估值比较证据不足，暂不能判断估值位置。", "valuation.insufficient"
    # 最多两个主要特征；有同行方向分歧时优先，避免单指标概括全体。
    peer_parts = [p for p in parts if "同行" in p]
    if len(peer_parts) == 2 and ("较低" in peer_parts[0] and "较高" in peer_parts[1]
                                  or "较高" in peer_parts[0] and "较低" in peer_parts[1]):
        return "，".join(peer_parts) + "，估值指标存在分歧。", "valuation.divergence"
    selected = [parts[0], peer_parts[0]] if peer_parts and "采样" in parts[0] else parts[:2]
    return "，".join(dict.fromkeys(selected)) + "；位置不直接代表高低估。", "valuation.position"


def _industry(comparison: dict) -> tuple[str, str]:
    periods = comparison.get("periods", {})
    for key, fallback in (("latest", "最新同期"), ("annual", "完整年度")):
        period = periods.get(key, {})
        metrics = _peer_metrics({"p": period}, "p")
        profits = [metrics[k]["percentile"] for k in ("roe", "net_margin") if k in metrics]
        growth = metrics.get("revenue_growth_yoy", {}).get("percentile")
        parts = []
        if profits:
            positions = {_position(p) for p in profits}
            parts.append("盈利指标相对位置不一致" if len(positions) > 1 else f"盈利指标同行位置{positions.pop()}")
        if growth is not None:
            parts.append(f"收入增速同行位置{_position(growth)}")
        if parts:
            return f"{period.get('label') or fallback}：" + "，".join(parts) + "。", "industry.position"
    return "同期有效同行证据不足，暂不能判断相对经营位置。", "industry.insufficient"


def _sentiment(m: dict) -> tuple[str, str]:
    score = m.get("risk_score")
    if m.get("assessment_status") == "complete" and score in (0, 2, 5, 10):
        state = "本次有效样本未识别负面" if score == 0 else "本次有效样本识别到负面内容"
        return state + "，仅代表有限样本，不能外推整体风险。", "sentiment.assessed"
    return "相关可评估样本不足，暂不能判断整体舆情风险水平。", "sentiment.insufficient"


def dimension_text(key: str, label: str, metrics: dict, comparison: dict, state: DimensionState,
                   profile: MarketProfile, *, has_result: bool) -> tuple[str, str]:
    if state.reason == "not_collected":
        return "本期未采集可评估舆情样本，不能视为零风险。", "sentiment.not_collected"
    if state.reason == "market_policy" and profile.key != "a-shares":
        return state.detail or "本维度不适用。", f"{key}.not_applicable"
    if key == "sentiment" and profile.sentiment_policy == "news_or_assessed":
        return _sentiment(metrics)
    if key == "financial" and profile.financial_special_policy == "facts_only":
        fields = ("net_interest_income_ratio_pct", "provision_to_revenue_pct", "roe_pct", "roa_pct")
        facts = [f"{metric_display_name(field)} {format_metric_value(field, metrics[field])}"
                 for field in fields if is_finite_number(metrics.get(field))]
        if facts:
            return "东财 F10 银行年度披露：" + "，".join(facts) + "。", "financial.bank_facts"
    if key == "financial" and state.reason == "industry_na":
        return state.detail, "financial.industry_na"
    if not has_result:
        return f"{label}数据不足，暂不能形成判断。", f"{key}.insufficient"
    if key == "financial":
        if profile.period_basis == "report_period":
            financial = {k: v for k, v in metrics.items() if k not in {"operating_trends", "cash_profit_ratio"}}
            if "币种未核实" in str(financial.get("amount_basis", "")):
                for field in ("revenue_growth_yoy", "profit_growth_yoy"):
                    financial.pop(field, None)
            text, rule = _financial(financial)
            return "按财报报告期解读：" + text, rule
        if profile.period_basis == "ttm":
            profit = metrics.get("net_profit_ttm")
            if is_finite_number(profit) and cast(float, profit) < 0:
                return "已核实的 TTM 净利润为负。", "financial.loss"
            if not is_finite_number(profit):
                if is_finite_number(metrics.get("net_profit")) and metrics["net_profit"] < 0:
                    return "已披露财季出现亏损，TTM 盈利仍需核验。", "financial.loss"
                return "TTM 财务证据不足，暂不能判断整体财务健康。", "financial.insufficient"
            return _financial({"net_profit": profit, "roe": metrics.get("roe")})
        return _financial(metrics)
    if key == "technical":
        return _technical(metrics)
    if key == "valuation":
        return _valuation(metrics, comparison)
    if key == "industry":
        if profile.annual_industry_policy == "reference_only":
            latest = comparison.get("periods", {}).get("latest", {})
            text, rule = _industry({"periods": {"latest": latest}})
            if comparison.get("periods", {}).get("annual", {}).get("reference_only"):
                text += "完整年度指标另列参考。"
            return text, rule
        return _industry(comparison)
    return _sentiment(metrics)
