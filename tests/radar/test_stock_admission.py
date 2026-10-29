"""共同准入纯计算测试；所有审核事实为明确标识的合成数据。"""

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta

import pytest

from radar.stocks.admission import evaluate_common_admission

AS_OF = date(2026, 10, 15)
WINDOW = [date(2026, 8, 1) + timedelta(days=i) for i in range(60)]


def fact(kind, payload):
    return {'symbol': '600900', 'kind': 'admission_' + kind, 'payload': payload,
            'source_url': 'https://static.cninfo.com.cn/test.pdf', 'content_sha256': 'a' * 64,
            'disclosed_on': '2026-10-01', 'confirmable': True, 'review_status': 'confirmed',
            'evidence_id': kind, 'latest_review': {'decision': 'confirmed', 'reviewed_at': '2026-10-14T00:00:00+00:00'}}


def facts():
    return [fact('security', {'exchange': 'SSE', 'security_type': 'ordinary_a', 'a_listed_on': '2020-02-29', 'issuer_type': 'ordinary'}),
            fact('capitalization', {'currency': 'CNY', 'scope': 'company_all_shares', 'share_classes_complete': True,
                 'current': {'date': WINDOW[-1].isoformat(), 'amount_yuan': '20000000000'},
                 'daily': [{'date': day.isoformat(), 'amount_yuan': '20000000000'} for day in WINDOW]}),
            fact('profitability', {'currency': 'CNY', 'scope': 'company_parent', 'annual': [
                {'year': year, 'parent_profit_yuan': '1', 'deducted_parent_profit_yuan': '1', 'disclosed_on': f'{year+1}-04-01'}
                for year in (2023, 2024, 2025)], 'ttm': {'period_end': '2026-06-30', 'disclosed_on': '2026-08-01',
                 'parent_profit_yuan': '1', 'deducted_parent_profit_yuan': '1', 'latest_announced': True}}),
            fact('audit', {'verdict': 'clear', 'basis': '合成核验', 'years': [2023, 2024, 2025]}),
            fact('governance', {'verdict': 'clear', 'basis': '合成治理专项核验'}),
            fact('leadership', {'verdict': 'clear', 'basis': '合成龙头证据', 'industry_level1': '工业',
                 'industry_level2': '电力', 'business_scope': '水力发电'}),
            fact('financial_quality', {'verdict': 'clear', 'basis': '合成财务专项核验', 'issuer_type': 'ordinary'})]


def checks(result):
    return {row['code']: row for row in result['checks']}


def test_all_common_passed_is_not_admission():
    result = evaluate_common_admission('600900', facts(), AS_OF, market_window=WINDOW)
    assert result['common_status'] == 'passed'
    assert result['formal_screening_enabled'] is False
    assert result['admission_status'] == 'pending_verification'
    assert result['rule_version'] == 'common-approved-2026-10-29-v3'
    assert checks(result)['capitalization_current']['values']['amount_yuan'] == '20000000000'


def test_exact_decimal_capitalization_and_independent_failure():
    rows = facts()
    rows[1]['payload']['current']['amount_yuan'] = '19999999999.999999999'
    rows[1]['payload']['daily'].pop()
    result = evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW)
    assert checks(result)['capitalization_current']['outcome'] == 'failed'
    assert checks(result)['capitalization_average_60d']['outcome'] == 'pending_verification'
    assert result['common_status'] == 'failed'


@pytest.mark.parametrize('value', ['0', '-1'])
@pytest.mark.parametrize('field', ['parent_profit_yuan', 'deducted_parent_profit_yuan'])
def test_annual_profit_failure_cannot_be_overridden(value, field):
    rows = facts()
    rows[2]['payload']['annual'][0][field] = value
    rows[2]['payload']['verdict'] = 'clear'
    assert evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW)['common_status'] == 'failed'


def test_no_annual_substitution_for_ttm():
    rows = facts()
    del rows[2]['payload']['ttm']
    result = evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW)
    assert checks(result)['ttm_parent_profit']['outcome'] == 'pending_verification'
    assert checks(result)['annual_parent_profit_2025']['outcome'] == 'passed'


@pytest.mark.parametrize('mutation', [
    {'review_status': 'expired'}, {'confirmable': False}, {'source_url': 'http://example.com'},
    {'content_sha256': None}, {'symbol': '000333'}, {'disclosed_on': '2026-10-16'},
    {'latest_review': {'decision': 'confirmed', 'reviewed_at': '2026-07-17T00:00:00+00:00'}},
    {'latest_review': {'decision': 'confirmed', 'reviewed_at': '2026-10-16T00:00:00+00:00'}},
    {'latest_review': {'decision': 'confirmed', 'reviewed_at': '2026-10-14T00:00:00'}},
])
def test_unverified_or_expired_group_is_unknown(mutation):
    rows = facts()
    rows[4].update(mutation)
    result = evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW)
    assert checks(result)['governance']['outcome'] == 'pending_verification'


def test_conflict_does_not_select_favorable_payload():
    rows = facts()
    bad = deepcopy(rows[4])
    bad['payload']['verdict'] = 'failed'
    bad['evidence_id'] = 'conflicting'
    rows.append(bad)
    result = evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW)
    assert checks(result)['governance']['outcome'] == 'pending_verification'
    assert set(checks(result)['governance']['evidence_ids']) == {'governance', 'conflicting'}


def test_financial_institution_cannot_use_ordinary_quality():
    rows = facts()
    rows[0]['payload']['issuer_type'] = 'bank'
    result = evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW)
    assert checks(result)['financial_quality']['outcome'] == 'pending_verification'


def test_listing_leap_anniversary():
    rows = facts()
    rows[0]['payload']['a_listed_on'] = '2020-02-29'
    assert checks(evaluate_common_admission('600900', rows, date(2023, 2, 27)))['listing_age']['outcome'] == 'pending_verification'
    # 将审核与原文日期移到目标截点前，独立验证周年规则。
    rows[0]['disclosed_on'] = '2023-02-01'
    rows[0]['latest_review']['reviewed_at'] = '2023-02-01T00:00:00+00:00'
    assert checks(evaluate_common_admission('600900', rows, date(2023, 2, 27)))['listing_age']['outcome'] == 'failed'
    assert checks(evaluate_common_admission('600900', rows, date(2023, 2, 28)))['listing_age']['outcome'] == 'passed'


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'earlier', 'negative', 'missing_amount', 'none', 'wrong_date'])
def test_capitalization_requires_exact_market_window(change):
    rows = facts()
    daily = rows[1]['payload']['daily']
    window = list(WINDOW)
    if change == 'missing':
        daily.pop()
    elif change == 'duplicate':
        daily[-1]['date'] = daily[-2]['date']
    elif change == 'earlier':
        daily[0]['date'] = (WINDOW[0] - timedelta(days=1)).isoformat()
    elif change == 'negative':
        daily[0]['amount_yuan'] = '-1'
    elif change == 'missing_amount':
        del daily[0]['amount_yuan']
    elif change == 'none':
        window = None
    elif change == 'wrong_date':
        window[-1] = window[-2]
    result = checks(evaluate_common_admission('600900', rows, AS_OF, market_window=window))
    assert result['capitalization_average_60d']['outcome'] == 'pending_verification'


@pytest.mark.parametrize('group,key,value', [
    (1, 'currency', 'USD'), (1, 'scope', 'a_shares'), (1, 'share_classes_complete', 'true'),
    (2, 'currency', 'USD'), (2, 'scope', 'ordinary_shareholders'),
    (0, 'issuer_type', []), (0, 'exchange', []), (0, 'security_type', []),
])
def test_wrong_currency_scope_or_dynamic_type(group, key, value):
    rows = facts()
    rows[group]['payload'][key] = value
    assert evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW)['common_status'] == 'pending_verification'


@pytest.mark.parametrize('value', [None, True, 'NaN', 'Infinity', '1e100', [], {}, ''])
def test_invalid_profit_never_becomes_zero_or_pass(value):
    rows = facts()
    rows[2]['payload']['annual'][0]['parent_profit_yuan'] = value
    result = checks(evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW))
    assert result['annual_parent_profit_2023']['outcome'] == 'pending_verification'
    assert 'values' not in result['annual_parent_profit_2023']


@pytest.mark.parametrize('change', ['duplicate', 'future', 'before_end', 'missing'])
def test_annual_period_integrity(change):
    rows = facts()
    annual = rows[2]['payload']['annual']
    if change == 'duplicate':
        annual.append(deepcopy(annual[0]))
    elif change == 'future':
        annual[0]['disclosed_on'] = '2027-01-01'
    elif change == 'before_end':
        annual[0]['disclosed_on'] = '2023-12-31'
    else:
        annual.pop(0)
    result = checks(evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW))
    assert result['annual_parent_profit_2023']['outcome'] == 'pending_verification'


@pytest.mark.parametrize('field,value', [('parent_profit_yuan', '0'), ('deducted_parent_profit_yuan', '-1')])
def test_ttm_independent_zero_negative_fail(field, value):
    rows = facts()
    rows[2]['payload']['ttm'][field] = value
    result = evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW)
    assert result['common_status'] == 'failed'


@pytest.mark.parametrize('field,value', [('period_end', '2026-10-16'), ('disclosed_on', '2026-01-01'),
                                        ('latest_announced', False), ('parent_profit_yuan', 'NaN')])
def test_bad_ttm_is_unknown(field, value):
    rows = facts()
    rows[2]['payload']['ttm'][field] = value
    result = checks(evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW))
    assert result['ttm_parent_profit']['outcome'] == 'pending_verification'


def test_independent_profit_failure_despite_other_unknown_amount():
    rows = facts()
    rows[2]['payload']['annual'][1]['parent_profit_yuan'] = None
    rows[2]['payload']['annual'][1]['deducted_parent_profit_yuan'] = '-1'
    result = evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW)
    assert result['common_status'] == 'failed'
    assert checks(result)['annual_parent_profit_2024']['outcome'] == 'pending_verification'
    assert checks(result)['annual_deducted_profit_2024']['outcome'] == 'failed'


def test_identical_payload_sources_merge_and_audit_years_required():
    rows = facts()
    duplicate = deepcopy(rows[4])
    duplicate['evidence_id'] = 'second_source'
    rows.append(duplicate)
    rows[3]['payload']['years'] = [2024, 2025]
    result = checks(evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW))
    assert result['governance']['outcome'] == 'passed'
    assert set(result['governance']['evidence_ids']) == {'governance', 'second_source'}
    assert result['audit']['outcome'] == 'pending_verification'


@pytest.mark.parametrize('index', [3, 4, 5, 6])
def test_specialist_failed_is_hard_failure(index):
    rows = facts()
    rows[index]['payload']['verdict'] = 'failed'
    result = evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW)
    assert result['common_status'] == 'failed'
    assert result['admission_status'] == 'excluded'
    assert result['formal_screening_enabled'] is False


def test_unrelated_hints_and_dynamic_records_never_authorize():
    rows = [None, [], {'kind': ['admission_security']}, {'kind': 'research_hint', 'financial_verified': True}]
    result = evaluate_common_admission('600900', rows, AS_OF)
    assert result['common_status'] == 'pending_verification'
    assert all(row['outcome'] == 'pending_verification' for row in result['checks'])


@pytest.mark.parametrize('field,value,expected', [('exchange', 'HKEX', 'failed'), ('security_type', 'etf', 'failed'),
    ('security_type', 'unknown', 'pending_verification'), ('security_type', '', 'pending_verification'),
    ('exchange', None, 'pending_verification'), ('exchange', 'unknown', 'pending_verification')])
def test_explicit_excluded_security_vs_unknown(field, value, expected):
    rows = facts()
    rows[0]['payload'][field] = value
    result = checks(evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW))
    assert result['security']['outcome'] == expected


def test_average_failure_survives_missing_current():
    rows = facts()
    del rows[1]['payload']['current']
    rows[1]['payload']['daily'][0]['amount_yuan'] = '19999999999.999999999'
    result = evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW)
    assert result['common_status'] == 'failed'
    assert checks(result)['capitalization_current']['outcome'] == 'pending_verification'
    assert checks(result)['capitalization_average_60d']['outcome'] == 'failed'


def test_review_exact_ninety_days_and_today_end_boundary():
    rows = facts()
    end = datetime(2026, 10, 15, 15, 59, 59, 999999, tzinfo=UTC)
    rows[4]['latest_review']['reviewed_at'] = (end - timedelta(days=90)).isoformat()
    assert checks(evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW))['governance']['outcome'] == 'pending_verification'
    rows[4]['latest_review']['reviewed_at'] = (end - timedelta(days=90) + timedelta(microseconds=1)).isoformat()
    assert checks(evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW))['governance']['outcome'] == 'passed'
    rows[4]['latest_review']['reviewed_at'] = end.isoformat()
    assert checks(evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW))['governance']['outcome'] == 'passed'


def test_extreme_decimal_exponent_is_unknown_without_crashing():
    rows = facts()
    rows[2]['payload']['annual'][0]['parent_profit_yuan'] = '1e1000000'
    result = checks(evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW))
    assert result['annual_parent_profit_2023']['outcome'] == 'pending_verification'


def test_nonfinite_field_does_not_hide_independent_failure():
    rows = facts()
    rows[2]['payload']['annual'][0]['parent_profit_yuan'] = float('nan')
    rows[2]['payload']['annual'][0]['deducted_parent_profit_yuan'] = '-1'
    result = evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW)
    assert result['common_status'] == 'failed'
    assert checks(result)['annual_parent_profit_2023']['outcome'] == 'pending_verification'
    assert checks(result)['annual_deducted_profit_2023']['outcome'] == 'failed'


def test_approved_302_security_code_is_supported():
    rows = facts()
    for row in rows:
        row['symbol'] = '302001'
    rows[0]['payload']['exchange'] = 'SZSE'
    result = evaluate_common_admission('302001', rows, AS_OF, market_window=WINDOW)
    assert result['common_status'] == 'passed'
    assert checks(result)['security']['outcome'] == 'passed'


@pytest.mark.parametrize('current_amount,last_daily_amount', [
    ('20000000000', '30000000000'), ('19999999999', '20000000000'),
])
def test_current_and_last_daily_capitalization_conflict_is_unknown(current_amount, last_daily_amount):
    rows = facts()
    rows[1]['payload']['current']['amount_yuan'] = current_amount
    rows[1]['payload']['daily'][-1]['amount_yuan'] = last_daily_amount
    result = evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW)
    assert result['common_status'] == 'pending_verification'
    assert checks(result)['capitalization_current']['outcome'] == 'pending_verification'
    assert checks(result)['capitalization_average_60d']['outcome'] == 'pending_verification'
    assert '冲突' in checks(result)['capitalization_current']['reason']


def test_equal_capitalization_decimal_representations_are_consistent():
    rows = facts()
    rows[1]['payload']['current']['amount_yuan'] = '2e10'
    rows[1]['payload']['daily'][-1]['amount_yuan'] = '20000000000.0'
    assert evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW)['common_status'] == 'passed'


@pytest.mark.parametrize('value', ['1e-999999999', '-1e-999999999', '0e-999999999'])
def test_extreme_negative_exponent_is_unknown(value):
    rows = facts()
    rows[2]['payload']['annual'][0]['parent_profit_yuan'] = value
    result = checks(evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW))
    assert result['annual_parent_profit_2023']['outcome'] == 'pending_verification'


@pytest.mark.parametrize('value', ['-0', '-0.0', '-0E+10'])
def test_negative_zero_profit_fails_strict_positive_condition(value):
    rows = facts()
    rows[2]['payload']['annual'][0]['parent_profit_yuan'] = value
    result = checks(evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW))
    assert result['annual_parent_profit_2023']['outcome'] == 'failed'


def test_technical_exponent_budget_does_not_add_profit_floor():
    rows = facts()
    rows[2]['payload']['annual'][0]['parent_profit_yuan'] = '1e-100'
    result = checks(evaluate_common_admission('600900', rows, AS_OF, market_window=WINDOW))
    assert result['annual_parent_profit_2023']['outcome'] == 'passed'
