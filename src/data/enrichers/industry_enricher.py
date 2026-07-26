"""行业充实器 — 同业对比与行业中位数计算"""
import logging
from data.enricher import DataEnricher
from data.schemas import (
    AnalysisContext, DimensionSufficiency, SufficiencyLevel,
    EnrichedIndustry, PeerComparison,
)

logger = logging.getLogger(__name__)


class IndustryEnricher(DataEnricher):
    def enrich(self, ctx: AnalysisContext) -> AnalysisContext:
        ind_data = ctx.industry_data

        if ind_data is None or (not ind_data.industry or ind_data.industry == "未知"):
            ctx.sufficiency.industry = DimensionSufficiency(
                level=SufficiencyLevel.INSUFFICIENT, reason="行业数据完全缺失",
                sample_count=0, score_weight=0.0,
            )
            return ctx

        top_peers = ind_data.top_peers or []

        peer_comparisons = []
        for p in top_peers:
            peer_comparisons.append(PeerComparison(
                symbol=p.symbol, name=p.name, market_cap=p.market_cap,
            ))

        peer_count = len(top_peers)
        valid_head_count = len(peer_comparisons)

        if peer_count >= 8 and valid_head_count >= 4:
            level = SufficiencyLevel.SUFFICIENT
            weight = 1.0
            reason = f"行业有效可比公司 {peer_count} 家，头部 {valid_head_count} 家数据完整"
        elif peer_count >= 3 or valid_head_count >= 2:
            level = SufficiencyLevel.PARTIAL
            weight = 0.5
            reason = f"可比较公司 {peer_count} 家（3-7），横向对比参考价值有限"
        else:
            level = SufficiencyLevel.INSUFFICIENT
            weight = 0.0
            reason = f"可比较公司仅 {peer_count} 家（<3），无法进行有效对比"

        ctx.enriched_industry = EnrichedIndustry(
            peer_count=peer_count, top_peers=peer_comparisons,
        )

        ctx.sufficiency.industry = DimensionSufficiency(
            level=level, reason=reason, sample_count=peer_count, score_weight=weight,
        )
        return ctx
