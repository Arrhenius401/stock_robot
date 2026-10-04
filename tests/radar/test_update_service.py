"""组合更新的持久化、隔离和跨执行者互斥回归。"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from filelock import FileLock

from radar.collector_store import CollectorStore
from radar.collector_worker import CollectorWorker
from radar.universe import UniverseRepository
from radar.update_service import UpdateService, UpdateStore

ROOT = Path(__file__).parents[2]


class Calendar:
    def latest_completed(self, now):
        return date(2026, 9, 30)

    def trading_days(self, start, end):
        return (date(2026, 9, 30),)


class Markets:
    def __init__(self):
        self.calls = 0

    def refresh(self, universe, snapshot, now):
        self.calls += 1
        return {"items": [{"status": "fresh"}], "checked_at": now.isoformat()}


class Snapshots:
    def get_snapshot(self, run_id):
        return self.latest_completed("overseas_etf")

    def latest_completed(self, universe_id):
        return {"run_id": "snapshot", "as_of_date": "2026-09-30", "items": []}


def make_service(tmp_path, calendar=None, markets=None):
    calls = []

    def refresh(pool, **kwargs):
        calls.append(pool)
        for symbol in kwargs["retry_symbols"]:
            kwargs["on_item"](symbol, True, None)
        return "snapshot"

    worker = CollectorWorker(CollectorStore(tmp_path / "radar_collector.db"),
                             UniverseRepository(ROOT / "config/radar_universes"),
                             calendar or Calendar(), refresh, state_dir=tmp_path)
    return UpdateService(worker, Snapshots(), markets or Markets()), calls


def test_immediate_durable_enqueue_does_not_require_calendar_or_daemon(tmp_path):
    class BrokenCalendar(Calendar):
        def latest_completed(self, now):
            raise RuntimeError("日历下载失败")

    service, calls = make_service(tmp_path, BrokenCalendar())
    first = service.enqueue("overseas_etf")
    assert first["status"] == "queued"
    assert service.enqueue("overseas_etf")["id"] == first["id"]
    assert calls == []
    assert service.execute_one()
    result = UpdateStore(service.store.db_path).get(first["id"])
    assert result["status"] == "partial"
    assert result["etf"]["status"] == "failed"
    assert result["overseas"]["items"][0]["status"] == "fresh"
    assert service.worker.store.runtime()["status"] == "never"


def test_completed_etf_is_reused_but_overseas_updates_again(tmp_path):
    markets = Markets()
    service, calls = make_service(tmp_path, markets=markets)
    first = service.enqueue("overseas_etf")
    service.execute_one()
    assert service.store.get(first["id"])["status"] == "completed"
    second = service.enqueue("overseas_etf")
    assert second["id"] != first["id"]
    service.execute_one()
    assert calls == ["overseas_etf"]
    assert markets.calls == 2


def test_shared_execution_lock_prevents_claim_and_duplicate_work(tmp_path):
    service, calls = make_service(tmp_path)
    task = service.enqueue("overseas_etf")
    with FileLock(tmp_path / "radar_collector.execute.lock"):
        assert service.execute_one() is False
    assert service.store.get(task["id"])["status"] == "queued"
    service.execute_one()
    assert calls == ["overseas_etf"]


def test_lease_recovery_is_bounded_and_preserves_live_owner(tmp_path):
    store = UpdateStore(tmp_path / "radar_collector.db")
    task = store.enqueue("overseas_etf", None)
    now = datetime.now(UTC)
    store.claim("first", now)
    assert store.recover_expired(now + timedelta(seconds=30)) == 0
    assert store.recover_expired(now + timedelta(seconds=100)) == 1
    second = store.claim("second", now + timedelta(seconds=101))
    assert second and second["attempt"] == 2
    assert store.recover_expired(now + timedelta(seconds=201)) == 1
    store.claim("third", now + timedelta(seconds=202))
    store.recover_expired(now + timedelta(seconds=302))
    assert store.get(task["id"])["status"] == "failed"


def test_automatic_overseas_checks_on_domestic_holiday_and_deduplicates(tmp_path):
    service, _ = make_service(tmp_path)
    store = service.worker.store
    store.update_settings(enabled=True, hour=18, minute=30, revision=store.settings()["revision"])
    now = datetime.fromisoformat("2026-10-01T19:00:00+08:00")
    service.schedule(now)
    service.schedule(now)
    assert len(service.store.list_runs()) == 2
    assert all(task["source"] == "automatic" for task in service.store.list_runs())
    service.schedule(now + timedelta(days=1))
    # 仍在执行队列中的池不堆积重复请求。
    assert len(service.store.list_runs()) == 2


def test_disabling_automatic_cancels_combined_queue_but_keeps_manual(tmp_path):
    service, calls = make_service(tmp_path)
    automatic = service.store.enqueue("overseas_etf", None, source="automatic")
    manual = service.enqueue("cn_hk_etf")
    service.execute_one()
    assert service.store.get(automatic["id"])["status"] == "cancelled"
    assert service.store.get(manual["id"])["status"] == "completed"
    assert calls == ["cn_hk_etf"]


def test_disabling_during_etf_stops_automatic_overseas_at_boundary(tmp_path):
    markets = Markets()
    service, _ = make_service(tmp_path, markets=markets)
    service.manual_only = False
    store = service.worker.store
    store.update_settings(enabled=True, hour=18, minute=30, revision=store.settings()["revision"])
    original = service.worker.refresh

    def refresh(*args, **kwargs):
        result = original(*args, **kwargs)
        store.update_settings(enabled=False, hour=18, minute=30, revision=store.settings()["revision"])
        return result

    service.worker.refresh = refresh
    task = service.store.enqueue("overseas_etf", None, source="automatic")
    service.execute_one()
    assert service.store.get(task["id"])["status"] == "cancelled"
    assert markets.calls == 0


def test_manual_takeover_promotes_running_child_and_survives_auto_disable(tmp_path):
    markets = Markets()
    service, _ = make_service(tmp_path, markets=markets)
    service.manual_only = False
    store = service.worker.store
    store.update_settings(enabled=True, hour=18, minute=30, revision=store.settings()["revision"])
    original = service.worker.refresh

    def refresh(*args, **kwargs):
        result = service.enqueue("overseas_etf")
        assert result["source"] == "manual"
        store.update_settings(enabled=False, hour=18, minute=30, revision=store.settings()["revision"])
        return original(*args, **kwargs)

    service.worker.refresh = refresh
    task = service.store.enqueue("overseas_etf", None, source="automatic")
    service.execute_one()
    result = service.store.get(task["id"])
    assert result["status"] == "completed"
    assert store.get_run(result["etf"]["task_id"])["source"] == "manual"
    assert markets.calls == 1


def test_etf_retry_finishes_before_combined_result_and_overseas_once(tmp_path, monkeypatch):
    markets = Markets()
    service, _ = make_service(tmp_path, markets=markets)
    attempts = []

    def refresh(pool, **kwargs):
        attempts.append(pool)
        for symbol in kwargs["retry_symbols"]:
            kwargs["on_item"](symbol, len(attempts) > 1, "临时错误" if len(attempts) == 1 else None)
        return "snapshot"

    service.worker.refresh = refresh
    retry = service.worker.store.retry

    def immediate_retry(run_id, manual=True, now=None):
        return retry(run_id, manual=manual, now=datetime.now(UTC) - timedelta(seconds=61))

    monkeypatch.setattr(service.worker.store, "retry", immediate_retry)
    task = service.enqueue("overseas_etf")
    service.execute_one()
    result = service.store.get(task["id"])
    assert result["status"] == "completed"
    assert result["etf"]["status"] == "completed"
    assert attempts == ["overseas_etf", "overseas_etf"]
    assert markets.calls == 1


def test_explicit_target_uses_its_snapshot_instead_of_latest(tmp_path):
    service, _ = make_service(tmp_path)
    seen = []

    class SnapshotsWithHistory(Snapshots):
        def get_snapshot(self, run_id):
            return {"run_id": run_id, "as_of_date": "2026-09-30", "items": []}

        def latest_completed(self, universe_id):
            return {"run_id": "newer", "as_of_date": "2026-10-08", "items": []}

    class RecordingMarkets(Markets):
        def refresh(self, universe, snapshot, now):
            seen.append(snapshot["as_of_date"])
            return super().refresh(universe, snapshot, now)

    service.snapshots = SnapshotsWithHistory()
    service.overseas = RecordingMarkets()
    task = service.enqueue("overseas_etf", date(2026, 9, 30))
    service.execute_one()
    assert service.store.get(task["id"])["snapshot_run_id"] == "snapshot"
    assert seen == ["2026-09-30"]
