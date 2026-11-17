"""当前价格共用入口：收盘后必须查询，行情时间不能使用采集时间。"""
from datetime import datetime

import pytest

from data.current_quote import collect_current_quote
from data.latest_quote import price_presentation


@pytest.mark.parametrize("market,symbol,path,raw,price", [
    ("a-shares", "002714", "data.latest_quote.load_latest_quote",
     {"status": "ok", "price": 45.82, "quoted_at": "2026-11-17T16:14:24+08:00", "source": "腾讯行情"}, 45.82),
    ("hk", "00700.HK", "data.latest_quote.load_hk_quote",
     {"status": "ok", "price": 376, "quoted_at": "2026-11-17T16:08:19+08:00", "source": "腾讯港股快照"}, 376),
    ("us", "NVDA", "data.us_stock.us_quote",
     {"price": 156.14, "quoted_at": "2026-11-17 17:39:11", "close_time": "Nov 16 04:00PM EST",
      "after_hours_price": 155.4}, 156.14),
])
def test_non_open_sessions_still_fetch(mocker, market, symbol, path, raw, price):
    fetch = mocker.patch(path, return_value=raw)
    quote = collect_current_quote(market, symbol, daily_end="2026-11-16",
                                  now=datetime.fromisoformat("2026-11-17T17:40:00+08:00"))
    fetch.assert_called_once()
    assert quote["status"] == "ok" and quote["price"] == price
    assert quote["quoted_at"] != "2026-11-17T17:40:00+08:00"
    display = price_presentation(quote, 46.3, "2026-11-16")
    assert "UTC" in display["note"] and "来源" not in display["note"]
    assert ("未开盘" if market == "us" else "已收盘") in display["note"]


@pytest.mark.parametrize("stamp", ["2026-11-18T16:00:00+08:00", "2026-11-15T16:00:00+08:00", "2026-11-17T16:00:00"])
def test_invalid_quote_time_falls_back(mocker, stamp):
    mocker.patch("data.latest_quote.load_latest_quote", return_value={
        "status": "ok", "price": 45.82, "quoted_at": stamp})
    quote = collect_current_quote("a-shares", "002714", daily_end="2026-11-16",
                                  now=datetime.fromisoformat("2026-11-17T17:40:00+08:00"))
    assert quote["status"] == "unavailable"
    assert price_presentation(quote, 46.3, "2026-11-16")["value"] == 46.3


def test_weekend_quote_keeps_actual_date(mocker):
    mocker.patch("data.latest_quote.load_latest_quote", return_value={
        "status": "ok", "price": 45.82, "quoted_at": "2026-11-20T15:00:00+08:00"})
    quote = collect_current_quote("a-shares", "002714", daily_end="2026-11-20",
                                  now=datetime.fromisoformat("2026-11-21T10:00:00+08:00"))
    display = price_presentation(quote, 46.3, "2026-11-20")
    assert "休市" in display["note"] and "2026-11-20" in display["note"]


@pytest.mark.parametrize("market,symbol,path", [
    ("a-shares", "002714", "data.latest_quote.load_latest_quote"),
    ("hk", "00700.HK", "data.latest_quote.load_hk_quote"),
])
def test_weekday_old_snapshot_does_not_claim_intraday_quote(mocker, market, symbol, path):
    mocker.patch(path, return_value={"status": "ok", "price": 1,
                                    "quoted_at": "2026-11-16T16:00:00+08:00"})
    quote = collect_current_quote(market, symbol, now=datetime.fromisoformat("2026-11-17T10:00:00+08:00"))
    assert quote["status"] == "ok"
    assert quote["market_state"] == "休市或行情未更新，最近报价"


@pytest.mark.parametrize("now,close,expected", [
    ("2026-07-07T08:00:00-04:00", "Jul 06 04:00PM EDT", "2026-07-06T16:00:00-04:00"),
    ("2027-01-01T08:00:00-05:00", "Dec 31 04:00PM EST", "2026-12-31T16:00:00-05:00"),
    ("2026-11-27T14:00:00-05:00", "Nov 27 01:00PM EST", "2026-11-27T13:00:00-05:00"),
])
def test_us_close_time_handles_dst_year_and_early_close(mocker, now, close, expected):
    mocker.patch("data.us_stock.us_quote", return_value={"price": 100, "close_time": close,
                                                       "after_hours_price": 999})
    quote = collect_current_quote("us", "NVDA", now=datetime.fromisoformat(now))
    assert quote["price"] == 100 and quote["quoted_at"] == expected
    assert "行情未更新" not in quote["market_state"]


def test_us_intraday_uses_source_time_not_collection_time(mocker):
    mocker.patch("data.us_stock.us_quote", return_value={"price": 100,
        "quoted_at": "2026-11-17 23:00:00", "close_time": "Nov 16 04:00PM EST"})
    quote = collect_current_quote("us", "NVDA", now=datetime.fromisoformat("2026-11-17T10:05:00-05:00"))
    assert quote["quoted_at"] == "2026-11-17T10:00:00-05:00"
    assert quote["market_state"] == "交易中"


@pytest.mark.parametrize("close", ["", "Nov 16 04:00PM EDT", "Nov 16 13:00PM EST"])
def test_us_invalid_close_time_never_uses_after_hours(mocker, close):
    mocker.patch("data.us_stock.us_quote", return_value={"price": 100, "close_time": close,
                                                       "after_hours_price": 999})
    quote = collect_current_quote("us", "NVDA", now=datetime.fromisoformat("2026-11-17T08:00:00-05:00"))
    assert quote["status"] == "unavailable"


def test_network_failure_preserves_daily_fallback(mocker):
    mocker.patch("data.us_stock.us_quote", side_effect=TimeoutError("模拟失败"))
    quote = collect_current_quote("us", "NVDA", now=datetime.fromisoformat("2026-11-17T08:00:00-05:00"))
    display = price_presentation(quote, 90, "2026-11-16")
    assert display["value"] == 90 and display["kind"] == "close_fallback"
    assert display["note"] == "报价暂不可用，日线收盘价；截至 2026-11-16"


@pytest.mark.parametrize("stamp", [None, "", "bad", "2026-11-17T15:00:00"])
def test_legacy_invalid_timestamp_does_not_break_report(stamp):
    display = price_presentation({"status": "ok", "price": 100, "quoted_at": stamp}, 90, "2026-11-16")
    assert display["value"] == 90 and "行情时间无效" in display["note"]
