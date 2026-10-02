"""独立协调器的重复触发、关闭和共享执行锁。"""

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from filelock import FileLock

from radar.collector_store import CollectorStore
from radar.collector_worker import CollectorWorker
from radar.universe import UniverseRepository


class Calendar:
    def latest_completed(self, now):
        return date(2026, 9, 29)

    def trading_days(self, start, end):
        return (date(2026, 9, 29),)


def worker(tmp_path, refresh):
    repository = UniverseRepository(Path(__file__).parents[2] / 'config/radar_universes')
    store = CollectorStore(tmp_path / 'collector.db')
    return CollectorWorker(store, repository, Calendar(), refresh, state_dir=tmp_path)


def test_manual_runs_while_automatic_disabled_and_is_idempotent(tmp_path):
    calls = []

    def refresh(pool, **kwargs):
        calls.append(pool)
        for symbol in kwargs['retry_symbols']:
            kwargs['on_item'](symbol, True, None)
        kwargs['on_phase']('scoring')
        return 'snapshot'

    service = worker(tmp_path, refresh)
    now = datetime.now(ZoneInfo('Asia/Shanghai')) + timedelta(seconds=2)
    runs = service.enqueue_manual(now)
    assert service.enqueue_manual(now)[0]['id'] == runs[0]['id']
    claim_time = max(datetime.fromisoformat(run['available_at']) for run in runs) + timedelta(seconds=1)
    while service.execute_one(claim_time):
        pass
    assert len(calls) == len(runs)
    assert all(item['status'] == 'completed' for item in service.store.list_runs())
    assert service.store.settings()['enabled'] is False


def test_execution_lock_prevents_claiming_while_other_executor_runs(tmp_path):
    service = worker(tmp_path, lambda *args, **kwargs: 'snapshot')
    now = datetime.now(ZoneInfo('Asia/Shanghai')) + timedelta(seconds=2)
    service.enqueue_manual(now)
    with FileLock(tmp_path / 'radar_collector.execute.lock'):
        assert service.execute_one(now) is False
    assert all(run['status'] == 'queued' for run in service.store.list_runs())


def test_disabled_automatic_does_not_schedule_but_manual_queue_survives(tmp_path):
    service = worker(tmp_path, lambda *args, **kwargs: 'snapshot')
    now = datetime.now(ZoneInfo('Asia/Shanghai')) + timedelta(seconds=2)
    assert service.coordinate(now, recovery=True) == []
    assert service.enqueue_manual(now)


def test_failed_request_is_bounded_and_retries_only_failed_items(tmp_path):
    requested = []

    def refresh(pool, **kwargs):
        requested.append(set(kwargs['retry_symbols']))
        for index, symbol in enumerate(sorted(kwargs['retry_symbols'])):
            kwargs['on_item'](symbol, index != 0, '超时' if index == 0 else None)
        return 'partial-snapshot'

    service = worker(tmp_path, refresh)
    now = datetime.now(ZoneInfo('Asia/Shanghai')) + timedelta(seconds=2)
    run = service.enqueue_manual(now)[0]
    service.execute_one(now)
    result = service.store.get_run(run['id'])
    assert result['status'] == 'retry_wait'
    assert len([item for item in result['items'] if item['status'] == 'completed']) > 0


def test_manual_revives_cancelled_auto_task(tmp_path):
    service = worker(tmp_path, lambda *args, **kwargs: 'snapshot')
    now = datetime.now(ZoneInfo('Asia/Shanghai')) + timedelta(seconds=2)
    service.store.update_settings(enabled=True, hour=18, minute=30, revision=0)
    automatic = service.coordinate(now, recovery=True)[0]
    service.store.update_settings(enabled=False, hour=18, minute=30, revision=1)
    manual = service.enqueue_manual(now)[0]
    assert manual['id'] == automatic['id']
    assert manual['source'] == 'manual'
    assert manual['status'] == 'queued'


def test_broken_pool_config_finishes_task_without_retry(tmp_path, monkeypatch):
    from radar.universe import UniverseConfigError

    service = worker(tmp_path, lambda *args, **kwargs: 'snapshot')
    now = datetime.now(ZoneInfo('Asia/Shanghai')) + timedelta(seconds=2)
    run = service.enqueue_manual(now)[0]

    def invalid(target):
        raise UniverseConfigError('池配置已损坏')

    monkeypatch.setattr(service.repository, 'enabled_on', invalid)
    assert service.execute_one(now)
    result = service.store.get_run(run['id'])
    assert result is not None
    assert result['status'] == 'failed'
    assert '损坏' in result['error_summary']


def test_invalid_completed_cache_enters_failed_retry_items(tmp_path):
    attempts = []

    def refresh(pool, **kwargs):
        attempts.append(set(kwargs['retry_symbols']))
        for symbol in kwargs['retry_symbols']:
            kwargs['on_item'](symbol, True, None)
        if len(attempts) == 2:
            kwargs['on_item'](min(attempts[0]), False, '缓存已失效')
        return 'snapshot'

    service = worker(tmp_path, refresh)
    now = datetime.now(ZoneInfo('Asia/Shanghai')) + timedelta(seconds=2)
    pool = service.repository.enabled_on(date(2026, 9, 29))[0].id
    run = service.enqueue_manual(now, universe_id=pool)[0]
    now = datetime.fromisoformat(run['available_at']) + timedelta(seconds=1)
    assert service.execute_one(now)
    service.store.finish(run['id'], 'partial')
    retried = service.store.retry(run['id'], manual=True)
    now = datetime.fromisoformat(retried['available_at']) + timedelta(seconds=1)
    assert service.execute_one(now)
    result = service.store.get_run(run['id'])
    assert result['status'] == 'retry_wait'
    pending = [entry for entry in result['items'] if entry['status'] == 'pending']
    assert len(pending) == 1
    assert service.execute_one(now + timedelta(minutes=10))
    assert attempts[2] == {pending[0]['symbol']}


def test_startup_hook_runs_only_for_single_instance(tmp_path):
    service = worker(tmp_path, lambda *args, **kwargs: 'snapshot')
    calls = []

    def startup():
        calls.append('started')
        service.stop()

    service.serve(startup_hook=startup)
    assert calls == ['started']


def test_all_pool_fingerprints_validated_before_enqueue(tmp_path, monkeypatch):
    service = worker(tmp_path, lambda *args, **kwargs: 'snapshot')
    calls = []

    def fingerprint(pool, target):
        calls.append(pool.id)
        if len(calls) == 2:
            raise ValueError('第二个池评分配置损坏')
        return 'valid'

    monkeypatch.setattr(service, '_fingerprint', fingerprint)
    import pytest

    with pytest.raises(ValueError, match='评分配置损坏'):
        service.enqueue_manual(datetime.now(ZoneInfo('Asia/Shanghai')))
    assert service.store.list_runs() == []
