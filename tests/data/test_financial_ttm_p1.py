"""现金流跨市场期间与缺失回归。"""
from datetime import date

import pytest

from data.financial_ttm import financial_ttm
from data.schemas import FinancialData


@pytest.mark.parametrize("market", ["a-shares", "hk"])
def test_cumulative_cash_flow_ttm(market):
    rows = [FinancialData(symbol="TEST", fiscal_quarter=period, operating_cash_flow=value,
                          amount_currency_verified=market == "hk", report_currency="HKD" if market == "hk" else "CNY")
            for period, value in [(date(2025, 6, 30), 20), (date(2025, 12, 31), 50), (date(2026, 6, 30), 30)]]
    assert financial_ttm(rows, "operating_cash_flow", market) == 60
    assert financial_ttm(rows, "capital_expenditure", market) is None
    assert financial_ttm(rows[1:], "operating_cash_flow", market) is None


@pytest.mark.parametrize("amount", [-5, 5, None])
def test_us_capex_sign_and_missing(mocker, amount):
    from data.us_stock import us_financials
    from tests.data.test_us_stock import _statement_frames

    frames = _statement_frames()
    for row in frames["现金流量表"].values():
        if amount is not None:
            row["005002"] = amount
    mocker.patch("data.us_stock._statement", side_effect=lambda _ticker, name: frames[name])
    mocker.patch("data.us_stock._annual_roe", return_value=(None, None))
    rows = us_financials("MSFT")
    latest = rows[-1]
    assert latest.capital_expenditure == (5 if amount is not None else None)
    assert latest.free_cash_flow == (19 if amount is not None else None)
    assert financial_ttm(rows, "capital_expenditure", "us") == (20 if amount is not None else None)


@pytest.mark.parametrize("market", ["a-shares", "hk", "us"])
def test_three_market_measured_percentile(market):
    from data.enrichers.valuation_enricher import ValuationEnricher
    from tests.data.test_hk_valuation_history import history_context

    ctx = history_context()
    ctx.market = market
    assert ctx.valuation_data is not None
    ctx.valuation_data.history_source = "百度实测估值（近一年）"
    enriched = ValuationEnricher().enrich(ctx).enriched_valuation
    assert enriched is not None
    assert enriched.pe_history_summary.sample_count == 130
    assert enriched.pe_percentile == pytest.approx(round(129 / 130 * 100, 1))


@pytest.mark.parametrize("equity,revenue,field,reason", [(-10, 10, "pb", "净资产非正"), (10, 0, "ps_ttm", "TTM 营收非正")])
def test_nonpositive_valuation_denominator(equity, revenue, field, reason):
    from analysis.valuation import ValuationAnalyzer
    from tests.analysis.test_valuation_us import _context

    ctx = _context(1)
    assert ctx.financial_data is not None
    for row in ctx.financial_data:
        row.common_equity = equity
        row.revenue = revenue
    result = ValuationAnalyzer().analyze(ctx)
    assert result.metrics[field] is None
    assert reason in result.metrics["valuation_applicability"][field]["reason"]



def test_measured_history_does_not_depend_on_price_window():
    from data.enrichers.valuation_enricher import ValuationEnricher
    from tests.data.test_hk_valuation_history import history_context

    ctx = history_context()
    ctx.price_data = []
    enriched = ValuationEnricher().enrich(ctx).enriched_valuation
    assert enriched is not None
    assert enriched.pe_history_summary.sample_count == 130
    assert all(point.close is None for point in enriched.daily_points)



def test_ps_requires_verified_currency_and_discloses_dates():
    from data.enrichers.valuation_enricher import ValuationEnricher
    from tests.analysis.test_valuation_us import _context

    ctx = _context(1)
    assert ctx.valuation_data is not None
    assert ctx.financial_data is not None
    ctx.valuation_data.market_cap = 100
    ctx.valuation_data.market_cap_as_of = date(2026, 11, 13)
    ValuationEnricher().enrich(ctx)
    assert ctx.valuation_data.ps_ttm is None
    for row in ctx.financial_data:
        row.amount_currency_verified = True
        row.report_currency = "USD"
    ValuationEnricher().enrich(ctx)
    assert ctx.valuation_data.ps_ttm == 2.5
    assert any("市值日期 2026-11-13" in note and "截至 2026-12-30" in note
               for note in ctx.valuation_data.valuation_notes)
