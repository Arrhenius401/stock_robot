"""舆情分析模块 — 新闻采集与情绪评估"""
from typing import Any, Literal

from analysis.base import AnalysisModule
from data.schemas import AnalysisContext, AnalysisResult, SufficiencyLevel


class SentimentAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> Literal["sentiment"]:
        return "sentiment"

    def analyze(self, context: AnalysisContext,
                config: dict[str, Any] | None = None) -> AnalysisResult:
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

        if context.sufficiency and context.sufficiency.sentiment.level == SufficiencyLevel.INSUFFICIENT:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="舆情数据不足", metrics=metrics,
                                  score=None, score_detail="舆情维度数据不足，跳过打分")

        if config:
            from analysis.financial import FinancialAnalyzer
            scorer = FinancialAnalyzer._get_scorer(config)
            score, score_detail, risk_flags = scorer.score_sentiment(context)
            industry_note = scorer._industry_note() if hasattr(scorer, '_industry_note') else ""
            return AnalysisResult(dimension=self.dimension, status=status, summary=summary,
                                  metrics=metrics, score=score, score_detail=score_detail,
                                  risk_flags=risk_flags, industry_note=industry_note)

        return AnalysisResult(dimension=self.dimension, status=status, summary=summary,
                              metrics=metrics)
