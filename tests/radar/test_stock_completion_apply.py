"""补全执行：计划哈希、dry-run、--yes、适配器 provider。"""
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
    ]}
    body['plan_sha256'] = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True,
                                                    separators=(',', ':')).encode()).hexdigest()
    return body


def fields():
    return {'netcash_operate': 109452084.73, 'construct_long_asset': 553057873.74,
            'assign_dividend_profit': 168462000.00, 'end_cash': 286438980.59}


def turnover_daily():
    start = date(2026, 11, 7) - timedelta(days=89)
    return [{'date': (start + timedelta(days=i)).isoformat(), 'amount_yuan': '120000000'} for i in range(60)]


def providers():
    return {
        'annual_report': lambda symbol, name, years: {2023: {'fields': fields()}},
        'turnover': lambda symbol, as_of: {'daily': turnover_daily()},
    }


def test_dry_run_never_calls_providers(tmp_path):
    plan = plan_payload()
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(plan), encoding='utf-8')
    assert load_plan(path)['plan_sha256'] == plan['plan_sha256']
    run = apply_plan(plan, dry_run=True)
    assert run['dry_run'] is True
    assert all(item['status'] == 'planned' for item in run['items'])


def test_yes_runs_two_adapters_with_injected_providers():
    run = apply_plan(plan_payload(), yes=True, providers=providers())
    statuses = {item['symbol']: item['status'] for item in run['items']}
    assert statuses == {'603087': 'complete', '002155': 'complete'}


def test_missing_provider_stays_pending_without_guessing():
    run = apply_plan(plan_payload(), yes=True, providers={})
    statuses = {item['symbol']: item['status'] for item in run['items']}
    assert statuses == {'603087': 'pending_verification', '002155': 'pending_verification'}


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
