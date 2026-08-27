from datetime import date

from analysis.sentiment import SentimentAnalyzer
from data.enrichers.sentiment_enricher import SentimentEnricher
from data.schemas import (
    AnalysisContext,
    DataSufficiency,
    DimensionSufficiency,
    NewsData,
    RawSentimentData,
    RawSentimentItem,
    SufficiencyLevel,
)


def make_ctx(**kwargs) -> AnalysisContext:
    """构造充足度标记为 SUFFICIENT 的上下文，让分析器按自身数据逻辑判定状态"""
    ctx = AnalysisContext(**kwargs)
    ctx.sufficiency = DataSufficiency(
        price=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=100, score_weight=1.0),
        financial=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=4, score_weight=1.0),
        valuation=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=100, score_weight=1.0),
        industry=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=10, score_weight=1.0),
        sentiment=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=10, score_weight=1.0),
    )
    return ctx



class TestSentimentAnalyzer:
    def test_dimension_is_sentiment(self):
        assert SentimentAnalyzer().dimension == "sentiment"

    def test_with_news_data(self):
        ctx = make_ctx(symbol="000001", name="测试", news_data=NewsData(
            symbol="000001", date=date(2026,7,1),
            headlines=["业绩增长超预期", "机构上调目标价", "大股东增持"]
        ))
        result = SentimentAnalyzer().analyze(ctx)
        assert result.status == "ok"
        assert result.metrics["headline_count"] == 3

    def test_empty_headlines_returns_partial(self):
        ctx = make_ctx(symbol="000001", name="测试", news_data=NewsData(
            symbol="000001", date=date(2026,7,1), headlines=[]
        ))
        result = SentimentAnalyzer().analyze(ctx)
        assert result.status == "partial"

    def test_no_data_returns_unavailable(self):
        ctx = AnalysisContext(symbol="000001", name="测试")
        result = SentimentAnalyzer().analyze(ctx)
        assert result.status == "unavailable"


def test_llm_fallback_text_skips_annotation():
    """LLM 返回降级文案时跳过标注，不进入 json.loads 报错路径"""
    class FakeLLM:
        def generate(self, prompt):
            return "（LLM 分析暂时不可用：xxx，请检查 API 配置）"

    raw = RawSentimentData(
        symbol="000001", fetch_date=date(2026, 8, 23),
        items=[RawSentimentItem(title=f"新闻{i}", source="news",
                                publish_date=date(2026, 8, 23)) for i in range(10)],
    )
    ctx = AnalysisContext(symbol="000001", name="测试", raw_sentiment=raw)
    enricher = SentimentEnricher(llm=FakeLLM())
    ctx = enricher.enrich(ctx)
    assert ctx.enriched_sentiment is not None
    assert ctx.enriched_sentiment.all_items == []


def test_llm_empty_response_skips_annotation():
    """LLM 空响应时同样跳过标注"""
    class FakeLLM:
        def generate(self, prompt):
            return ""

    raw = RawSentimentData(
        symbol="000001", fetch_date=date(2026, 8, 23),
        items=[RawSentimentItem(title=f"新闻{i}", source="news",
                                publish_date=date(2026, 8, 23)) for i in range(3)],
    )
    ctx = AnalysisContext(symbol="000001", name="测试", raw_sentiment=raw)
    enricher = SentimentEnricher(llm=FakeLLM())
    ctx = enricher.enrich(ctx)
    assert ctx.enriched_sentiment is not None
    assert ctx.enriched_sentiment.all_items == []
