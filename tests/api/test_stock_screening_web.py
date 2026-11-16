"""网页筛选的完整快照、范围和计划绑定契约。"""
import threading
import time
from datetime import date

import pytest

from radar.stocks.research_store import StockRevisionConflict
from radar.stocks.screening_web import ScreeningService, _digest, project
from radar.stocks.screening_web_cash import completion_cash_verdict


@pytest.mark.parametrize('interest,expected', [(2, 'passed'), (None, 'pending_verification'), (.5, 'failed')])
def test_archive_cash_completion_reuses_original_independent_interest(interest, expected):
    cutoff, symbol = date(2026, 11, 7), '603087'
    source = {'status': 'current', 'symbol': symbol, 'as_of': cutoff.isoformat(),
              'evaluation_as_of': cutoff.isoformat(), 'financial': {'source_company_type': '4'}}
    main = {'SECURITY_CODE': symbol, 'CURRENCY': 'CNY', 'REPORT_DATE': '2025-12-31',
            'NOTICE_DATE': '2026-04-01', 'UPDATE_DATE': '2026-04-01'}
    if interest is not None:
        main['INTEREST_COVERAGE_RATIO'] = interest
    collection = {'records': {'cashflow': [], 'main': [main], 'profit': []}}
    evidence = {'symbol': symbol, 'as_of': cutoff.isoformat(), 'years': {
        str(y): {'fields': {'netcash_operate': 100, 'construct_long_asset': 10,
                            'assign_dividend_profit': 20, 'end_cash': 100},
                 'source': {'symbol': symbol, 'fiscal_year': y, 'disclosed_on': f'{y+1}-04-01',
                            'currency': 'CNY', 'unit': 'CNY_yuan', 'source_url': 'https://example.org/annual.pdf',
                            'content_sha256': 'a'*64}} for y in (2023, 2024, 2025)}}
    verdict = completion_cash_verdict(source, collection, evidence, cutoff, symbol, 'ordinary')
    assert verdict['outcome'] == expected
    # 补全不修改原已验收来源。
    assert collection['records']['cashflow'] == []
    evidence['years']['2025']['source'].pop('disclosed_on')
    assert completion_cash_verdict(source, collection, evidence, cutoff, symbol, 'ordinary')['outcome'] == 'pending_verification'


@pytest.mark.parametrize('invalid', [{'CURRENCY': 'USD'}, {'SECURITY_CODE': '000001'},
                                   {'NOTICE_DATE': '2027-04-01'}, {'UPDATE_DATE': '2027-04-01'}])
def test_archive_completion_cannot_relabel_invalid_old_cash_values(invalid):
    cutoff, symbol = date(2026, 11, 7), '603087'
    source = {'status': 'current', 'symbol': symbol, 'as_of': cutoff.isoformat(),
              'evaluation_as_of': cutoff.isoformat(), 'financial': {'source_company_type': '4'}}
    main = {'SECURITY_CODE': symbol, 'CURRENCY': 'CNY', 'REPORT_DATE': '2025-12-31',
            'NOTICE_DATE': '2026-04-01', 'UPDATE_DATE': '2026-04-01', 'INTEREST_COVERAGE_RATIO': 2}
    old = {**main, 'NETCASH_OPERATE': 100, 'CONSTRUCT_LONG_ASSET': 10,
           'ASSIGN_DIVIDEND_PORFIT': 20, 'END_CASH': 100, **invalid}
    collection = {'records': {'cashflow': [old], 'main': [main], 'profit': []}}
    evidence = {'symbol': symbol, 'as_of': cutoff.isoformat(), 'years': {
        str(y): {'fields': {'netcash_operate': 100, 'construct_long_asset': 10,
                            'assign_dividend_profit': 20, 'end_cash': -1},
                 'source': {'symbol': symbol, 'fiscal_year': y, 'disclosed_on': f'{y+1}-04-01',
                            'currency': 'CNY', 'unit': 'CNY_yuan', 'source_url': 'https://example.org/annual.pdf',
                            'content_sha256': 'a'*64}} for y in (2023, 2024, 2025)}}
    # 无效旧正值不能盖过真实归档负值，旧规则应失败。
    assert completion_cash_verdict(source, collection, evidence, cutoff, symbol, 'ordinary')['outcome'] == 'failed'
    # 合法旧负余额也不能被补全的正余额覆盖。
    old.update(SECURITY_CODE=symbol, CURRENCY='CNY', NOTICE_DATE='2026-04-01', UPDATE_DATE='2026-04-01')
    old.pop('END_CASH')
    old['END_CCE'] = -1
    for value in evidence['years'].values():
        value['fields']['end_cash'] = 100
    assert completion_cash_verdict(source, collection, evidence, cutoff, symbol, 'ordinary')['outcome'] == 'failed'


def report(outcome='passed'):
    return {'items': [{'symbol': '000001', 'name': '平安银行', 'evaluation_valid': True,
                       'common_status': 'passed', 'checks': [{'code': 'audit', 'outcome': 'passed'}],
                       'pools': {'dividend_value': {'checks': [{'code': 'cash_review', 'outcome': outcome}]}}}]}


def test_projection_never_promotes_empty_invalid_or_missing_data():
    assert project(report(), 'dividend_value')[0]['status'] == 'passed'
    assert project(report('pending_verification'), 'dividend_value')[0]['status'] == 'pending'
    assert project(report('failed'), 'dividend_value')[0]['status'] == 'failed'
    invalid = report()
    invalid['items'][0]['evaluation_valid'] = False
    assert project(invalid, 'dividend_value')[0]['status'] == 'pending'
    assert project(report(), 'growth_trend')[0]['status'] == 'pending'


def test_growth_cash_attention_remains_pending_while_liquidity_observe_is_not_failure():
    source = report('attention')
    pool = source['items'][0]['pools'].pop('dividend_value')
    pool['approved_conditions_status'] = 'pending_verification'
    source['items'][0]['pools']['growth_trend'] = pool
    assert project(source, 'growth_trend')[0]['status'] == 'pending'
    pool['checks'][0]['outcome'] = 'passed'
    pool['checks'].append({'code': 'liquidity', 'outcome': 'observe'})
    assert project(source, 'growth_trend')[0]['status'] == 'passed'
    source = report()
    source['items'][0]['pools']['dividend_value']['checks'].append({'code': 'liquidity', 'outcome': 'warning'})
    assert project(source, 'dividend_value')[0]['status'] == 'passed'


def test_read_only_and_stale_plan(tmp_path):
    service = ScreeningService(tmp_path, lambda *_: report(), lambda *_: None)
    assert service.listing('dividend_value', 'all', '', 1, 20)['counts']['screened'] == 0
    assert not tmp_path.joinpath('stock-screening.sqlite').exists()
    service._put('snapshots', 'dividend_value', {'as_of': '2026-11-07', 'report': report()})
    with pytest.raises(StockRevisionConflict):
        service.start('dividend_value', 'completion', plan_sha256='0'*64)


def test_common_pending_is_reported_even_without_automatic_adapter(tmp_path):
    service = ScreeningService(tmp_path, lambda *_: report(), lambda *_: None)
    pending = report()
    pending['items'][0]['checks'][0]['outcome'] = 'pending_verification'
    service._put('snapshots', 'dividend_value', {'as_of': '2026-11-07', 'report': pending})
    plan = service.plan('dividend_value')
    assert plan['items'] == []
    assert plan['pending_total'] == 1
    assert plan['manual_count'] == 1
    assert plan['automatic_count'] == 0
    assert plan['blocked_items'][0]['symbol'] == '000001'


def test_screening_api_reads_empty_without_initializing_database(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.stock_radar import create_stock_router

    app = FastAPI()
    app.include_router(create_stock_router(state_dir=tmp_path), prefix='/api/v1/radar')
    with TestClient(app) as client:
        url = '/api/v1/radar/stocks/dividend_value'
        data = client.get(url + '/screening').json()
        assert data['items'] == [] and data['page_size'] == 20
        assert client.get(url + '/screening/rules').json()['items'][0]['code'] == 'listing_age'
        assert client.get(url + '/completion/plan').status_code == 422
    assert not tmp_path.joinpath('stock-screening.sqlite').exists()


@pytest.mark.parametrize('pool', ['dividend_value', 'growth_trend'])
@pytest.mark.parametrize('condition', ['liquidity', 'cash_review'])
def test_completion_uses_pool_liquidity_semantics_and_requires_independent_interest(tmp_path, monkeypatch, pool, condition):
    service = ScreeningService(tmp_path, lambda *_: None, lambda *_: None)
    source = report('pending_verification')
    checks = source['items'][0]['pools'].pop('dividend_value')
    checks['checks'][0]['code'] = condition
    source['items'][0]['pools'][pool] = checks
    snapshot = {'as_of': '2026-11-07', 'report': source}
    service._put('snapshots', pool, snapshot)
    plan = {'as_of': '2026-11-07', 'snapshot_sha256': _digest(snapshot)}
    evidence = ({'daily': [{'amount_yuan': 100} for _ in range(60)]} if condition == 'liquidity' else
                {'years': {str(y): {'fields': {'netcash_operate': 100, 'assign_dividend_profit': 10,
                                             'end_cash': 100}} for y in (2023, 2024, 2025)}})
    monkeypatch.setattr('radar.stocks.screening_web.apply_plan', lambda *_a, **_k: {
        'items': [{'symbol': '000001', 'condition': condition, 'status': 'complete', 'evidence': evidence,
                   'source_kind': 'market_turnover_60d' if condition == 'liquidity' else 'annual_report_cashflow'}]})
    service._run({'run_id': 'test-run', 'pool_id': pool, 'kind': 'completion', 'as_of': '2026-11-07',
                  'progress': {'total': 1}, 'results': []}, None, plan)
    result = service.snapshot(pool)
    assert result is not None
    assert project(result['report'], pool)[0]['status'] == ('passed' if condition == 'liquidity' else 'pending')


def test_running_job_keeps_previous_complete_results_and_is_deduplicated(tmp_path):
    gate = threading.Event()
    def collect(*_args):
        gate.wait(timeout=5)
        raise OSError('来源不可用')
    service = ScreeningService(tmp_path, lambda *_: report('failed'), collect)
    service._put('snapshots', 'dividend_value', {'as_of': '2026-11-07', 'report': report()})
    task = service.start('dividend_value', 'screening', scope={'companies': [{'symbol': '000001'}]})
    assert service.start('dividend_value', 'screening')['run_id'] == task['run_id']
    other = ScreeningService(tmp_path, lambda *_: None, lambda *_: None)
    other.recover()
    latest = other.latest('dividend_value')
    assert latest is not None
    assert latest['status'] in {'queued', 'running'}
    assert service.listing('dividend_value', 'passed', '', 1, 20)['total'] == 1
    gate.set()
    for _ in range(100):
        saved = service.task(task['run_id'])
        assert saved is not None
        if saved['status'] == 'failed':
            break
        time.sleep(.01)
    saved = service.task(task['run_id'])
    assert saved is not None
    assert saved['status'] == 'failed'
    assert service.listing('dividend_value', 'passed', '', 1, 20)['total'] == 1
