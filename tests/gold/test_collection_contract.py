"""黄金统一采集：失败等待、刷新和同源请求合并。"""
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

from data.base import CollectionResult
from data.collection_state import make_record
from data.schemas import DataCollectionRecord
from gold.data import GoldCollector
from gold.models import GoldObservation, GoldRules, GoldSeries
from gold.sources import empty_series

NOW = datetime(2026, 10, 26, 10, tzinfo=UTC)


def record(series: GoldSeries) -> DataCollectionRecord:
    assert series.collection_record is not None
    return series.collection_record


def sample(key):
    series = empty_series(key, NOW, "")
    series.status, series.error = "ok", None
    series.observations = [GoldObservation(observation_date=date(2026, 10, 23), value=100)]
    return series.model_dump(mode="json")


def test_failure_wait_and_force_refresh(tmp_path):
    calls = []

    def fail(*args, **kwargs):
        calls.append(True)
        raise TimeoutError("上游超时")

    collector = GoldCollector(tmp_path, request_executor=fail, sources=["dollar"])
    first = collector.collect(now=NOW).series["dollar"]
    second = collector.collect(now=NOW + timedelta(minutes=29)).series["dollar"]
    assert len(calls) == 1
    assert record(first).result_state == "failed"
    assert record(first).collected_at is None
    assert record(second).cache_status == "cache"
    collector.collect(now=NOW + timedelta(minutes=29), refresh=True)
    assert len(calls) == 2
    collector.collect(now=NOW + timedelta(minutes=60))
    assert len(calls) == 3


def test_concurrent_same_source_uses_one_request(tmp_path):
    start = threading.Barrier(2)
    calls = []

    def execute(module, function, kwargs, *, timeout):
        calls.append(True)
        time.sleep(.2)
        return sample(kwargs["key"])

    def collect():
        collector = GoldCollector(tmp_path, request_executor=execute, sources=["dollar"])
        start.wait(timeout=5)
        return collector.collect(now=NOW).series["dollar"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: collect(), range(2)))
    assert len(calls) == 1
    assert all(value.as_of == date(2026, 10, 23) for value in values)
    assert {record(value).cache_status for value in values} == {"live", "cache"}


def full_series():
    series = GoldSeries.model_validate(sample("dollar"))
    series.observations = [GoldObservation(observation_date=NOW.date() - timedelta(days=i), value=100 + i / 100)
                           for i in range(2200, 0, -1)]
    return series


def test_legacy_complete_snapshot_survives_first_partial_refresh(tmp_path):
    def partial(*args, **kwargs):
        result = sample("dollar")
        result["observations"][0]["observation_date"] = (NOW.date() - timedelta(days=1)).isoformat()
        return result
    collector = GoldCollector(tmp_path, request_executor=partial, sources=["dollar"])
    path = collector._cache_path("dollar")
    path.parent.mkdir(parents=True)
    old = full_series()
    expires = NOW + timedelta(hours=2)
    path.write_text(json.dumps({"expires_at": expires.isoformat(), "series": old.model_dump(mode="json")}), encoding="utf-8")
    current = collector.collect(now=NOW + timedelta(minutes=1), refresh=True).series["dollar"]
    assert record(current).result_state == "partial"
    saved = json.loads(collector._snapshot_path("dollar", "last_complete").read_text(encoding="utf-8"))
    assert len(saved["series"]["observations"]) == 2200
    assert saved["expires_at"] == expires.isoformat()


def test_complete_partial_failure_recovery_preserves_complete_snapshot(tmp_path):
    response = {"value": lambda *a, **k: full_series().model_dump(mode="json")}
    def execute(*args, **kwargs):
        return response["value"](*args, **kwargs)
    collector = GoldCollector(tmp_path, request_executor=execute, sources=["dollar"])
    first = collector.collect(now=NOW).series["dollar"]
    assert record(first).result_state == "complete"
    def shortened(*args, **kwargs):
        value = full_series()
        value.observations = value.observations[-900:]
        return value.model_dump(mode="json")
    response["value"] = shortened
    partial = collector.collect(now=NOW + timedelta(minutes=1), refresh=True).series["dollar"]
    assert record(partial).result_state == "partial"
    saved = json.loads(collector._snapshot_path("dollar", "last_complete").read_text(encoding="utf-8"))
    assert len(saved["series"]["observations"]) == 2200
    def fail(*args, **kwargs):
        raise TimeoutError("上游超时")
    response["value"] = fail
    failed = collector.collect(now=NOW + timedelta(minutes=2), refresh=True).series["dollar"]
    assert failed.status == "stale"
    assert record(failed).error_kind == "timeout"
    assert failed.as_of == first.as_of
    response["value"] = lambda *a, **k: full_series().model_dump(mode="json")
    recovered = collector.collect(now=NOW + timedelta(minutes=3), refresh=True).series["dollar"]
    assert recovered.status == "ok"
    assert record(recovered).result_state == "complete"
    assert not collector._snapshot_path("dollar", "last_attempt").exists()


def test_unexpected_source_exception_keeps_cached_reference(tmp_path, monkeypatch):
    collector = GoldCollector(
        tmp_path, request_executor=lambda *a, **k: full_series().model_dump(mode="json"), sources=["dollar"],
    )
    first = collector.collect(now=NOW).series["dollar"]
    def boom(*args, **kwargs):
        raise RuntimeError("意外错误")
    monkeypatch.setattr(collector, "_collect_source", boom)
    failed = collector.collect(now=NOW + timedelta(minutes=1)).series["dollar"]
    assert failed.status == "stale"
    assert record(failed).error_kind == "unexpected"
    assert failed.as_of == first.as_of


def test_corrupt_but_valid_json_snapshot_is_revalidated(tmp_path):
    calls = []
    def execute(*a, **k):
        calls.append(True)
        return sample("dollar")
    collector = GoldCollector(tmp_path, request_executor=execute, sources=["dollar"])
    saved = full_series()
    saved.collection_record = make_record(saved.source, "complete", collected_at=saved.fetched_at)
    saved.observations.append(saved.observations[-1])
    collector._write_cache("dollar", saved, NOW)
    result = collector.collect(now=NOW).series["dollar"]
    assert calls == [True]
    assert record(result).result_state == "partial"


def test_conflicting_rows_and_failure_record_are_not_cached(tmp_path, monkeypatch):
    collector = GoldCollector(tmp_path, sources=["dollar"])
    value = GoldSeries.model_validate(sample("dollar"))
    failure = make_record(value.source, "failed", error_kind="timeout", error_message="失败")
    monkeypatch.setattr(collector, "_request", lambda *args: CollectionResult(rows=[value], record=failure))
    result = collector.collect(now=NOW).series["dollar"]
    assert record(result).error_kind == "contract_error"
    assert not list(tmp_path.rglob("*.json"))


def test_unwritable_cache_does_not_block_live_source(tmp_path, monkeypatch):
    from pathlib import Path
    original = Path.mkdir
    def guarded(path, *args, **kwargs):
        if path.name == "cache":
            raise PermissionError("不可写")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", guarded)
    collector = GoldCollector(tmp_path, request_executor=lambda *a, **k: sample("dollar"), sources=["dollar"])
    value = collector.collect(now=NOW).series["dollar"]
    assert value.status == "ok"
    assert record(value).result_state == "partial"
    assert "缓存不可用" in " ".join(value.notes)


def test_older_request_cannot_overwrite_newer_success_or_failure(tmp_path):
    def execute(module, function, kwargs, *, timeout):
        value = sample("dollar")
        value["fetched_at"] = kwargs["now_iso"]
        return value
    collector = GoldCollector(tmp_path, request_executor=execute, sources=["dollar"])
    collector.collect(now=NOW + timedelta(minutes=1), refresh=True)
    path = collector._cache_path("dollar")
    newer = json.loads(path.read_text(encoding="utf-8"))["series"]["fetched_at"]
    collector.collect(now=NOW, refresh=True)
    assert json.loads(path.read_text(encoding="utf-8"))["series"]["fetched_at"] == newer
    other = GoldCollector(tmp_path / "other", sources=["dollar"])
    failure = make_record("美联储 H.10", "failed", error_kind="timeout")
    other._write_attempt("dollar", failure, NOW + timedelta(minutes=1))
    other._write_attempt("dollar", failure, NOW)
    attempt = json.loads(other._snapshot_path("dollar", "last_attempt").read_text(encoding="utf-8"))
    assert attempt["attempted_at"] == (NOW + timedelta(minutes=1)).isoformat()


def test_newer_success_is_not_hidden_by_old_attempt(tmp_path):
    calls = []
    def execute(*a, **k):
        calls.append(True)
        return sample("dollar")
    collector = GoldCollector(tmp_path, request_executor=execute, sources=["dollar"])
    collector.collect(now=NOW)
    old_failure = make_record("美联储 H.10", "failed", error_kind="timeout")
    collector._atomic_json(collector._snapshot_path("dollar", "last_attempt"), {
        "attempted_at": (NOW - timedelta(minutes=1)).isoformat(), "record": old_failure.model_dump(mode="json"),
    })
    result = collector.collect(now=NOW + timedelta(minutes=1)).series["dollar"]
    assert calls == [True]
    assert result.status == "ok"
    assert record(result).cache_status == "cache"


def test_failure_wait_starts_when_request_finishes(tmp_path):
    def slow_failure(*args, **kwargs):
        time.sleep(.15)
        raise TimeoutError("上游超时")
    collector = GoldCollector(tmp_path, request_executor=slow_failure, sources=["dollar"])
    collector.collect(now=NOW)
    value = json.loads(collector._snapshot_path("dollar", "last_attempt").read_text(encoding="utf-8"))
    attempted = datetime.fromisoformat(value["attempted_at"])
    assert (attempted - NOW).total_seconds() >= .1


def test_cross_process_lock_wait_respects_total_budget(tmp_path):
    from pathlib import Path

    calls = []
    collector = GoldCollector(tmp_path, rules=GoldRules(total_timeout_seconds=.2),
                              request_executor=lambda *a, **k: calls.append(True), sources=["dollar"])
    path = collector._cache_path("dollar")
    path.parent.mkdir(parents=True)
    ready, release = tmp_path / "ready", tmp_path / "release"
    program = """\
import sys, time
from pathlib import Path
from filelock import FileLock
with FileLock(sys.argv[1]):
    Path(sys.argv[2]).touch()
    end = time.monotonic() + 10
    while not Path(sys.argv[3]).exists() and time.monotonic() < end:
        time.sleep(.02)
"""
    process = subprocess.Popen([sys.executable, "-c", program, str(path) + ".lock", str(ready), str(release)])
    try:
        end = time.monotonic() + 8
        while not ready.exists() and time.monotonic() < end:
            time.sleep(.02)
        assert ready.exists(), "子进程未获取来源锁"
        started = time.monotonic()
        value = collector.collect(now=NOW).series["dollar"]
        assert time.monotonic() - started < 1.5
        assert record(value).error_kind == "timeout"
        assert calls == []
    finally:
        Path(release).touch()
        process.wait(timeout=10)
