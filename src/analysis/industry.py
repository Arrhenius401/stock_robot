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
        return AnalysisResult(dimension=self.dimension, status=status, summary=summary, metrics=metrics)
