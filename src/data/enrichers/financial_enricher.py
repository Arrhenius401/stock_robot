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

        if ctx.market == "hk":
            # 港股可能混合年度、半年度与季度披露，不能把四期等同四个独立季度。
            valid_count = sum(f.revenue is not None or f.net_profit is not None for f in financials)
            level = SufficiencyLevel.PARTIAL if valid_count else SufficiencyLevel.INSUFFICIENT
            weight = 0.5 if valid_count else 0.0
            reason = f"具备 {count} 个报告期，其中 {valid_count} 期有关键财务值；仅按披露口径展示，不混推滚动年值"
        elif count >= 4 and valid_profit_count >= 3 and valid_revenue_count >= 3:
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

        if ctx.market == "a-shares" and any(f.financial_selection_status == "partial" for f in financials):
            # 已确认的选包缺项不能被旧期数门槛重新标为完整；旧缓存 unknown 保持兼容。
            if level == SufficiencyLevel.SUFFICIENT:
                level, weight = SufficiencyLevel.PARTIAL, 0.5
            notes = list(dict.fromkeys(f.financial_selection_note for f in financials if f.financial_selection_note))
            reason = f"具备 {count} 期财报；" + ("；".join(notes) or "来源包仍有缺项，仅按实际可用事实分析")

        ctx.sufficiency.financial = DimensionSufficiency(
            level=level, reason=reason, sample_count=count, score_weight=weight,
        )
        return ctx
