"""指数充实器测试"""
import pytest
from datetime import date, timedelta
from src.index.enricher import compute_percentile, IndexValuationEnricher, tag_valuation
from src.data.schemas import (
    AnalysisTarget, IndexAnalysisContext, IndexValuationData,
    IndexPriceData
)


class TestComputePercentile:
    def test_percentile_midpoint(self):
        """当前值恰好为中位数的分位"""
        values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        pct = compute_percentile(50, values)
        assert abs(pct - 50.0) < 5  # 中位数附近

    def test_percentile_low(self):
        values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        pct = compute_percentile(5, values)
        assert pct < 10

    def test_percentile_high(self):
        values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        pct = compute_percentile(200, values)
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
            symbol="000300", date=date.today(),
            pe_ttm=12.5, pb=1.4,
        )
        daily = []
        base = date.today() - timedelta(days=1200)
        for i in range(1000):
            daily.append(base + timedelta(days=i))

        enricher = IndexValuationEnricher()
        daily_pe = [10 + (i % 10) for i in range(1000)]
        result = enricher.enrich(ctx, daily_pe_values=daily_pe, daily_pb_values=[])

        assert result.valuation_data.pe_percentile is not None
