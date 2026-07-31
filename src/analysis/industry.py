"""行业分析模块 — 行业分类与同业对比"""
from typing import Any

from analysis.base import AnalysisModule
from data.schemas import AnalysisContext, AnalysisResult, SufficiencyLevel


class IndustryAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> str:
        return "industry"

    def analyze(self, context: AnalysisContext,
                config: dict[str, Any] | None = None) -> AnalysisResult:
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

        if context.sufficiency and context.sufficiency.industry.level == SufficiencyLevel.INSUFFICIENT:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="行业数据不足", metrics=metrics,
                                  score=None, score_detail="行业维度数据不足，跳过打分")

        if config:
            from analysis.financial import FinancialAnalyzer
            scorer = FinancialAnalyzer._get_scorer(config)
            score, score_detail, risk_flags = scorer.score_industry(context)
            industry_note = scorer._industry_note() if hasattr(scorer, '_industry_note') else ""
            return AnalysisResult(dimension=self.dimension, status=status, summary=summary,
                                  metrics=metrics, score=score, score_detail=score_detail,
                                  risk_flags=risk_flags, industry_note=industry_note)

        return AnalysisResult(dimension=self.dimension, status=status, summary=summary,
                              metrics=metrics)
