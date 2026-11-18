from datetime import date

import pytest

from data.schemas import IndexClosePriceData
from index.hk_ohlc import fetch_hstech_ohlc, match_hstech_ohlc


@pytest.mark.parametrize("change", ["gap", "mismatch", "bounds", "duplicate", "valid"])
def test_hstech_ohlc_matches_all_official_days_or_rejects(change):
    rows = [IndexClosePriceData(symbol="HSTECH", trade_date=date(2026, 11, day), close=100)
            for day in (16, 17)]
    quotes = [[str(row.trade_date), "99", "100.004", "101", "98"] for row in rows]
    if change == "gap":
        quotes.pop()
    elif change == "mismatch":
        quotes[-1][2] = "100.006"
    elif change == "bounds":
        quotes[-1][3] = "99"
    elif change == "duplicate":
        quotes.append(quotes[-1])
    result = match_hstech_ohlc(rows, quotes)
    if change == "valid":
        assert result is not None
        assert [(r.open, r.close, r.high, r.low) for r in result] == [(99, 100, 101, 98)] * 2
        assert result[0]._data_source == "恒生官方收盘 + 腾讯 OHLC"
    else:
        assert result is None


def test_hstech_request_failure_preserves_official_input(mocker):
    from requests import Timeout

    mocker.patch("index.hk_ohlc.tencent_history", side_effect=Timeout())
    official = [IndexClosePriceData(symbol="HSTECH", trade_date=date(2026, 11, 17), close=100)]
    assert fetch_hstech_ohlc(official) is None
    assert official[0].model_dump(exclude_none=True) == {
        "symbol": "HSTECH", "trade_date": date(2026, 11, 17), "close": 100,
    }
