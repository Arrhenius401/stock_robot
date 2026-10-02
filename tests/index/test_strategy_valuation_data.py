"""公开官方估值数据的口径和降级回归。"""
from datetime import date, timedelta

import pytest

from index.valuation_data import StrategyValuationProvider

END = date(2026, 9, 30)


def rows(count=252):
    days = []
    cursor = END
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return [{"tradeDate": d.isoformat(), "indexCode": "930740", "close": 100, "peg": i + 10} for i, d in enumerate(reversed(days))]


@pytest.fixture
def provider(tmp_path, monkeypatch):
    value = StrategyValuationProvider(cache_path=tmp_path / "cache.db")
    monkeypatch.setattr(value, "_factsheet", lambda *_: {})
    return value


def feed(provider, monkeypatch, data):
    monkeypatch.setattr(provider, "_get_json", lambda *_: {"data": data})


def test_history_sorted_deduplicated_and_has_real_coverage(provider, monkeypatch):
    data = rows()
    feed(provider, monkeypatch, list(reversed(data)) + [data[0]])
    result = provider.fetch("930740", end=END)
    assert result is not None
    assert result.pe_ttm == 261
    assert result.pe_percentile == 100
    assert result.pe_sample_count == 252
    assert result.percentile_sample_start == date.fromisoformat(data[0]["tradeDate"])
    assert result.percentile_sample_end == END
    assert result.pe_as_of == END
    assert result.pb_percentile is None
    assert result.valuation_valid


@pytest.mark.parametrize("count, expected", [(251, None), (252, 100)])
def test_independent_pe_sample_threshold(provider, monkeypatch, count, expected):
    feed(provider, monkeypatch, rows(count))
    result = provider.fetch("930740", end=END)
    assert result is not None
    assert result.pe_percentile == expected
    assert result.valuation_valid is (count >= 252)


def test_bad_rows_and_calendar_window_filtered(provider, monkeypatch):
    data = rows(252)
    data += [dict(data[0], indexCode="000300"), dict(data[0], tradeDate="invalid"),
             dict(data[0], tradeDate="2026-10-01"), dict(data[0], tradeDate="2020-01-01"),
             dict(data[0], tradeDate="2026-09-26"), dict(data[0], tradeDate="2026-09-29", close=0)]
    data[1]["peg"] = float("nan")
    data[2]["peg"] = float("inf")
    data[3]["peg"] = -1
    feed(provider, monkeypatch, data)
    result = provider.fetch("930740", end=END)
    assert result is not None
    assert result.pe_sample_count == 249
    assert result.pe_percentile is None


def test_latest_missing_pe_does_not_borrow_old_value(provider, monkeypatch):
    data = rows(253)
    data[-1]["peg"] = None
    feed(provider, monkeypatch, data)
    result = provider.fetch("930740", end=END)
    assert result is not None
    assert result.pe_ttm is None
    assert result.pe_as_of is None
    assert result.pe_sample_count == 252
    assert result.pe_percentile is None
    assert not result.valuation_valid


def test_stale_history_never_claims_current_percentile(provider, monkeypatch):
    data = rows()
    for row in data:
        row["tradeDate"] = (date.fromisoformat(row["tradeDate"]) - timedelta(days=21)).isoformat()
    feed(provider, monkeypatch, data)
    result = provider.fetch("930740", end=END)
    assert result is not None
    assert result.pe_percentile is None
    assert not result.valuation_valid
    assert any("过期" in note for note in result.valuation_notes)


def test_history_failure_still_returns_factsheet_and_short_caches(provider, monkeypatch):
    def fail(*_):
        raise ConnectionError("network")
    monkeypatch.setattr(provider, "_get_json", fail)
    monkeypatch.setattr(provider, "_factsheet", lambda *_: {"symbol": "930740", "as_of": END, "pb": 1.2, "source_url": "https://example.com/a.pdf"})
    writes = []
    monkeypatch.setattr(provider, "_write_cached", lambda *args, **kwargs: writes.append(kwargs.get("ttl", 86400)))
    result = provider.fetch("930740", end=END)
    assert result is not None
    assert result.pb == 1.2
    assert result.pb_as_of == END
    assert result.pb_sample_count == 0
    assert result.pb_percentile is None
    assert writes == [300, 86400]


def test_snapshot_pe_keeps_unknown_basis_separate(provider, monkeypatch):
    monkeypatch.setattr(provider, "_factsheet", lambda *_: {"symbol": "980092", "as_of": END, "pe_snapshot": 15, "pb": 1.5, "pe_basis": "官方单张未声明TTM"})
    result = provider.fetch("980092", provider="cni", end=END)
    assert result is not None
    assert result.pe_snapshot == 15
    assert result.pe_snapshot_as_of == END
    assert result.pe_snapshot_basis == "官方单张未声明TTM"
    assert result.pe_ttm is None
    assert result.pe_sample_count == 0
    assert result.pb == 1.5


@pytest.mark.parametrize("snapshot", [{"symbol": "000300", "as_of": END, "pb": 1}, {"symbol": "930740", "as_of": "2026-10-01", "pb": 1}])
def test_invalid_snapshot_rejected_without_losing_history(provider, monkeypatch, snapshot):
    feed(provider, monkeypatch, rows())
    monkeypatch.setattr(provider, "_factsheet", lambda *_: snapshot)
    result = provider.fetch("930740", end=END)
    assert result is not None
    assert result.pe_ttm == 261
    assert result.pb is None


def test_failed_fetch_cached_as_empty(provider, monkeypatch):
    calls = []
    def fail(*_):
        calls.append(1)
        raise ConnectionError("network")
    monkeypatch.setattr(provider, "_get_json", fail)
    result = provider.fetch("930740", end=END)
    assert result is not None
    assert result.date == END
    assert result.pe_as_of is None and result.pb_as_of is None
    assert any("请求失败" in note for note in result.valuation_notes)
    assert provider.fetch("930740", end=END) == result
    assert len(calls) == 1


def test_factsheet_failure_does_not_hide_valid_history(provider, monkeypatch):
    feed(provider, monkeypatch, rows())
    def fail(*_):
        raise ConnectionError("pdf network")
    monkeypatch.setattr(provider, "_factsheet", fail)
    result = provider.fetch("930740", end=END)
    assert result is not None
    assert result.pe_percentile == 100
    assert any("单张请求失败" in note for note in result.valuation_notes)


def test_daily_pb_uses_exact_name_and_does_not_replace_pe(provider, monkeypatch):
    data = [dict(row, indexCode="000015") for row in rows()]
    def get(url, params=None):
        if url.endswith("indexValuation"):
            return {"data": {"indexValuations": [
                {"indexName": "中证红利", "tradeDate": "20260930", "pb": 99},
                {"indexName": "红利指数", "tradeDate": "20260930", "pb": 0.9, "peg": 999}]}}
        return {"data": data}
    monkeypatch.setattr(provider, "_get_json", get)
    result = provider.fetch("000015", end=END)
    assert result is not None
    assert result.pb == 0.9
    assert result.pe_ttm == 261
    assert result.pb_sample_count == 0
    assert result.pb_percentile is None


def test_successful_cache_has_distinct_epoch_and_prevents_network(provider, monkeypatch):
    feed(provider, monkeypatch, rows())
    monkeypatch.setattr(provider, "_factsheet", lambda *_: {"symbol": "930740", "as_of": END, "pb": 1.2})
    first = provider.fetch("930740", end=END)
    def fail(*_):
        raise AssertionError("不应再请求网络")
    monkeypatch.setattr(provider, "_get_json", fail)
    monkeypatch.setattr(provider, "_factsheet", fail)
    assert provider.fetch("930740", end=END) == first
    assert provider._read_cached("valuation_factsheet", f"csi:930740:{END}")["ok"]
    assert provider._read_cached("csi_history_status", f"930740:{END}")["ok"]
    assert provider._read_cached("strategy_valuation", f"csi:930740:{END}") is None


def test_expired_partial_cache_retries_missing_source(provider, monkeypatch):
    feed(provider, monkeypatch, rows())
    first = provider.fetch("930740", end=END)
    assert first is not None and first.pb is None
    # 模拟本次短缓存到期，不改变正常缓存实现。
    original_read = provider._read_cached
    monkeypatch.setattr(provider, "_read_cached", lambda *_: None)
    monkeypatch.setattr(provider, "_factsheet", lambda *_: {"symbol": "930740", "as_of": END, "pb": 1.2})
    second = provider.fetch("930740", end=END)
    assert second is not None and second.pb == 1.2
    monkeypatch.setattr(provider, "_read_cached", original_read)
    assert provider.fetch("930740", end=END) == second


@pytest.mark.parametrize("age, pb", [(62, 1.2), (63, None)])
def test_monthly_snapshot_age_guard(provider, monkeypatch, age, pb):
    sampled = END - timedelta(days=age)
    monkeypatch.setattr(provider, "_factsheet", lambda *_: {"symbol": "980092", "as_of": sampled, "pb": 1.2})
    result = provider.fetch("980092", provider="cni", end=END)
    assert result is not None and result.pb == pb


def test_daily_snapshot_older_than_fourteen_days_rejected(provider, monkeypatch):
    monkeypatch.setattr(provider, "_get_json", lambda *_: {"data": {"indexValuations": [
        {"indexName": "红利指数", "tradeDate": "20260915", "pb": 0.9}]}})
    assert provider._current_pb("000015", END) == {}


def test_undisclosed_pb_explained_with_usable_pe(provider, monkeypatch):
    feed(provider, monkeypatch, rows())
    monkeypatch.setattr(provider, "_factsheet", lambda *_: {"symbol": "930740", "as_of": END, "pb": None})
    result = provider.fetch("930740", end=END)
    assert result is not None
    assert result.pe_percentile == 100
    assert any("未披露 PB" in note for note in result.valuation_notes)
