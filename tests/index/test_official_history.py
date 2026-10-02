"""共享历史请求、覆盖区间与来源失败隔离回归。"""
from datetime import date

import pytest

from index.strategy_data import StrategyDataProvider
from index.valuation_data import StrategyValuationProvider

END = date(2026, 9, 30)


def row(day="2026-09-30", pe=10):
    return {"indexCode": "930740", "tradeDate": day, "close": 100, "peg": pe}


def test_prices_then_valuation_share_history_across_instances(tmp_path, monkeypatch):
    path = tmp_path / "cache.db"
    calls = []
    def get(*args):
        calls.append(args)
        return {"data": [row()]}
    monkeypatch.setattr(StrategyDataProvider, "_get_json", get)
    monkeypatch.setattr(StrategyValuationProvider, "_factsheet", lambda *_: {})
    assert StrategyDataProvider(path).fetch_prices("930740", end=END)
    result = StrategyValuationProvider(path).fetch("930740", end=END)
    assert result is not None and result.pe_ttm == 10
    assert len(calls) == 1
    assert calls[0][2]["startDate"] == "20210930"


def test_incremental_overlap_replaces_revisions_and_keeps_coverage(tmp_path, monkeypatch):
    provider = StrategyDataProvider(tmp_path / "cache.db")
    responses = [[row("2026-09-29"), row()], [row(pe=20), row("2026-10-01", 21)]]
    calls = []
    def get(_url, params):
        calls.append(params)
        return {"data": responses.pop(0)}
    monkeypatch.setattr(provider, "_get_json", get)
    provider.fetch_official_history("930740", END)
    updated = provider.fetch_official_history("930740", date(2026, 10, 1))
    assert calls[-1]["startDate"] == "20260921"
    assert [item["peg"] for item in updated] == [20, 21]
    # 新指数首条行情不是请求覆盖起点，查询已覆盖的早期区间无需再次请求。
    assert provider.fetch_official_history("930740", END) == [row(pe=20)]
    assert len(calls) == 2


def test_incremental_failure_never_advances_coverage_and_recovers(tmp_path, monkeypatch):
    provider = StrategyDataProvider(tmp_path / "cache.db")
    monkeypatch.setattr(provider, "_get_json", lambda *_: {"data": [row()]})
    provider.fetch_official_history("930740", END)
    def fail(*_):
        raise TimeoutError("offline")
    monkeypatch.setattr(provider, "_get_json", fail)
    next_day = date(2026, 10, 1)
    with pytest.raises(RuntimeError, match="历史"):
        provider.fetch_official_history("930740", next_day)
    with pytest.raises(RuntimeError):
        provider.fetch_official_history("930740", next_day)
    provider._cache.invalidate("csi_history_status", f"930740:{next_day}", "v2")
    monkeypatch.setattr(provider, "_get_json", lambda *_: {"data": [row(), row("2026-10-01")]})
    assert len(provider.fetch_official_history("930740", next_day)) == 2


def test_filters_invalid_future_and_wrong_code_rows(tmp_path, monkeypatch):
    provider = StrategyDataProvider(tmp_path / "cache.db")
    data = [row(), row("2026-10-01"), row("2026-09-26"), dict(row(), indexCode="000300"), dict(row(), close=0)]
    monkeypatch.setattr(provider, "_get_json", lambda *_: {"data": data})
    assert provider.fetch_official_history("930740", END) == [row()]


def test_full_calibration_after_thirty_days(tmp_path, monkeypatch):
    provider = StrategyDataProvider(tmp_path / "cache.db")
    clock = [1000000.0]
    monkeypatch.setattr("index.official_history.time.time", lambda: clock[0])
    calls = []
    def get(_url, params):
        calls.append(params)
        return {"data": [row()]}
    monkeypatch.setattr(provider, "_get_json", get)
    provider.fetch_official_history("930740", END)
    clock[0] += 30 * 86400
    provider.fetch_official_history("930740", date(2026, 10, 1))
    assert calls[-1]["startDate"] == "20211001"


def test_failed_snapshot_retry_does_not_refetch_history(tmp_path, monkeypatch):
    provider = StrategyValuationProvider(tmp_path / "cache.db")
    history_calls = []
    def get(*_):
        history_calls.append(1)
        return {"data": [row()]}
    monkeypatch.setattr(provider, "_get_json", get)
    def fail(*_):
        raise TimeoutError("pdf")
    monkeypatch.setattr(provider, "_factsheet", fail)
    provider.fetch("930740", end=END)
    with provider._cache._get_conn() as connection:
        connection.execute("UPDATE cache SET created_at=created_at-301 WHERE data_type=?", ("valuation_factsheet",))
    monkeypatch.setattr(provider, "_factsheet", lambda *_: {"symbol": "930740", "as_of": END, "pb": 1.2})
    result = provider.fetch("930740", end=END)
    assert result is not None and result.pb == 1.2
    assert len(history_calls) == 1


def test_undisclosed_snapshot_is_success_and_cached(tmp_path, monkeypatch):
    provider = StrategyValuationProvider(tmp_path / "cache.db")
    monkeypatch.setattr(provider, "_get_json", lambda *_: {"data": [row()]})
    calls = []
    def snapshot(*_):
        calls.append(1)
        return {"symbol": "930740", "as_of": END, "pb": None}
    monkeypatch.setattr(provider, "_factsheet", snapshot)
    provider.fetch("930740", end=END)
    provider.fetch("930740", end=END)
    assert calls == [1]
    cached = provider._read_cached("valuation_factsheet", f"csi:930740:{END}")
    assert cached["ok"] is True


def test_real_collector_reuses_target_history(tmp_path, monkeypatch):
    from data.schemas import AnalysisTarget, StrategySnapshot
    from index.collector import IndexDataCollector

    provider = StrategyValuationProvider(tmp_path / "cache.db")
    calls = []
    def get(_url, params):
        calls.append(params["indexCode"])
        return {"data": [dict(row(), indexCode=params["indexCode"])]}
    monkeypatch.setattr(provider, "_get_json", get)
    monkeypatch.setattr(provider, "_factsheet", lambda *_: {})
    monkeypatch.setattr(provider, "collect", lambda *_: StrategySnapshot(
        source="官方", as_of=END, strategy_kind="dividend_low_volatility", members=[]))
    monkeypatch.setattr("index.valuation_data.StrategyValuationProvider", lambda: provider)
    target = AnalysisTarget(target_type="index", symbol="930740", name="红利低波", index_style="strategy")
    context = IndexDataCollector().collect(target)
    assert context.price_data
    assert context.valuation_data is not None and context.valuation_data.pe_ttm == 10
    assert calls.count("930740") == 1
    assert calls.count("000300") == 1


def test_empty_incremental_response_preserves_success_artifact(tmp_path, monkeypatch):
    import json

    provider = StrategyDataProvider(tmp_path / "cache.db")
    monkeypatch.setattr(provider, "_get_json", lambda *_: {"data": [row()]})
    provider.fetch_official_history("930740", END)
    monkeypatch.setattr(provider, "_get_json", lambda *_: {"data": []})
    with pytest.raises(RuntimeError):
        provider.fetch_official_history("930740", date(2026, 10, 1))
    raw = provider._cache.get_stale("csi_history_artifact", "930740", "v2")
    assert raw is not None
    artifact = json.loads(raw)
    assert artifact["covered_end"] == END.isoformat()
    assert artifact["rows"] == [row()]


def test_daily_pb_failure_expiry_retries_only_daily_source(tmp_path, monkeypatch):
    provider = StrategyValuationProvider(tmp_path / "cache.db")
    calls = {"history": 0, "factsheet": 0, "daily": 0}
    def get(*_):
        calls["history"] += 1
        return {"data": [dict(row(), indexCode="000015")]}
    def sheet(*_):
        calls["factsheet"] += 1
        return {"symbol": "000015", "as_of": END, "pb": 0.7}
    def daily(*_):
        calls["daily"] += 1
        if calls["daily"] == 1:
            raise TimeoutError("daily")
        return {"symbol": "000015", "as_of": END, "pb": 0.8}
    monkeypatch.setattr(provider, "_get_json", get)
    monkeypatch.setattr(provider, "_factsheet", sheet)
    monkeypatch.setattr(provider, "_current_pb", daily)
    first = provider.fetch("000015", end=END)
    assert first is not None and first.pb == 0.7
    provider.fetch("000015", end=END)
    assert calls == {"history": 1, "factsheet": 1, "daily": 1}
    with provider._cache._get_conn() as connection:
        ttls = dict(connection.execute("SELECT data_type,ttl_seconds FROM cache"))
        assert ttls["valuation_daily_pb"] == 300
        assert ttls["valuation_factsheet"] == 86400
        connection.execute("UPDATE cache SET created_at=created_at-301 WHERE data_type=?", ("valuation_daily_pb",))
    recovered = provider.fetch("000015", end=END)
    assert recovered is not None and recovered.pb == 0.8
    assert calls == {"history": 1, "factsheet": 1, "daily": 2}


def test_calendar_end_jump_triggers_full_calibration(tmp_path, monkeypatch):
    provider = StrategyDataProvider(tmp_path / "cache.db")
    calls = []
    def get(_url, params):
        calls.append(params)
        return {"data": [row()]}
    monkeypatch.setattr(provider, "_get_json", get)
    provider.fetch_official_history("930740", END)
    provider.fetch_official_history("930740", date(2026, 10, 30))
    assert calls[-1]["startDate"] == "20211030"


def test_new_successful_coverage_supersedes_old_failure_status(tmp_path, monkeypatch):
    provider = StrategyDataProvider(tmp_path / "cache.db")
    calls = []
    def get(_url, params):
        calls.append(params["endDate"])
        if params["endDate"] == "20261001":
            raise TimeoutError("failure")
        return {"data": [row(), row("2026-10-01"), row("2026-10-02")]}
    monkeypatch.setattr(provider, "_get_json", get)
    provider.fetch_official_history("930740", END)
    with pytest.raises(RuntimeError):
        provider.fetch_official_history("930740", date(2026, 10, 1))
    provider.fetch_official_history("930740", date(2026, 10, 2))
    assert provider.fetch_official_history("930740", date(2026, 10, 1)) == [row(), row("2026-10-01")]
    assert len(calls) == 3


def test_historical_slice_recalibrates_after_thirty_days(tmp_path, monkeypatch):
    provider = StrategyDataProvider(tmp_path / "cache.db")
    clock = [1000000.0]
    monkeypatch.setattr("index.official_history.time.time", lambda: clock[0])
    calls = []
    def get(_url, params):
        calls.append(params)
        return {"data": [row("2026-09-29"), row()]}
    monkeypatch.setattr(provider, "_get_json", get)
    historical_end = date(2026, 9, 29)
    provider.fetch_official_history("930740", historical_end)
    provider.fetch_official_history("930740", END)
    clock[0] += 31 * 86400
    result = provider.fetch_official_history("930740", historical_end)
    assert result == [row("2026-09-29")]
    assert len(calls) == 3
    assert calls[-1]["startDate"] == "20210929"
    assert calls[-1]["endDate"] == "20260929"
