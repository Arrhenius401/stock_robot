"""港股盘中报价：快照时点、身份与失败回退。"""
from datetime import datetime, timedelta

import pytest

from data.hk_alternatives import tencent_snapshot
from data.latest_quote import HONG_KONG, load_hk_quote

NOW = datetime(2026, 11, 13, 10, 0, tzinfo=HONG_KONG)


class _Response:
    def __init__(self, text: str):
        self.content = text.encode("gbk")

    def raise_for_status(self):
        return None


def test_tencent_snapshot_parses_fields(mocker):
    fields = [""] * 40
    fields[0], fields[1], fields[2] = "100", "腾讯控股", "00700"
    fields[3], fields[4], fields[30], fields[32] = "386.600", "398.800", "2026/11/13 16:08:22", "-3.06"
    body = 'v_hk00700="' + "~".join(fields) + '";'
    mocker.patch("data.hk_alternatives.requests.get", return_value=_Response(body))
    snapshot = tencent_snapshot("00700")
    assert snapshot["name"] == "腾讯控股"
    assert snapshot["price"] == pytest.approx(386.6)
    assert snapshot["previous"] == pytest.approx(398.8)
    assert snapshot["change_pct"] == pytest.approx(-3.06)
    assert snapshot["quoted_at"].isoformat().startswith("2026-11-13T16:08:22")


def snapshot(**overrides):
    data = {
        "name": "腾讯控股", "price": 417.8, "previous": 415.0,
        "quoted_at": NOW, "change_pct": 0.67,
    }
    data.update(overrides)
    return data


def test_hk_quote_returns_intraday_price_and_change(mocker):
    mocker.patch("data.hk_alternatives.tencent_snapshot", return_value=snapshot())
    quote = load_hk_quote("00700.HK", now=NOW)
    assert quote["status"] == "ok"
    assert quote["name"] == "腾讯控股"
    assert quote["price"] == pytest.approx(417.8)
    assert quote["change_pct"] == pytest.approx(0.67)
    assert "行情时间" in quote["note"]


def test_hk_adapter_preserves_previous_date_for_shared_session_validation(mocker):
    mocker.patch("data.hk_alternatives.tencent_snapshot", return_value=snapshot(
        quoted_at=NOW - timedelta(days=1),
    ))
    quote = load_hk_quote("00700.HK", now=NOW)
    assert quote["status"] == "ok"
    assert quote["quoted_at"].startswith("2026-11-12")


def test_hk_quote_rejects_invalid_price(mocker):
    mocker.patch("data.hk_alternatives.tencent_snapshot", return_value=snapshot(price=None))
    quote = load_hk_quote("00700.HK", now=NOW)
    assert quote["status"] == "unavailable"


def test_hk_quote_rejects_invalid_symbol():
    assert load_hk_quote("0070.HK", now=NOW)["status"] == "unavailable"
