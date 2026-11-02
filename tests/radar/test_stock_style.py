"""已确认风格条件的合成事实回归，不产生正式成员。"""

from copy import deepcopy
from datetime import date, timedelta

import pytest

from radar.stocks.admission import evaluate_common_admission
from radar.stocks.style import evaluate_stock_styles

AS_OF = date(2026, 10, 15)
WINDOW = [date(2026, 8, 1) + timedelta(days=index) for index in range(60)]
YEARS = [2023, 2024, 2025]


def fact(kind, payload):
    return {'symbol': '600900', 'kind': kind, 'payload': payload, 'source_url': 'https://static.cninfo.com.cn/test.pdf',
            'content_sha256': 'a' * 64, 'disclosed_on': '2026-10-01', 'confirmable': True,
            'evidence_id': kind, 'review_status': 'confirmed',
            'latest_review': {'decision': 'confirmed', 'reviewed_at': '2026-10-14T00:00:00+00:00'}}


def facts():
    rows = [fact('admission_security', {'exchange': 'SSE', 'security_type': 'ordinary_a', 'a_listed_on': '2020-01-01', 'issuer_type': 'ordinary'}),
            fact('admission_capitalization', {'currency': 'CNY', 'scope': 'company_all_shares', 'share_classes_complete': True,
                  'current': {'date': WINDOW[-1].isoformat(), 'amount_yuan': '30000000000'},
                  'daily': [{'date': day.isoformat(), 'amount_yuan': '30000000000'} for day in WINDOW]}),
            fact('admission_profitability', {'currency': 'CNY', 'scope': 'company_parent',
                  'annual': [{'year': year, 'parent_profit_yuan': '100', 'deducted_parent_profit_yuan': '100',
                              'disclosed_on': f'{year+1}-04-01'} for year in YEARS],
                  'ttm': {'period_end': '2026-06-30', 'disclosed_on': '2026-08-01', 'latest_announced': True,
                          'parent_profit_yuan': '100', 'deducted_parent_profit_yuan': '100'}}),
            fact('admission_audit', {'verdict': 'clear', 'basis': '合成审核', 'years': YEARS}),
            fact('admission_governance', {'verdict': 'clear', 'basis': '合成治理'}),
            fact('admission_leadership', {'verdict': 'clear', 'basis': '合成龙头', 'industry_level1': '工业', 'industry_level2': '电力', 'business_scope': '发电'}),
            fact('admission_financial_quality', {'verdict': 'clear', 'basis': '合成质量', 'issuer_type': 'ordinary'}),
            fact('style_cash_flow', {'issuer_type': 'ordinary', 'currency': 'CNY', 'scope': 'company_consolidated',
                  'parent_profit_scope_reconciled': True,
                  'annual': [{'year': year, 'operating_cash_flow_yuan': '80', 'disclosed_on': f'{year+1}-04-01'} for year in YEARS]}),
            fact('style_cash_review', {'issuer_type': 'ordinary', 'verdict': 'clear', 'basis': '合成现金专项'}),
            fact('style_dividend_history', {'currency': 'CNY', 'scope': 'company_all_shares', 'share_classes_complete': True,
                  'coverage_through': AS_OF.isoformat(), 'annual': [{'year': year, 'implemented_cash_yuan': amount,
                  'coverage_complete': True, 'latest_paid_on': f'{year+1}-05-01', 'disclosed_on': f'{year+1}-06-01'}
                  for year, amount in zip(YEARS, ['10', '8', '9'], strict=True)]}),
            fact('style_liquidity', {'currency': 'CNY', 'scope': 'a_share_raw_turnover',
                  'daily': [{'date': day.isoformat(), 'amount_yuan': '100000000'} for day in WINDOW]})]
    return rows


def group(rows, kind):
    return next(row['payload'] for row in rows if row['kind'] == kind)


def check(pool, code):
    return next(row for row in pool['checks'] if row['code'] == code)


def evaluate(rows=None):
    return evaluate_stock_styles('600900', facts() if rows is None else rows, AS_OF, market_window=WINDOW)


def test_all_approved_conditions_still_do_not_publish():
    result = evaluate()
    assert result['common_status'] == 'passed'
    assert result['formal_screening_enabled'] is False
    assert result['members_published'] == 0
    for pool in result['pools'].values():
        assert pool['approved_conditions_status'] == 'passed'
        assert pool['style_status'] == 'pending_configuration'
        assert pool['admission_status'] == 'pending_verification'
        assert pool['unconfigured_rules']
        assert pool['opportunity_status'] == 'unavailable'
    dividend = result['pools']['dividend_value']
    assert check(dividend, 'cash_ratio')['outcome'] == 'passed'
    assert check(dividend, 'cash_ratio')['values']['ratio'] == '0.8'
    assert dividend['branches']['dividend']['approved_conditions_status'] == 'passed'


@pytest.mark.parametrize('amount,dividend_outcome,growth_outcome', [('80', 'passed', 'passed'),
    ('79.999999999999999999', 'failed', 'attention'), ('80.000000000000000001', 'passed', 'passed'), ('-1', 'failed', 'attention')])
def test_cash_ratio_boundary_is_style_specific(amount, dividend_outcome, growth_outcome):
    rows = facts()
    group(rows, 'style_cash_flow')['annual'][0]['operating_cash_flow_yuan'] = amount
    result = evaluate(rows)
    red, growth = result['pools']['dividend_value'], result['pools']['growth_trend']
    assert check(red, 'cash_ratio')['outcome'] == dividend_outcome
    assert check(growth, 'cash_ratio')['outcome'] == growth_outcome
    assert growth['approved_conditions_status'] == 'passed'
    if dividend_outcome == 'failed':
        assert red['admission_status'] == 'excluded'
        assert growth['admission_status'] == 'pending_verification'


def test_zero_dividend_fails_only_dividend_branch():
    rows = facts()
    group(rows, 'style_dividend_history')['annual'][1]['implemented_cash_yuan'] = '0'
    result = evaluate(rows)['pools']['dividend_value']
    assert result['branches']['dividend']['approved_conditions_status'] == 'failed'
    assert result['branches']['value']['approved_conditions_status'] == 'passed'
    assert result['admission_status'] == 'pending_verification'


@pytest.mark.parametrize('amount,red,growth', [('100000000', 'passed', 'passed'), ('99999999.999999999', 'warning', 'observe')])
def test_liquidity_is_not_eligibility(amount, red, growth):
    rows = facts()
    for row in group(rows, 'style_liquidity')['daily']:
        row['amount_yuan'] = amount
    result = evaluate(rows)
    assert check(result['pools']['dividend_value'], 'liquidity')['outcome'] == red
    assert check(result['pools']['growth_trend'], 'liquidity')['outcome'] == growth
    assert all(pool['approved_conditions_status'] == 'passed' for pool in result['pools'].values())
    assert result['pools']['growth_trend']['opportunity_status'] == ('observe' if growth == 'observe' else 'unavailable')


def test_cash_review_does_not_cover_missing_or_bad_numbers():
    rows = facts()
    group(rows, 'style_cash_flow')['annual'][0]['operating_cash_flow_yuan'] = None
    red = evaluate(rows)['pools']['dividend_value']
    assert check(red, 'cash_ratio')['outcome'] == 'pending_verification'
    assert red['approved_conditions_status'] == 'pending_verification'


@pytest.mark.parametrize('mutation', [
    {'scope': 'company_parent'}, {'currency': 'USD'}, {'parent_profit_scope_reconciled': False},
    {'issuer_type': 'bank'},
])
def test_cash_scope_must_be_reconciled(mutation):
    rows = facts()
    group(rows, 'style_cash_flow').update(mutation)
    assert check(evaluate(rows)['pools']['dividend_value'], 'cash_ratio')['outcome'] == 'pending_verification'


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'extra', 'future', 'before_end', 'nan', 'extreme', 'bool'])
def test_cash_annual_integrity(change):
    rows = facts()
    annual = group(rows, 'style_cash_flow')['annual']
    if change == 'missing':
        annual.pop()
    elif change == 'duplicate':
        annual[-1] = deepcopy(annual[0])
    elif change == 'extra':
        annual.append({'year': 2022, 'operating_cash_flow_yuan': '80', 'disclosed_on': '2023-01-01'})
    elif change == 'future':
        annual[0]['disclosed_on'] = '2027-01-01'
    elif change == 'before_end':
        annual[0]['disclosed_on'] = '2023-12-31'
    else:
        annual[0]['operating_cash_flow_yuan'] = {'nan': float('nan'), 'extreme': '1e-999999999', 'bool': True}[change]
    assert check(evaluate(rows)['pools']['dividend_value'], 'cash_ratio')['outcome'] == 'pending_verification'


def test_denominator_cannot_be_supplied_by_cash_evidence():
    rows = facts()
    group(rows, 'style_cash_flow')['parent_profit_sum_yuan'] = '1'
    for row in group(rows, 'admission_profitability')['annual']:
        row['parent_profit_yuan'] = '1000'
    red = evaluate(rows)['pools']['dividend_value']
    assert check(red, 'cash_ratio')['outcome'] == 'failed'
    assert check(red, 'cash_ratio')['values']['parent_profit_sum_yuan'] == '3000'


def test_cash_and_profit_signs_are_distinct_and_sum_exactly():
    rows = facts()
    for row, amount in zip(group(rows, 'style_cash_flow')['annual'], ['1e18', '-1e18', '240'], strict=True):
        row['operating_cash_flow_yuan'] = amount
    result = evaluate(rows)
    assert result['common_status'] == 'passed'
    assert check(result['pools']['dividend_value'], 'cash_ratio')['values']['ratio'] == '0.8'
    assert check(result['pools']['dividend_value'], 'cash_ratio')['outcome'] == 'passed'


@pytest.mark.parametrize('issuer', ['bank', 'securities', 'insurance'])
def test_financial_cash_uses_type_matched_review_without_ratio(issuer):
    rows = facts()
    group(rows, 'admission_security')['issuer_type'] = issuer
    group(rows, 'admission_financial_quality')['issuer_type'] = issuer
    group(rows, 'style_cash_review')['issuer_type'] = issuer
    result = evaluate(rows)
    for pool in result['pools'].values():
        assert check(pool, 'cash_ratio')['outcome'] == 'not_applicable'
        assert pool['approved_conditions_status'] == 'passed'
    group(rows, 'style_cash_review')['issuer_type'] = 'ordinary'
    for pool in evaluate(rows)['pools'].values():
        assert check(pool, 'cash_review')['outcome'] == 'pending_verification'
        assert pool['approved_conditions_status'] == 'pending_verification'


def test_failed_cash_review_is_not_growth_hard_exclusion():
    rows = facts()
    group(rows, 'style_cash_review')['verdict'] = 'failed'
    result = evaluate(rows)
    assert result['pools']['dividend_value']['admission_status'] == 'excluded'
    growth = result['pools']['growth_trend']
    assert check(growth, 'cash_review')['outcome'] == 'attention'
    assert growth['admission_status'] == 'pending_verification'


@pytest.mark.parametrize('change', ['unknown', 'negative', 'future_paid', 'missing_paid', 'future_disclosed',
                                     'wrong_currency', 'coverage', 'coverage_through', 'duplicate', 'missing'])
def test_dividend_unknown_or_bad_evidence_never_fails_value_branch(change):
    rows = facts()
    payload = group(rows, 'style_dividend_history')
    row = payload['annual'][0]
    if change == 'unknown':
        row['implemented_cash_yuan'] = None
    elif change == 'negative':
        row['implemented_cash_yuan'] = '-1'
    elif change == 'future_paid':
        row['latest_paid_on'] = '2027-01-01'
    elif change == 'missing_paid':
        del row['latest_paid_on']
    elif change == 'future_disclosed':
        row['disclosed_on'] = '2027-01-01'
    elif change == 'wrong_currency':
        payload['currency'] = 'HKD'
    elif change == 'coverage':
        row['coverage_complete'] = False
    elif change == 'coverage_through':
        payload['coverage_through'] = '2026-10-14'
    elif change == 'duplicate':
        payload['annual'][-1] = deepcopy(row)
    else:
        payload['annual'].pop()
    red = evaluate(rows)['pools']['dividend_value']
    assert red['branches']['dividend']['approved_conditions_status'] == 'pending_verification'
    assert red['branches']['value']['approved_conditions_status'] == 'passed'
    assert red['approved_conditions_status'] == 'passed'


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'older', 'negative', 'none', 'currency', 'scope'])
def test_liquidity_must_match_complete_market_window(change):
    rows = facts()
    payload = group(rows, 'style_liquidity')
    daily = payload['daily']
    if change == 'missing':
        daily.pop()
    elif change == 'duplicate':
        daily[-1]['date'] = daily[0]['date']
    elif change == 'older':
        daily[0]['date'] = (WINDOW[0] - timedelta(days=1)).isoformat()
    elif change == 'negative':
        daily[0]['amount_yuan'] = '-1'
    elif change == 'none':
        daily[0]['amount_yuan'] = None
    elif change == 'currency':
        payload['currency'] = 'USD'
    else:
        payload['scope'] = 'adjusted_turnover'
    result = evaluate(rows)
    for pool in result['pools'].values():
        assert check(pool, 'liquidity')['outcome'] == 'unavailable'
        assert pool['approved_conditions_status'] == 'passed'


@pytest.mark.parametrize('change', ['conflict', 'expired', 'future', 'source', 'unreviewed'])
def test_style_source_authority_is_required(change):
    rows = facts()
    source = next(row for row in rows if row['kind'] == 'style_cash_flow')
    if change == 'conflict':
        other = deepcopy(source)
        other['payload']['annual'][0]['operating_cash_flow_yuan'] = '81'
        other['evidence_id'] = 'conflict'
        rows.append(other)
    elif change == 'expired':
        source['latest_review']['reviewed_at'] = '2026-07-01T00:00:00+00:00'
    elif change == 'future':
        source['disclosed_on'] = '2027-01-01'
    elif change == 'source':
        source['content_sha256'] = None
    else:
        source['review_status'] = 'pending_verification'
        source['financial_verified'] = True
    result = evaluate(rows)
    assert check(result['pools']['dividend_value'], 'cash_ratio')['outcome'] == 'pending_verification'
    assert result['pools']['growth_trend']['approved_conditions_status'] == 'pending_verification'


def test_common_hard_failure_excludes_both_pools_and_dividend_branches():
    rows = facts()
    group(rows, 'admission_profitability')['ttm']['parent_profit_yuan'] = '0'
    result = evaluate(rows)
    assert result['common_status'] == 'failed'
    for pool in result['pools'].values():
        assert pool['admission_status'] == 'excluded'
        assert pool['approved_conditions_status'] == 'failed'
    assert all(branch['admission_status'] == 'excluded' for branch in result['pools']['dividend_value']['branches'].values())


def test_unreasonable_dynamic_payloads_are_local_unknowns():
    rows = facts()
    group(rows, 'style_cash_flow')['annual'] = {'bad': True}
    group(rows, 'style_cash_review')['issuer_type'] = []
    group(rows, 'style_dividend_history')['annual'] = [None]
    group(rows, 'style_liquidity')['daily'] = [True]
    result = evaluate(rows)
    assert result['common_status'] == 'passed'
    assert all(pool['admission_status'] == 'pending_verification' for pool in result['pools'].values())


def test_cash_checks_trace_actual_profit_and_issuer_evidence():
    red = evaluate()['pools']['dividend_value']
    assert set(check(red, 'cash_ratio')['evidence_ids']) == {'style_cash_flow', 'admission_profitability', 'admission_security'}
    assert set(check(red, 'cash_review')['evidence_ids']) == {'style_cash_review', 'admission_security'}


def test_style_evaluation_does_not_modify_source_facts():
    rows = facts()
    before = deepcopy(rows)
    evaluate(rows)
    assert rows == before


def structured_source(status='current'):
    return {'symbol': '600900', 'evaluation_as_of': AS_OF.isoformat(), 'as_of': AS_OF.isoformat(),
            'status': status, 'run_id': 'synthetic', 'financial': {'symbol': '600900', 'as_of': AS_OF.isoformat(),
            'cash_flow_reference': {'reference_condition_status': 'passed', 'ratio': '0.8',
                'operating_cash_flow_sum_yuan': '240', 'parent_profit_sum_yuan': '300',
                'annual': [{'year': year, 'operating_cash_flow_yuan': '80', 'parent_profit_yuan': '100'} for year in YEARS]}}}


def structured_evaluate(rows, source):
    common = evaluate_common_admission(
        '600900', rows, AS_OF, market_window=WINDOW)
    common['financial_source'] = source
    return evaluate_stock_styles('600900', rows, AS_OF, market_window=WINDOW, common_result=common)


def test_structured_cash_uses_current_bound_source():
    result = structured_evaluate(facts(), structured_source())
    cash = check(result['pools']['dividend_value'], 'cash_ratio')
    assert cash['outcome'] == 'passed'
    assert cash['channel'] == 'structured_cash_flow'
    assert check(result['pools']['growth_trend'], 'cash_ratio')['outcome'] == 'passed'


@pytest.mark.parametrize('change', ['cash', 'profit', 'historical', 'invalid', 'missing', 'issuer'])
def test_structured_cash_requires_matching_audited_basis(change):
    rows = facts()
    source = structured_source()
    if change == 'cash':
        group(rows, 'style_cash_flow')['annual'][0]['operating_cash_flow_yuan'] = '79'
    elif change == 'profit':
        group(rows, 'admission_profitability')['annual'][0]['parent_profit_yuan'] = '101'
    elif change == 'issuer':
        group(rows, 'admission_security')['issuer_type'] = 'unknown'
    elif change == 'missing':
        source['financial']['cash_flow_reference']['annual'].pop()
    else:
        source['status'] = change
    result = structured_evaluate(rows, source)
    for pool in result['pools'].values():
        assert check(pool, 'cash_ratio')['outcome'] == 'pending_verification'


def test_structured_cash_below_threshold_is_only_growth_attention():
    rows = facts()
    source = structured_source()
    for row in group(rows, 'style_cash_flow')['annual']:
        row['operating_cash_flow_yuan'] = '79'
    reference = source['financial']['cash_flow_reference']
    for row in reference['annual']:
        row['operating_cash_flow_yuan'] = '79'
    reference.update(reference_condition_status='failed', ratio='0.79', operating_cash_flow_sum_yuan='237')
    result = structured_evaluate(rows, source)
    assert check(result['pools']['dividend_value'], 'cash_ratio')['outcome'] == 'failed'
    assert check(result['pools']['growth_trend'], 'cash_ratio')['outcome'] == 'attention'


def test_structured_cash_source_conflict_is_pending():
    source = structured_source()
    source['financial']['cash_flow_reference']['cross_source_differences'] = [{'metric': 'parent_profit_yuan'}]
    result = structured_evaluate(facts(), source)
    assert check(result['pools']['dividend_value'], 'cash_ratio')['outcome'] == 'pending_verification'


def test_structured_cash_nonpositive_annual_profit_remains_pending():
    rows = facts()
    group(rows, 'admission_profitability')['annual'][0]['parent_profit_yuan'] = '0'
    source = structured_source()
    source['financial']['cash_flow_reference']['annual'][0]['parent_profit_yuan'] = '0'
    result = structured_evaluate(rows, source)
    cash = check(result['pools']['dividend_value'], 'cash_ratio')
    assert cash['outcome'] == 'pending_verification'
    assert '分母' in cash['reason']


def test_structured_cash_financial_issuer_still_uses_special_review():
    rows = facts()
    group(rows, 'admission_security')['issuer_type'] = 'bank'
    group(rows, 'style_cash_review')['issuer_type'] = 'bank'
    result = structured_evaluate(rows, structured_source('invalid'))
    for pool in result['pools'].values():
        assert check(pool, 'cash_ratio')['outcome'] == 'not_applicable'
        assert check(pool, 'cash_review')['outcome'] == 'passed'
