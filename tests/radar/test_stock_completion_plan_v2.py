"""计划 v2：共同条件缺项分类、共享阶段去重与失败项排除。"""
from __future__ import annotations

from datetime import date

from radar.stocks.completion_plan_v2 import (
    build_completion_plan_v2,
    plan_automatic_symbols,
    plan_firm_items,
    plan_shared_stages,
)

DAY = date(2026, 11, 13)


def row(symbol: str, condition: str, outcome: str, group: str = 'common', reason: str = '') -> dict:
    return {'代码': symbol, '名称': f'公司{symbol}', '池': 'dividend_value', '分类': group,
            '条件': condition, '结果': outcome, '原因': reason or condition}


def details() -> list[dict]:
    return [
        row('600001', 'listing_age', 'passed'), row('600001', 'governance', 'pending_verification', reason='监管来源缺失'),
        row('600001', 'leadership', 'pending_verification', reason='缺少同业排名'),
        row('600001', 'dividend_history', 'passed', group='style'),
        row('600002', 'listing_age', 'passed'), row('600002', 'governance', 'passed'),
        row('600002', 'leadership', 'passed'), row('600002', 'cash_review', 'pending_verification', group='style'),
        row('600003', 'listing_age', 'passed'), row('600003', 'governance', 'passed'),
        row('600003', 'leadership', 'passed'), row('600003', 'dividend_history', 'pending_verification', group='style'),
        row('600004', 'governance', 'failed', reason='近三年纪律处分'),
    ]


def test_shared_conditions_become_shared_stages(tmp_path):
    plan = build_completion_plan_v2(details(), tmp_path / 'radar.db', DAY, pools={'dividend_value'})
    shared = plan['shared_stages']
    assert [stage['stage'] for stage in shared] == ['governance', 'industry_scale']
    assert shared[0]['symbols'] == ['600001']
    governance = [item for item in plan['items'] if item['condition'] == 'governance']
    assert governance[0]['evidence_class'] == 'shared_refresh'
    assert governance[0]['executable'] == {'mode': 'shared_stage', 'stage': 'governance'}
    assert plan_shared_stages(plan) == ['governance', 'industry_scale']
    assert '600004' not in {item['symbol'] for item in plan['items']}


def test_style_items_are_classified_by_execution(tmp_path):
    plan = build_completion_plan_v2(details(), tmp_path / 'radar.db', DAY, pools={'dividend_value'})
    classes = {item['symbol']: item['evidence_class'] for item in plan['items'] if item['group'] == 'style'}
    assert classes['600002'] == 'auto_firm_refresh'
    assert classes['600003'] == 'external_required'
    assert [item['symbol'] for item in plan_firm_items(plan)] == ['600002']
    assert plan_automatic_symbols(plan) >= {'600001', '600002'}


def test_shared_ready_turns_common_items_into_external_evidence(tmp_path):
    shared = {'governance': {'current': True, 'reason': '同日快照'},
              'industry_scale': {'current': True, 'reason': '同截点排名'}}
    plan = build_completion_plan_v2(details(), tmp_path / 'radar.db', DAY, pools={'dividend_value'}, shared=shared)
    assert plan['shared_stages'] == []
    common = [item for item in plan['items'] if item['group'] == 'common']
    assert common and all(item['evidence_class'] == 'external_required' for item in common)
    assert all(item['executable']['mode'] == 'none' for item in common)
