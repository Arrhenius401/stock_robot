"""行情数据充足判定充实器"""
from data.enricher import DataEnricher
from data.schemas import (
    AnalysisContext,
    DataSufficiency,
    DimensionSufficiency,
    SufficiencyLevel,
)


class PriceEnricher(DataEnricher):
    def enrich(self, ctx: AnalysisContext) -> AnalysisContext:
        prices = ctx.price_data or []
        count = len(prices)

        if count >= 60:
            level = SufficiencyLevel.SUFFICIENT
            weight = 1.0
            reason = f"有效行情数据 {count} 条，满足 60 条门槛"
        elif count >= 20:
            level = SufficiencyLevel.PARTIAL
            weight = 0.5
            reason = f"有效行情数据 {count} 条（20–59），缺少长期均线数据，技术分析维度受限"
        else:
            level = SufficiencyLevel.INSUFFICIENT
            weight = 0.0
            reason = f"有效行情数据仅 {count} 条（<20），无法计算任何技术指标" if count > 0 else "行情数据完全缺失"

        ctx.sufficiency = DataSufficiency(
            price=DimensionSufficiency(level=level, reason=reason, sample_count=count, score_weight=weight),
            financial=DimensionSufficiency(level=SufficiencyLevel.INSUFFICIENT, reason="待充实", sample_count=0, score_weight=0.0),
            valuation=DimensionSufficiency(level=SufficiencyLevel.INSUFFICIENT, reason="待充实", sample_count=0, score_weight=0.0),
            industry=DimensionSufficiency(level=SufficiencyLevel.INSUFFICIENT, reason="待充实", sample_count=0, score_weight=0.0),
            sentiment=DimensionSufficiency(level=SufficiencyLevel.INSUFFICIENT, reason="待充实", sample_count=0, score_weight=0.0),
        )
        return ctx
