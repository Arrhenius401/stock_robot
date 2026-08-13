from datetime import date, timedelta

from analysis.technical import TechnicalAnalyzer
from data.schemas import AnalysisContext, PriceData


class TestTechnicalAnalyzer:
    def test_dimension_is_technical(self):
        a = TechnicalAnalyzer()
        assert a.dimension == "technical"

    def test_full_data_analysis(self):
        prices = []
        base = date(2026, 1, 5)
        for i in range(120):
            prices.append(PriceData(
                symbol="000001",
                trade_date=base + timedelta(days=i),
                open=10.0 + i * 0.02,
                high=10.5 + i * 0.02,
                low=9.8 + i * 0.02,
                close=10.3 + i * 0.02,
                volume=5000000 + i * 10000,
            ))
        ctx = AnalysisContext(symbol="000001", name="测试", price_data=prices)
        result = TechnicalAnalyzer().analyze(ctx)
        assert result.status == "ok"
        assert "latest_close" in result.metrics
        # MA/MACD/量价计算已移至 GeneralScorer.score_technical()，通过 config 驱动

    def test_insufficient_price_data_returns_partial(self):
        prices = [PriceData(symbol="000001", trade_date=date(2026,7,1), open=10, high=11, low=9.5, close=10.5, volume=1_000_000)]
        ctx = AnalysisContext(symbol="000001", name="测试", price_data=prices)
        result = TechnicalAnalyzer().analyze(ctx)
        assert result.status == "partial"

    def test_no_price_data_returns_unavailable(self):
        ctx = AnalysisContext(symbol="000001", name="测试")
        result = TechnicalAnalyzer().analyze(ctx)
        assert result.status == "unavailable"
