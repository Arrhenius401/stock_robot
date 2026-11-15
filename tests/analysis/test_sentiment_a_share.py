"""A 股新闻参考：提示个股新闻/公告接口的间接报道边界。"""
from datetime import date

from analysis.sentiment import SentimentAnalyzer
from data.schemas import (
    AnalysisContext,
    DataSufficiency,
    DimensionSufficiency,
    NewsData,
    RawSentimentData,
    RawSentimentItem,
    SufficiencyLevel,
)


def _news() -> NewsData:
    news = NewsData(
        symbol="600519", date=date(2026, 11, 13),
        headlines=["贵州茅台发布公告", "2只个股大宗交易超5000万元"],
    )
    news._raw_sentiment = RawSentimentData(
        symbol="600519", fetch_date=date(2026, 11, 13),
        items=[
            RawSentimentItem(title="贵州茅台发布公告", source="announcement",
                             publish_date=date(2026, 11, 13), content="",
                             url="https://example.com/1", media="公告"),
            RawSentimentItem(title="2只个股大宗交易超5000万元", source="news",
                             publish_date=date(2026, 11, 13), content="",
                             url="https://example.com/2", media="证券时报"),
        ],
    )
    return news


def test_a_share_objective_news_has_source_disclaimer():
    context = AnalysisContext(
        symbol="600519", name="贵州茅台", market="a-shares",
        news_data=_news(),
        extra_analysis_enabled=False,
        sufficiency=DataSufficiency(sentiment=DimensionSufficiency(
            level=SufficiencyLevel.SUFFICIENT, sample_count=2,
        )),
    )
    result = SentimentAnalyzer().analyze(context)
    assert "个股新闻与公告接口" in result.metrics["news_disclaimer"]
    assert result.metrics["headlines"] == ["贵州茅台发布公告"]
