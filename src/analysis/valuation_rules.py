"""估值证据逐项校验；残留历史结果不能替代当前有效快照。"""
from datetime import date

from data.schemas import AnalysisContext
from utils.numbers import is_finite_number, is_positive_finite, is_valid_percentile


def valuation_snapshot_dates(ctx: AnalysisContext) -> dict[str, date | None]:
    """逐项日期优先；仅未携带逐项日期字段的旧港股对象沿用接口共同日期。"""
    val = ctx.valuation_data
    if val is None:
        return dict.fromkeys(("pe_ttm", "pb", "ps_ttm"))
    # 显式的 None 是日期未知，不能借另一指标或汇总日期补成已知。
    legacy_date = (val.date if ctx.market == "hk"
                   and {"pe_as_of", "pb_as_of"}.isdisjoint(val.model_fields_set) else None)
    return {
        "pe_ttm": (val.pe_as_of or legacy_date) if val.pe_ttm is not None else None,
        "pb": (val.pb_as_of or legacy_date) if val.pb is not None else None,
        "ps_ttm": val.ps_as_of if val.ps_ttm is not None else None,
    }


def valuation_evidence(ctx: AnalysisContext) -> tuple[float | None, float | None, float | None]:
    """返回可用于计分的 PE/PB 分位和行业 PE 溢价，保留现有计分档位。"""
    val, ev, ei = ctx.valuation_data, ctx.enriched_valuation, ctx.enriched_industry
    pe_ok = val is not None and is_positive_finite(val.pe_ttm)
    pb_ok = val is not None and is_positive_finite(val.pb)
    if ctx.market == "hk":
        # 分位仍使用自身曲线参照值，但当前已知非正时不再借旧正值分位计分。
        pe_nonpositive = val is not None and is_finite_number(val.pe_ttm) and not pe_ok
        pb_nonpositive = val is not None and is_finite_number(val.pb) and not pb_ok
        pe_summary = ev.pe_history_summary if ev else None
        pb_summary = ev.pb_history_summary if ev else None
        pe_pct = (ev.pe_percentile if not pe_nonpositive and ev and pe_summary and pe_summary.source
                  and pe_summary.sample_count >= 120 and is_positive_finite(pe_summary.reference)
                  and is_valid_percentile(ev.pe_percentile) else None)
        pb_pct = (ev.pb_percentile if not pb_nonpositive and ev and pb_summary and pb_summary.source
                  and pb_summary.sample_count >= 120 and is_positive_finite(pb_summary.reference)
                  and is_valid_percentile(ev.pb_percentile) else None)
    else:
        pe_pct = ev.pe_percentile if pe_ok and ev and is_valid_percentile(ev.pe_percentile) else None
        pb_pct = ev.pb_percentile if pb_ok and ev and is_valid_percentile(ev.pb_percentile) else None
    comparison = getattr(ei, "comparison", {}) if ei else {}
    if comparison:
        premium = None
        for metric in comparison.get("valuation", {}).get("metrics", []):
            if metric.get("key") != "pe_ttm":
                continue
            target, median = metric.get("target"), metric.get("median")
            count = metric.get("sample_count")
            if (metric.get("status") == "ok" and is_finite_number(count) and count >= 5
                    and is_positive_finite(target) and is_positive_finite(median)):
                calculated = (target - median) / median * 100
                premium = calculated if is_finite_number(calculated) else None
            break
        return pe_pct, pb_pct, premium
    return pe_pct, pb_pct, None
