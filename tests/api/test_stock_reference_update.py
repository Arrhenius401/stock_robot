"""基础资料API与命令行只按已保存候选冻结，不读取全市场。"""
import json
from datetime import date
from pathlib import Path

from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.stock_radar import create_stock_router
from radar.stocks.update_runner import StockUpdateRunner
from stock_robot.stock_updates import reference_update, run_one_update
from tests.radar.test_stock_financial_source import DAY, environment
from tests.radar.test_stock_reference_task import scope_document


def seed_scope(tmp_path):
    research, _, tasks, _ = environment(tmp_path)
    tasks.enqueue(kind='discover_candidates', pool_ids=['growth_trend', 'dividend_value'], target_date=date(2026, 10, 16), rule_version='unconfigured')
    result = StockUpdateRunner(tasks, tmp_path / 'stock-updates/cache', tmp_path / 'stock-updates/artifacts',
                              roster_collector=lambda *a, **kw: scope_document()).execute_one('scope')
    assert result is not None and result['status'] == 'completed'
    return research, tasks


def test_reference_api_freezes_two_pools_without_exposing_raw_scope(tmp_path):
    _, tasks = seed_scope(tmp_path)
    wakes = []
    class FakeService:
        def __init__(self, runner):
            pass
        def wake(self):
            wakes.append(1)
        def stop(self):
            pass
    app = FastAPI()
    app.include_router(create_stock_router(state_dir=tmp_path, service_factory=FakeService), prefix='/api/v1/radar')
    with TestClient(app) as web:
        candidate = web.get('/api/v1/radar/stocks/candidates?pool_id=growth_trend').json()['items'][0]
        assert candidate['industry_level1'] == '公用事业' and candidate['industry_level2'] == '电力'
        assert candidate['admission_status'] == 'pending_verification'
        response = web.post('/api/v1/radar/stocks/reference-review', json={'pool_ids': ['growth_trend', 'dividend_value'], 'target_date': DAY.isoformat()})
        assert response.status_code == 202, response.text
        run = response.json()
        assert run['kind'] == 'reference_review' and len(wakes) == 1
        assert 'scope_document' not in run['input_snapshot'] and 'lease_token' not in run
        frozen = tasks.get(run['run_id'])['input_snapshot']
        assert len(frozen['companies']) == 1
        assert web.get('/api/v1/radar/stocks/updates/latest?kind=reference_review').json()['run_id'] == run['run_id']


def test_reference_cli_uses_saved_scope_and_exposes_command(tmp_path):
    research, tasks = seed_scope(tmp_path)
    output = tmp_path / 'result.json'
    result = CliRunner().invoke(reference_update, ['--db', str(research.db_path), '--pool', 'growth_trend',
        '--as-of', DAY.isoformat(), '--pool-dir', str(Path('config/radar_stock_pools').resolve()), '--output', str(output)])
    assert result.exit_code == 0, result.output
    data = json.loads(output.read_bytes())
    assert data['kind'] == 'reference_review'
    assert tasks.get(data['run_id'])['input_snapshot']['companies'][0]['symbol'] == '600900'


def test_cli_execution_defaults_to_local_calendar_when_no_window_file(tmp_path, monkeypatch):
    from radar.stocks.reference_collect import ReferenceUnavailable
    from radar.stocks.reference_task import REFERENCE_VERSION
    from tests.radar.test_stock_reference_task import snapshot
    research, tasks = seed_scope(tmp_path)
    frozen = snapshot()
    tasks.enqueue(kind='reference_review', pool_ids=frozen['pool_ids'], target_date=DAY, rule_version=REFERENCE_VERSION, snapshot=frozen)
    trace, seen = {'days': ['明确交易日'] * 60}, []
    monkeypatch.setattr('radar.stocks.update_service.load_market_window', lambda *args: ([], trace))
    class FakeCollector:
        def __init__(self, *args):
            pass
        def collect(self, channel, symbol, *, window):
            seen.append(window)
            raise ReferenceUnavailable('仅验证日历传递，不采集')
    monkeypatch.setattr('radar.stocks.reference_collect.ReferenceCollector', FakeCollector)
    result = CliRunner().invoke(run_one_update, ['--db', str(research.db_path), '--pool-dir', str(Path('config/radar_stock_pools').resolve())])
    assert result.exit_code == 0, result.output
    assert seen == [trace, trace]
