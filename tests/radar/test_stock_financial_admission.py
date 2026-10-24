"""八项盈利保守合并后的共同结果传播至两池风格。"""
from copy import deepcopy

import pytest

from radar.stocks.admission import evaluate_common_admission
from radar.stocks.financial_admission import merge_common_financial
from radar.stocks.style import evaluate_stock_styles
from tests.radar.test_stock_style import AS_OF, WINDOW, facts, group


@pytest.mark.parametrize('conflict', ['manual_failed', 'structured_failed', 'missing', 'historical'])
def test_merge_keeps_conflicts_and_propagates_common_to_styles(conflict):
    evidence = facts()
    if conflict == 'manual_failed':
        group(evidence, 'admission_profitability')['ttm']['parent_profit_yuan'] = '-1'
    manual = evaluate_common_admission('600900', evidence, AS_OF, market_window=WINDOW)
    numeric = [{'code': row['code'], 'outcome': 'passed'} for row in manual['checks']
               if row['code'].startswith(('annual_', 'ttm_'))]
    assert len(numeric) == 8
    if conflict == 'structured_failed':
        next(row for row in numeric if row['code'] == 'ttm_parent_profit')['outcome'] = 'failed'
    source = {'as_of': AS_OF.isoformat(), 'status': 'current', 'financial': {'financial_numeric_checks': numeric}}
    if conflict == 'missing':
        source['financial'] = None
    if conflict == 'historical':
        source.update(as_of='2026-10-14', status='historical')
    original = deepcopy(manual)
    merged = merge_common_financial(manual, source, AS_OF)
    assert manual == original
    unrelated = [row for row in merged['checks'] if not row.get('channel')]
    assert unrelated == [row for row in manual['checks'] if not row['code'].startswith(('annual_', 'ttm_'))]
    ttm = next(row for row in merged['checks'] if row['code'] == 'ttm_parent_profit')
    assert ttm['outcome'] == 'pending_verification'
    assert ttm['manual_check'] and ttm['structured_check']
    expected = 'failed' if conflict == 'manual_failed' else 'pending_verification'
    assert merged['common_status'] == expected
    styled = evaluate_stock_styles('600900', evidence, AS_OF, market_window=WINDOW, common_result=merged)
    for pool in styled['pools'].values():
        assert pool['approved_conditions_status'] == expected
        assert pool['admission_status'] == ('excluded' if expected == 'failed' else 'pending_verification')
    assert styled['formal_screening_enabled'] is False and styled['members_published'] == 0


@pytest.mark.parametrize(('historical', 'label'), [('passed', '通过'), ('failed', '不满足'), ('pending_verification', '待补')])
def test_historical_checks_show_date_and_original_outcome_only_as_reference(historical, label):
    evidence = facts()
    manual = evaluate_common_admission('600900', evidence, AS_OF, market_window=WINDOW)
    numeric = [{'code': row['code'], 'outcome': historical, 'reason': '历史样本'} for row in manual['checks']
               if row['code'].startswith(('annual_', 'ttm_'))]
    source = {'as_of': '2026-10-14', 'status': 'historical', 'financial': {'financial_numeric_checks': numeric}}
    merged = merge_common_financial(manual, source, AS_OF)
    checks = [row for row in merged['checks'] if row.get('channel') == 'structured_financial']
    assert len(checks) == 8 and all(row['outcome'] == 'pending_verification' for row in checks)
    for check in checks:
        assert check['reference_as_of'] == '2026-10-14'
        assert check['reference_check']['outcome'] == historical
        assert '2026-10-14' in check['reason'] and label in check['reason'] and '仅供参考' in check['reason']
    assert merged['common_status'] == 'pending_verification'
