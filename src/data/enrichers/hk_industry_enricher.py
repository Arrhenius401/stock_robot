"""港股恒生二级行业充实器 — 港股通同行业 PE/PB 中位数与分位。"""
import statistics

from data.enricher import DataEnricher
from data.schemas import (
    AnalysisContext,
    DimensionSufficiency,
    EnrichedIndustry,
    SufficiencyLevel,
)
from utils.numbers import is_positive_finite


def _percentile(target: float | None, values: list[float]) -> float | None:
    if not is_positive_finite(target) or not values or target is None:
        return None
    return round(sum(value < target for value in values) / len(values) * 100, 1)


class HKIndustryEnricher(DataEnricher):
    def enrich(self, ctx: AnalysisContext) -> AnalysisContext:
        if ctx.market != "hk":
            return ctx
        ind_data = ctx.industry_data
        if ind_data is None or not ind_data.industry:
            ctx.enriched_industry = None
            ctx.sufficiency.industry = DimensionSufficiency(
                level=SufficiencyLevel.INSUFFICIENT, reason="恒生行业数据缺失",
                sample_count=0, score_weight=0.0,
            )
            return ctx

        valuations = getattr(ind_data, "_peer_valuations", {}) or {}
        peer_pes: list[float] = []
        peer_pbs: list[float] = []
        for valuation in valuations.values():
            if valuation.pe_ttm is not None and is_positive_finite(valuation.pe_ttm):
                peer_pes.append(valuation.pe_ttm)
            if valuation.pb is not None and is_positive_finite(valuation.pb):
                peer_pbs.append(valuation.pb)

        peer_count = len(ind_data.peers)
        if peer_count >= 8 and len(peer_pes) >= 4 and len(peer_pbs) >= 4:
            level, weight = SufficiencyLevel.SUFFICIENT, 1.0
            reason = f"港股通同一恒生二级同行 {peer_count} 家，PE/PB 样本充足"
        elif peer_count >= 3:
            level, weight = SufficiencyLevel.PARTIAL, 0.5
            reason = f"港股通同行 {peer_count} 家，横向对比参考价值有限"
        else:
            level, weight = SufficiencyLevel.INSUFFICIENT, 0.0
            reason = f"港股通同行仅 {peer_count} 家（<3），无法进行有效对比"

        median_pe = statistics.median(peer_pes) if peer_pes else None
        median_pb = statistics.median(peer_pbs) if peer_pbs else None
        target_pe = getattr(ind_data, "_target_pe", None)
        target_pb = getattr(ind_data, "_target_pb", None)
        premium = None
        if (target_pe is not None and median_pe is not None
                and is_positive_finite(target_pe) and is_positive_finite(median_pe)):
            premium = round((target_pe - median_pe) / median_pe * 100, 1)

        ctx.enriched_industry = EnrichedIndustry(
            peer_count=peer_count,
            industry_median_pe=median_pe,
            industry_median_pb=median_pb,
            target_pe_premium=premium,
            target_pe_percentile=_percentile(target_pe, peer_pes),
            target_pb_percentile=_percentile(target_pb, peer_pbs),
            peer_pe_sample=len(peer_pes),
            peer_pb_sample=len(peer_pbs),
        )
        ctx.sufficiency.industry = DimensionSufficiency(
            level=level, reason=reason, sample_count=peer_count, score_weight=weight,
        )
        return ctx
