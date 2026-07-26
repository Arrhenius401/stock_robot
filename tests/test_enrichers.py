"""充实器单元测试"""
import pytest
from datetime import date, timedelta
from data.schemas import (
    AnalysisContext,
    FinancialData,
    PriceData,
    SufficiencyLevel,
)
from data.enrichers.price_enricher import PriceEnricher
from data.enrichers.financial_enricher import FinancialEnricher


def make_price_data(n: int) -> list[PriceData]:
    """生成 n 条行情数据"""
    return [
        PriceData(
            symbol="000001",
            trade_date=date(2026, 1, 1) + timedelta(days=i),
            open=10.0,
            high=10.5,
            low=9.8,
            close=10.2,
            volume=1000000,
        )
        for i in range(n)
    ]


class TestPriceEnricher:
    def test_sufficient_60_plus(self):
        ctx = AnalysisContext(symbol="000001", name="测试", price_data=make_price_data(120))
        enricher = PriceEnricher()
        result = enricher.enrich(ctx)
        assert result.sufficiency.price.level == SufficiencyLevel.SUFFICIENT
        assert result.sufficiency.price.score_weight == 1.0
        assert result.sufficiency.price.sample_count == 120

    def test_partial_20_to_59(self):
        ctx = AnalysisContext(symbol="000001", name="测试", price_data=make_price_data(40))
        enricher = PriceEnricher()
        result = enricher.enrich(ctx)
        assert result.sufficiency.price.level == SufficiencyLevel.PARTIAL
        assert result.sufficiency.price.score_weight == 0.5

    def test_insufficient_below_20(self):
        ctx = AnalysisContext(symbol="000001", name="测试", price_data=make_price_data(10))
        enricher = PriceEnricher()
        result = enricher.enrich(ctx)
        assert result.sufficiency.price.level == SufficiencyLevel.INSUFFICIENT
        assert result.sufficiency.price.score_weight == 0.0

    def test_none_price_data_is_insufficient(self):
        ctx = AnalysisContext(symbol="000001", name="测试", price_data=None)
        enricher = PriceEnricher()
        result = enricher.enrich(ctx)
        assert result.sufficiency.price.level == SufficiencyLevel.INSUFFICIENT


def make_financial_data(n: int) -> list[FinancialData]:
    return [
        FinancialData(
            symbol="000001",
            fiscal_quarter=date(2025, 12 - i * 3, 1) if (12 - i * 3) > 0 else date(2025, 12, 1),
            revenue=100e8, net_profit=10e8, deducted_net_profit=9e8,
            total_assets=500e8, total_equity=50e8, operating_cash_flow=8e8,
            roe=0.12, gross_margin=0.45,
        )
        for i in range(n)
    ]


class TestFinancialEnricher:
    def test_sufficient_4_plus_complete(self):
        ctx = AnalysisContext(symbol="000001", name="测试",
                              financial_data=make_financial_data(6))
        ctx = PriceEnricher().enrich(ctx)
        ctx = FinancialEnricher().enrich(ctx)
        assert ctx.sufficiency.financial.level == SufficiencyLevel.SUFFICIENT
        assert ctx.sufficiency.financial.score_weight == 1.0
        assert ctx.sufficiency.financial.sample_count == 6

    def test_partial_2_to_3(self):
        ctx = AnalysisContext(symbol="000001", name="测试",
                              financial_data=make_financial_data(2))
        ctx = PriceEnricher().enrich(ctx)
        ctx = FinancialEnricher().enrich(ctx)
        assert ctx.sufficiency.financial.level == SufficiencyLevel.PARTIAL
        assert ctx.sufficiency.financial.score_weight == 0.5

    def test_insufficient_less_than_2(self):
        ctx = AnalysisContext(symbol="000001", name="测试",
                              financial_data=make_financial_data(1))
        ctx = PriceEnricher().enrich(ctx)
        ctx = FinancialEnricher().enrich(ctx)
        assert ctx.sufficiency.financial.level == SufficiencyLevel.INSUFFICIENT
        assert ctx.sufficiency.financial.score_weight == 0.0

    def test_insufficient_missing_profits(self):
        data = make_financial_data(4)
        for d in data:
            d.net_profit = None
        ctx = AnalysisContext(symbol="000001", name="测试", financial_data=data)
        ctx = PriceEnricher().enrich(ctx)
        ctx = FinancialEnricher().enrich(ctx)
        assert ctx.sufficiency.financial.level == SufficiencyLevel.INSUFFICIENT
