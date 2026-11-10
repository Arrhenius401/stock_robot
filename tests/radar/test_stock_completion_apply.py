"""补全执行：计划哈希、dry-run、--yes、三适配器证据。"""
import hashlib
import json
from datetime import date, timedelta

from radar.stocks.completion_apply import apply_plan, load_plan


def plan_payload():
    body = {'version': 'completion-plan-v1', 'as_of': '2026-11-07', 'pools': [], 'items': [
        {'symbol': '603087', 'condition': 'cash_review', 'gap': {'years': [2023]},
         'source': {'kind': 'annual_report_cashflow'}},
        {'symbol': '002155', 'condition': 'liquidity',
         'source': {'kind': 'market_turnover_60d'}},
        {'symbol': '688981', 'condition': 'dividend_history',
         'source': {'kind': 'dividend_source_retry'}},
    ]}
    body['plan_sha256'] = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                                    separators=(',', ':')).encode()).hexdigest()
    return body


def cashflow_text():
    return ('经营活动产生的现金流量净额 1,094,520,847.73\n'
            '购建固定资产、无形资产和其他长期资产支付的现金 553,057,873.74\n'
            '分配股利、利润或偿付利息支付的现金 84,000,000.00\n'
            '期末现金及现金等价物余额 286,438,980.59\n')


def turnover_rows():
    start = date(2026, 11, 7) - timedelta(days=89)
    return [{'date': (start + timedelta(days=i)).isoformat(), 'amount_yuan': '120000000'} for i in range(60)]


def test_dry_run_never_reads_evidence(tmp_path):
    plan = plan_payload()
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(plan), encoding='utf-8')
    assert load_plan(path)['plan_sha256'] == plan['plan_sha256']
    run = apply_plan(plan, dry_run=True)
    assert run['dry_run'] is True
    assert all(item['status'] == 'planned' for item in run['items'])


def test_yes_runs_three_adapters_with_injected_evidence():
    run = apply_plan(plan_payload(), yes=True, providers={
        'annual_report': lambda symbol, years: {2023: cashflow_text()},
        'turnover': lambda symbol, as_of: turnover_rows(),
        'source_retry': lambda symbol, as_of: {'status': 'complete'},
    })
    statuses = {item['symbol']: item['status'] for item in run['items']}
    assert statuses == {'603087': 'complete', '002155': 'complete', '688981': 'complete'}
    assert all('status' in item for item in run['items'])


def test_missing_evidence_stays_pending_without_guessing():
    run = apply_plan(plan_payload(), yes=True, providers={
        'annual_report': lambda symbol, years: {2023: '无字段'},
        'turnover': lambda symbol, as_of: turnover_rows()[:10],
        'source_retry': lambda symbol, as_of: {'status': 'partial'},
    })
    statuses = {item['symbol']: item['status'] for item in run['items']}
    assert statuses == {'603087': 'pending_verification', '002155': 'pending_verification',
                        '688981': 'pending_verification'}


def test_plan_hash_mismatch_is_rejected(tmp_path):
    plan = plan_payload()
    plan['items'].append({'symbol': 'X', 'condition': 'Y'})
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(plan), encoding='utf-8')
    try:
        load_plan(path)
    except ValueError as exc:
        assert '哈希' in str(exc)
    else:
        raise AssertionError('must reject')
