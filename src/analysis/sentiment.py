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

        # ===== 打分逻辑 =====
        from data.schemas import SufficiencyLevel

        if context.sufficiency and context.sufficiency.sentiment.level == SufficiencyLevel.INSUFFICIENT:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="舆情数据不足", metrics=metrics,
                                  score=None, score_detail="舆情维度数据不足，跳过打分")

        es = context.enriched_sentiment
        score = 5.0
        score_parts = ["基础分 5"]
        risk_flags = []

        if es and es.total_count > 0:
            if es.total_count >= 10:
                score += 2; score_parts.append(f"有效消息 {es.total_count} 条（充足），+2 分")
            else:
                score += 1; score_parts.append(f"有效消息 {es.total_count} 条（偏少），+1 分")
            pos_ratio = es.positive_count / es.total_count if es.total_count > 0 else 0
            if pos_ratio > 0.5:
                score += 2; score_parts.append(f"利好占比 {pos_ratio:.0%}，+2 分")
            elif pos_ratio > 0.3:
                score += 1; score_parts.append(f"利好占比 {pos_ratio:.0%}，+1 分")
            else:
                score_parts.append(f"利好占比 {pos_ratio:.0%}，不加分")
            if es.negative_count > 0 and any(e.severity == "major" for e in es.all_items):
                score -= 1; score_parts.append("存在重大利空事件，-1 分")
                risk_flags.append("major_negative_news")
            if any(e.source == "announcement" for e in es.all_items if hasattr(e, 'source')):
                score += 1; score_parts.append("包含官方公告，+1 分")

        score = round(max(0.0, min(10.0, score)), 1)
        score_detail = "；".join(score_parts)
        return AnalysisResult(dimension=self.dimension, status=status, summary=summary,
                              metrics=metrics, score=score, score_detail=score_detail,
                              risk_flags=risk_flags)
