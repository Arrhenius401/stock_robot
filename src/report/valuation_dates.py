"""统一估值日期展示副本；不得用采集日期推断行情日期。"""
import re
from datetime import date, datetime
from typing import Any

from analysis.valuation_rules import valuation_snapshot_dates
from data.schemas import AnalysisContext, IndexValuationData
from report.formatter import format_metric_value, format_metrics, metric_display_name
from utils.numbers import is_finite_number

_DATED_KEYS = ("pe_ttm", "pb", "ps_ttm", "pe_snapshot", "pe_percentile", "pb_percentile", "dividend_yield")
_DETAIL_KEYS = ("pe_as_of", "pb_as_of", "pe_snapshot_as_of", "ps_as_of", "pe_basis", "pb_basis",
                "pe_snapshot_basis", "pe_source_url", "pb_source_url", "pe_snapshot_source_url")


def _date_text(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
            return value
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            return None
    return None


def date_presentation(metrics: dict[str, Any], dates: dict[str, Any], summary: str = "", *, baseline: str | None = None) -> dict[str, Any]:
    """统一基准仅用于呈现；有值无日期必须显式标未知。"""
    known = {key: _date_text(dates.get(key)) for key in _DATED_KEYS}
    base = _date_text(baseline) or next((known[key] for key in _DATED_KEYS if metrics.get(key) is not None and known[key]), None)
    shown = format_metrics(metrics)
    details = list(metrics.get("valuation_details", []))
    for key in _DETAIL_KEYS:
        value = shown.pop(key, None)
        if metrics.get(key) is not None and metrics.get(key) != "":
            details.append(f"{metric_display_name(key)}：{value}")
    for key in _DATED_KEYS:
        if metrics.get(key) is None:
            continue
        period = known[key]
        suffix = "（日期未记录）" if period is None else f"（截至 {period}）" if period != base else ""
        shown[key] = f"{shown[key]}{suffix}"
        if suffix and is_finite_number(metrics[key]):
            labels = {"pe_ttm": r"PE(?:-TTM|\(TTM\))", "pb": r"PB", "ps_ttm": r"PS\(TTM\)",
                      "pe_snapshot": "官方单张PE", "pe_percentile": r"(?:PE历史分位|历史分位|PE曲线参照值处于历史|PE处于历史)",
                      "pb_percentile": "PB历史分位", "dividend_yield": "股息率"}
            formatted = str(format_metric_value(key, metrics[key])).split("（", 1)[0]
            plain = f"{metrics[key]:.2f}" + ("%" if key.endswith("percentile") or key == "dividend_yield" else "")
            original = "|".join(re.escape(token) for token in sorted({formatted, plain}, key=len, reverse=True))
            pattern = rf"({labels[key]}\s*)({original})(?![\d.,])(?!（(?:截至 |日期未记录))"
            summary = re.sub(pattern, lambda match, suffix=suffix: match.group(0) + suffix, summary)
    # 机器元数据与附属说明不展开为正文指标行。
    for key in ("valuation_details", "valuation_applicability", "historical_valuation_applicability"):
        shown.pop(key, None)
    return {"cutoff": f"估值数据截至 {base}；日期不同的指标另行注明。" if base else "估值数据日期未记录。",
            "metrics": shown, "details": details, "summary": summary, "baseline": base, "dates": known}


def stock_date_presentation(ctx: AnalysisContext, metrics: dict[str, Any], summary: str = "") -> dict[str, Any]:
    enriched = ctx.enriched_valuation
    dates: dict[str, Any] = dict(valuation_snapshot_dates(ctx))
    if enriched:
        for key in ("pe", "pb"):
            if ctx.market == "hk":
                dates[f"{key}_percentile"] = getattr(enriched, f"{key}_history_summary").as_of
            else:
                periods = [p.trade_date for p in enriched.daily_points if getattr(p, key) is not None]
                dates[f"{key}_percentile"] = max(periods) if periods else None
    display = date_presentation(metrics, dates, summary)
    if enriched:
        windows: dict[tuple[str, str, int], list[str]] = {}
        for key, label in (("pe", "PE"), ("pb", "PB")):
            periods = [point.trade_date for point in enriched.daily_points if getattr(point, key) is not None]
            if periods and metrics.get(f"{key}_percentile") is not None:
                window = (min(periods).isoformat(), max(periods).isoformat(), len(periods))
                windows.setdefault(window, []).append(label)
        for (start, end, count), labels in windows.items():
            method = "同源实测估值" if ctx.market == "hk" else "以本次财务分母派生估值"
            display["cutoff"] += (f" {'、'.join(labels)}分位区间：{start} 至 {end}，{count}个有效样本；"
                                  f"{method}，不保证覆盖上市以来完整历史。")
    return display


def index_date_presentation(val: IndexValuationData | None, metrics: dict[str, Any], *, strategy: bool, summary: str = "") -> dict[str, Any]:
    dates: dict[str, Any] = {}
    if val:
        # 普通指数 date 是源表日期；策略指数 date 是多源日期的汇总，不能代表每项。
        fallback = None if strategy or val.pe_snapshot_period else val.date
        dates.update(pe_ttm=val.pe_as_of or fallback, pb=val.pb_as_of or fallback,
                     pe_snapshot=val.pe_snapshot_period or val.pe_snapshot_as_of,
                     pe_percentile=val.percentile_sample_end, pb_percentile=val.pb_as_of if strategy else val.percentile_sample_end,
                     dividend_yield=fallback)
    return date_presentation(metrics, dates, summary)
