"""共同来源选择与港股备用来源回归。"""
from datetime import date, timedelta

import pandas as pd
import pytest

from data.akshare import AkShareAdapter


@pytest.fixture(autouse=True)
def isolate_ps_snapshot(mocker):
    """本文件只验证原行情/百度来源，新 PS 独立测试且禁止意外联网。"""
    mocker.patch("data.akshare.valuation_comparison_snapshot", side_effect=TimeoutError())


def history(count, close=505):
    return [[(date(2026, 7, 1) + timedelta(days=i)).isoformat(), "500", str(close), "510", "495", "100"]
            for i in range(count)]


def chart(metric, points, code="00700"):
    return {"Result": [{"DisplayData": {"resultData": {"tplData": {"result": {
        "code": code, "releaseDate": "2004-06-16", "chartInfo": [{"header": [metric], "body": points}],
    }}}}}]}


@pytest.mark.parametrize(("primary_count", "backup_count", "expected_close"),
                         [(60, 90, 505), (59, 60, 506), (10, 9, 505), (9, 10, 506), (9, 9, 506)])
def test_hk_shared_short_history_selection(mocker, primary_count, backup_count, expected_close):
    primary = mocker.patch("data.akshare.tencent_history", return_value=history(primary_count))
    backup = mocker.patch("data.akshare.daily_history", return_value=[
        ",".join(row) for row in history(backup_count, 506)])
    rows = AkShareAdapter().fetch("00700.HK", market="hk", data_type="price",
                                 start_date="20260701", end_date="20261019")
    assert rows[0].close == expected_close
    assert len(rows) == (primary_count if primary_count >= 60 else max(primary_count, backup_count))
    assert "腾讯" in rows[0]._data_source if expected_close == 505 else "东方财富" in rows[0]._data_source
    primary.assert_called_once()
    assert backup.call_count == (0 if primary_count >= 60 else 1)


def test_a_share_uses_same_selector_and_correct_source(mocker):
    from data import akshare
    selector = mocker.spy(akshare, "select_history")
    frame = pd.DataFrame([{"date": row[0], "open": row[1], "close": row[2], "high": row[3], "low": row[4], "volume": row[5]}
                          for row in history(60)])
    mocker.patch("data.akshare._ak_daily", return_value=frame)
    backup = mocker.patch("data.akshare._ak_hist")
    rows = AkShareAdapter().fetch("000001", data_type="price")
    assert rows[0]._data_source == "新浪行情（前复权）"
    backup.assert_not_called()
    assert selector.call_count == 1
    mocker.patch("data.akshare.tencent_history", return_value=history(60))
    AkShareAdapter().fetch("00700.HK", market="hk", data_type="price",
                           start_date="20260701", end_date="20261019")
    assert selector.call_count == 2


def test_tencent_request_validates_unadjusted_target_and_timeout(mocker):
    from data.hk_alternatives import tencent_history
    request = mocker.patch("requests.get")
    request.return_value.json.return_value = {"code": 0, "data": {"hk00700": {"day": history(2)}}}
    assert len(tencent_history("00700", "20260701", "20261019", 111)) == 2
    assert request.call_args.kwargs == {"params": {"param": "hk00700,day,2026-07-01,2026-10-19,111,"}, "timeout": 10}
    request.assert_called_once()


@pytest.mark.parametrize("payload", [{"code": 1}, {"code": 0, "data": {"hk00005": {"day": history(2)}}},
    {"code": 0, "data": {"hk00700": {"qfqday": history(2)}}}])
def test_tencent_rejects_wrong_code_or_adjusted_response(mocker, payload):
    from data.hk_alternatives import tencent_history
    mocker.patch("requests.get").return_value.json.return_value = payload
    with pytest.raises((ValueError, TypeError)):
        tencent_history("00700", "20260701", "20261019", 111)


def test_hk_history_rejects_bad_ohlc_and_date(mocker):
    mocker.patch("data.akshare.tencent_history", return_value=history(2) + [
        ["2026-07-03", "500", "520", "510", "495", "100"],
        ["2026-07-04", "500", "505", "510", "495", "-1"],
        ["bad", "500", "505", "510", "495", "100"]])
    mocker.patch("data.akshare.daily_history", return_value=[])
    rows = AkShareAdapter().fetch("00700.HK", market="hk", data_type="price",
                                 start_date="20260701", end_date="20261019")
    assert len(rows) == 2


@pytest.mark.parametrize(("pe_points", "pb_points", "expected"), [
    ([["2026-10-18", "14"], ["2026-10-19", "15"]], [["2026-10-18", "3"]], (date(2026, 10, 18), 14, 3)),
    ([["2026-10-18", "14"]], [["2026-10-19", "3"]], (date(2026, 10, 19), None, 3)),
    ([["2026-10-19", "14"]], [], (date(2026, 10, 19), 14, None)),
])
def test_baidu_valuation_uses_real_common_date_and_partial_diagnostic(mocker, pe_points, pb_points, expected):
    mocker.patch("data.akshare.stock_snapshot", side_effect=TimeoutError())
    def response(_url, *, params, timeout):
        assert timeout == 10
        metric = params["query"]
        result = mocker.Mock()
        result.json.return_value = chart(metric, pe_points if metric == "市盈率(TTM)" else pb_points)
        return result
    request = mocker.patch("requests.get", side_effect=response)
    adapter = AkShareAdapter()
    rows = adapter.fetch("00700.HK", market="hk", data_type="valuation")
    assert len(rows) == 1
    assert (rows[0].date, rows[0].pe_ttm, rows[0].pb) == expected
    assert "百度" in rows[0]._data_source
    assert request.call_count == 2
    diagnostic = adapter.get_collection_error()
    assert diagnostic is not None
    assert "东方财富" in diagnostic.error_message
    if expected[1] is None:
        assert "共同日期" in diagnostic.error_message


@pytest.mark.parametrize(("code", "metric"), [("00005", "市盈率(TTM)"), ("00700", "市盈率")])
def test_baidu_rejects_wrong_code_and_non_ttm(mocker, code, metric):
    from data.hk_alternatives import baidu_metric
    mocker.patch("requests.get").return_value.json.return_value = chart(metric, [["2026-10-19", "14"]], code)
    with pytest.raises(ValueError):
        baidu_metric("00700", "市盈率(TTM)")


def test_one_snapshot_metric_survives_missing_history(mocker):
    mocker.patch("data.akshare.stock_snapshot", return_value={"f57": "00700", "f86": 1792138094, "f164": 14})
    backup = mocker.patch("data.akshare.baidu_metric", return_value={})
    rows = AkShareAdapter().fetch("00700.HK", market="hk", data_type="valuation")
    assert rows[0].pe_ttm == 14
    assert rows[0].pb is None
    assert backup.call_count == 2


def test_hk_backup_success_preserves_primary_failure(mocker):
    primary = mocker.patch("data.akshare.tencent_history", side_effect=TimeoutError())
    backup = mocker.patch("data.akshare.daily_history", return_value=[",".join(row) for row in history(60)])
    adapter = AkShareAdapter()
    rows = adapter.fetch("00700.HK", market="hk", data_type="price",
                         start_date="20260701", end_date="20261019")
    assert len(rows) == 60
    assert rows[0]._data_source == "东方财富港股行情（不复权）"
    diagnostic = adapter.get_collection_error()
    assert diagnostic is not None
    assert "腾讯港股行情" in diagnostic.error_message
    assert diagnostic.error_kind == "timeout"
    primary.assert_called_once()
    backup.assert_called_once()


def test_baidu_single_metric_timeout_keeps_other_metric(mocker):
    mocker.patch("data.akshare.stock_snapshot", side_effect=TimeoutError())
    def fetch(_code, metric):
        if metric == "市盈率(TTM)":
            raise TimeoutError()
        return {date(2026, 10, 19): 3.0}
    backup = mocker.patch("data.akshare.baidu_metric", side_effect=fetch)
    adapter = AkShareAdapter()
    rows = adapter.fetch("00700.HK", market="hk", data_type="valuation")
    assert len(rows) == 1
    assert rows[0].pe_ttm is None
    assert rows[0].pb == 3
    assert backup.call_count == 2
    diagnostic = adapter.get_collection_error()
    assert diagnostic is not None
    assert "东方财富" in diagnostic.error_message
    assert "百度港股市盈率(TTM)" in diagnostic.error_message


def test_baidu_does_not_use_bad_future_or_release_dates(mocker):
    from data.hk_alternatives import baidu_metric
    mocker.patch("requests.get").return_value.json.return_value = chart("市净率", [
        ["bad", 2], ["2099-01-01", 3], ["2026-10-18", "nan"], ["2026-10-17", True], ["2026-10-16", 4]])
    assert baidu_metric("00700", "市净率") == {date(2026, 10, 16): 4}


def test_tencent_request_timeout_is_single_call(mocker):
    from data.hk_alternatives import tencent_history
    request = mocker.patch("requests.get", side_effect=TimeoutError())
    with pytest.raises(TimeoutError):
        tencent_history("00700", "20260701", "20261019", 111)
    request.assert_called_once()
