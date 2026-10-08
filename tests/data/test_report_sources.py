from datetime import date, timedelta

import pandas as pd
import pytest

from data.akshare import AkShareAdapter


def frame(count):
    return pd.DataFrame(
        [
            {
                "日期": (date(2026, 1, 1) + timedelta(days=i)).isoformat(),
                "开盘": 10,
                "最高": 11,
                "最低": 9,
                "收盘": 10,
                "成交量": 100,
            }
            for i in range(count)
        ]
    )


@pytest.mark.parametrize("secondary", [0, 1])
def test_short_primary_preserved_and_source_correct(monkeypatch, secondary):
    monkeypatch.setattr("data.akshare._ak_daily", lambda **kwargs: frame(19))
    monkeypatch.setattr("data.akshare._ak_hist", lambda **kwargs: frame(secondary))
    result = AkShareAdapter().fetch("000001", data_type="price")
    assert len(result) == 19
    assert result[0]._data_source == "腾讯行情（前复权）"


def test_secondary_source_recorded(monkeypatch):
    monkeypatch.setattr("data.akshare._ak_daily", lambda **kwargs: frame(1))
    monkeypatch.setattr("data.akshare._ak_hist", lambda **kwargs: frame(20))
    result = AkShareAdapter().fetch("000001", data_type="price")
    assert len(result) == 20
    assert result[0]._data_source == "东方财富行情（前复权）"


def test_adapter_diagnostics_thread_isolated(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from requests.exceptions import ReadTimeout

    adapter = AkShareAdapter()
    barrier = Barrier(2)

    def worker(source, failed):
        if failed:
            adapter._record_collection_error(source, ReadTimeout("secret"))
        barrier.wait(timeout=5)
        record = adapter.get_collection_error()
        return record.source if record else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker, "原线程", True)
        second = pool.submit(worker, "另线程", False)
        assert first.result() == "原线程"
        assert second.result() is None
    assert adapter.get_collection_error() is None
