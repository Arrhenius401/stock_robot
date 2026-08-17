"""指数充实器测试"""
from datetime import datetime, timedelta

from src.data.schemas import AnalysisTarget, IndexAnalysisContext, IndexValuationData
from src.index.enricher import IndexValuationEnricher, compute_percentile, tag_valuation


class TestComputePercentile:
    def test_percentile_midpoint(self):
        """当前值恰好为中位数的分位"""
        values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        pct = compute_percentile(50, values)
        assert pct is not None
        assert abs(pct - 50.0) < 5  # 中位数附近

    def test_percentile_low(self):
        values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        pct = compute_percentile(5, values)
        assert pct is not None
        assert pct < 10

    def test_percentile_high(self):
        values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
        pct = compute_percentile(200, values)
        assert pct is not None
        assert pct > 90

    def test_percentile_empty(self):
        assert compute_percentile(50, []) is None

    def test_percentile_single(self):
        assert compute_percentile(50, [50]) == 50.0


class TestTagValuation:
    def test_undervalued(self):
        assert tag_valuation(15) == "undervalued"

    def test_neutral(self):
        assert tag_valuation(40) == "neutral"

    def test_overvalued(self):
        assert tag_valuation(85) == "overvalued"

    def test_invalid_none(self):
        assert tag_valuation(None) == "invalid"


class TestIndexValuationEnricher:
    def test_enrich_computes_percentiles(self):
        target = AnalysisTarget(
            target_type="index", symbol="000300",
            name="沪深300", market="a-shares", index_style="broad"
        )
        ctx = IndexAnalysisContext(target=target)
        ctx.valuation_data = IndexValuationData(
            symbol="000300", date=datetime.now().astimezone().astimezone().date(),
            pe_ttm=12.5, pb=1.4,
        )
        daily = []
        base = datetime.now().astimezone().astimezone().date() - timedelta(days=1200)
        for i in range(1000):
            daily.append(base + timedelta(days=i))

        enricher = IndexValuationEnricher()
        daily_pe = [10.0 + (i % 10) for i in range(1000)]
        result = enricher.enrich(ctx, daily_pe_values=daily_pe, daily_pb_values=[])

        val = result.valuation_data
        assert val is not None
        assert val.pe_percentile is not None
