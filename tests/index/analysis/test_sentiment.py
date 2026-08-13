"""指数舆情面分析测试"""
from src.data.schemas import (
    AnalysisTarget,
    IndexAnalysisContext,
)
from src.index.analysis.sentiment import IndexSentimentAnalyzer


class TestIndexSentimentAnalyzer:
    def test_dimension(self):
        assert IndexSentimentAnalyzer().dimension == "index_sentiment"

    def test_analyze_no_data(self):
        target = AnalysisTarget(
            target_type="index", symbol="000300",
            name="沪深300", market="a-shares", index_style="broad"
        )
        ctx = IndexAnalysisContext(target=target)
        result = IndexSentimentAnalyzer().analyze(ctx)
        assert result.status == "unavailable"
        assert "暂无有效市场舆情信号" in result.summary
