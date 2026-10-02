"""配置雷达采集状态存储测试。"""

from radar.collector_store import CollectorStore


def test_collector_store_preserves_latest_status_per_universe(tmp_path):
    store = CollectorStore(tmp_path / "collector.db")

    store.record("cn_hk_etf", "completed", "run-cn")
    store.record("overseas_etf", "failed", "上游超时")

    states = store.latest(("cn_hk_etf", "overseas_etf", "missing"))

    assert states[0]["status"] == "completed"
    assert states[0]["run_id"] == "run-cn"
    assert states[1]["status"] == "failed"
    assert states[1]["error_summary"] == "上游超时"
    assert states[2] == {"universe_id": "missing", "status": "never"}


def test_collector_store_records_runtime_and_recent_events(tmp_path):
    store = CollectorStore(tmp_path / "collector.db")

    store.record_started()
    store.record_heartbeat()
    store.record("cn_hk_etf", "completed", "run-cn")

    assert store.runtime()["heartbeat_at"]
    assert store.recent_events()[0]["message"] == "cn_hk_etf 采集完成：run-cn"

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest


def test_settings_migration_and_revision(tmp_path):
    store = CollectorStore(tmp_path / "collector.db")
    store.record("old", "completed", "snapshot")
    assert store.settings()["enabled"] is False
    updated = store.update_settings(enabled=True, hour=19, minute=15, revision=0)
    assert updated["revision"] == 1
    assert CollectorStore(store.db_path).settings() == updated
    with pytest.raises(ValueError, match="修订"):
        store.update_settings(enabled=False, hour=18, minute=30, revision=0)
    assert store.latest(("old",))[0]["run_id"] == "snapshot"
    with pytest.raises(ValueError):
        store.update_settings(enabled=True, hour=24, minute=0, revision=1)


def test_queue_idempotency_atomic_claim_and_expiry(tmp_path):
    store = CollectorStore(tmp_path / "collector.db")
    now = datetime.now(UTC) + timedelta(seconds=1)
    run = store.enqueue("pool", "2026-09-29", "fp", "automatic", ["510300", "510500"])
    assert store.enqueue("pool", "2026-09-29", "fp", "automatic", ["510300"])["id"] == run["id"]
    with ThreadPoolExecutor(2) as pool:
        claimed = list(pool.map(lambda worker: CollectorStore(store.db_path).claim(worker, now, 60), ["a", "b"]))
    assert len([item for item in claimed if item]) == 1
    owner = next(item for item in claimed if item)["worker_id"]
    with pytest.raises(ValueError, match="租约"):
        store.set_phase(run["id"], "syncing", worker_id="outsider")
    store.record_item(run["id"], "510300", "completed", worker_id=owner)
    assert store.recover_expired(now + timedelta(seconds=61)) == 1
    assert store.get_run(run["id"])["status"] == "retry_wait"
    assert store.get_run(run["id"])["items"][0]["status"] == "completed"


def test_retry_preserves_success_and_terminal_automatic_idempotency(tmp_path):
    store = CollectorStore(tmp_path / "collector.db")
    now = datetime.now(UTC) + timedelta(seconds=1)
    run = store.enqueue("pool", "2026-09-29", "fp", "automatic", ["ok", "bad"])
    store.update_settings(enabled=True, hour=18, minute=30, revision=0)
    for attempt in range(1, 4):
        claimed = store.claim("worker", now, 60)
        assert claimed is not None
        assert claimed["attempt"] == attempt
        store.record_item(run["id"], "ok", "completed", worker_id="worker")
        store.record_item(run["id"], "bad", "failed", "超时", worker_id="worker")
        store.finish(run["id"], "failed", "超时", worker_id="worker")
        if attempt < 3:
            store.retry(run["id"], manual=False, now=now)
            now += timedelta(minutes=6)
    assert store.enqueue("pool", "2026-09-29", "fp", "automatic", ["ok", "bad"])["status"] == "failed"
    with pytest.raises(ValueError, match="次数"):
        store.retry(run["id"], manual=False, now=now)
    retried = store.retry(run["id"], manual=True, now=now)
    assert retried["items"][0]["status"] == "completed"
    assert retried["items"][1]["status"] == "pending"
    claimed = store.claim("worker", now)
    assert claimed is not None
    assert claimed["source"] == "manual"


def test_disabling_cancels_automatic_only(tmp_path):
    store = CollectorStore(tmp_path / "collector.db")
    store.update_settings(enabled=True, hour=18, minute=30, revision=0)
    automatic = store.enqueue("pool", "2026-09-29", "a", "automatic", ["a"])
    store.claim("worker")
    queued = store.enqueue("pool", "2026-09-29", "b", "automatic", ["a"])
    manual = store.enqueue("pool", "2026-09-29", "c", "manual", ["a"])
    store.update_settings(enabled=False, hour=18, minute=30, revision=1)
    assert store.get_run(automatic["id"])["cancel_requested"] is True
    assert store.get_run(queued["id"])["status"] == "cancelled"
    assert store.get_run(manual["id"])["status"] == "queued"


def test_real_legacy_schema_migrates_without_losing_audit(tmp_path):
    import sqlite3

    database = tmp_path / "radar_collector.db"
    marker = tmp_path / "radar_collector.disabled"
    marker.write_text("disabled", encoding="utf-8")
    with sqlite3.connect(database) as conn:
        conn.executescript("""
            CREATE TABLE collector_status(universe_id TEXT PRIMARY KEY,completed_at TEXT NOT NULL,
                status TEXT NOT NULL,run_id TEXT,error_summary TEXT);
            INSERT INTO collector_status VALUES ('old','2026-09-01','completed','old-snapshot',NULL);
            CREATE TABLE collector_runtime(singleton INTEGER PRIMARY KEY,started_at TEXT NOT NULL,heartbeat_at TEXT NOT NULL);
            INSERT INTO collector_runtime VALUES (1,'2026-09-01','2026-09-01');
            CREATE TABLE collector_events(id INTEGER PRIMARY KEY AUTOINCREMENT,occurred_at TEXT NOT NULL,
                universe_id TEXT,level TEXT NOT NULL,message TEXT NOT NULL);
            INSERT INTO collector_events(occurred_at,level,message) VALUES ('2026-09-01','info','旧事件');
        """)
    store = CollectorStore(database)
    assert store.settings()["enabled"] is False
    assert store.latest(("old",))[0]["run_id"] == "old-snapshot"
    assert store.recent_events()[0]["message"] == "旧事件"
    assert store.runtime()["started_at"] == "2026-09-01"


def test_heartbeat_owner_and_lease_renewal(tmp_path):
    store = CollectorStore(tmp_path / "collector.db")
    store.record_started("owner", "waiting")
    store.record_heartbeat("intruder", "syncing")
    assert store.runtime()["phase"] == "waiting"
    store.record_heartbeat("owner", "syncing")
    assert store.runtime()["worker_id"] == "owner"
    assert store.runtime()["phase"] == "syncing"
    run = store.enqueue("pool", "2026-09-29", "fp", "manual", ["a"])
    now = datetime.fromisoformat(run["created_at"]) + timedelta(seconds=1)
    store.claim("owner", now)
    store.renew_lease(run["id"], "owner", now + timedelta(seconds=50))
    assert store.recover_expired(now + timedelta(seconds=70)) == 0
    with pytest.raises(ValueError, match="租约"):
        store.renew_lease(run["id"], "intruder", now)


def test_disabled_auto_retry_and_naive_time_rejected(tmp_path):
    store = CollectorStore(tmp_path / "collector.db")
    run = store.enqueue("pool", "2026-09-29", "fp", "automatic", ["a"])
    store.claim("owner")
    store.finish(run["id"], "failed", worker_id="owner")
    with pytest.raises(ValueError, match="关闭"):
        store.retry(run["id"], manual=False)
    with pytest.raises(ValueError, match="时区"):
        store.claim("owner", datetime(2026, 9, 30, tzinfo=None))  # noqa: DTZ001 -- 验证无时区输入被拒绝
    store.retry(run["id"], manual=True)
    assert store.claim("owner") is not None



def test_finish_updates_legacy_status_and_cancel_preserves_snapshot(tmp_path):
    store = CollectorStore(tmp_path / "collector.db")
    run = store.enqueue("pool", "2026-09-29", "fp", "manual", ["a"])
    store.claim("owner")
    store.finish(run["id"], "partial", snapshot_run_id="snapshot", worker_id="owner")
    assert store.latest(("pool",))[0]["run_id"] == "snapshot"
    second = store.enqueue("pool", "2026-09-30", "fp", "manual", ["a"])
    store.claim("owner")
    store.finish(second["id"], "cancelled", worker_id="owner")
    assert store.latest(("pool",))[0]["run_id"] == "snapshot"


def test_manual_background_retry_works_with_automatic_disabled(tmp_path):
    store = CollectorStore(tmp_path / "collector.db")
    run = store.enqueue("pool", "2026-09-29", "fp", "manual", ["ok", "bad"])
    store.claim("worker")
    store.record_item(run["id"], "ok", "completed", worker_id="worker")
    store.record_item(run["id"], "bad", "failed", worker_id="worker")
    store.finish(run["id"], "partial", worker_id="worker")
    retried = store.retry(run["id"], manual=False)
    assert retried["source"] == "manual"
    assert retried["attempt"] == 1
    assert retried["items"][0]["status"] == "completed"


def test_recovery_cancel_and_reenable_preserve_success(tmp_path):
    store = CollectorStore(tmp_path / "collector.db")
    store.update_settings(enabled=True, hour=18, minute=30, revision=0)
    run = store.enqueue("pool", "2026-09-29", "fp", "recovery", ["ok", "pending"])
    store.claim("worker")
    store.record_item(run["id"], "ok", "completed", worker_id="worker")
    store.update_settings(enabled=False, hour=18, minute=30, revision=1)
    assert store.get_run(run["id"])["cancel_requested"]
    store.finish(run["id"], "cancelled", worker_id="worker")
    store.update_settings(enabled=True, hour=18, minute=30, revision=2)
    retried = store.retry(run["id"], manual=False)
    assert retried["source"] == "recovery"
    assert retried["items"][0]["status"] == "completed"


def test_runtime_error_visible_and_event_audited(tmp_path):
    store = CollectorStore(tmp_path / "collector.db")
    store.record_started("owner")
    store.record_heartbeat("owner", "blocked", error_summary="日历不可用")
    store.record_event("error", "日历不可用")
    assert store.runtime()["last_error"] == "日历不可用"
    assert store.recent_events()[0]["message"] == "日历不可用"
    store.record_heartbeat("owner", "waiting")
    assert store.runtime()["last_error"] is None


def test_superseded_target_cancels_only_old_automatic_pending(tmp_path):
    store = CollectorStore(tmp_path / "collector.db")
    old = store.enqueue("pool", "2026-09-28", "old", "recovery", ["ok", "pending"])
    manual = store.enqueue("pool", "2026-09-28", "manual", "manual", ["ok"])
    latest = store.enqueue("pool", "2026-09-29", "new", "automatic", ["ok"])
    assert store.cancel_superseded("2026-09-29") == 1
    result = store.get_run(old["id"])
    assert result["status"] == "cancelled"
    assert "最新目标" in result["error_summary"]
    assert store.get_run(manual["id"])["status"] == "queued"
    assert store.get_run(latest["id"])["status"] == "queued"


def test_promote_manual_detaches_from_automatic_cancel_without_losing_owner(tmp_path):
    store = CollectorStore(tmp_path / "collector.db")
    run = store.enqueue("pool", "2026-09-29", "fp", "automatic", ["a"])
    store.claim("worker")
    store.update_settings(enabled=False, hour=18, minute=30, revision=0)
    promoted = store.promote_manual(run["id"])
    assert promoted["source"] == "manual"
    assert promoted["cancel_requested"] is False
    assert promoted["worker_id"] == "worker"
    assert promoted["status"] == "running"
    store.finish(run["id"], "partial", worker_id="worker")
    store.retry(run["id"], manual=False)
    promoted = store.promote_manual(run["id"])
    assert promoted["status"] == "queued"
    assert store.claim("worker") is not None


def test_cached_success_can_be_explicitly_invalidated_after_validation_failure(tmp_path):
    store = CollectorStore(tmp_path / "collector.db")
    run = store.enqueue("pool", "2026-09-29", "fp", "manual", ["good", "invalid"])
    store.claim("owner")
    store.record_item(run["id"], "good", "completed", worker_id="owner")
    store.record_item(run["id"], "invalid", "completed", worker_id="owner")
    store.invalidate_item(run["id"], "invalid", "缓存复权口径已失效", "owner")
    with pytest.raises(ValueError, match="租约"):
        store.invalidate_item(run["id"], "good", "不允许旧worker修改", "other")
    result = store.get_run(run["id"])
    assert [item["status"] for item in result["items"]] == ["completed", "failed"]
    store.finish(run["id"], "partial", worker_id="owner")
    retried = store.retry(run["id"], manual=False)
    assert [item["status"] for item in retried["items"]] == ["completed", "pending"]
    assert any("缓存" in event["message"] for event in store.recent_events())
