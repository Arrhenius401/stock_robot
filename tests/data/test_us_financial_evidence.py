"""现金流证据必须来自源表，缺失或冲突不能借用利润表背书。"""
from datetime import date, timedelta

import pytest

from analysis.financial_scoring import cashflow_compatible
from data.us_stock import us_financials
from tests.data.test_us_stock import _statement_frames


@pytest.mark.parametrize("problem", ["", "currency", "accounting", "missing"])
def test_us_cashflow_positions_and_net_margin(mocker, problem):
    frames = _statement_frames()
    periods = sorted(frames["综合损益表"])
    for index, end in enumerate(periods):
        start = (date.fromisoformat(periods[index - 1]) + timedelta(days=1)).isoformat() if index else "2025-06-28"
        for name in ("综合损益表", "现金流量表"):
            frames[name][end]["_meta"] = {"start": start, "currency": "USD", "accounting": "US GAAP"}
    cash = frames["现金流量表"][periods[-1]]["_meta"]
    if problem == "currency":
        cash["currency"] = "EUR"
    elif problem == "accounting":
        cash["accounting"] = "IFRS"
    elif problem == "missing":
        cash.clear()
    mocker.patch("data.us_stock._statement", side_effect=lambda _ticker, name: frames[name])
    latest = us_financials("AAPL")[-1]
    assert latest.net_margin == pytest.approx(.1)
    assert "计算值" in latest.net_margin_basis
    assert cashflow_compatible(latest) is (not problem)
    if not problem:
        assert latest.field_positions["operating_cash_flow"]["currency"] == "USD"
        assert latest.field_positions["operating_cash_flow"]["start"] == latest.field_positions["net_profit"]["start"]
