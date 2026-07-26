"""财务数据充足判定充实器"""
from data.enricher import DataEnricher
from data.schemas import AnalysisContext, DimensionSufficiency, SufficiencyLevel


class FinancialEnricher(DataEnricher):
    def enrich(self, ctx: AnalysisContext) -> AnalysisContext:
        financials = ctx.financial_data or []
        count = len(financials)

        # 检查非空利润数量
        valid_profit_count = sum(1 for f in financials if f.net_profit is not None)
        valid_revenue_count = sum(1 for f in financials if f.revenue is not None)

        if count >= 4 and valid_profit_count >= 3 and valid_revenue_count >= 3:
            level = SufficiencyLevel.SUFFICIENT
            weight = 1.0
            reason = f"具备 {count} 期季报，关键字段完整，可计算 TTM 指标"
        elif count >= 2 and valid_profit_count >= 1:
            level = SufficiencyLevel.PARTIAL
            weight = 0.5
            reason = f"仅 {count} 期季报（2–3 期），历史对比数据有限"
        else:
            level = SufficiencyLevel.INSUFFICIENT
            weight = 0.0
            reason = f"季报仅 {count} 期或关键字段大面积缺失，无法完成财务分析"

        ctx.sufficiency.financial = DimensionSufficiency(
            level=level, reason=reason, sample_count=count, score_weight=weight,
        )
        return ctx
