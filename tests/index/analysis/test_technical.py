"""指数技术面分析测试"""
import pytest
from datetime import date, timedelta
from src.index.analysis.technical import IndexTechnicalAnalyzer
from src.data.schemas import AnalysisTarget, IndexAnalysisContext, IndexPriceData


@pytest.fixture
def tech_ctx():
    target = AnalysisTarget(
        target_type="index", symbol="000300",
        name="沪深300", market="a-shares", index_style="broad"
    )
    ctx = IndexAnalysisContext(target=target)
    base = date.today() - timedelta(days=120)
    prices = []
    for i in range(120):
        prices.append(IndexPriceData(
            symbol="000300", trade_date=base + timedelta(days=i),
            open=4000 + i * 2, high=4010 + i * 2,
            low=3990 + i * 2, close=4005 + i * 2,
            volume=1000000, change_pct=0.1
        ))
    ctx.price_data = prices
    return ctx


class TestIndexTechnicalAnalyzer:
    def test_dimension(self):
        analyzer = IndexTechnicalAnalyzer()
        assert analyzer.dimension == "index_technical"

    def test_analyze_bull_trend(self, tech_ctx):
        analyzer = IndexTechnicalAnalyzer()
        result = analyzer.analyze(tech_ctx)
        assert result.status in ("ok", "partial")
        assert "tag" in result.metrics
        assert result.metrics["tag"] in ("bull", "shake", "bear")

    def test_analyze_no_data(self):
        target = AnalysisTarget(
            target_type="index", symbol="000300",
            name="沪深300", market="a-shares", index_style="broad"
        )
        ctx = IndexAnalysisContext(target=target)
        analyzer = IndexTechnicalAnalyzer()
        result = analyzer.analyze(ctx)
        assert result.status == "unavailable"
