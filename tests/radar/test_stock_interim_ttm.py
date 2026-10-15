"""季度累计到TTM的保守计算回归；均为合成资料。"""
from copy import deepcopy
from datetime import date
from typing import Any, cast

import pytest

from radar.stocks.interim_ttm import prepare_ttm

AS_OF = date(2026, 10, 15)


def proposal(year, amount, index, months=None, metric='parent_net_profit'):
    disclosed = '2026-08-31' if months else '2026-04-01'
    url, sha = f'https://static.cninfo.com.cn/{index}.pdf', f'{index:064x}'
    fields = {'symbol': '600900', 'fiscal_year': year, 'metric': metric, 'amount_yuan': amount,
              'currency': 'CNY', 'scope': 'reported_parent' if 'parent' in metric else 'consolidated',
              'status': 'matched_template', 'source_url': url, 'disclosed_on': disclosed}
    if months:
        fields.update(period_months=months, period_end=f'{year}-{months:02d}-' + ('30' if months in (6, 9) else '31'),
                      period_basis='year_to_date', comparison_basis='current')
    origin_period = {key: fields[key] for key in ('fiscal_year', 'period_months', 'period_end')} if months else {}
    return {'symbol': '600900', 'kind': 'quarterly_financial_proposal' if months else 'financial_proposal',
            'payload': {'fields': fields, 'origin': {'status': 'matched_template', 'source_url': url,
                        'disclosed_on': disclosed, 'file_sha256': sha, **origin_period}},
            'source_url': url, 'content_sha256': sha, 'disclosed_on': disclosed,
            'confirmable': True, 'evidence_id': f'{index+100:064x}', 'latest_review': None,
            'review_status': 'pending_verification'}


def rows():
    return [proposal(y, v, i+j*3, m, metric) for j, metric in enumerate(
        ['parent_net_profit', 'deducted_parent_net_profit']) for i, (y,v,m) in enumerate(
        [(2025, '100.10', None), (2026, '70.20', 6), (2025, '60.10', 6)], 1)]


def coverage(period='2026-06-30'):
    row = proposal(2026, '0', 20, 6)
    row['kind'] = 'admission_reporting_coverage'
    row['payload'] = {'period_end': period, 'coverage_through': AS_OF.isoformat(), 'report_types_complete': True,
                      'scope_comparable': True, 'basis': '合成审核依据'}
    row['review_status'] = 'confirmed'
    row['latest_review'] = {'review_id': 1, 'decision': 'confirmed', 'reviewed_at': '2026-10-15T10:00:00+08:00'}
    return row


def run(data=None):
    return prepare_ttm('600900', rows() if data is None else data, AS_OF)


def test_exact_math_research_only_and_complete_refs():
    result = run()
    item = result['metrics']['parent_profit_yuan']
    assert item['amount_yuan'] == '110.2'
    assert item['status'] == 'available'
    assert len(item['sources']) == len(item['components']) == 3
    assert item['sources'][0]['origin']['file_sha256']
    assert result['latest_announced'] is False
    assert result['requires_review'] is True
    assert result['formal_screening_enabled'] is False
    assert len(result['dependency_inventory']) == 6


def test_explicit_coverage_only_establishes_latest_when_both_profits_complete():
    assert run(rows()+[coverage()])['latest_announced'] is True
    assert run(rows()[:3]+[coverage()])['latest_announced'] is False


@pytest.mark.parametrize('change', ['scope_comparable', 'report_types_complete', 'basis', 'coverage_through', 'period_end', 'review'])
def test_bad_coverage_cannot_establish_latest(change):
    row = coverage()
    if change in ('scope_comparable', 'report_types_complete'):
        row['payload'][change] = False
    elif change == 'basis':
        row['payload'][change] = ''
    elif change == 'review':
        row['latest_review'] = None
    else:
        row['payload'][change] = '2026-03-31'
    assert run(rows()+[row])['latest_announced'] is False


def test_equal_sources_merge_conflict_not_chosen():
    duplicate = proposal(2025, '100.100', 22)
    assert run(rows()+[duplicate])['metrics']['parent_profit_yuan']['amount_yuan'] == '110.2'
    duplicate['payload']['fields']['amount_yuan'] = '101'
    assert run(rows()+[duplicate])['metrics']['parent_profit_yuan']['status'] == 'conflict'


@pytest.mark.parametrize('amount', ['0', '-5', '0.00000000000000000001'])
def test_no_positive_only_filter(amount):
    data = rows()
    data[0]['payload']['fields']['amount_yuan'] = amount
    assert run(data)['metrics']['parent_profit_yuan']['status'] == 'available'


def test_new_failed_document_blocks_old_complete_period():
    document = proposal(2026, '0', 23, 9)
    document['kind'] = 'quarterly_financial_document'
    document['confirmable'] = False
    document['payload'] = {'symbol': '600900', 'period_end': '2026-09-30', 'fiscal_year': 2026,
                           'period_months': 9, 'status': 'failed'}
    document['disclosed_on'] = '2026-10-10'
    result = run(rows()+[coverage(), document])
    assert result['period_end'] == '2026-09-30'
    assert result['metrics']['parent_profit_yuan']['amount_yuan'] is None
    assert result['latest_announced'] is False


@pytest.mark.parametrize('change', ['future', 'wrong_symbol', 'scope', 'currency', 'nan', 'bool', 'period_basis', 'origin_sha', 'rejected', 'future_review', 'expired'])
def test_invalid_raw_component_not_used(change):
    data = rows()
    row = data[1]
    if change == 'future':
        row['disclosed_on'] = row['payload']['fields']['disclosed_on'] = row['payload']['origin']['disclosed_on'] = '2027-01-01'
    elif change == 'wrong_symbol':
        row['payload']['fields']['symbol'] = '000333'
    elif change in ('scope', 'currency', 'period_basis'):
        row['payload']['fields'][change] = 'wrong'
    elif change in ('nan', 'bool'):
        row['payload']['fields']['amount_yuan'] = 'NaN' if change == 'nan' else True
    elif change == 'origin_sha':
        row['payload']['origin']['file_sha256'] = 'f'*64
    elif change == 'rejected':
        row['review_status'] = 'rejected'
    else:
        row['review_status'] = 'confirmed'
        row['latest_review'] = {'review_id': 2, 'decision': 'confirmed', 'reviewed_at':
                                '2026-10-16T00:00:00+08:00' if change == 'future_review' else '2026-01-01T00:00:00+08:00'}
    assert run(data)['metrics']['parent_profit_yuan']['amount_yuan'] is None


def test_coverage_conflict_and_input_immutability():
    first, second = coverage(), coverage()
    second['evidence_id'] = 'a'*64
    second['payload']['basis'] = '另一个矛盾判断'
    data = rows()+[first, second]
    original = deepcopy(data)
    assert run(data)['latest_announced'] is False
    assert data == original


def test_annual_only_invalid_input_and_no_fallback():
    assert run([rows()[0]])['period_end'] == '2025-12-31'
    assert prepare_ttm('bad', [], AS_OF)['unresolved']
    assert prepare_ttm('600900', [], cast(Any, 'bad'))['unresolved']


def test_newer_completed_annual_report_blocks_old_quarter_latest():
    data = [proposal(2024, '100', 31), proposal(2025, '70', 32, 9), proposal(2024, '60', 33, 9),
            proposal(2025, '100', 34), coverage('2025-09-30')]
    result = run(data)
    assert result['period_end'] == '2025-12-31'
    assert result['period_months'] == 12
    assert result['latest_announced'] is False
    assert all(item['amount_yuan'] is None for item in result['metrics'].values())


@pytest.mark.parametrize('change', ['symbol', 'future', 'malformed_period', 'source'])
def test_invalid_newer_document_cannot_change_known_period(change):
    doc = proposal(2026, '0', 23, 9)
    doc['kind'] = 'quarterly_financial_document'
    doc['payload'] = {'symbol': '600900', 'period_end': '2026-09-30', 'fiscal_year': 2026, 'period_months': 9}
    doc['disclosed_on'] = '2026-10-10'
    if change == 'symbol':
        doc['payload']['symbol'] = '000333'
    elif change == 'future':
        doc['disclosed_on'] = '2026-10-16'
    elif change == 'source':
        doc['source_url'] = 'http://localhost/x'
    else:
        doc['payload']['period_end'] = '2026-09-31'
    assert run(rows()+[doc])['period_end'] == '2026-06-30'


def test_inventory_review_identity_failure_is_explicit_and_no_latest():
    data = rows()+[coverage()]
    data[0]['latest_review'] = {'review_id': True}
    result = run(data)
    assert result['inventory_complete'] is False
    assert result['latest_announced'] is False


@pytest.mark.parametrize('months,disclosed', [(3, '2026-04-30'), (9, '2026-10-10')])
def test_quarterly_periods_must_use_cumulative_basis(months, disclosed):
    data = rows()
    for row in data:
        if row['kind'] == 'quarterly_financial_proposal':
            fields = row['payload']['fields']
            fields['period_months'] = months
            fields['period_end'] = f'{fields["fiscal_year"]}-{months:02d}-' + ('31' if months == 3 else '30')
            row['payload']['origin'].update({key: fields[key] for key in ('fiscal_year', 'period_months', 'period_end')})
            row['disclosed_on'] = fields['disclosed_on'] = row['payload']['origin']['disclosed_on'] = disclosed
    result = run(data)
    assert result['period_months'] == months
    assert result['metrics']['parent_profit_yuan']['amount_yuan'] == '110.2'


def test_latest_complete_profits_even_if_revenue_missing_and_confirmed_raw_allowed():
    data = rows()+[coverage()]
    data[0]['latest_review'] = {'review_id': 5, 'decision': 'confirmed', 'reviewed_at': '2026-10-01T12:00:00+08:00'}
    data[0]['review_status'] = 'confirmed'
    result = run(data)
    assert result['latest_announced'] is True
    assert result['metrics']['revenue_yuan']['status'] == 'missing'
    assert result['metrics']['parent_profit_yuan']['sources'][0]['review_id'] == 5


def test_repeated_evidence_identity_does_not_allow_latest():
    data = rows()+[coverage()]
    data.append(deepcopy(data[0]))
    assert run(data)['latest_announced'] is False


def test_new_failed_document_without_pdf_hash_still_blocks_fallback():
    document = {'symbol': '600900', 'kind': 'quarterly_financial_document', 'evidence_id': 'e'*64,
                'source_url': 'https://static.cninfo.com.cn/failed.pdf', 'content_sha256': None,
                'disclosed_on': '2026-10-10', 'confirmable': False, 'latest_review': None,
                'review_status': 'pending_verification', 'payload': {'symbol': '600900', 'fiscal_year': 2026,
                'period_months': 9, 'period_end': '2026-09-30', 'status': 'failed'}}
    result = run(rows()+[coverage(), document])
    assert result['period_end'] == '2026-09-30'
    assert result['latest_announced'] is False


@pytest.mark.parametrize('change', ['single_quarter', 'unknown', 'missing', 'origin_year', 'origin_month', 'origin_end'])
def test_comparison_identity_must_match_report_period(change):
    data = rows()+[coverage()]
    fields, origin = data[1]['payload']['fields'], data[1]['payload']['origin']
    if change in ('single_quarter', 'unknown'):
        fields['comparison_basis'] = change
    elif change == 'missing':
        del fields['comparison_basis']
    elif change == 'origin_year':
        origin['fiscal_year'] = 2025
        origin['period_end'] = '2025-06-30'
    elif change == 'origin_month':
        origin.update(period_months=3, period_end='2026-03-31')
    else:
        origin['period_end'] = '2026-06-29'
    result = run(data)
    assert result['metrics']['parent_profit_yuan']['amount_yuan'] is None
    assert result['latest_announced'] is False
