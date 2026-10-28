"""行情候选在切源前校验，不因条数足够而接纳异常或倒退历史。"""
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from data.akshare import AkShareAdapter


def a_frame(count, *, end=date(2026, 10, 23), close=10):
    return pd.DataFrame([
        {"date": (end - timedelta(days=count - i - 1)).isoformat(),
         "open": 10, "high": 12, "low": 9, "close": close, "volume": 100}
        for i in range(count)
    ])


def hk_lines(count, *, end=date(2026, 10, 23), close=10):
    return [[(end - timedelta(days=count - i - 1)).isoformat(), 10, close, 12, 9, 100]
            for i in range(count)]


@pytest.mark.parametrize(("field", "value"), [
    ("close", 20), ("open", 0), ("high", float("inf")),
    ("low", float("nan")), ("volume", -1),
])
def test_invalid_primary_ohlcv_tries_existing_backup(mocker, field, value):
    primary = a_frame(60)
    primary[field] = value
    mocker.patch("data.akshare._ak_daily", return_value=primary)
    backup = mocker.patch("data.akshare._ak_hist", return_value=a_frame(90, close=11))
    rows = AkShareAdapter().fetch("000001", data_type="price", days=365)
    assert len(rows) == 90 and all(row.close == 11 for row in rows)
    backup.assert_called_once()
    assert backup.call_args.kwargs["adjust"] == "qfq"


def test_future_rows_cannot_make_primary_sufficient(mocker):
    future = datetime.now().astimezone().date() + timedelta(days=100)
    mocker.patch("data.akshare._ak_daily", return_value=a_frame(60, end=future))
    backup = mocker.patch("data.akshare._ak_hist", return_value=a_frame(90, close=11))
    rows = AkShareAdapter().fetch("000001", data_type="price", days=365)
    assert len(rows) == 90 and rows[-1].close == 11
    backup.assert_called_once()


def test_conflicting_duplicate_day_rejects_entire_primary(mocker):
    primary = a_frame(60)
    conflict = primary.iloc[-1].copy()
    conflict["close"] = 11
    primary = pd.concat([primary, pd.DataFrame([conflict])], ignore_index=True)
    mocker.patch("data.akshare._ak_daily", return_value=primary)
    backup = mocker.patch("data.akshare._ak_hist", return_value=a_frame(90, close=11))
    rows = AkShareAdapter().fetch("000001", data_type="price", days=365)
    assert len(rows) == 90 and all(row.close == 11 for row in rows)
    backup.assert_called_once()


def test_identical_duplicate_does_not_inflate_valid_history_count(mocker):
    primary = a_frame(59)
    primary = pd.concat([primary, primary.tail(1)], ignore_index=True)
    mocker.patch("data.akshare._ak_daily", return_value=primary)
    backup = mocker.patch("data.akshare._ak_hist", return_value=a_frame(60, close=11))
    rows = AkShareAdapter().fetch("000001", data_type="price", days=365)
    assert len(rows) == 60 and rows[-1].close == 11
    backup.assert_called_once()


def test_isolated_bad_row_keeps_other_legal_rows_from_same_source(mocker):
    primary = a_frame(61)
    primary.loc[0, "close"] = 100
    mocker.patch("data.akshare._ak_daily", return_value=primary)
    backup = mocker.patch("data.akshare._ak_hist")
    rows = AkShareAdapter().fetch("000001", data_type="price", days=365)
    assert len(rows) == 60 and all(row.close == 10 for row in rows)
    backup.assert_not_called()


@pytest.mark.parametrize("market", ["a-shares", "hk"])
def test_known_history_coverage_tries_backup_before_accepting_60_rows(mocker, market):
    if market == "hk":
        mocker.patch("data.akshare.tencent_history", return_value=hk_lines(60))
        backup = mocker.patch("data.akshare.daily_history", return_value=[
            ",".join(map(str, row)) for row in hk_lines(90, close=11)])
        symbol = "00700.HK"
    else:
        mocker.patch("data.akshare._ak_daily", return_value=a_frame(60))
        backup = mocker.patch("data.akshare._ak_hist", return_value=a_frame(90, close=11))
        symbol = "000001"
    rows = AkShareAdapter().fetch(symbol, data_type="price", market=market, minimum_points=90)
    assert len(rows) == 90 and rows[-1].close == 11
    backup.assert_called_once()


@pytest.mark.parametrize("market", ["a-shares", "hk"])
def test_known_history_end_rejects_sufficient_but_older_primary(mocker, market):
    if market == "hk":
        mocker.patch("data.akshare.tencent_history", return_value=hk_lines(60, end=date(2026, 9, 1)))
        backup = mocker.patch("data.akshare.daily_history", return_value=[
            ",".join(map(str, row)) for row in hk_lines(60, close=11)])
        symbol = "00700.HK"
    else:
        mocker.patch("data.akshare._ak_daily", return_value=a_frame(60, end=date(2026, 9, 1)))
        backup = mocker.patch("data.akshare._ak_hist", return_value=a_frame(60, close=11))
        symbol = "000001"
    rows = AkShareAdapter().fetch(symbol, data_type="price", market=market,
                                 minimum_end_date=date(2026, 10, 23))
    assert len(rows) == 60 and rows[-1].close == 11
    backup.assert_called_once()


def test_both_sources_short_preserve_longest_legal_whole_package(mocker):
    mocker.patch("data.akshare._ak_daily", return_value=a_frame(65))
    backup = mocker.patch("data.akshare._ak_hist", return_value=a_frame(70, close=11))
    rows = AkShareAdapter().fetch("000001", data_type="price", minimum_points=90)
    assert len(rows) == 70 and all(row.close == 11 for row in rows)
    backup.assert_called_once()


def test_all_older_sources_return_no_current_history(mocker):
    mocker.patch("data.akshare._ak_daily", return_value=a_frame(60, end=date(2026, 9, 1)))
    mocker.patch("data.akshare._ak_hist", return_value=a_frame(90, end=date(2026, 9, 2)))
    rows = AkShareAdapter().fetch("000001", data_type="price", minimum_end_date=date(2026, 10, 23))
    assert rows == []


def test_hk_conflicting_duplicate_also_tries_backup(mocker):
    lines = hk_lines(60)
    conflict = list(lines[-1])
    conflict[2] = 11
    mocker.patch("data.akshare.tencent_history", return_value=[*lines, conflict])
    backup = mocker.patch("data.akshare.daily_history", return_value=[
        ",".join(map(str, row)) for row in hk_lines(90, close=11)])
    rows = AkShareAdapter().fetch("00700.HK", market="hk", data_type="price")
    assert len(rows) == 90 and rows[-1].close == 11
    backup.assert_called_once()
