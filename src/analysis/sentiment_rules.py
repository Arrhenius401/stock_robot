"""新舆情风险计分入口：只消费完成并经过校验的评估结果。"""
from data.schemas import AnalysisContext


def score_sentiment(ctx: AnalysisContext) -> tuple[float | None, str, list[str]]:
    es = ctx.enriched_sentiment
    if es is None or es.assessment_status != "complete" or es.risk_score not in (0, 2, 5, 10):
        return None, es.assessment_reason if es else "尚未完成新舆情风险评估", []
    flags = ["major_negative_news"] if es.risk_score == 10 else []
    detail = ("本次有效样本未识别到负面" if es.risk_score == 0
              else f"有效负面最高严重度对应风险 {es.risk_score:g} 分，多条不累加")
    return es.risk_score, detail, flags
