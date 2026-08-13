"""指数舆情分析 — 市场层面舆情，禁止个股新闻"""
from typing import Any, Literal

from analysis.base import AnalysisModule
from data.schemas import AnalysisResult, IndexAnalysisContext


class IndexSentimentAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> Literal["index_sentiment"]:
        return "index_sentiment"

    def analyze(self, context: IndexAnalysisContext,
                config: dict[str, Any] | None = None) -> AnalysisResult:
        raw = context.raw_sentiment
        if raw is None or not raw.items:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="暂无有效市场舆情信号", metrics={})

        items = raw.items
        headlines = [item.title for item in items]

        # 简单的正面/负面关键词计数，不做 LLM 标注（LLM 在报告中统一解读）
        positive_kw = ["利好", "上涨", "突破", "增长", "回升", "改善", "扩张"]
        negative_kw = ["利空", "下跌", "下滑", "衰退", "收紧", "风险", "危机"]

        pos_count = sum(1 for h in headlines if any(kw in h for kw in positive_kw))
        neg_count = sum(1 for h in headlines if any(kw in h for kw in negative_kw))

        if pos_count > neg_count:
            sent_tag = "positive"
        elif neg_count > pos_count:
            sent_tag = "negative"
        else:
            sent_tag = "neutral"

        metrics = {
            "headline_count": len(headlines),
            "top_headlines": headlines[:10],
            "tag": sent_tag,
        }

        return AnalysisResult(dimension=self.dimension, status="ok",
                              summary=f"近1日市场要闻 {len(headlines)} 条",
                              metrics=metrics)
