"""结构化初筛只验证数值，不能冒充股票池资格。"""

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from radar.stocks.structured_screen import screen_structured_financials

RESOURCE = Path(__file__).parents[1] / 'resources/radar/eastmoney-structured-financial-samples.json'


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('离线数值初筛禁止网络')
    monkeypatch.setattr('requests.sessions.Session.request', blocked)


@pytest.fixture
def sample():
    result = json.loads(RESOURCE.read_text(encoding='utf-8'))[0]
    for row in result['records']['profit']:
        row['PARENT_NETPROFIT'] = '10'
        row['DEDUCT_PARENT_NETPROFIT'] = '10'
    for row in result['records']['main']:
        row['PARENTNETPROFIT'] = '10'
        row['KCFJCXSYJLR'] = '10'
    for row in result['records']['cashflow']:
        row['NETCASH_OPERATE'] = '8'
    return result


def check(result, code):
    return next(row for row in result['financial_numeric_checks'] if row['code'] == code)


def row(sample, table, end):
    return next(value for value in sample['records'][table] if value['REPORT_DATE'].startswith(end))


@pytest.mark.parametrize('end,code', [('2023-12-31', 'annual_parent_profit_2023'),
                                    ('2026-06-30', 'ttm_parent_profit')])
def test_auxiliary_conflicting_revisions_cannot_hide_negative_alternative(sample, end, code):
    revision = deepcopy(row(sample, 'main', end))
    revision['PARENTNETPROFIT'] = '-1000'
    sample['records']['main'].append(revision)
    result = screen_structured_financials(sample)
    assert check(result, code)['outcome'] == 'pending_verification'
    assert result['financial_numeric_status'] == 'pending_verification'
    assert check(result, code)['cross_source_differences']


@pytest.mark.parametrize('index', range(4))
def test_real_samples_are_numeric_only(index):
    sample = json.loads(RESOURCE.read_text(encoding='utf-8'))[index]
    result = screen_structured_financials(sample)
    assert len(result['financial_numeric_checks']) == 8
    assert result['financial_verified'] is False
    assert result['formal_screening_enabled'] is False
    assert result['members_published'] == 0
    assert result['issuer_applicability'] == 'unverified'
    assert result['source_company_type'] is None
    assert 'admission_status' not in result
    assert result['uncovered_conditions']


def test_positive_values_cash_boundary_and_no_input_mutation(sample):
    original = deepcopy(sample)
    result = screen_structured_financials(sample)
    assert sample == original
    assert result['financial_numeric_status'] == 'passed'
    cash = result['cash_flow_reference']
    assert cash['ratio'] == '0.8'
    assert cash['reference_condition_status'] == 'passed'
    assert cash['applicability'] == 'requires_issuer_classification'
    assert cash['growth_role'] == 'quality_indicator_only'


@pytest.mark.parametrize('amount', ['0', '-1'])
def test_nonpositive_and_missing_are_separate_failure_wins(sample, amount):
    row(sample, 'profit', '2023-12-31')['PARENT_NETPROFIT'] = amount
    row(sample, 'main', '2023-12-31')['PARENTNETPROFIT'] = amount
    row(sample, 'profit', '2024-12-31')['DEDUCT_PARENT_NETPROFIT'] = None
    result = screen_structured_financials(sample)
    assert result['financial_numeric_status'] == 'failed'
    assert check(result, 'annual_parent_profit_2023')['outcome'] == 'failed'
    assert check(result, 'annual_deducted_profit_2024')['outcome'] == 'pending_verification'


def test_annual_cross_source_sign_change_is_pending_and_keeps_primary(sample):
    row(sample, 'main', '2023-12-31')['PARENTNETPROFIT'] = '-1'
    result = screen_structured_financials(sample)
    value = check(result, 'annual_parent_profit_2023')
    assert value['outcome'] == 'pending_verification'
    assert value['amount_yuan'] == '10'
    assert value['sources']
    assert value['cross_source_differences'][0]['main_sources']


def test_ttm_alternatives_can_change_sign_with_all_components_positive(sample):
    row(sample, 'profit', '2025-06-30')['PARENT_NETPROFIT'] = '19'
    row(sample, 'main', '2025-06-30')['PARENTNETPROFIT'] = '21'
    result = screen_structured_financials(sample)
    value = check(result, 'ttm_parent_profit')
    assert value['amount_yuan'] == '1'
    assert value['outcome'] == 'pending_verification'
    assert value['alternative_min_yuan'] == '-1'
    assert value['alternative_max_yuan'] == '1'


def test_all_cross_source_combinations_not_only_whole_table_ttm(sample):
    row(sample, 'main', '2026-06-30')['PARENTNETPROFIT'] = '1'
    row(sample, 'main', '2025-06-30')['PARENTNETPROFIT'] = '19'
    value = check(screen_structured_financials(sample), 'ttm_parent_profit')
    assert value['outcome'] == 'pending_verification'
    assert value['alternative_min_yuan'] == '-8'


def test_differences_that_do_not_change_decision_are_retained(sample):
    row(sample, 'main', '2026-06-30')['PARENTNETPROFIT'] = '11'
    value = check(screen_structured_financials(sample), 'ttm_parent_profit')
    assert value['outcome'] == 'passed'
    assert value['amount_yuan'] == '10'
    assert value['cross_source_differences']


def test_duplicate_revision_and_missing_latest_do_not_pass(sample):
    duplicate = deepcopy(row(sample, 'profit', '2023-12-31'))
    duplicate['PARENT_NETPROFIT'] = '12'
    sample['records']['profit'].append(duplicate)
    sample['records']['profit'].remove(row(sample, 'profit', '2026-06-30'))
    result = screen_structured_financials(sample)
    assert check(result, 'annual_parent_profit_2023')['outcome'] == 'pending_verification'
    assert check(result, 'ttm_parent_profit')['outcome'] == 'pending_verification'
    assert check(result, 'ttm_parent_profit')['amount_yuan'] is None


@pytest.mark.parametrize('company_type', ['1', '2', '3', '4', None])
def test_vendor_type_never_confers_issuer_applicability_or_cash_exclusion(sample, company_type):
    sample['company_type'] = company_type
    for value in sample['records']['cashflow']:
        value['NETCASH_OPERATE'] = '-100'
    result = screen_structured_financials(sample)
    assert result['source_company_type'] == company_type
    assert result['issuer_applicability'] == 'unverified'
    assert result['financial_numeric_status'] == 'passed'
    assert result['cash_flow_reference']['reference_condition_status'] == 'failed'
    assert result['cash_flow_reference']['dividend_applicability'] == 'pending_verification'


def test_untrusted_normalized_result_is_ignored_and_order_is_stable(sample):
    sample['normalized'] = {'financial_numeric_status': 'failed'}
    sample['financial_numeric_checks'] = [{'outcome': 'failed'}]
    expected = screen_structured_financials(sample)
    for values in sample['records'].values():
        values.reverse()
    assert screen_structured_financials(sample) == expected
    assert expected['financial_numeric_status'] == 'passed'


def test_exact_cash_boundary_is_not_rounded_to_pass(sample):
    row(sample, 'cashflow', '2023-12-31')['NETCASH_OPERATE'] = '7.999999999999999999999999999999999999999999999'
    assert screen_structured_financials(sample)['cash_flow_reference']['reference_condition_status'] == 'failed'


def test_cash_cross_source_denominator_can_change_reference_decision(sample):
    row(sample, 'main', '2023-12-31')['PARENTNETPROFIT'] = '11'
    cash = screen_structured_financials(sample)['cash_flow_reference']
    assert cash['reference_condition_status'] == 'pending_verification'
    assert cash['ratio'] == '0.8'


def test_ttm_nonpositive_is_numeric_failure_when_alternatives_agree(sample):
    row(sample, 'profit', '2025-06-30')['PARENT_NETPROFIT'] = '20'
    row(sample, 'main', '2025-06-30')['PARENTNETPROFIT'] = '20'
    value = check(screen_structured_financials(sample), 'ttm_parent_profit')
    assert value['amount_yuan'] == '0'
    assert value['outcome'] == 'failed'


def test_small_positive_ttm_survives_large_exponent_gap(sample):
    row(sample, 'profit', '2026-06-30')['PARENT_NETPROFIT'] = '1e-1000'
    row(sample, 'main', '2026-06-30')['PARENTNETPROFIT'] = '1e-1000'
    value = check(screen_structured_financials(sample), 'ttm_parent_profit')
    assert value['outcome'] == 'passed'
    assert Decimal(value['amount_yuan']) == Decimal('1e-1000')
    assert value['amount_yuan'] != '0'


def test_cash_missing_stays_unknown_while_profit_conditions_can_pass(sample):
    row(sample, 'cashflow', '2024-12-31')['NETCASH_OPERATE'] = None
    result = screen_structured_financials(sample)
    assert result['financial_numeric_status'] == 'passed'
    assert result['cash_flow_reference']['ratio'] is None
    assert result['cash_flow_reference']['reference_condition_status'] == 'pending_verification'
