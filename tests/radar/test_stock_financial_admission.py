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
    assert unrelated == [row for row in manual['checks'] if row['code'] != 'audit' and not row['code'].startswith(('annual_', 'ttm_'))]
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


def _audit_source_for_test():
    return {'symbol': '600900', 'as_of': AS_OF.isoformat(), 'evaluation_as_of': AS_OF.isoformat(),
            'status': 'current', 'financial': {'financial_numeric_checks': []},
            'audit_clues': {'symbol': '600900', 'as_of': AS_OF.isoformat(), 'status': 'available',
                            'annual': [{'year': year, 'status': 'available', 'opinion': '标准无保留意见',
                                        'other_opinion': None, 'updated_after_cutoff': False,
                                        'security_code': '600900', 'secucode': '600900.SH',
                                        'report_type': '年报', 'report_date_name': f'{year}年报'}
                                       for year in (2023, 2024, 2025)],
                            'source': {'source_url': 'https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/xjllbAjaxNew',
                                       'content_sha256': 'a'*64, 'version': 'eastmoney-structured-financial-v1',
                                       'as_of': AS_OF.isoformat(), 'fetched_at': '2026-10-15T08:00:00+08:00',
                                       'request_params': {'code': 'SH600900', 'reportType': '1', 'reportDateType': '0',
                                                          'companyType': '4', 'dates': '2023-12-31,2024-12-31,2025-12-31'}}}}


def test_structured_audit_accepts_only_bound_standard_opinions_without_faking_review():
    evidence = [row for row in facts() if row['kind'] != 'admission_audit']
    manual = evaluate_common_admission('600900', evidence, AS_OF, market_window=WINDOW)
    result = merge_common_financial(manual, _audit_source_for_test(), AS_OF)
    check = next(row for row in result['checks'] if row['code'] == 'audit')
    assert check['outcome'] == 'passed' and check['channel'] == 'structured_audit'
    assert check['manual_check']['outcome'] == 'pending_verification'
    assert result['admission_status'] == 'pending_verification'


def test_structured_audit_accepts_duplicate_standard_other_opinion():
    evidence = [row for row in facts() if row['kind'] != 'admission_audit']
    manual = evaluate_common_admission('600900', evidence, AS_OF, market_window=WINDOW)
    source = _audit_source_for_test()
    source['audit_clues']['annual'][0]['other_opinion'] = '标准无保留意见'
    result = merge_common_financial(manual, source, AS_OF)
    assert next(row for row in result['checks'] if row['code'] == 'audit')['outcome'] == 'passed'


def test_structured_audit_emphasis_is_pending_and_serious_opinion_fails():
    evidence = [row for row in facts() if row['kind'] != 'admission_audit']
    manual = evaluate_common_admission('600900', evidence, AS_OF, market_window=WINDOW)
    emphasis = _audit_source_for_test()
    emphasis['audit_clues']['annual'][0]['other_opinion'] = '带强调事项段的无保留意见'
    assert next(row for row in merge_common_financial(manual, emphasis, AS_OF)['checks']
                if row['code'] == 'audit')['outcome'] == 'pending_verification'
    serious = _audit_source_for_test()
    serious['audit_clues']['annual'][0]['opinion'] = '保留意见'
    assert next(row for row in merge_common_financial(manual, serious, AS_OF)['checks']
                if row['code'] == 'audit')['outcome'] == 'failed'


@pytest.mark.parametrize('case', ['wrong_symbol', 'wrong_cutoff', 'historical', 'nonstandard', 'extra', 'late_update', 'missing',
                                  'missing_params', 'wrong_exchange', 'wrong_report_type', 'unrequested_year', 'wrong_row_period'])
def test_structured_audit_rejects_unbound_or_incomplete_results(case):
    evidence = [row for row in facts() if row['kind'] != 'admission_audit']
    manual = evaluate_common_admission('600900', evidence, AS_OF, market_window=WINDOW)
    source = _audit_source_for_test()
    if case == 'wrong_symbol': source['audit_clues']['symbol'] = '600036'
    if case == 'wrong_cutoff': source['evaluation_as_of'] = '2026-10-14'
    if case == 'historical': source['status'] = 'historical'
    if case == 'nonstandard': source['audit_clues']['annual'][0]['opinion'] = '保留意见'
    if case == 'extra': source['audit_clues']['annual'][0]['other_opinion'] = '保留意见'
    if case == 'late_update': source['audit_clues']['annual'][0]['updated_after_cutoff'] = True
    if case == 'missing': source['audit_clues']['annual'].pop()
    if case == 'missing_params': source['audit_clues']['source'].pop('request_params')
    if case == 'wrong_exchange': source['audit_clues']['source']['request_params']['code'] = 'SZ600900'
    if case == 'wrong_report_type': source['audit_clues']['source']['request_params']['reportType'] = '2'
    if case == 'unrequested_year': source['audit_clues']['source']['request_params']['dates'] = '2023-12-31,2024-12-31'
    if case == 'wrong_row_period': source['audit_clues']['annual'][0]['report_type'] = '半年报'
    result = merge_common_financial(manual, source, AS_OF)
    expected = 'failed' if case in {'nonstandard', 'extra'} else 'pending_verification'
    assert next(row for row in result['checks'] if row['code'] == 'audit')['outcome'] == expected


def test_structured_audit_never_overrides_conflicting_manual_failure():
    evidence = facts()
    group(evidence, 'admission_audit')['verdict'] = 'failed'
    manual = evaluate_common_admission('600900', evidence, AS_OF, market_window=WINDOW)
    result = merge_common_financial(manual, _audit_source_for_test(), AS_OF)
    audit = next(row for row in result['checks'] if row['code'] == 'audit')
    assert audit['outcome'] == 'pending_verification'
    assert audit['manual_check']['outcome'] == 'failed'
    assert result['common_status'] == 'pending_verification'


@pytest.mark.parametrize('status', ['unavailable', 'invalid', 'not_attempted', 'version_mismatch'])
def test_missing_audit_projection_cannot_fall_back_to_manual_pass(status):
    manual = evaluate_common_admission('600900', facts(), AS_OF, market_window=WINDOW)
    assert next(row for row in manual['checks'] if row['code'] == 'audit')['outcome'] == 'passed'
    source = {'symbol': '600900', 'as_of': AS_OF.isoformat(), 'evaluation_as_of': AS_OF.isoformat(),
              'status': status, 'financial': None}
    result = merge_common_financial(manual, source, AS_OF)
    assert next(row for row in result['checks'] if row['code'] == 'audit')['outcome'] == 'pending_verification'


def test_invalid_audit_request_preserves_manual_failure():
    evidence = facts()
    group(evidence, 'admission_audit')['verdict'] = 'failed'
    manual = evaluate_common_admission('600900', evidence, AS_OF, market_window=WINDOW)
    source = _audit_source_for_test()
    source['audit_clues']['source']['request_params']['code'] = 'SZ600900'
    result = merge_common_financial(manual, source, AS_OF)
    assert next(row for row in result['checks'] if row['code'] == 'audit')['outcome'] == 'failed'
    assert result['common_status'] == 'failed'
