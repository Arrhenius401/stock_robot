"""行业分析模块 — 行业分类与同业对比"""
from analysis.base import AnalysisModule
from data.schemas import AnalysisContext, AnalysisResult


class IndustryAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> str:
        return "industry"

    def analyze(self, context: AnalysisContext) -> AnalysisResult:
        ind_data = context.industry_data
        if ind_data is None:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="行业数据不可用", metrics={})

        metrics = {
            "industry": ind_data.industry,
            "sector": ind_data.sector,
            "peers": ind_data.peers,
        }
        status = "ok" if ind_data.industry and ind_data.industry != "未知" else "partial"
        summary = f"所属行业: {ind_data.industry}" if status == "ok" else "行业分类数据有限"

        # ===== 打分逻辑 =====
        from data.schemas import SufficiencyLevel

        if context.sufficiency and context.sufficiency.industry.level == SufficiencyLevel.INSUFFICIENT:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="行业数据不足", metrics=metrics,
                                  score=None, score_detail="行业维度数据不足，跳过打分")

        ei = context.enriched_industry
        score = 5.0
        score_parts = ["基础分 5"]
        risk_flags = []

        if ei:
            if ei.peer_count >= 8:
                score += 2; score_parts.append(f"行业可比公司 {ei.peer_count} 家（充足），+2 分")
            elif ei.peer_count >= 3:
                score += 1; score_parts.append(f"行业可比公司 {ei.peer_count} 家（偏少），+1 分")
            rank = ei.target_market_cap_rank
            if rank is not None and rank <= 5:
                score += 2; score_parts.append(f"市值行业第 {rank} 位（头部），+2 分")
            elif rank is not None:
                score += 1; score_parts.append(f"市值行业第 {rank} 位，+1 分")
            if ei.industry_median_gross_margin is not None and context.financial_data:
                latest_gm = context.financial_data[0].gross_margin if context.financial_data else None
                if latest_gm is not None:
                    diff = (latest_gm - ei.industry_median_gross_margin) * 100
                    if diff > 5:
                        score += 1; score_parts.append(f"毛利率高于行业 {diff:.0f}pp，+1 分")
                    elif diff < -5:
                        score_parts.append(f"毛利率低于行业 {abs(diff):.0f}pp，不加分")
                        risk_flags.append("industry_weak_margin")

        score = round(min(score, 10.0), 1)
        score_detail = "；".join(score_parts)
        return AnalysisResult(dimension=self.dimension, status=status, summary=summary,
                              metrics=metrics, score=score, score_detail=score_detail,
                              risk_flags=risk_flags)
