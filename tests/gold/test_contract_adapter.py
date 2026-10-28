"""黄金专用来源遵循统一数据和诊断契约。"""
from datetime import UTC, date, datetime, timedelta

import pytest

from data.base import CollectionResult, DataSource
from data.collection_state import record_consistent
from gold.adapter import GoldSourceAdapter
from gold.models import GoldObservation, SourceKey
from gold.sources import empty_series
from radar.provider_request import ProviderRequestError

NOW = datetime(2026, 10, 26, 10, tzinfo=UTC)


def valid_response(key: SourceKey = "dollar"):
    series = empty_series(key, NOW, "")
    series.status, series.error = "ok", None
    series.observations = [GoldObservation(observation_date=date(2026, 10, 23), value=100)]
    return series.model_dump(mode="json")


def test_adapter_implements_contract_and_passes_original_budget(tmp_path):
    calls = []

    def execute(module, function, kwargs, *, timeout):
        calls.append((module, function, kwargs, timeout))
        return valid_response()

    adapter = GoldSourceAdapter(execute, tmp_path, 2592000)
    assert isinstance(adapter, DataSource)
    assert type(adapter).fetch_result is not DataSource.fetch_result
    assert adapter.supports("gold", "dollar")
    assert adapter.supports("gold", "gold_dollar")
    assert not adapter.supports("a-shares", "dollar")
    result = adapter.fetch_result("GOLD", key="dollar", now=NOW, timeout=13)
    assert isinstance(result, CollectionResult)
    assert len(result.rows) == 1 and result.rows[0].as_of == date(2026, 10, 23)
    assert result.record is not None and record_consistent(result.record)
    assert result.record.source == "美联储 H.10"
    assert result.record.collected_at == NOW
    assert result.record.result_state == "complete" and result.record.cache_status == "live"
    assert calls == [("gold.sources", "fetch_source", {"key": "dollar", "now_iso": NOW.isoformat(),
                      "history_dir": str(tmp_path), "historical_cache_seconds": 2592000}, 13)]


@pytest.mark.parametrize("invalid", ["unit", "identity", "future", "empty", "future_fetch", "stale"])
def test_bad_source_results_are_failed_without_rows(tmp_path, invalid):
    result = valid_response()
    if invalid == "unit":
        result["unit"] = "wrong"
    elif invalid == "identity":
        result["key"] = "fx"
    elif invalid == "future":
        result["observations"][0]["observation_date"] = (NOW.date() + timedelta(days=1)).isoformat()
    elif invalid == "empty":
        result["observations"] = []
    elif invalid == "future_fetch":
        result["fetched_at"] = (NOW + timedelta(seconds=1)).isoformat()
    elif invalid == "stale":
        result["status"] = "stale"
    adapter = GoldSourceAdapter(lambda *args, **kwargs: result, tmp_path, 10)
    response = adapter.fetch_result("GOLD", key="dollar", now=NOW, timeout=3)
    assert response.rows == []
    assert response.record is not None and record_consistent(response.record)
    assert response.record.result_state == "failed"
    assert response.record.collected_at is None


def test_network_failure_has_safe_diagnostic_and_does_not_leak_secret(tmp_path):
    def fail(*args, **kwargs):
        raise RuntimeError("https://example.org/?key=secret 原始响应")

    response = GoldSourceAdapter(fail, tmp_path, 10).fetch_result("GOLD", key="dollar", now=NOW, timeout=3)
    assert response.rows == [] and response.record is not None
    assert response.record.result_state == "failed" and response.record.collected_at is None
    assert "secret" not in response.record.error_message
    assert "原始响应" not in response.record.error_message


@pytest.mark.parametrize("error", [TimeoutError("秘密"), ProviderRequestError("数据源请求超过 3 秒，已终止超时进程")])
def test_timeout_is_distinct_from_other_failures(tmp_path, error):
    def fail(*args, **kwargs):
        raise error

    response = GoldSourceAdapter(fail, tmp_path, 10).fetch_result("GOLD", key="dollar", now=NOW, timeout=3)
    assert response.record is not None and response.record.error_kind == "timeout"
    assert response.record.error_message == "黄金来源请求超时"


def test_global_unavailable_does_not_call_network(tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("不可用来源不应请求")

    response = GoldSourceAdapter(forbidden, tmp_path, 10).fetch_result("GOLD", key="global_reserves", now=NOW, timeout=3)
    assert response.rows == [] and response.record is not None
    assert response.record.result_state == "failed"
    assert "未通过接入验收" in response.record.error_message


def test_invalid_call_fails_without_network_and_fetch_is_compatible(tmp_path):
    calls = []

    def execute(*args, **kwargs):
        calls.append(True)
        return valid_response()

    adapter = GoldSourceAdapter(execute, tmp_path, 10)
    assert adapter.fetch_result("WRONG", key="dollar", now=NOW, timeout=3).rows == []
    assert adapter.fetch_result("GOLD", key="not_real", now=NOW, timeout=3).rows == []
    assert adapter.fetch_result("GOLD", key="dollar", now=NOW.replace(tzinfo=None), timeout=3).rows == []
    assert adapter.fetch_result("GOLD", key="dollar", now=NOW, timeout=0).rows == []
    assert not calls
    rows = adapter.fetch("GOLD", key="dollar", now=NOW, timeout=3)
    assert len(rows) == 1 and rows[0].as_of == date(2026, 10, 23) and calls == [True]
