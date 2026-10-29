"""指数舆情分析 — 市场层面舆情，禁止个股新闻"""
from typing import Any, Literal

from analysis.base import AnalysisModule
from data.schemas import AnalysisResult, IndexAnalysisContext
from index.news import normalize_news_item


class IndexSentimentAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> Literal["index_sentiment"]:
        return "index_sentiment"

    def analyze(self, context: IndexAnalysisContext,
                config: dict[str, Any] | None = None) -> AnalysisResult:
        items = [normalize_news_item(item) for item in context.news_items[:10]]
        items = [item for item in items if item["title"]]
        if not items and context.raw_sentiment:
            # 旧快照无真实发布时间，不借采集日伪造新闻日。
            items = [{"title": item.title, "source": item.source, "published_at": None, "url": item.url} for item in context.raw_sentiment.items[:10]]
        if not items:
            return AnalysisResult(dimension=self.dimension, status="unavailable", summary="未取得可核查的市场新闻", metrics={})
        return AnalysisResult(dimension=self.dimension, status="partial" if any(not i.get("published_at") or i.get("source") == "未记录" for i in items) else "ok",
                              summary=f"本次取得市场新闻 {len(items)} 条；不作情感打分",
                              metrics={"headline_count": len(items), "top_headlines": [i["title"] for i in items], "items": items, "notes": ["市场新闻不等于指数专属事件；未记录时间的条目不归为当日新闻"]})
