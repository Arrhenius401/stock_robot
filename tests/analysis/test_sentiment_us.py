"""美股新闻参考：客观模式下提示关键词检索的局限。"""
from datetime import date

from analysis.sentiment import SentimentAnalyzer
from data.schemas import (
    AnalysisContext,
    DataSufficiency,
    DimensionSufficiency,
    NewsData,
    SufficiencyLevel,
)


def test_us_objective_news_has_keyword_disclaimer():
    context = AnalysisContext(
        symbol="MSFT", name="微软", market="us",
        news_data=NewsData(symbol="MSFT", date=date(2026, 11, 13), headlines=["微软发布服务声明"]),
        extra_analysis_enabled=False,
        sufficiency=DataSufficiency(sentiment=DimensionSufficiency(
            level=SufficiencyLevel.SUFFICIENT, sample_count=1,
        )),
    )
    result = SentimentAnalyzer().analyze(context)
    assert result.status in {"ok", "partial"}
    assert "关键词检索" in result.metrics["news_disclaimer"]
    assert "同名或间接报道" in result.metrics["news_disclaimer"]
