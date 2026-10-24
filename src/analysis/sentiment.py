"""舆情分析模块 — 新闻采集与情绪评估"""
from typing import Any, Literal

from analysis.base import AnalysisModule
from data.schemas import AnalysisContext, AnalysisResult, SufficiencyLevel


def _hk_news_result(context: AnalysisContext) -> AnalysisResult:
    """港股资讯仅作可追溯检索参考，独立于舆情打分和充足度门槛。"""
    from data.hk_news import HK_TZ, SOURCE, current_news

    news = context.news_data
    snapshot = news.hk_news if news else None
    disclaimer = "关键词检索结果，可能包含间接相关报道；不作AI筛选或情绪评分，不保证完整覆盖近30天新闻。"
    if snapshot is None:
        return AnalysisResult(dimension="sentiment", status="unavailable", summary="新闻暂时获取失败",
            metrics={"news_items": [], "news_disclaimer": disclaimer, "news_notes": []}, score=None)
    snapshot = current_news(snapshot)
    record = context.collection_records.get("news")
    stale = bool(news and news._from_stale_cache) or getattr(record, "cache_status", "") == "stale"
    notes = []
    if stale:
        notes.append("本次刷新失败，使用此前缓存；仍按最近30天过滤，原采集时间未更新。")
    if snapshot.query_errors:
        notes.append("部分关键词查询失败或未执行，检索不完整。")
    if snapshot.limit_reached:
        notes.append("已达到单关键词100条候选上限，不保证覆盖窗口内全部新闻。")
    items = snapshot.items[:20]
    summary = (f"近30天关键词检索结果，展示最新{len(items)}条" if items
               else "本次检索未获得近30天有效记录")
    if stale and not items:
        summary = "新闻暂时获取失败，暂无可展示的近30天记录"
    return AnalysisResult(dimension="sentiment", status="partial" if stale or snapshot.query_errors else "ok",
        summary=summary, score=None, score_detail="关键词检索参考，不参与情绪评分或分析覆盖率",
        metrics={"news_items": [{"title": item.title,
            "published_at": item.published_at.astimezone(HK_TZ).isoformat(),
            "links": [link.model_dump() for link in item.links]} for item in items],
            "news_fetched_at": snapshot.fetched_at.astimezone(HK_TZ).isoformat(),
            "news_source": SOURCE, "news_disclaimer": disclaimer, "news_notes": notes})


class SentimentAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> Literal["sentiment"]:
        return "sentiment"

    def analyze(self, context: AnalysisContext,
                config: dict[str, Any] | None = None) -> AnalysisResult:
        if context.market == "hk":
            return _hk_news_result(context)
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
