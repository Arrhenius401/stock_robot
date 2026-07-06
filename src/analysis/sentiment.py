"""舆情分析模块 — 新闻采集与情绪评估"""
from analysis.base import AnalysisModule
from data.schemas import AnalysisContext, AnalysisResult


class SentimentAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> str:
        return "sentiment"

    def analyze(self, context: AnalysisContext) -> AnalysisResult:
        news = context.news_data
        if news is None:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="舆情数据不可用", metrics={})

        headlines = news.headlines or []
        metrics = {
            "headline_count": len(headlines),
            "headlines": headlines,
            "date": news.date.isoformat(),
        }

        if len(headlines) == 0:
            status = "partial"
            summary = "近期无相关新闻"
        else:
            status = "ok"
            summary = f"近1日共 {len(headlines)} 条相关新闻"

        return AnalysisResult(dimension=self.dimension, status=status, summary=summary, metrics=metrics)
