"""最新报价的身份、时点与顺序降级，真实响应字段布局来自2026-11-14探测。"""
from datetime import datetime
from unittest.mock import Mock

import pytest

from data.latest_quote import (
    load_latest_quote,
    parse_eastmoney,
    parse_sina,
    parse_tencent,
    price_presentation,
)

NOW = datetime.fromisoformat("2026-11-14T16:00:00+08:00")


def tencent(price="18.75", symbol="000338", stamp="20261113161430"):
    fields = [""] * 47
    fields[1], fields[2], fields[3], fields[4], fields[30] = "潍柴动力", symbol, price, "19.80", stamp
    return 'v_sz000338="' + "~".join(fields) + '";'


def sina():
    fields = [""] * 33
    fields[0], fields[2], fields[3], fields[30], fields[31] = "潍柴动力", "19.80", "18.75", "2026-11-13", "16:11:30"
    return 'var hq_str_sz000338="' + ",".join(fields) + '";'


def test_three_parsers_agree_and_keep_source_time():
    quotes = [parse_tencent(tencent(), "000338"), parse_sina(sina(), "000338"),
              parse_eastmoney({"data": {"f57": "000338", "f43": 18.75, "f60": 19.8,
                                       "f86": 1794555255}}, "000338")]
    assert all(q["price"] == 18.75 for q in quotes)
    assert all(q["quoted_at"].startswith("2026-11-13") for q in quotes)
    assert all(q["quoted_at"] != NOW.isoformat() for q in quotes)


@pytest.mark.parametrize("raw", [tencent(price="0"), tencent(price="NaN"), tencent(symbol="601899"), tencent(stamp="bad")])
def test_bad_values_or_identity_rejected(raw):
    with pytest.raises(ValueError):
        parse_tencent(raw, "000338")


def test_primary_success_does_not_call_backup(monkeypatch):
    request = Mock(return_value=Mock(text=tencent(), raise_for_status=lambda: None))
    monkeypatch.setattr("data.latest_quote.requests.get", request)
    q = load_latest_quote("000338", now=NOW)
    assert q["source"] == "腾讯行情" and request.call_count == 1


def test_failed_primary_uses_sina_and_bad_time_rejects(monkeypatch):
    request = Mock(side_effect=[TimeoutError("主源超时"), Mock(text=sina(), raise_for_status=lambda: None)])
    monkeypatch.setattr("data.latest_quote.requests.get", request)
    q = load_latest_quote("000338", now=NOW)
    assert q["source"] == "新浪行情" and len(q["attempts"]) == 2
    request.side_effect = None
    request.return_value = Mock(text=tencent(stamp="20991113161430"), raise_for_status=lambda: None,
        json=lambda: {"data": None})
    assert load_latest_quote("000338", now=NOW)["status"] == "unavailable"


def test_old_quote_cannot_override_newer_daily(monkeypatch):
    request = Mock(return_value=Mock(text=tencent(), raise_for_status=lambda: None, json=lambda: {"data": None}))
    monkeypatch.setattr("data.latest_quote.requests.get", request)
    assert load_latest_quote("000338", daily_end="2026-11-14", now=NOW)["status"] == "unavailable"


def test_two_failed_sources_use_eastmoney(monkeypatch):
    response = Mock(raise_for_status=lambda: None, json=lambda: {"data": {
        "f57": "000338", "f43": 18.75, "f60": 19.8, "f86": 1794555255}})
    request = Mock(side_effect=[TimeoutError("腾讯失败"), ValueError("新浪无效"), response])
    monkeypatch.setattr("data.latest_quote.requests.get", request)
    quote = load_latest_quote("000338", now=NOW)
    assert quote["source"] == "东方财富行情" and request.call_count == 3


def test_budget_stops_scheduling_more_sources(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("data.latest_quote.time.monotonic", lambda: clock[0])

    def timeout(request, deadline):
        clock[0] = 12
        raise TimeoutError("模拟到期")

    monkeypatch.setattr("data.latest_quote.request_with_deadline", timeout)
    quote = load_latest_quote("000338", now=NOW)
    assert quote["status"] == "unavailable" and len(quote["attempts"]) == 1


def test_unsupported_code_does_not_request_or_break_report(monkeypatch):
    request = Mock()
    monkeypatch.setattr("data.latest_quote.requests.get", request)
    assert load_latest_quote("bad", now=NOW)["status"] == "unavailable"
    request.assert_not_called()


def test_boolean_previous_close_does_not_create_change():
    quote = parse_eastmoney({"data": {"f57": "000338", "f43": 18.75,
        "f60": True, "f86": 1794555255}}, "000338")
    assert quote["change_pct"] is None


def test_quote_display_never_changes_daily_indicators():
    from datetime import date

    from data.schemas import AnalysisContext, PriceData
    from report.payload import build_stock_payload
    from report.scoring import build_report

    ctx = AnalysisContext(symbol="000338", name="潍柴动力", current_quote={
        "status": "ok", "symbol": "000338", "price": 19.15, "change_pct": -3.28,
        "quoted_at": "2026-11-16T10:00:00+08:00", "source": "新浪行情"},
        price_data=[PriceData(symbol="000338", trade_date=date(2026, 11, 13),
                              open=19, high=20, low=18, close=18.75, volume=10)])
    payload = build_stock_payload(ctx.symbol, ctx.name, [], {}, ctx)
    report = build_report(ctx.symbol, ctx.name, [], {}, ctx)
    assert payload["overview"]["current_price"]["value"] == 19.15
    assert payload["overview"]["latest_close"] == 18.75
    assert payload["overview"]["change_pct"] == -3.28
    assert "19.15" in report and "新浪行情" in report and "10:00:00" in report
    ctx.current_quote = {"status": "unavailable"}
    fallback = build_stock_payload(ctx.symbol, ctx.name, [], {}, ctx)
    assert fallback["overview"]["current_price"]["value"] == 18.75
    assert "回退" in fallback["overview"]["current_price"]["note"]


def test_us_market_closed_prefers_latest_close():
    display = price_presentation({"status": "market_closed", "symbol": "MSFT"}, 523.88, "2026-11-13")
    assert display["value"] == 523.88
    assert display["kind"] == "close_fallback"
    assert "已收盘" in display["note"] and "2026-11-13" in display["note"]


def test_us_premarket_prefers_latest_close():
    display = price_presentation({"status": "market_pre", "symbol": "MSFT"}, 523.88, "2026-11-13")
    assert display["value"] == 523.88
    assert "尚未开盘" in display["note"]


def test_us_intraday_quote_displays_quote_time_instead_of_capture_note():
    display = price_presentation({
        "status": "ok", "symbol": "MSFT", "price": 524.1, "change_pct": 0.2,
        "quoted_at": "2026-11-13T10:01:00-05:00", "source": "新浪美股快照（延迟约15分钟）",
        "note": "盘中报价（延迟约15分钟）；采集时间：2026-11-13 10:01:00",
    }, 523.88, "2026-11-13")
    assert display["value"] == 524.1
    assert display["kind"] == "quote"
    assert display["note"] == "交易中；2026-11-13 10:01:00 UTC-5"


def test_us_intraday_quote_failure_falls_back_with_reason():
    display = price_presentation({
        "status": "unavailable", "symbol": "MSFT",
        "note": "盘中报价暂不可用，回退最近收盘价",
    }, 523.88, "2026-11-13")
    assert display["value"] == 523.88
    assert "盘中报价暂不可用" in display["note"] and "2026-11-13" in display["note"]
