from datetime import date, datetime, timedelta

import pytest

from analysis.valuation import ValuationAnalyzer
from data.enrichers.valuation_enricher import ValuationEnricher
from data.schemas import AnalysisContext, PriceData, ValuationData


def context(count=260):
    collected = datetime.now().astimezone()
    start = collected.date() - timedelta(days=1800)
    days = [start + timedelta(days=index) for index in range(count)]
    valuation = ValuationData(
        symbol="600519", date=days[-1], pe_ttm=21.9, pb=6.6, ps_ttm=10.3,
        ps_as_of=days[-1], ps_source="东财日频估值历史-市销率（口径未声明）",
        pe_history={day: 20.0 for day in days},
        pb_history={day: 6.0 for day in days},
        extended_pe_history={day: 20.0 for day in days},
        extended_pb_history={day: 6.0 for day in days},
        history_source="东财日频估值历史", history_window_days=1827,
        history_window_label="近五年",
    )
    prices = [PriceData(symbol="600519", trade_date=day, open=1, high=1, low=1,
                        close=1, volume=1) for day in days]
    return AnalysisContext(symbol="600519", name="贵州茅台", market="a-shares",
                           collected_at=collected, valuation_data=valuation,
                           price_data=prices)


def test_extended_history_accepts_eastmoney_source_and_requires_252():
    result = ValuationEnricher.extended_history(context())
    assert result["pe"]["source"] == "东财日频估值历史"
    assert result["pe"]["percentile"] is not None
    assert result["pe"]["sample_count"] == 260
    small = ValuationEnricher.extended_history(context(251))
    assert small["pe"]["percentile"] is None


def test_valuation_analyzer_exposes_ps_and_long_window():
    ctx = context()
    result = ValuationAnalyzer().analyze(ctx)
    assert result.metrics["ps_ttm"] == pytest.approx(10.3)
    assert result.metrics["extended_valuation_history"]["pe"]["window"] == "近五年"
    assert any("PS(TTM)" in item for item in result.metrics.get("valuation_details", []))


def test_missing_ps_is_derived_from_market_cap_and_ttm_revenue():
    from data.schemas import FinancialData

    rows = [
        FinancialData(symbol="600519", fiscal_quarter=date(2024, 12, 31),
                      period_start=date(2024, 1, 1), period_basis="cumulative",
                      revenue=400, net_profit=40, field_precision={"revenue": "source_native", "net_profit": "source_native"}),
        FinancialData(symbol="600519", fiscal_quarter=date(2025, 9, 30),
                      period_start=date(2025, 1, 1), period_basis="cumulative",
                      revenue=300, net_profit=30, field_precision={"revenue": "source_native", "net_profit": "source_native"}),
        FinancialData(symbol="600519", fiscal_quarter=date(2025, 12, 31),
                      period_start=date(2025, 1, 1), period_basis="cumulative",
                      revenue=400, net_profit=40, field_precision={"revenue": "source_native", "net_profit": "source_native"}),
        FinancialData(symbol="600519", fiscal_quarter=date(2026, 9, 30),
                      period_start=date(2026, 1, 1), period_basis="cumulative",
                      revenue=360, net_profit=36, field_precision={"revenue": "source_native", "net_profit": "source_native"}),
    ]
    ctx = AnalysisContext(symbol="600519", name="贵州茅台", market="a-shares",
                          valuation_data=ValuationData(symbol="600519", date=date(2026, 9, 30),
                                                       pe_ttm=20, pb=6, market_cap=1000),
                          financial_data=rows)
    ValuationEnricher._fill_derived_ps(ctx)
    assert ctx.valuation_data is not None
    assert ctx.valuation_data.ps_ttm == pytest.approx(1000 / 460)
    assert "派生" in ctx.valuation_data.ps_source


def test_verified_eastmoney_shares_take_priority_without_network(mocker):
    ctx = context()
    assert ctx.valuation_data is not None
    ctx.valuation_data.total_shares = 1250081601
    ctx.valuation_data.total_shares_as_of = ctx.valuation_data.date
    ctx.valuation_data.total_shares_source = "东财日频估值历史"
    mocker.patch("data.enrichers.valuation_enricher.get_total_shares_result", side_effect=AssertionError("不应联网"))
    assert ValuationEnricher()._get_total_shares(ctx, 100, 100) == 1250081601
