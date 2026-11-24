from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from data.akshare import AkShareAdapter


def tencent_text(pe="22.21", pb="6.73"):
    fields = ["0"] * 48
    fields[1], fields[2], fields[3] = "贵州茅台", "600519", "1460.00"
    fields[30], fields[39], fields[46] = "20261124150522", pe, pb
    return 'v_sh600519="' + "~".join(fields) + '";'


def history_frame():
    today = datetime.now().astimezone().date()
    records = []
    for offset in (10, 9, 8, 7, 6, 5):
        day = today - timedelta(days=offset)
        records.append({"数据日期": day.isoformat(), "当日收盘价": 1400,
                        "PE(TTM)": 21.0 + offset / 100, "市净率": 6.5,
                        "市销率": 10.3, "总市值": 1.8e12,
                        "总股本": 1250081601})
    return pd.DataFrame(records)


class FakeResponse:
    def __init__(self, text):
        self.text = text


def test_tencent_snapshot_keeps_ps_and_long_history(mocker):
    mocker.patch("requests.get", return_value=FakeResponse(tencent_text()))
    mocker.patch("data.a_valuation_history.ak.stock_value_em", return_value=history_frame())
    row = AkShareAdapter().fetch("600519", market="a-shares", data_type="valuation")[0]
    assert row.pe_ttm == pytest.approx(22.21)
    assert row.pb == pytest.approx(6.73)
    assert row.ps_ttm == pytest.approx(10.3)
    assert row.ps_as_of is not None
    assert row.history_source == "东财日频估值历史"
    assert row.history_window_days == 1827
    assert row.history_window_label == "近五年"
    assert row.extended_pe_history


def test_tencent_failure_uses_eastmoney_latest_snapshot(mocker):
    mocker.patch("requests.get", side_effect=TimeoutError("tencent down"))
    mocker.patch("data.a_valuation_history.ak.stock_value_em", return_value=history_frame())
    row = AkShareAdapter().fetch("600519", market="a-shares", data_type="valuation")[0]
    assert row.pe_ttm is not None and row.pb is not None
    assert row.pe_as_of == row.ps_as_of
    assert "东财" in (row._data_source or "")


def test_both_snapshot_sources_fail_uses_baidu_latest(mocker):
    mocker.patch("requests.get", side_effect=TimeoutError("tencent down"))
    mocker.patch("data.a_valuation_history.ak.stock_value_em", side_effect=ValueError("eastmoney down"))
    mocker.patch("data.akshare.baidu_metric", side_effect=lambda _s, metric, **_k: {
        date(2026, 11, 21): 20.0 if metric == "市盈率(TTM)" else 6.0})
    row = AkShareAdapter().fetch("600519", market="a-shares", data_type="valuation")[0]
    assert row.pe_ttm == pytest.approx(20.0)
    assert row.pb == pytest.approx(6.0)
    assert "百度" in (row._data_source or "")


def test_default_price_window_is_365_calendar_days(mocker):
    today = datetime.now().astimezone().date()
    captured = {}

    def fake_daily(**kwargs):
        captured["start"] = kwargs["start_date"]
        rows = []
        for index in range(60):
            day = today - timedelta(days=index)
            rows.append({"date": day.isoformat(), "open": 10, "high": 11,
                         "low": 9, "close": 10, "volume": 100})
        return pd.DataFrame(rows)

    mocker.patch("data.akshare._ak_daily", side_effect=fake_daily)
    mocker.patch("data.akshare._ak_hist", return_value=pd.DataFrame())
    AkShareAdapter()._fetch_price("600519")
    assert captured["start"] == (today - timedelta(days=365)).strftime("%Y%m%d")


@pytest.mark.parametrize("missing", [("pe",), ("pb",), ("pe", "pb")])
def test_missing_eastmoney_metrics_fall_back_independently(mocker, missing):
    frame = history_frame()
    for key in missing:
        frame["PE(TTM)" if key == "pe" else "市净率"] = None
    mocker.patch("requests.get", side_effect=TimeoutError("腾讯失败"))
    mocker.patch("data.a_valuation_history.ak.stock_value_em", return_value=frame)
    today = datetime.now().astimezone().date()
    mocker.patch("data.akshare.baidu_metric", side_effect=lambda _symbol, metric, **kwargs: {
        today: 20.0 if metric == "市盈率(TTM)" else 6.0})
    row = AkShareAdapter().fetch("600519", market="a-shares", data_type="valuation")[0]
    for key in missing:
        assert getattr(row, f"{key}_source").startswith("百度")
        assert getattr(row, f"{key}_history")
        assert row.history_metric_sources[key].startswith("百度")
    assert row.ps_ttm == pytest.approx(10.3)


def test_latest_missing_with_older_values_still_uses_baidu_snapshot(mocker):
    frame = history_frame()
    frame.loc[frame.index[-1], ["PE(TTM)", "市净率"]] = None
    mocker.patch("requests.get", side_effect=TimeoutError("腾讯失败"))
    mocker.patch("data.a_valuation_history.ak.stock_value_em", return_value=frame)
    today = datetime.now().astimezone().date()
    mocker.patch("data.akshare.baidu_metric", return_value={today: 20.0})
    row = AkShareAdapter().fetch("600519", market="a-shares", data_type="valuation")[0]
    assert row.pe_source.startswith("百度") and row.pb_source.startswith("百度")
    assert row.history_metric_sources["pe"].startswith("东财")
