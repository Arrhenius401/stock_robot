from datetime import date

import pytest

from data.us_stock import us_financials, us_valuation


@pytest.mark.parametrize("close_time,expected", [
    ("2026-11-23T16:00:00-05:00", date(2026, 11, 23)),
    ("invalid", date(2026, 11, 24)),
])
def test_market_cap_uses_quote_close_date(mocker, close_time, expected):
    mocker.patch("data.us_stock._baidu_metric", return_value={})
    mocker.patch("data.us_stock.us_quote", return_value={
        "market_cap": 1000, "quoted_at": "2026-11-24", "close_time": close_time,
    })
    # 用现有结果类型隔离股息网络边界。
    from data.dividend_yield import DividendYield
    mocker.patch("data.dividend_yield.us_dividend_yield", return_value=DividendYield(None, None, "", ""))
    assert us_valuation("MSFT")[0].market_cap_as_of == expected


def test_msft_gets_five_year_history(mocker):
    calls = []

    def fake_baidu(_ticker, metric, chart_select="近一年"):
        calls.append(chart_select)
        value = 10.0 if metric == "市盈率(TTM)" else 2.0
        if chart_select == "近五年":
            return {date(2022, 1, 3): value, date(2026, 11, 20): value}
        return {date(2026, 11, 20): value}

    mocker.patch("data.us_stock._baidu_metric", side_effect=fake_baidu)
    mocker.patch("data.us_stock.us_quote", return_value={"market_cap": 1e12, "quoted_at": "2026-11-24"})
    row = us_valuation("MSFT")[0]
    assert "近五年" in calls
    assert row.history_window_days == 1827
    assert row.extended_pe_history


def test_us_financials_prefers_same_period_balance(mocker):
    from tests.data.test_us_stock import _statement_frames

    frames = _statement_frames()
    latest = "2026-09-25"
    frames["资产负债表"][latest] = {"004005999": 1040, "004013999": 520, "004017999": 520}
    mocker.patch("data.us_stock._statement", side_effect=lambda _ticker, name: frames[name])
    mocker.patch("data.us_stock._annual_roe", return_value=(1.5, date(2026, 9, 25)))
    latest_row = us_financials("AAPL")[-1]
    assert latest_row.total_assets == 1040
    assert "资产负债表最近可得" not in latest_row.financial_selection_note


def test_us_ps_derives_without_pe_pb():
    from data.enrichers.valuation_enricher import ValuationEnricher
    from data.schemas import AnalysisContext, FinancialData, ValuationData

    ctx = AnalysisContext(symbol="MSFT", name="MSFT", market="us",
        valuation_data=ValuationData(symbol="MSFT", date=date(2026, 11, 23),
            market_cap=1000, market_cap_as_of=date(2026, 11, 23)),
        financial_data=[FinancialData(symbol="MSFT", fiscal_quarter=date(2026, 9, 30),
            reported_revenue_ttm=200, reported_revenue_source="SEC XBRL")])
    ValuationEnricher().enrich(ctx)
    assert ctx.valuation_data is not None
    assert ctx.enriched_valuation is not None
    assert ctx.valuation_data.ps_ttm == 5
    assert ctx.valuation_data.ps_as_of == date(2026, 11, 23)
    assert ctx.enriched_valuation.pe_percentile is None
    assert ctx.enriched_valuation.pb_percentile is None
