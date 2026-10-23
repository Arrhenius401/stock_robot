"""估值证据逐项校验；残留历史结果不能替代当前有效快照。"""
from data.schemas import AnalysisContext
from utils.numbers import is_finite_number, is_positive_finite, is_valid_percentile


def valuation_evidence(ctx: AnalysisContext) -> tuple[float | None, float | None, float | None]:
    """返回可用于计分的 PE/PB 分位和行业 PE 溢价，保留现有计分档位。"""
    val, ev, ei = ctx.valuation_data, ctx.enriched_valuation, ctx.enriched_industry
    pe_ok = val is not None and is_positive_finite(val.pe_ttm)
    pb_ok = val is not None and is_positive_finite(val.pb)
    if ctx.market == "hk":
        # 港股分位的参照值属于自身曲线，不能用其他来源快照决定其排名。
        pe_summary = ev.pe_history_summary if ev else None
        pb_summary = ev.pb_history_summary if ev else None
        pe_pct = (ev.pe_percentile if ev and pe_summary and pe_summary.source
                  and pe_summary.sample_count >= 120 and is_positive_finite(pe_summary.reference)
                  and is_valid_percentile(ev.pe_percentile) else None)
        pb_pct = (ev.pb_percentile if ev and pb_summary and pb_summary.source
                  and pb_summary.sample_count >= 120 and is_positive_finite(pb_summary.reference)
                  and is_valid_percentile(ev.pb_percentile) else None)
        return pe_pct, pb_pct, None
    pe_pct = ev.pe_percentile if pe_ok and ev and is_valid_percentile(ev.pe_percentile) else None
    pb_pct = ev.pb_percentile if pb_ok and ev and is_valid_percentile(ev.pb_percentile) else None
    premium = (ei.target_pe_premium if pe_ok and ei and is_positive_finite(ei.industry_median_pe)
               and is_finite_number(ei.target_pe_premium) else None)
    return pe_pct, pb_pct, premium
