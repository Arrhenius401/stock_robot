"""已保存网页财务来源的只读接线与归档绑定。"""
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from radar.stocks.evaluation_store import StockEvaluationStore
from radar.stocks.financial_source import load_financial_source
from radar.stocks.membership_store import StockMembershipStore
from radar.stocks.pools import StockPoolRepository
from radar.stocks.research_store import (
    StockResearchError,
    StockResearchStore,
    StockRevisionConflict,
)
from radar.stocks.structured_screen import RULE_VERSION
from radar.stocks.update_runner import StockUpdateRunner, _artifact
from radar.stocks.update_runs import StockUpdateStore
from stock_robot.stock_admission import evaluate_store

DAY = date(2026, 10, 18)
SYMBOL = '600900'


def test_status_columns_keep_profit_and_cash_separate():
    from radar.stocks.financial_source import financial_status_columns

    failed_profit = financial_status_columns({
        'financial_numeric_status': 'failed',
        'cash_flow_reference': {'ratio': '1.20', 'reference_condition_status': 'passed'},
    })
    assert failed_profit == {'financial_numeric': 'failed', 'cash_ratio': '1.20',
                             'cash_ratio_status': 'passed'}
    pending_profit = financial_status_columns({
        'financial_numeric_status': 'pending_verification',
        'cash_flow_reference': {'ratio': None, 'reference_condition_status': 'pending_verification'},
    })
    assert pending_profit['financial_numeric'] == 'pending_verification'
    assert pending_profit['cash_ratio_status'] == 'pending_verification'
    assert financial_status_columns(None)['financial_numeric'] == ''


def environment(tmp_path):
    clock = [datetime(2026, 10, 20, 8, tzinfo=UTC)]
    research = StockResearchStore(tmp_path/'radar.db', clock=lambda: clock[0])
    research.import_companies([{'symbol': SYMBOL, 'name': '合成公司'}], source_sha256='a'*64,
                              source_label='合成', data_as_of=DAY)
    archive = StockEvaluationStore(research)
    pools = StockPoolRepository(Path(__file__).parents[2]/'config/radar_stock_pools')
    return research, archive, StockUpdateStore(StockMembershipStore(archive, pools)), clock


def enqueue(tasks, day=DAY, pool='dividend_value', version=RULE_VERSION):
    snapshot = {'kind': 'financial_review', 'pool_ids': [pool], 'scope_complete': True,
                'scope_as_of': DAY.isoformat(), 'scope_sha256': 'b'*64, 'source_run_id': 'scope',
                'companies': [{'symbol': SYMBOL, 'name': '合成公司'}]}
    return tasks.enqueue(kind='financial_review', pool_ids=[pool], target_date=day,
                         rule_version=version, snapshot=snapshot)


def complete(tasks, tmp_path):
    samples = json.loads((Path(__file__).parents[1]/'resources/radar/eastmoney-structured-financial-samples.json').read_text(encoding='utf-8'))
    sample = next(row for row in samples if row['symbol'] == SYMBOL)
    sample['as_of'] = DAY.isoformat()
    result = StockUpdateRunner(tasks, tmp_path/'cache', tmp_path/'stock-updates/artifacts',
                               financial_collector=lambda *a, **kw: sample).execute_one('worker')
    assert result is not None
    return result


def test_missing_database_and_task_table_are_read_only(tmp_path):
    missing = tmp_path/'missing/radar.db'
    assert load_financial_source(missing, SYMBOL, DAY)['status'] == 'unavailable'
    assert not missing.parent.exists()
    research = StockResearchStore(tmp_path/'radar.db')
    before = research.db_path.read_bytes()
    assert load_financial_source(research.db_path, SYMBOL, DAY)['status'] == 'unavailable'
    assert research.db_path.read_bytes() == before
    with research._connect() as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='stocks_update_runs'").fetchall()


def test_recompute_and_historical_reference(tmp_path):
    research, _, tasks, _ = environment(tmp_path)
    enqueue(tasks)
    task = complete(tasks, tmp_path)
    source = load_financial_source(research.db_path, SYMBOL, DAY)
    assert source['status'] == 'current' and source['run_id'] == task['run_id']
    assert len(source['financial']['financial_numeric_checks']) == 8
    assert source['financial']['financial_numeric_status'] == 'passed'
    assert 'path' not in json.dumps(source)
    report = evaluate_store(research, DAY, symbol=SYMBOL, include_styles=True, financial_sources={SYMBOL: source})
    item = report['items'][0]
    assert item['financial_source'] == source and item['evaluation_valid']
    assert len([c for c in item['checks'] if c.get('channel') == 'structured_financial']) == 8
    assert all(c['outcome'] == 'passed' for c in item['checks'] if c.get('channel') == 'structured_financial')
    assert item['common_status'] == 'pending_verification'
    old = load_financial_source(research.db_path, SYMBOL, DAY + timedelta(days=1))
    assert old['status'] == 'historical' and old['as_of'] == DAY.isoformat()
    later = evaluate_store(research, DAY + timedelta(days=1), symbol=SYMBOL, financial_sources={SYMBOL: old})['items'][0]
    assert all(c['outcome'] == 'pending_verification' for c in later['checks'] if c.get('channel') == 'structured_financial')


def test_latest_across_pools_and_future_never_falls_back(tmp_path):
    research, _, tasks, _ = environment(tmp_path)
    enqueue(tasks)
    complete(tasks, tmp_path)
    enqueue(tasks, DAY + timedelta(days=1))
    assert load_financial_source(research.db_path, SYMBOL, DAY)['status'] == 'current'
    newer = enqueue(tasks, pool='growth_trend')
    selected = load_financial_source(research.db_path, SYMBOL, DAY)
    assert selected['run_id'] == newer['run_id'] and selected['status'] == 'not_attempted'


def test_saved_favorable_flag_is_not_trusted(tmp_path):
    research, _, tasks, _ = environment(tmp_path)
    enqueue(tasks)
    task = complete(tasks, tmp_path)
    task['results'][0]['artifact']['financial_numeric_status'] = 'failed'
    with research._write() as connection:
        connection.execute('UPDATE stocks_update_runs SET results=?', (json.dumps(task['results']),))
    assert load_financial_source(research.db_path, SYMBOL, DAY)['financial']['financial_numeric_status'] == 'passed'


@pytest.mark.parametrize('mutation', ['artifact_missing', 'companies_broken', 'companies_empty', 'symbol_broken', 'name_missing'])
def test_invalid_latest_cannot_fall_back_to_older_pass(tmp_path, mutation):
    research, _, tasks, _ = environment(tmp_path)
    enqueue(tasks)
    older = complete(tasks, tmp_path)
    enqueue(tasks, pool='growth_trend')
    latest = complete(tasks, tmp_path)
    with research._write() as connection:
        if mutation == 'artifact_missing':
            latest['results'][0]['artifact'].update(sha256='c'*64, path=str(tmp_path/'stock-updates/artifacts'/('c'*64+'.json')))
            connection.execute('UPDATE stocks_update_runs SET results=? WHERE run_id=?', (json.dumps(latest['results']), latest['run_id']))
        else:
            snapshot = latest['input_snapshot']
            if mutation == 'companies_empty':
                snapshot['companies'] = []
            elif mutation == 'symbol_broken':
                snapshot['companies'][0]['symbol'] = 'BROKEN'
            elif mutation == 'name_missing':
                snapshot['companies'] = [{'symbol': '000001'}]
            else:
                snapshot['companies'] = 'broken'
            connection.execute('UPDATE stocks_update_runs SET input_snapshot=? WHERE run_id=?', (json.dumps(snapshot), latest['run_id']))
    source = load_financial_source(research.db_path, SYMBOL, DAY)
    assert source['status'] == 'invalid' and source['run_id'] == latest['run_id']
    assert Path(older['results'][0]['artifact']['path']).is_file()


def test_valid_unrelated_latest_scope_can_be_skipped(tmp_path):
    research, _, tasks, _ = environment(tmp_path)
    enqueue(tasks)
    older = complete(tasks, tmp_path)
    latest = enqueue(tasks, pool='growth_trend')
    snapshot = latest['input_snapshot']
    snapshot['companies'] = [{'symbol': '000001', 'name': '其他合成公司'}]
    with research._write() as connection:
        connection.execute('UPDATE stocks_update_runs SET input_snapshot=? WHERE run_id=?', (json.dumps(snapshot), latest['run_id']))
    source = load_financial_source(research.db_path, SYMBOL, DAY)
    assert source['status'] == 'current' and source['run_id'] == older['run_id']


@pytest.mark.parametrize('mutation', ['hash', 'identity', 'date', 'version', 'scope'])
def test_bad_latest_artifact_or_binding_is_pending(tmp_path, mutation):
    research, _, tasks, _ = environment(tmp_path)
    enqueue(tasks)
    task = complete(tasks, tmp_path)
    if mutation == 'version':
        with research._write() as connection:
            connection.execute("UPDATE stocks_update_runs SET rule_version='old'")
    elif mutation == 'scope':
        with research._write() as connection:
            snapshot = task['input_snapshot']
            snapshot['scope_complete'] = False
            connection.execute('UPDATE stocks_update_runs SET input_snapshot=?', (json.dumps(snapshot),))
    else:
        path = Path(task['results'][0]['artifact']['path'])
        payload = json.loads(path.read_bytes())
        if mutation == 'identity':
            payload['symbol'] = '000001'
        elif mutation == 'date':
            payload['as_of'] = '2026-10-17'
        if mutation == 'hash':
            path.write_text('corrupt', encoding='utf-8')
        else:
            task['results'][0]['artifact'].update(_artifact(path.parent, payload))
            with research._write() as connection:
                connection.execute('UPDATE stocks_update_runs SET results=?', (json.dumps(task['results']),))
    source = load_financial_source(research.db_path, SYMBOL, DAY)
    assert source['status'] in {'invalid', 'version_mismatch'}
    assert 'path' not in json.dumps(source)


def test_archive_binds_source_and_detects_change_without_revision(tmp_path):
    research, archive, tasks, clock = environment(tmp_path)
    cutoff = clock[0].date()
    source = load_financial_source(research.db_path, SYMBOL, cutoff)
    inputs = archive.capture_inputs(symbol=SYMBOL)
    inputs['companies'][0].update(financial_source=source, financial_as_of=cutoff.isoformat())
    report = evaluate_store(research, cutoff, symbol=SYMBOL, include_styles=True, financial_sources={SYMBOL: source})
    saved = archive.save_evaluation(report, inputs)
    assert archive.assert_current(saved['evaluation_id'])
    enqueue(tasks)
    with pytest.raises(StockRevisionConflict):
        archive.assert_current(saved['evaluation_id'])
    with pytest.raises(StockRevisionConflict):
        archive.save_evaluation(report, inputs)


@pytest.mark.parametrize('mutation', ['missing', 'invalid', 'source_day', 'symbol'])
def test_archive_rejects_invalid_financial_input_binding(tmp_path, mutation):
    research, archive, _, clock = environment(tmp_path)
    cutoff = clock[0].date()
    source = load_financial_source(research.db_path, SYMBOL, cutoff)
    inputs = archive.capture_inputs(symbol=SYMBOL)
    entry = inputs['companies'][0]
    entry.update(financial_source=source, financial_as_of=cutoff.isoformat())
    report = evaluate_store(research, cutoff, include_styles=True, financial_sources={SYMBOL: source})
    if mutation == 'missing':
        entry.pop('financial_as_of')
    elif mutation == 'invalid':
        entry['financial_as_of'] = '../private'
    elif mutation == 'source_day':
        source['evaluation_as_of'] = '2026-10-17'
    else:
        source['symbol'] = '000001'
    with pytest.raises(StockResearchError):
        archive.save_evaluation(report, inputs)


def test_corrupted_archived_artifact_becomes_stale(tmp_path):
    research, archive, tasks, clock = environment(tmp_path)
    clock[0] = datetime(2026, 10, 18, 8, tzinfo=UTC)
    enqueue(tasks)
    task = complete(tasks, tmp_path)
    source = load_financial_source(research.db_path, SYMBOL, DAY)
    inputs = archive.capture_inputs(symbol=SYMBOL)
    inputs['companies'][0].update(financial_source=source, financial_as_of=DAY.isoformat())
    report = evaluate_store(research, DAY, include_styles=True, financial_sources={SYMBOL: source})
    saved = archive.save_evaluation(report, inputs)
    assert saved['freshness']['current']
    Path(task['results'][0]['artifact']['path']).write_bytes(b'corrupt')
    with pytest.raises(StockRevisionConflict):
        archive.assert_current(saved['evaluation_id'])


def test_cash_projection_is_recomputed_and_retains_annual_sources(tmp_path):
    research, _, tasks, _ = environment(tmp_path)
    enqueue(tasks)
    complete(tasks, tmp_path)
    source = load_financial_source(research.db_path, SYMBOL, DAY)
    cash = source['financial']['cash_flow_reference']
    assert [row['year'] for row in cash['annual']] == [2023, 2024, 2025]
    assert cash['sources']
    assert all(row['period_end'] and row['disclosed_on'] for row in cash['sources'])
    assert 'path' not in json.dumps(cash)


def test_saved_financial_cash_projection_reaches_style_with_audited_facts(tmp_path):
    from radar.stocks.admission import evaluate_common_admission
    from radar.stocks.style import evaluate_stock_styles
    from tests.radar.test_stock_style import WINDOW, check, facts

    research, _, tasks, _ = environment(tmp_path)
    enqueue(tasks)
    samples = json.loads((Path(__file__).parents[1]/'resources/radar/eastmoney-structured-financial-samples.json').read_text(encoding='utf-8'))
    sample = next(row for row in samples if row['symbol'] == SYMBOL)
    sample['as_of'] = DAY.isoformat()
    for row in sample['records']['profit']:
        row['PARENT_NETPROFIT'] = '100'
    for row in sample['records']['main']:
        row['PARENTNETPROFIT'] = '100'
    for row in sample['records']['cashflow']:
        row['NETCASH_OPERATE'] = '80'
    StockUpdateRunner(tasks, tmp_path/'cache', tmp_path/'stock-updates/artifacts',
                      financial_collector=lambda *a, **kw: sample).execute_one('worker')
    source = load_financial_source(research.db_path, SYMBOL, DAY)
    evidence = facts()
    common = evaluate_common_admission(SYMBOL, evidence, DAY, market_window=WINDOW)
    common['financial_source'] = source
    result = evaluate_stock_styles(SYMBOL, evidence, DAY, market_window=WINDOW, common_result=common)
    for pool in result['pools'].values():
        cash = check(pool, 'cash_ratio')
        assert cash['outcome'] == 'passed'
        assert cash['channel'] == 'structured_cash_flow'
    assert source['financial']['cash_flow_reference']['sources']


def test_archived_audit_result_expires_if_bound_audit_source_changes(tmp_path, monkeypatch):
    import radar.stocks.research_candidates as candidates

    research, archive, tasks, clock = environment(tmp_path)
    clock[0] = datetime(2026, 10, 18, 8, tzinfo=UTC)
    enqueue(tasks)
    complete(tasks, tmp_path)
    changed = [False]
    def fake_audit(_db, symbol, as_of):
        return {'symbol': symbol, 'as_of': as_of.isoformat(), 'status': 'available',
                'annual': [{'year': year, 'status': 'available', 'updated_after_cutoff': False,
                            'opinion': '保留意见' if changed[0] and year == 2025 else '标准无保留意见',
                            'other_opinion': None, 'security_code': symbol, 'secucode': symbol+'.SH',
                            'report_type': '年报', 'report_date_name': f'{year}年报'} for year in (2023, 2024, 2025)],
                'source': {'content_sha256': ('b' if changed[0] else 'a') * 64, 'as_of': as_of.isoformat(),
                           'version': 'eastmoney-structured-financial-v1', 'fetched_at': '2026-10-18T08:00:00+08:00',
                           'source_url': 'https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/xjllbAjaxNew',
                           'request_params': {'code': 'SH'+symbol, 'reportType': '1', 'reportDateType': '0',
                                              'companyType': '4', 'dates': '2023-12-31,2024-12-31,2025-12-31'}}}
    monkeypatch.setattr(candidates, 'load_audit_clues', fake_audit)
    source = load_financial_source(research.db_path, SYMBOL, DAY)
    report = evaluate_store(research, DAY, symbol=SYMBOL, include_styles=True, financial_sources={SYMBOL: source})
    audit = next(x for x in report['items'][0]['checks'] if x['code'] == 'audit')
    assert audit['outcome'] == 'passed'
    inputs = archive.capture_inputs(symbol=SYMBOL)
    inputs['companies'][0].update(financial_source=source, financial_as_of=DAY.isoformat())
    saved = archive.save_evaluation(report, inputs)
    assert archive.assert_current(saved['evaluation_id'])
    changed[0] = True
    with pytest.raises(StockRevisionConflict):
        archive.assert_current(saved['evaluation_id'])


def test_audit_raw_tampering_invalidates_source_and_archive(tmp_path):
    import hashlib

    research, archive, tasks, clock = environment(tmp_path)
    clock[0] = datetime(2026, 10, 18, 8, tzinfo=UTC)
    enqueue(tasks)
    task = complete(tasks, tmp_path)
    item = task['results'][0]
    payload = json.loads(Path(item['artifact']['path']).read_bytes())
    rows = [{'SECURITY_CODE': SYMBOL, 'SECUCODE': SYMBOL+'.SH', 'REPORT_DATE': f'{year}-12-31',
             'NOTICE_DATE': f'{year+1}-04-01', 'UPDATE_DATE': f'{year+1}-04-01',
             'REPORT_TYPE': '年报', 'REPORT_DATE_NAME': f'{year}年报',
             'OPINION_TYPE': '标准无保留意见', 'OSOPINION_TYPE': None} for year in (2023, 2024, 2025)]
    raw = json.dumps({'code': 0, 'data': rows}).encode()
    root = tmp_path/'stock-updates/cache/structured-financial'
    root.mkdir(parents=True)
    raw_path = root/'cashflow.json'
    raw_path.write_bytes(raw)
    payload['collection']['sources']['cashflow'] = {
        'raw_artifact': str(raw_path), 'bytes': len(raw), 'content_sha256': hashlib.sha256(raw).hexdigest(),
        'source_url': 'https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/xjllbAjaxNew',
        'fetched_at': '2026-10-18T08:00:00+08:00', 'as_of': DAY.isoformat(),
        'version': 'eastmoney-structured-financial-v1',
        'request_params': {'code': 'SH'+SYMBOL, 'reportType': '1', 'reportDateType': '0',
                           'companyType': '4', 'dates': '2023-12-31,2024-12-31,2025-12-31'}}
    item['artifact'] = _artifact(tmp_path/'stock-updates/artifacts', payload)
    with research._write() as connection:
        connection.execute('UPDATE stocks_update_runs SET results=? WHERE run_id=?', (json.dumps(task['results']), task['run_id']))
    source = load_financial_source(research.db_path, SYMBOL, DAY)
    assert source['audit_clues']['classification']['status'] == 'standard_opinions'
    report = evaluate_store(research, DAY, symbol=SYMBOL, include_styles=True, financial_sources={SYMBOL: source})
    assert next(x for x in report['items'][0]['checks'] if x['code'] == 'audit')['outcome'] == 'passed'
    inputs = archive.capture_inputs(symbol=SYMBOL)
    inputs['companies'][0].update(financial_source=source, financial_as_of=DAY.isoformat())
    saved = archive.save_evaluation(report, inputs)
    assert archive.assert_current(saved['evaluation_id'])
    raw_path.write_bytes(raw+b' ')
    changed = load_financial_source(research.db_path, SYMBOL, DAY)
    assert changed['audit_clues']['status'] == 'unavailable'
    new_report = evaluate_store(research, DAY, symbol=SYMBOL, include_styles=True, financial_sources={SYMBOL: changed})
    assert next(x for x in new_report['items'][0]['checks'] if x['code'] == 'audit')['outcome'] == 'pending_verification'
    with pytest.raises(StockRevisionConflict):
        archive.assert_current(saved['evaluation_id'])


def test_financial_source_rejects_task_change_while_loading_audit(tmp_path, monkeypatch):
    import radar.stocks.research_candidates as candidates

    research, _, tasks, _ = environment(tmp_path)
    enqueue(tasks)
    complete(tasks, tmp_path)
    def changing_audit(_db, symbol, as_of):
        enqueue(tasks, pool='growth_trend')
        return {'symbol': symbol, 'as_of': as_of.isoformat(), 'status': 'unavailable'}
    monkeypatch.setattr(candidates, 'load_audit_clues', changing_audit)
    source = load_financial_source(research.db_path, SYMBOL, DAY)
    assert source['status'] == 'invalid' and source['financial'] is None
