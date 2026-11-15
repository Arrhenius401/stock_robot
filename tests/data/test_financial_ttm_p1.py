"""现金流跨市场期间与缺失回归。"""
from datetime import date

import pytest

from data.financial_ttm import financial_ttm
from data.schemas import FinancialData


@pytest.mark.parametrize("market", ["a-shares", "hk"])
def test_cumulative_cash_flow_ttm(market):
    rows = [FinancialData(symbol="TEST", fiscal_quarter=period, operating_cash_flow=value)
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
