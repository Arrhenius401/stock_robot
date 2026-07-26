"""充实器单元测试"""
import pytest
from datetime import date, timedelta
from data.schemas import (
    AnalysisContext,
    FinancialData,
    PriceData,
    SufficiencyLevel,
    ValuationData,
)
from data.enrichers.price_enricher import PriceEnricher
from data.enrichers.financial_enricher import FinancialEnricher
from data.enrichers.valuation_enricher import ValuationEnricher


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


def make_price_series(n: int, close: float = 10.0) -> list[PriceData]:
    return [
        PriceData(symbol="000001", trade_date=date(2025, 7, 1) + timedelta(days=i),
                  open=close-0.1, high=close+0.1, low=close-0.2, close=close,
                  volume=1000000)
        for i in range(n)
    ]


class TestValuationEnricher:
    def test_insufficient_when_price_partial(self):
        """行情数据不足 60 条时，估值标记为 insufficient"""
        prices = make_price_series(30)
        financials = make_financial_data(4)
        ctx = AnalysisContext(symbol="000001", name="测试",
                              price_data=prices, financial_data=financials)
        ctx = PriceEnricher().enrich(ctx)
        ctx = FinancialEnricher().enrich(ctx)
        ctx = ValuationEnricher().enrich(ctx)
        assert ctx.sufficiency.valuation.level == SufficiencyLevel.INSUFFICIENT

    def test_insufficient_no_financial(self):
        """无财务数据时标记 insufficient"""
        prices = make_price_series(200)
        ctx = AnalysisContext(symbol="000001", name="测试",
                              price_data=prices, financial_data=None)
        ctx = PriceEnricher().enrich(ctx)
        ctx = ValuationEnricher().enrich(ctx)
        assert ctx.sufficiency.valuation.level == SufficiencyLevel.INSUFFICIENT

    def test_sufficient_with_valid_data(self):
        """有足够的行情和财务数据时正常生成估值序列"""
        prices = make_price_series(200, close=10.0)
        financials = make_financial_data(4)
        ctx = AnalysisContext(symbol="000001", name="测试",
                              price_data=prices, financial_data=financials)
        # 模拟 valuation_data（当前单时点估值，用于总股本回退逻辑）
        ctx.valuation_data = ValuationData(symbol="000001", date=date.today(),
                                           pe_ttm=7.5, pb=0.85, ps_ttm=1.2)
        ctx = PriceEnricher().enrich(ctx)
        ctx = FinancialEnricher().enrich(ctx)
        ctx = ValuationEnricher().enrich(ctx)
        assert ctx.sufficiency.valuation.level == SufficiencyLevel.SUFFICIENT
        assert ctx.enriched_valuation is not None
        assert len(ctx.enriched_valuation.daily_points) > 0
        assert ctx.enriched_valuation.pe_percentile is not None
