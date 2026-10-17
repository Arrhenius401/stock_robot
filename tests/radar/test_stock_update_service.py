"""手动线程、真实SQLite任务及重启恢复回归。"""
import threading
import time
from datetime import date
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.stock_radar import create_stock_router
from radar.stocks.evaluation_store import StockEvaluationStore
from radar.stocks.membership_store import StockMembershipStore
from radar.stocks.pools import StockPoolRepository
from radar.stocks.research_store import StockResearchStore
from radar.stocks.update_runner import StockUpdateRunner
from radar.stocks.update_runs import StockUpdateStore
from radar.stocks.update_service import StockUpdateService

POOL_DIR = Path(__file__).parents[2] / 'config/radar_stock_pools'


def task_store(state):
    research = StockResearchStore(state / 'radar.db')
    return StockUpdateStore(StockMembershipStore(StockEvaluationStore(research), StockPoolRepository(POOL_DIR)))


def packet(as_of):
    return {'as_of': as_of.isoformat(), 'status': 'partial', 'scope_complete': False,
            'pools': {'dividend_value': {'complete': False}, 'growth_trend': {'complete': False}},
            'items': [{'index_code': '000510', 'snapshot_date': '2026-10-15', 'effective_date': None, 'complete': True}],
            'companies': [{'symbol': '600000', 'name': '浦发银行', 'pool_ids': ['dividend_value'],
                           'source_indexes': ['000510'], 'source_records': []}]}


def wait_terminal(tasks, run_id):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = tasks.get(run_id)
        if result['status'] in ('partial', 'completed', 'failed'):
            return result
        time.sleep(0.02)
    raise AssertionError('手动任务未收敛')


def test_manual_thread_discovers_without_membership(tmp_path):
    tasks = task_store(tmp_path)
    run = tasks.enqueue(kind='discover_candidates', pool_ids=['dividend_value', 'growth_trend'], target_date=date(2026, 10, 15), rule_version='test')
    runner = StockUpdateRunner(tasks, tmp_path / 'stock-updates/cache', tmp_path / 'stock-updates/artifacts',
                               roster_collector=lambda _cache, as_of: packet(as_of))
    service = StockUpdateService(runner)
    try:
        service.wake()
        result = wait_terminal(tasks, run['run_id'])
        assert result['status'] == 'partial'
        assert result['automatic_enabled'] is False
        assert tasks.research.list_companies()['total'] == 1
        assert tasks.memberships.list_members('dividend_value')['total'] == 0
    finally:
        service.stop()
    assert service._thread is not None and not service._thread.is_alive()


def test_lifespan_only_resumes_existing_work(tmp_path):
    state = tmp_path / 'state'
    calls = []

    class FakeService:
        def __init__(self, runner):
            calls.append('created')

        def wake(self):
            calls.append('wake')

        def stop(self):
            calls.append('stop')

    def app():
        app = FastAPI()
        app.include_router(create_stock_router(state_dir=state, service_factory=FakeService))
        return app

    with TestClient(app()):
        assert calls == []
    assert not state.exists()
    tasks = task_store(state)
    tasks.enqueue(kind='discover_candidates', pool_ids=['dividend_value'], target_date=date(2026, 10, 15), rule_version='test')
    with TestClient(app()):
        assert calls == ['created', 'wake']
    assert calls[-1] == 'stop'


def test_stop_during_adapter_does_not_claim_next(tmp_path):
    tasks = task_store(tmp_path)
    first = tasks.enqueue(kind='discover_candidates', pool_ids=['dividend_value'], target_date=date(2026, 10, 14), rule_version='test')
    second = tasks.enqueue(kind='discover_candidates', pool_ids=['dividend_value'], target_date=date(2026, 10, 15), rule_version='test')
    entered, release = threading.Event(), threading.Event()

    def collector(_cache, as_of):
        entered.set()
        assert release.wait(5)
        return packet(as_of)

    service = StockUpdateService(StockUpdateRunner(tasks, tmp_path / 'cache', tmp_path / 'artifacts', roster_collector=collector))
    service.wake()
    assert entered.wait(5)
    service.stop(timeout=0)
    assert tasks.get(first['run_id'])['status'] == 'running'
    release.set()
    assert service._thread is not None
    service._thread.join(timeout=5)
    assert not service._thread.is_alive()
    assert tasks.get(second['run_id'])['status'] == 'queued'
