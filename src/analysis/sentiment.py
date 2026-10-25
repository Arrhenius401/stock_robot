"""舆情分析模块 — 新闻采集与情绪评估"""
from typing import Any, Literal

from analysis.base import AnalysisModule
from analysis.sentiment_rules import score_sentiment
from data.schemas import AnalysisContext, AnalysisResult, SufficiencyLevel
from data.sentiment_rules import assessment_inputs


def _hk_news_result(context: AnalysisContext) -> AnalysisResult:
    """港股资讯仅作可追溯检索参考，独立于舆情打分和充足度门槛。"""
    from data.hk_news import HK_TZ, SOURCE, current_news

    news = context.news_data
    snapshot = news.hk_news if news else None
    disclaimer = "关键词检索结果，可能包含间接相关报道；不作AI筛选或情绪评分，不保证完整覆盖近30天新闻。"
    if snapshot is None:
        return AnalysisResult(dimension="sentiment", status="unavailable", summary="新闻暂时获取失败",
            metrics={"news_items": [], "news_disclaimer": disclaimer, "news_notes": []}, score=None)
    snapshot = current_news(snapshot, context.collected_at)
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
            "content": item.content,
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
        es = context.enriched_sentiment
        if es is not None and es.assessment_status != "legacy":
            inputs = assessment_inputs(context)
            originals = {item["item_id"]: item for item in inputs}
            all_items = [{**originals.get(item.item_id, {}), **item.model_dump(mode="json")}
                         for item in es.all_items]
            score, detail, flags = score_sentiment(context)
            metrics: dict[str, Any] = {
                "sentiment_scoring_version": "risk-v1", "risk_score": score,
                "participation_score": 10 - score if score is not None else None,
                "assessment_status": es.assessment_status, "assessment_reason": es.assessment_reason,
                "evaluated_count": es.evaluated_count,
                "window_start": es.window_start.isoformat() if es.window_start else None,
                "window_end": es.window_end.isoformat() if es.window_end else None,
                "total_count": es.total_count, "positive_count": es.positive_count,
                "negative_count": es.negative_count, "neutral_count": es.neutral_count,
                "all_items": all_items, "news_items": inputs,
                "headline_count": len(inputs), "headlines": [item["title"] for item in inputs],
                "news_disclaimer": "本次限量采集样本，不保证覆盖窗口内全部新闻公告；原文可追溯不代表多源核实。",
            }
            if context.market == "hk":
                fallback = _hk_news_result(context)
                metrics = {**fallback.metrics, **metrics}
            return AnalysisResult(dimension=self.dimension,
                status="ok" if score is not None else ("partial" if inputs else "unavailable"),
                summary=(f"近30天有效评估 {es.evaluated_count} 条；{es.assessment_reason}"),
                metrics=metrics, score=score, score_detail=detail, risk_flags=flags)
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

        return AnalysisResult(dimension=self.dimension, status=status, summary=summary,
                              metrics=metrics)
