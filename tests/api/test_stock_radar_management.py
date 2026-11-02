"""股票管理只读边界与审核接口回归。"""
from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.stock_radar import create_stock_router
from radar.stocks.research_store import StockResearchStore


def client(state):
    app = FastAPI()
    app.include_router(create_stock_router(state_dir=state), prefix='/api/v1/radar')
    return TestClient(app)


def seed(state):
    store = StockResearchStore(state / 'radar.db')
    store.import_companies([{'symbol': '600000', 'name': '浦发银行'}], source_sha256='a' * 64,
                           source_label='隔离测试', data_as_of=date(2026, 10, 15))
    return store


def test_empty_gets_create_nothing(tmp_path):
    state = tmp_path / 'missing'
    with client(state) as web:
        for path in ('candidates', 'drafts', 'memberships/events', 'updates', 'updates/latest', 'dividend_value/members'):
            assert web.get('/api/v1/radar/stocks/' + path).status_code == 200
        assert web.get('/api/v1/radar/stocks/companies/600000').status_code == 404
    assert not state.exists()


def test_detail_and_draft_share_historical_financial_source(tmp_path):
    from tests.radar.test_stock_financial_source import (
        SYMBOL,
        complete,
        enqueue,
        environment,
    )

    research, archive, tasks, _ = environment(tmp_path)
    enqueue(tasks)
    complete(tasks, tmp_path)
    company = research.get_company(SYMBOL)
    assert company is not None
    revision = company['revision']
    before = research.db_path.read_bytes()
    with client(tmp_path) as web:
        base = '/api/v1/radar/stocks/companies/' + SYMBOL
        detail = web.get(base).json()['evaluation']['items'][0]
        assert research.db_path.read_bytes() == before
        source = detail['financial_source']
        assert source['as_of'] == '2026-10-18' and source['scope_as_of'] == '2026-10-18'
        assert source['status'] == 'historical' and detail['evaluation_valid']
        historical = [check for check in detail['checks'] if check.get('channel') == 'structured_financial']
        assert len(historical) == 8 and all(check['outcome'] == 'pending_verification' for check in historical)
        assert all(check['reference_as_of'] == '2026-10-18' and check['reference_check']['outcome'] == 'passed' for check in historical)
        body = {'expected_revision': revision, 'request_id': 'financial-source', 'actor': '合成测试',
                'reason': '保留历史财务参考', 'pool_id': 'growth_trend'}
        response = web.post(base + '/draft', json=body)
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'blocked'
        evaluation = archive.list_evaluations(symbol=SYMBOL)['items'][0]
        saved = archive.get(evaluation['evaluation_id'])
        assert saved is not None
        assert saved['inputs']['companies'][0]['financial_source'] == source
        assert saved['report']['items'][0]['checks'] == detail['checks']
        assert saved['report']['items'][0]['financial_source'] == source
        # 来源更新后原请求仍重放原草案，不重算也不发布成员。
        enqueue(tasks, pool='growth_trend')
        replay = web.post(base + '/draft', json=body)
        assert replay.status_code == 200 and replay.json()['draft_id'] == response.json()['draft_id']
        stale = archive.get(evaluation['evaluation_id'])
        assert stale is not None and not stale['freshness']['current']
        assert not web.get('/api/v1/radar/stocks/growth_trend/members').json()['items']


def test_current_detail_uses_recomputed_eight_conditions(tmp_path, monkeypatch):
    from datetime import UTC, datetime

    import api.stock_radar as module
    from tests.radar.test_stock_financial_source import (
        SYMBOL,
        complete,
        enqueue,
        environment,
    )

    _, _, tasks, _ = environment(tmp_path)
    enqueue(tasks)
    complete(tasks, tmp_path)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 18, 8, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(module, 'datetime', FixedDatetime)
    with client(tmp_path) as web:
        response = web.get('/api/v1/radar/stocks/companies/' + SYMBOL)
        assert response.status_code == 200, response.text
        item = response.json()['evaluation']['items'][0]
        assert item['financial_source']['status'] == 'current'
        numeric = [check for check in item['checks'] if check.get('channel') == 'structured_financial']
        assert len(numeric) == 8 and all(check['outcome'] == 'passed' for check in numeric)
        assert item['common_status'] == 'pending_verification' and item['formal_screening_enabled'] is False


def test_detail_and_draft_bind_same_historical_market_source(tmp_path, monkeypatch):
    import api.stock_radar as routes
    import radar.stocks.evaluation_store as evaluations

    store = seed(tmp_path)
    company = store.get_company('600000')
    assert company is not None
    revision = company['revision']
    def loader(_state, symbol, cutoff, **_kwargs):
        return {'symbol': symbol, 'evaluation_as_of': cutoff.isoformat(), 'as_of': '2026-10-18',
                'status': 'historical', 'manifest_sha256': 'c' * 64, 'checks': [
                    {'code': code, 'outcome': 'passed'} for code in
                    ('capitalization_current', 'capitalization_average_60d')]}
    monkeypatch.setattr(routes, 'load_market_value_source', loader)
    monkeypatch.setattr(evaluations, 'load_market_value_source', loader)
    with client(tmp_path) as web:
        base = '/api/v1/radar/stocks/companies/600000'
        item = web.get(base).json()['evaluation']['items'][0]
        checks = [row for row in item['checks'] if row.get('channel') == 'supplier_market_value']
        assert len(checks) == 2 and all(row['outcome'] == 'pending_verification' for row in checks)
        assert all(row['reference_as_of'] == '2026-10-18' for row in checks)
        response = web.post(base + '/draft', json={'expected_revision': revision, 'request_id': 'market-value',
                            'actor': '测试', 'reason': '历史市值参考', 'pool_id': 'growth_trend'})
        assert response.status_code == 200, response.text
        archive = evaluations.StockEvaluationStore(store)
        record = archive.list_evaluations(symbol='600000')['items'][0]
        saved = archive.get(record['evaluation_id'])
        assert saved is not None
        assert saved['inputs']['companies'][0]['market_value_source'] == item['market_value_source']
        assert saved['report']['items'][0]['market_value_source'] == item['market_value_source']


def test_current_market_source_rejects_changed_calendar():
    import pytest

    from api.stock_radar import _check_market_window
    from radar.stocks.research_store import StockRevisionConflict

    window = [date(2026, 10, 16)]
    source = {'status': 'current', 'window_days': ['2026-10-16'], 'calendar_sha256': 'a' * 64}
    _check_market_window(source, window, {'calendar': {'snapshot_sha256': 'a' * 64}})
    with pytest.raises(StockRevisionConflict, match='市场日历'):
        _check_market_window(source, window, {'calendar': {'snapshot_sha256': 'b' * 64}})
    with pytest.raises(StockRevisionConflict, match='市场日历'):
        _check_market_window(source, None, {})


def test_candidates_details_and_review_conflict(tmp_path):
    state = tmp_path / 'state'
    store = seed(state)
    evidence = store.list_evidence('600000')[0]
    with client(state) as web:
        base = '/api/v1/radar/stocks'
        response = web.get(base + '/candidates?search=浦发').json()
        assert response['total'] == 0
        assert response['scope_status'] == 'not_prepared'
        assert response['formal_screening_enabled'] is False
        detail = web.get(base + '/companies/600000').json()
        assert detail['publication_enabled'] is False
        body = {'evidence_id': evidence['evidence_id'], 'expected_revision': 1, 'decision': 'confirmed', 'reviewer': '测试', 'reason': '核对测试资料'}
        assert web.post(base + '/companies/600000/reviews', json=body).status_code == 422
        body['decision'] = 'rejected'
        assert web.post(base + '/companies/600000/reviews', json=body).json()['revision'] == 2
        assert web.post(base + '/companies/600000/reviews', json=body).status_code == 409
        body['expected_revision'] = True
        assert web.post(base + '/companies/600000/reviews', json=body).status_code == 422


def test_draft_current_revision_and_no_external_evaluation(tmp_path):
    state = tmp_path / 'state'
    seed(state)
    with client(state) as web:
        base = '/api/v1/radar/stocks/companies/600000/draft'
        body = {'expected_revision': 0, 'request_id': 'one', 'actor': '测试', 'reason': '保存关注依据', 'pool_id': 'dividend_value'}
        assert web.post(base, json=body).status_code == 409
        body['expected_revision'] = 1
        result = web.post(base, json=body)
        assert result.status_code == 200, result.text
        assert result.json()['status'] == 'blocked'
        assert result.json()['publication_enabled'] is False
        body['passed'] = True
        assert web.post(base, json=body).status_code == 422


def test_unknown_pool_and_empty_refresh(tmp_path):
    with client(tmp_path / 'state') as web:
        base = '/api/v1/radar/stocks/'
        assert web.post(base + 'not-a-pool/update', json={}).status_code == 404
        assert web.post(base + 'dividend_value/update', json={}).status_code == 422


def test_draft_retry_replays_saved_result_without_time_dependent_evaluation(tmp_path, monkeypatch):
    import api.stock_radar as module

    store = seed(tmp_path)
    calls = []

    def window(*args):
        calls.append(1)
        return None, {'effective_at': f'2026-10-17T13:00:{len(calls):02d}+08:00'}

    monkeypatch.setattr(module, 'load_market_window', window)
    body = {'expected_revision': 1, 'request_id': 'lost-response', 'actor': '测试', 'reason': '核对后关注', 'pool_id': 'dividend_value'}
    with client(tmp_path) as web:
        path = '/api/v1/radar/stocks/companies/600000/draft'
        first = web.post(path, json=body)
        assert first.status_code == 200, first.text
        again = web.post(path, json=body)
        assert again.status_code == 200, again.text
        assert again.json()['draft_id'] == first.json()['draft_id']
        assert len(calls) == 1
        for field, value in [('reason', '不同理由'), ('expected_revision', 2), ('actor', '其他人')]:
            assert web.post(path, json={**body, field: value}).status_code == 409
    with store._connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM stocks_evaluations').fetchone()[0] == 1
        assert connection.execute('SELECT COUNT(*) FROM stocks_member_drafts').fetchone()[0] == 1



def test_get_company_never_creates_calendar_or_task_tables(tmp_path, monkeypatch):
    import sqlite3

    state = tmp_path / 'state'
    seed(state)
    before = sorted(path.name for path in state.iterdir())
    monkeypatch.setattr('radar.calendar.fetch_akshare_calendar', lambda: (_ for _ in ()).throw(AssertionError('禁止联网')))
    with client(state) as web:
        assert web.get('/api/v1/radar/stocks/companies/600000').status_code == 200
    assert sorted(path.name for path in state.iterdir() if path.suffix == '.db') == before
    with sqlite3.connect(state / 'radar.db') as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name IN ('stocks_update_runs','stocks_evaluations')").fetchall() == []


def test_confirm_original_evidence_and_reject_blank_reason(tmp_path):
    store = seed(tmp_path)
    fact = store.add_evidence('600000', kind='admission_identity', payload={'symbol': '600000'},
                              source_url='https://www.sse.com.cn/company/600000', disclosed_on=date(2026, 10, 15),
                              content_sha256='b' * 64, expected_revision=1)
    with client(tmp_path) as web:
        body = {'evidence_id': fact['evidence_id'], 'expected_revision': 2, 'decision': 'confirmed',
                'reviewer': '隔离测试审核人', 'reason': '已核对测试原文身份字段'}
        assert web.post('/api/v1/radar/stocks/companies/600000/reviews', json=body).json()['revision'] == 3
        body['reason'] = '   '
        assert web.post('/api/v1/radar/stocks/companies/600000/reviews', json=body).status_code == 422


def test_scoped_candidates_exclude_base_roster_and_preserve_source_dates(tmp_path):
    from radar.stocks.update_runner import StockUpdateRunner
    from tests.radar.test_stock_update_service import packet, task_store

    tasks = task_store(tmp_path)
    tasks.research.import_companies([{'symbol': '000001', 'name': '全市场底表公司'}], source_sha256='c' * 64,
                                    source_label='全市场底表', data_as_of=date(2026, 10, 15))
    run = tasks.enqueue(kind='discover_candidates', pool_ids=['dividend_value', 'growth_trend'],
                         target_date=date(2026, 10, 15), rule_version='test')
    runner = StockUpdateRunner(tasks, tmp_path / 'stock-updates/cache', tmp_path / 'stock-updates/artifacts',
                               roster_collector=lambda _cache, as_of: packet(as_of))
    runner.execute_one('isolated-worker')
    with client(tmp_path) as web:
        base = '/api/v1/radar/stocks/'
        dividend = web.get(base + 'candidates?pool_id=dividend_value').json()
        assert [item['symbol'] for item in dividend['items']] == ['600000']
        assert dividend['scope_status'] == 'partial'
        assert dividend['source_snapshots'][0]['snapshot_date'] == '2026-10-15'
        assert web.get(base + 'candidates?pool_id=growth_trend').json()['total'] == 0
        task = web.get(base + 'updates/' + run['run_id']).json()
        assert 'path' not in task['results'][0]['artifact']
        latest = tasks.enqueue(kind='discover_candidates', pool_ids=['dividend_value'], target_date=date(2026, 10, 15), rule_version='new')
        claimed = tasks.claim('failure-test')
        assert claimed is not None
        tasks.finish(latest['run_id'], 'failure-test', lease_token=claimed['lease_token'], status='failed', error='测试来源失败')
        failed = web.get(base + 'candidates?pool_id=dividend_value').json()
        assert failed['total'] == 0
        assert failed['scope_status'] == 'failed'
        assert failed['scope_error'] == '测试来源失败'


def test_discover_scopes_all_enabled_pools_and_no_client_rule(tmp_path):
    calls = []

    class FakeService:
        def __init__(self, runner):
            calls.append(runner)

        def wake(self):
            calls.append('wake')

        def stop(self):
            calls.append('stop')

    app = FastAPI()
    app.include_router(create_stock_router(state_dir=tmp_path, service_factory=FakeService))
    with TestClient(app) as web:
        response = web.post('/stocks/discover', json={'pool_id': 'dividend_value', 'as_of': '2026-10-15'})
        assert response.status_code == 202, response.text
        assert response.json()['pool_ids'] == ['dividend_value', 'growth_trend']
        assert response.json()['automatic_enabled'] is False
        assert calls[-1] == 'wake'
        assert web.post('/stocks/discover', json={'pool_id': 'dividend_value', 'rule_version': 'bypass'}).status_code == 422


def test_database_fault_503(tmp_path):
    (tmp_path / 'radar.db').write_text('not sqlite', encoding='utf-8')
    with client(tmp_path) as web:
        assert web.get('/api/v1/radar/stocks/candidates').status_code == 503




def test_scope_artifact_tamper_is_service_error(tmp_path):
    from radar.stocks.update_runner import StockUpdateRunner
    from tests.radar.test_stock_update_service import packet, task_store

    tasks = task_store(tmp_path)
    run = tasks.enqueue(kind='discover_candidates', pool_ids=['dividend_value'], target_date=date(2026, 10, 15), rule_version='test')
    StockUpdateRunner(tasks, tmp_path / 'stock-updates/cache', tmp_path / 'stock-updates/artifacts',
                      roster_collector=lambda _cache, as_of: packet(as_of)).execute_one('isolated-worker')
    artifact = tasks.get(run['run_id'])['results'][0]['artifact']
    from pathlib import Path
    Path(artifact['path']).write_bytes(b'corrupt')
    with client(tmp_path) as web:
        assert web.get('/api/v1/radar/stocks/candidates?pool_id=dividend_value').status_code == 503


def test_new_enabled_pool_without_scope_is_explicitly_unsupported(tmp_path):
    import shutil
    from pathlib import Path

    directory = tmp_path / 'pools'
    shutil.copytree(Path(__file__).parents[2] / 'config/radar_stock_pools', directory)
    content = (directory / 'growth_trend.yaml').read_text(encoding='utf-8')
    (directory / 'another_pool.yaml').write_text(content.replace('id: growth_trend', 'id: another_pool'), encoding='utf-8')
    app = FastAPI()
    app.include_router(create_stock_router(state_dir=tmp_path / 'state', pool_dir=directory))
    with TestClient(app) as web:
        response = web.get('/stocks/candidates?pool_id=another_pool')
        assert response.status_code == 200
        assert response.json()['scope_status'] == 'unsupported'
        assert response.json()['total'] == 0


def test_financial_empty_scope_is_read_only_and_cannot_start(tmp_path):
    state = tmp_path/'missing-financial'
    with client(state) as web:
        base = '/api/v1/radar/stocks/'
        scope = web.get(base+'dividend_value/financial-review/scope')
        assert scope.status_code == 200 and scope.json()['total'] == 0
        assert web.post(base+'dividend_value/financial-review', json={}).status_code == 422
        assert web.get(base+'updates/latest?kind=financial_review').json() is None
        assert web.post(base+'dividend_value/financial-review', json={'symbols': ['600000']}).status_code == 422
    assert not state.exists()


def test_financial_start_freezes_scope_and_kind_filtered_recovery(tmp_path):
    from radar.stocks.update_runner import StockUpdateRunner
    from tests.radar.test_stock_update_service import packet, task_store

    tasks = task_store(tmp_path)
    tasks.enqueue(kind='discover_candidates', pool_ids=['dividend_value'], target_date=date(2026, 10, 15), rule_version='test')
    def collect(cache, as_of):
        value = packet(as_of)
        value['pools']['dividend_value']['complete'] = True
        value['status'], value['scope_complete'] = 'complete', True
        return value
    StockUpdateRunner(tasks, tmp_path/'stock-updates/cache', tmp_path/'stock-updates/artifacts',
                      roster_collector=collect).execute_one('scope-worker')
    class IdleService:
        def __init__(self, runner):
            self.runner = runner
        def wake(self):
            return None
        def stop(self):
            return None
    app = FastAPI()
    app.include_router(create_stock_router(state_dir=tmp_path, service_factory=IdleService))
    with TestClient(app) as web:
        base = '/stocks/'
        scope = web.get(base+'dividend_value/financial-review/scope').json()
        assert scope['scope_complete'] is True and scope['total'] == 1
        started = web.post(base+'dividend_value/financial-review', json={})
        assert started.status_code == 202, started.text
        task = started.json()
        assert task['kind'] == 'financial_review' and task['progress']['not_attempted'] == 1
        latest = web.get(base+'updates/latest?pool_id=dividend_value&kind=financial_review').json()
        assert latest['run_id'] == task['run_id']
        assert web.get(base+'updates/latest?kind=refresh_members').json() is None
        results = web.get(base+'updates/'+task['run_id']+'/financial-results').json()
        assert results['items'][0]['financial_numeric_status'] == 'not_attempted'
        assert 'lease_token' not in task and 'lease_until' not in task
        assert 'companies' not in task['input_snapshot']
        assert tasks.get(task['run_id'])['input_snapshot']['scope_sha256'] == scope['scope_sha256']
        dividend = web.post(base+'dividend_value/dividend-review', json={})
        assert dividend.status_code == 202, dividend.text
        dividend_task = dividend.json()
        assert dividend_task['kind'] == 'dividend_review'
        assert 'companies' not in dividend_task['input_snapshot']
        latest_dividend = web.get(base+'updates/latest?pool_id=dividend_value&kind=dividend_review').json()
        assert latest_dividend['run_id'] == dividend_task['run_id']
        assert tasks.get(dividend_task['run_id'])['input_snapshot']['scope_sha256'] == scope['scope_sha256']
        turnover = web.post(base+'dividend_value/turnover-review', json={})
        assert turnover.status_code == 202, turnover.text
        turnover_task = turnover.json()
        assert turnover_task['kind'] == 'turnover_review' and 'companies' not in turnover_task['input_snapshot']
        latest_turnover = web.get(base+'updates/latest?pool_id=dividend_value&kind=turnover_review').json()
        assert latest_turnover['run_id'] == turnover_task['run_id']
        assert tasks.get(turnover_task['run_id'])['input_snapshot']['scope_sha256'] == scope['scope_sha256']


def test_detail_and_draft_bind_dividend_source_even_when_unavailable(tmp_path):
    state = tmp_path / 'state'
    store = seed(state)
    with client(state) as web:
        base = '/api/v1/radar/stocks/companies/600000'
        detail = web.get(base)
        assert detail.status_code == 200
        item = detail.json()['evaluation']['items'][0]
        assert item['dividend_source']['status'] == 'unavailable'
        company = store.get_company('600000')
        assert company is not None
        draft = web.post(base + '/draft', json={'expected_revision': company['revision'],
            'request_id': 'dividend-source-binding', 'actor': '测试', 'reason': '测试分红来源', 'pool_id': 'dividend_value'})
        assert draft.status_code == 200, draft.text
        assert draft.json()['status'] == 'blocked'


def test_manual_review_packet_is_on_demand_and_read_only(tmp_path):
    state = tmp_path/'state'
    store = seed(state)
    before = (store.get_company('600000'), store.list_evidence('600000'), store.review_history('600000'))
    with client(state) as web:
        result = web.get('/api/v1/radar/stocks/companies/600000/manual-review-packet')
        assert result.status_code == 200, result.text
        packet = result.json()
        assert packet['symbol'] == '600000' and len(packet['checks']) == 6
        assert packet['formal_screening_enabled'] is False and packet['members_published'] == 0
        assert web.get('/api/v1/radar/stocks/companies/600001/manual-review-packet').status_code == 404
    after = (store.get_company('600000'), store.list_evidence('600000'), store.review_history('600000'))
    assert after == before
