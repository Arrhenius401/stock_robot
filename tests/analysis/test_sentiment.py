from datetime import date
from analysis.sentiment import SentimentAnalyzer
from data.schemas import AnalysisContext, NewsData


class TestSentimentAnalyzer:
    def test_dimension_is_sentiment(self):
        assert SentimentAnalyzer().dimension == "sentiment"

    def test_with_news_data(self):
        ctx = AnalysisContext(symbol="000001", name="测试", news_data=NewsData(
            symbol="000001", date=date(2026,7,1),
            headlines=["业绩增长超预期", "机构上调目标价", "大股东增持"]
        ))
        result = SentimentAnalyzer().analyze(ctx)
        assert result.status == "ok"
        assert result.metrics["headline_count"] == 3

    def test_empty_headlines_returns_partial(self):
        ctx = AnalysisContext(symbol="000001", name="测试", news_data=NewsData(
            symbol="000001", date=date(2026,7,1), headlines=[]
        ))
        result = SentimentAnalyzer().analyze(ctx)
        assert result.status == "partial"

    def test_no_data_returns_unavailable(self):
        ctx = AnalysisContext(symbol="000001", name="测试")
        result = SentimentAnalyzer().analyze(ctx)
        assert result.status == "unavailable"
