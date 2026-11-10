"""A股结构化分红规则：未知不能冒充零，来源与日期严格约束。"""
from copy import deepcopy
from datetime import date

import pytest

from radar.stocks.dividend_structured import evaluate_dividend_records

DAY = date(2026, 10, 18)


def records():
    return [{'F001V': f'{y}年报', 'F006D': f'{y+1}-07-01', 'F012N': '8',
             'F023D': f'{y+1}-07-10'} for y in (2023, 2024, 2025)]


def secondary_records():
    return [{'SECURITY_CODE': '600900', 'REPORT_DATE': f'{y}-12-31',
             'NOTICE_DATE': f'{y+1}-07-01', 'EX_DIVIDEND_DATE': f'{y+1}-07-09',
             'PRETAX_BONUS_RMB': '8', 'ASSIGN_PROGRESS': '实施分配'} for y in (2023, 2024, 2025)]


def test_three_years_cash_dividend_uses_fiscal_year_and_actual_payment():
    result = evaluate_dividend_records('600900', DAY, records(), secondary_records())
    assert result['outcome'] == 'passed'
    assert [v['year'] for v in result['annual']] == [2023, 2024, 2025]
    assert result['financial_verified'] is False


def test_cninfo_multiple_events_in_same_year_and_date_pass():
    from radar.stocks.dividend_structured import evaluate_cninfo_records

    rows = records()
    duplicate = deepcopy(rows[0])
    duplicate['F012N'] = '9'
    rows.append(duplicate)
    result = evaluate_cninfo_records('600900', DAY, rows)
    assert result['outcome'] == 'passed'
    assert len(result['annual'][0]['events']) == 2


def test_missing_secondary_event_remains_pending():
    result = evaluate_dividend_records('600900', DAY, records(), [])
    assert result['outcome'] == 'pending_verification'
    assert all(row['outcome'] == 'pending_verification' for row in result['annual'])


@pytest.mark.parametrize('mutation', ['missing', 'future_paid', 'future_notice', 'zero', 'unknown_period', 'duplicate_conflict'])
def test_missing_or_ambiguous_is_not_failure_or_pass(mutation):
    rows=records()
    if mutation=='missing': rows.pop()
    elif mutation=='future_paid': rows[0]['F023D']='2026-11-01'
    elif mutation=='future_notice': rows[0]['F006D']='2026-11-01'
    elif mutation=='zero': rows[0]['F012N']='0'
    elif mutation=='unknown_period': rows[0]['F001V']='未知'
    else:
        other=deepcopy(rows[0]); other['F012N']='9'; rows.append(other)
    assert evaluate_dividend_records('600900', DAY, rows, [])['outcome'] == 'pending_verification'


def test_conflicting_cross_source_or_period_remains_pending():
    secondary=[{'SECURITY_CODE':'600900','REPORT_DATE':'2024-06-30','NOTICE_DATE':'2025-07-01',
                'EX_DIVIDEND_DATE':'2025-07-10','PRETAX_BONUS_RMB':'9','ASSIGN_PROGRESS':'实施分配'}]
    assert evaluate_dividend_records('600900', DAY, records(), secondary)['outcome'] == 'pending_verification'


def test_legacy_stock_pdf_commands_removed():
    from click.testing import CliRunner

    from stock_robot.cli import main

    result = CliRunner().invoke(main, ['radar', '--help'])
    assert result.exit_code == 0
    for name in ('dividend-archive', 'dividend-discover', 'dividend-extract', 'dividend-workflow',
                 'financial-extract', 'interim-discover', 'interim-extract', 'interim-review-packet',
                 'stock-annual-collect', 'stock-annual-reparse'):
        assert name not in result.output


def test_same_event_with_inconsistent_reporting_period_is_pending():
    rows=records()
    rows[1]['F001V']='2024三季报'
    secondary=[{'SECURITY_CODE':'600900','REPORT_DATE':'2024-06-30','NOTICE_DATE':'2025-07-01',
                'EX_DIVIDEND_DATE':'2025-07-10','PRETAX_BONUS_RMB':'8','ASSIGN_PROGRESS':'实施分配'}]
    assert evaluate_dividend_records('600900', DAY, rows, secondary)['outcome'] == 'pending_verification'


def test_real_cninfo_has_no_company_code_and_has_cross_source_period_conflict():
    import json
    from pathlib import Path

    from radar.stocks.dividend_structured import evaluate_cninfo_records

    root = Path(__file__).parents[1] / 'resources/radar'
    primary = json.loads((root/'cninfo-600900-dividend-history.json').read_text(encoding='utf-8'))
    secondary = json.loads((root/'eastmoney-600900-dividend-history.json').read_text(encoding='utf-8'))
    assert all('SECURITY_CODE' not in row for row in primary['records'])
    assert evaluate_cninfo_records('600900', date(2026, 11, 2), primary['records'])['outcome'] == 'passed'
    assert any(row['F001V'] == '2024三季报' and row['F006D'] == '2025-01-16' for row in primary['records'])
    assert any(row['REPORT_DATE'].startswith('2024-06-30') and row['NOTICE_DATE'].startswith('2025-01-16')
               for row in secondary['result']['data'])


@pytest.mark.parametrize('symbol', ['600900', '600036'])
def test_real_eastmoney_without_actual_payment_date_cannot_pass(symbol):
    import json
    from pathlib import Path

    path = Path(__file__).parents[1] / 'resources/radar' / f'eastmoney-{symbol}-dividend-history.json'
    sample = json.loads(path.read_text(encoding='utf-8'))
    assert evaluate_dividend_records(symbol, date(2026, 11, 2), [], sample['result']['data'])['outcome'] == 'pending_verification'


def test_collect_dividend_sources_cache_and_failure_are_separate(tmp_path):
    import json

    from radar.stocks.dividend_collect import collect_dividend
    calls = []
    def fetch(source, symbol):
        calls.append(source)
        if source != 'cninfo':
            return 503, b''
        rows = records()
        return 200, json.dumps({'resultcode': 200, 'count': len(rows), 'total': len(rows), 'records': rows}).encode()
    first = collect_dividend('600900', DAY, tmp_path, fetch=fetch, sleep=lambda _: None)
    assert first['status'] == 'complete' and first['selected_provider'] == 'cninfo'
    calls.clear()
    second = collect_dividend('600900', DAY, tmp_path, fetch=fetch, sleep=lambda _: None)
    assert 'cninfo' not in calls and second['sources']['cninfo']['cache_status'] == 'cache'
    assert second['sources']['cninfo']['fetched_at'] == first['sources']['cninfo']['fetched_at']


def test_collector_429_never_makes_missing_data_complete(tmp_path):
    from radar.stocks.dividend_collect import collect_dividend
    def fetch(source, symbol):
        return (429, b'') if source == 'cninfo' else (500, b'')
    result = collect_dividend('600900', DAY, tmp_path, fetch=fetch, sleep=lambda _: None)
    assert result['status'] == 'partial'
    assert result['sources']['cninfo']['status'] == 'stopped'
    assert result['sources']['eastmoney_page']['status'] == 'failed'


def test_rate_limited_source_is_skipped_for_other_companies(tmp_path):
    from radar.stocks.dividend_collect import collect_dividend
    calls = []
    def fetch(source, symbol):
        calls.append(source)
        return 503, b''
    result = collect_dividend('600900', DAY, tmp_path, fetch=fetch, sleep=lambda _: None, blocked_sources={'cninfo'})
    assert 'cninfo' not in calls
    assert result['sources']['cninfo']['status'] == 'stopped'


def test_cninfo_record_without_company_identity_uses_request_binding():
    import json

    from radar.stocks.dividend_collect import validate_source
    rows = records()
    raw = json.dumps({'resultcode': 200, 'count': len(rows), 'total': len(rows), 'records': rows}).encode()
    assert validate_source('cninfo', '600900', raw) == rows


def test_rejected_raw_response_is_preserved_without_false_success_or_retry(tmp_path):
    import base64
    import hashlib
    import json

    from radar.stocks.dividend_collect import collect_dividend, request_identity
    raw = json.dumps({'records': records()}).encode()
    calls = []
    def fetch(source, symbol):
        calls.append(source)
        return (200, raw) if source == 'cninfo' else (404, b'')
    result = collect_dividend('600900', DAY, tmp_path, fetch=fetch, sleep=lambda _: pytest.fail('结构错误不应重试'))
    rejected = result['sources']['cninfo']
    assert calls == ['eastmoney_page', 'cninfo']
    assert result['status'] == 'partial' and rejected['status'] == 'failed'
    assert rejected['reason'] == 'invalid_data' and rejected['request'] == request_identity('cninfo', '600900')
    assert rejected['sha256'] == hashlib.sha256(raw).hexdigest() and base64.b64decode(rejected['raw']) == raw
    assert not (tmp_path / f'cninfo-600900-{DAY.isoformat()}.json').exists()


def test_partial_task_archives_rejected_original_as_unverified(tmp_path):
    import base64
    import json

    from radar.stocks.dividend_collect import VERSION, collect_dividend
    from radar.stocks.dividend_source import load_dividend_source
    from radar.stocks.financial_task import read_financial_artifact
    from radar.stocks.update_runner import StockUpdateRunner
    from tests.radar.test_stock_financial_source import environment

    research, _, tasks, _ = environment(tmp_path)
    snapshot = {'kind': 'dividend_review', 'pool_ids': ['dividend_value'], 'scope_complete': True,
                'scope_as_of': DAY.isoformat(), 'scope_sha256': 'b'*64, 'source_run_id': 'scope',
                'companies': [{'symbol': '600900', 'name': '合成公司'}]}
    tasks.enqueue(kind='dividend_review', pool_ids=['dividend_value'], target_date=DAY,
                  rule_version=VERSION, snapshot=snapshot)
    raw = json.dumps({'records': records()}).encode()
    def fetch(source, symbol):
        return (200, raw) if source == 'cninfo' else (200, json.dumps({'success': True, 'result': {'pages': 1, 'data': []}}).encode())
    def collector(symbol, as_of, cache_dir, **kwargs):
        return collect_dividend(symbol, as_of, cache_dir, fetch=fetch, sleep=lambda _: None, **kwargs)
    runner = StockUpdateRunner(tasks, tmp_path/'cache', tmp_path/'stock-updates/artifacts', dividend_collector=collector)
    finished = runner.execute_one('isolated')
    assert finished is not None and finished['status'] == 'partial'
    saved = read_financial_artifact(finished['results'][0], tmp_path/'stock-updates/artifacts')
    assert base64.b64decode(saved['collection']['sources']['cninfo']['raw']) == raw
    source = load_dividend_source(research.db_path, '600900', DAY)
    assert source['status'] == 'partial' and source['result'] is None


def test_cache_write_failure_cannot_report_completed(tmp_path, monkeypatch):
    import json

    from radar.stocks import dividend_collect as module
    atomic = module._atomic
    def write(path, data):
        if path.name.startswith('cninfo-'):
            raise OSError('合成写入失败')
        return atomic(path, data)
    monkeypatch.setattr(module, '_atomic', write)
    def fetch(source, symbol):
        rows = records()
        value = {'resultcode': 200, 'count': len(rows), 'total': len(rows), 'records': rows}
        return (200, json.dumps(value).encode()) if source == 'cninfo' else (404, b'')
    result = module.collect_dividend('600900', DAY, tmp_path, fetch=fetch, sleep=lambda _: None)
    assert result['status'] == 'partial' and result['sources']['cninfo']['status'] == 'failed'


def test_dividend_task_and_source_bind_raw_artifact(tmp_path):
    import base64
    import hashlib
    import json
    from datetime import UTC, datetime

    from radar.stocks.dividend_collect import URLS, VERSION, request_identity
    from radar.stocks.dividend_source import load_dividend_source
    from radar.stocks.update_runner import StockUpdateRunner
    from tests.radar.test_stock_financial_source import environment

    research, archive, tasks, _ = environment(tmp_path)
    snapshot={'kind':'dividend_review','pool_ids':['dividend_value'],'scope_complete':True,
              'scope_as_of':DAY.isoformat(),'scope_sha256':'b'*64,'source_run_id':'scope',
              'companies':[{'symbol':'600900','name':'合成公司'}]}
    task=tasks.enqueue(kind='dividend_review',pool_ids=['dividend_value'],target_date=DAY,
                       rule_version=VERSION,snapshot=snapshot)
    source={}
    for name, payload in [('cninfo', {'resultcode': 200, 'count': 3, 'total': 3, 'records': records()})]:
        raw=json.dumps(payload).encode()
        source[name]={'version':VERSION,'source':name,'symbol':'600900','as_of':DAY.isoformat(),'url':URLS[name],
                      'status':'complete','fetched_at':datetime(2026, 10, 18, tzinfo=UTC).isoformat(),
                      'request':request_identity(name, '600900'), 'sha256':hashlib.sha256(raw).hexdigest(),
                      'raw':base64.b64encode(raw).decode('ascii')}
    collection={'version':VERSION,'symbol':'600900','as_of':DAY.isoformat(),'sources':source,'status':'complete', 'selected_provider':'cninfo'}
    runner=StockUpdateRunner(tasks,tmp_path/'cache',tmp_path/'stock-updates/artifacts',
                             dividend_collector=lambda *a,**kw:collection)
    result=runner.execute_one('isolated')
    assert result is not None
    assert result['run_id']==task['run_id'] and result['status']=='completed'
    loaded=load_dividend_source(research.db_path,'600900',DAY)
    assert loaded['status']=='current' and loaded['result']['outcome']=='passed'
    from stock_robot.stock_admission import evaluate_store
    inputs=archive.capture_inputs(symbol='600900')
    inputs['companies'][0].update(dividend_source=loaded, dividend_as_of=DAY.isoformat())
    report=evaluate_store(research, DAY, symbol='600900', include_styles=True, dividend_sources={'600900': loaded})
    saved=archive.save_evaluation(report, inputs)
    assert saved is not None
    artifact=next(item for item in result['results'] if item['scope']=='dividend:600900')['artifact']
    from pathlib import Path
    Path(artifact['path']).write_bytes(b'corrupt')
    assert load_dividend_source(research.db_path,'600900',DAY)['status']=='invalid'
    reloaded = archive.get(saved['evaluation_id'])
    assert reloaded is not None
    assert reloaded['freshness']['current'] is False


def test_later_collected_dividend_is_historical_even_at_same_cutoff(tmp_path):
    import base64
    import hashlib
    import json
    from datetime import UTC, datetime

    from radar.stocks.dividend_collect import URLS, VERSION, request_identity
    from radar.stocks.dividend_source import load_dividend_source
    from radar.stocks.update_runner import StockUpdateRunner
    from tests.radar.test_stock_financial_source import environment

    research, _, tasks, _ = environment(tmp_path)
    snapshot = {'kind': 'dividend_review', 'pool_ids': ['dividend_value'], 'scope_complete': True,
                'scope_as_of': DAY.isoformat(), 'scope_sha256': 'b'*64, 'source_run_id': 'scope',
                'companies': [{'symbol': '600900', 'name': '合成公司'}]}
    tasks.enqueue(kind='dividend_review', pool_ids=['dividend_value'], target_date=DAY,
                  rule_version=VERSION, snapshot=snapshot)
    sources = {}
    for name, payload in [('cninfo', {'resultcode': 200, 'count': 3, 'total': 3, 'records': records()})]:
        raw = json.dumps(payload).encode()
        sources[name] = {'version': VERSION, 'source': name, 'symbol': '600900', 'as_of': DAY.isoformat(),
                         'url': URLS[name], 'status': 'complete', 'fetched_at': datetime.now(UTC).isoformat(),
                         'request': request_identity(name, '600900'), 'sha256': hashlib.sha256(raw).hexdigest(),
                         'raw': base64.b64encode(raw).decode()}
    collection = {'version': VERSION, 'symbol': '600900', 'as_of': DAY.isoformat(), 'sources': sources, 'selected_provider': 'cninfo'}
    runner = StockUpdateRunner(tasks, tmp_path/'cache', tmp_path/'stock-updates/artifacts',
                               dividend_collector=lambda *args, **kwargs: collection)
    assert runner.execute_one('isolated') is not None
    assert load_dividend_source(research.db_path, '600900', DAY)['status'] == 'historical'


def test_newer_unfinished_task_blocks_old_source_and_can_resume(tmp_path):
    from radar.stocks.dividend_collect import VERSION
    from radar.stocks.dividend_source import load_dividend_source
    from radar.stocks.update_runner import StockUpdateRunner
    from tests.radar.test_stock_financial_source import environment

    research, _, tasks, clock = environment(tmp_path)
    snapshot = {'kind': 'dividend_review', 'pool_ids': ['dividend_value'], 'scope_complete': True,
                'scope_as_of': DAY.isoformat(), 'scope_sha256': 'b'*64, 'source_run_id': 'scope',
                'companies': [{'symbol': '600900', 'name': '合成公司'}]}
    old = tasks.enqueue(kind='dividend_review', pool_ids=['dividend_value'], target_date=DAY,
                        rule_version=VERSION, snapshot=snapshot)
    runner = StockUpdateRunner(tasks, tmp_path/'cache', tmp_path/'stock-updates/artifacts',
                               dividend_collector=lambda *args, **kwargs: None)
    partial = runner.execute_one('isolated')
    assert partial is not None and partial['status'] == 'partial'
    assert load_dividend_source(research.db_path, '600900', DAY)['run_id'] == old['run_id']
    resumed = tasks.resume_dividend(old['run_id'], 'dividend_value')
    assert resumed['status'] == 'queued' and resumed['input_snapshot'] == old['input_snapshot']
    partial = runner.execute_one('isolated')
    assert partial is not None and len(partial['results']) == 2
    from datetime import timedelta
    clock[0] += timedelta(seconds=1)
    snapshot['pool_ids'] = ['growth_trend']
    newer = tasks.enqueue(kind='dividend_review', pool_ids=['growth_trend'], target_date=DAY,
                          rule_version=VERSION, snapshot=snapshot)
    selected = load_dividend_source(research.db_path, '600900', DAY)
    assert selected['run_id'] == newer['run_id'] and selected['status'] == 'not_attempted'


def test_dividend_source_reaches_red_branch_without_fake_review():
    from radar.stocks.admission import evaluate_common_admission
    from radar.stocks.style import evaluate_stock_styles
    from tests.radar.test_stock_style import AS_OF, WINDOW, check, facts

    evidence = [row for row in facts() if row['kind'] != 'style_dividend_history']
    common = evaluate_common_admission('600900', evidence, AS_OF, market_window=WINDOW)
    source = {'symbol': '600900', 'evaluation_as_of': AS_OF.isoformat(), 'as_of': AS_OF.isoformat(),
              'status': 'current', 'result': {'outcome': 'passed', 'annual': []}}
    common['dividend_source'] = source
    result = evaluate_stock_styles('600900', evidence, AS_OF, market_window=WINDOW, common_result=common)
    assert check(result['pools']['dividend_value'], 'dividend_history')['outcome'] == 'passed'
    assert result['formal_screening_enabled'] is False
    source['status'] = 'historical'
    assert check(evaluate_stock_styles('600900', evidence, AS_OF, market_window=WINDOW,
                                      common_result=common)['pools']['dividend_value'],
                 'dividend_history')['outcome'] == 'pending_verification'


def test_single_cninfo_provider_can_prove_dividends_without_second_provider(tmp_path):
    import json

    from radar.stocks.dividend_collect import collect_dividend, verify_collection

    calls = []
    def fetch(source, symbol):
        calls.append(source)
        if source == 'cninfo':
            rows = records()
            return 200, json.dumps({'resultcode': 200, 'count': len(rows), 'total': len(rows), 'records': rows}).encode()
        return 503, b''
    result = collect_dividend('600900', DAY, tmp_path, fetch=fetch, sleep=lambda _: None)
    assert result['status'] == 'complete'
    assert result['selected_provider'] == 'cninfo'
    assert verify_collection(result, '600900', DAY)['outcome'] == 'passed'
    assert calls.count('cninfo') == 1


def test_cninfo_rejects_explicit_different_company():
    import json

    from radar.stocks.dividend_collect import validate_source

    rows = [{**records()[0], 'SECURITY_CODE': '600036'}]
    with pytest.raises(ValueError, match='公司身份'):
        validate_source('cninfo', '600900', json.dumps({'resultcode': 200, 'count': 1, 'total': 1, 'records': rows}).encode())


def test_eastmoney_success_does_not_fetch_cninfo_and_is_reverified(tmp_path):
    import json
    from copy import deepcopy

    from radar.stocks.dividend_collect import collect_dividend, verify_collection

    calls = []
    page = [{'SECURITY_CODE': '600900', 'NOTICE_DATE': f'{year+1}-07-01',
             'ASSIGN_PROGRESS': '实施方案', 'IMPL_PLAN_PROFILE': '10派8元',
             'EX_DIVIDEND_DATE': f'{year+1}-07-09', 'PAY_CASH_DATE': f'{year+1}-07-10'}
            for year in (2023, 2024, 2025)]
    def fetch(source, symbol):
        calls.append(source)
        if source == 'eastmoney_page':
            return 200, json.dumps({'fhyx': page}).encode()
        if source == 'eastmoney':
            return 200, json.dumps({'success': True, 'result': {'pages': 1, 'count': 3, 'data': secondary_records()}}).encode()
        pytest.fail('首选来源可用时不应请求备用来源')
    result = collect_dividend('600900', DAY, tmp_path, fetch=fetch, sleep=lambda _: None)
    assert calls == ['eastmoney_page', 'eastmoney']
    assert result['selected_provider'] == 'eastmoney'
    assert verify_collection(result, '600900', DAY)['outcome'] == 'passed'
    corrupted = deepcopy(result)
    corrupted['sources']['eastmoney']['sha256'] = '0' * 64
    with pytest.raises(ValueError):
        verify_collection(corrupted, '600900', DAY)


def test_eastmoney_ambiguous_event_requires_fallback():
    from radar.stocks.dividend_structured import evaluate_eastmoney_records

    page = [{'SECURITY_CODE': '600900', 'NOTICE_DATE': f'{year+1}-07-01',
             'ASSIGN_PROGRESS': '实施方案', 'IMPL_PLAN_PROFILE': '10派8元',
             'EX_DIVIDEND_DATE': f'{year+1}-07-09', 'PAY_CASH_DATE': f'{year+1}-07-10'}
            for year in (2023, 2024, 2025)]
    page.append(dict(page[0]))
    assert evaluate_eastmoney_records('600900', DAY, page, secondary_records())['outcome'] == 'pending_verification'


def test_eastmoney_rejects_truncated_count_and_conflicting_exchange():
    import json

    from radar.stocks.dividend_collect import validate_source

    with pytest.raises(ValueError, match='记录数'):
        validate_source('eastmoney', '600900', json.dumps({'success': True, 'result': {
            'pages': 1, 'count': 4, 'data': secondary_records()}}).encode())
    with pytest.raises(ValueError, match='公司身份'):
        validate_source('eastmoney_page', '600900', json.dumps({'fhyx': [
            {'SECURITY_CODE': '600900', 'SECUCODE': '600900.SZ'}]}).encode())


def test_incomplete_eastmoney_falls_back_to_cninfo(tmp_path):
    import json

    from radar.stocks.dividend_collect import collect_dividend, verify_collection

    calls = []
    def fetch(source, symbol):
        calls.append(source)
        if source == 'eastmoney_page':
            return 200, json.dumps({'fhyx': []}).encode()
        if source == 'eastmoney':
            return 200, json.dumps({'success': True, 'result': {'pages': 1, 'count': 0, 'data': []}}).encode()
        rows = records()
        return 200, json.dumps({'resultcode': 200, 'count': len(rows), 'total': len(rows), 'records': rows}).encode()
    result = collect_dividend('600900', DAY, tmp_path, fetch=fetch, sleep=lambda _: None)
    assert calls == ['eastmoney_page', 'eastmoney', 'cninfo']
    assert result['selected_provider'] == 'cninfo'
    assert verify_collection(result, '600900', DAY)['identity_basis'] == 'request_code'
