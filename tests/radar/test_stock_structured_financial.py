"""结构化财务映射使用真实裁切响应，不冒充PDF建议或已审核事实。"""

import json
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from radar.stocks.structured_financial import normalize_structured_financials

RESOURCE = Path(__file__).parents[1] / 'resources/radar/eastmoney-structured-financial-samples.json'


@pytest.fixture
def samples():
    return json.loads(RESOURCE.read_text(encoding='utf-8'))


@pytest.mark.parametrize('index', range(4))
def test_real_twenty_periods_preserve_source_and_compute_ttm(samples, index):
    source = samples[index]
    result = normalize_structured_financials(source)
    assert result['data_complete'] is True
    assert result['point_in_time_verified'] is False
    assert result['financial_verified'] is False
    assert result['members_published'] == 0
    assert len(result['annual']) == 3
    assert result['ttm']['period_end'] == '2026-06-30'
    for metric, key in [('parent_profit_yuan', 'PARENT_NETPROFIT'),
                        ('deducted_parent_profit_yuan', 'DEDUCT_PARENT_NETPROFIT')]:
        rows = {row['REPORT_DATE'][:10]: row for row in source['records']['profit']}
        expected = Decimal(str(rows['2025-12-31'][key])) + Decimal(str(rows['2026-06-30'][key])) - Decimal(str(rows['2025-06-30'][key]))
        assert Decimal(result['ttm']['metrics'][metric]['amount_yuan']) == expected
        assert len(result['ttm']['metrics'][metric]['components']) == 3
    ref = result['annual'][0]['metrics']['parent_profit_yuan']['sources'][0]
    assert ref['source_field'] == 'PARENT_NETPROFIT'
    assert ref['content_sha256'] == source['sources']['profit']['content_sha256']
    assert ref['source_unit'] == '元'
    assert ref['period_basis'] == 'year_to_date'
    assert 'page_number' not in ref
    assert 'review_status' not in ref
    assert ref['source_updated_at']
    assert ref['fetched_at']


def test_reconciles_main_against_fixed_primary_without_favorable_selection(samples):
    result = normalize_structured_financials(samples[0])
    differences = result['cross_source_differences']
    assert len(differences) == 2
    row = next(row for row in differences if row['period_end'] == '2026-06-30')
    assert row['metric'] == 'deducted_parent_profit_yuan'
    assert Decimal(row['difference_yuan']) == Decimal(967)
    assert row['primary_source'] == 'profit'


@pytest.mark.parametrize('field,value', [('PARENT_NETPROFIT', None), ('PARENT_NETPROFIT', True),
                                        ('PARENT_NETPROFIT', 'NaN'), ('CURRENCY', 'USD'),
                                        ('SECURITY_CODE', '600900'), ('NOTICE_DATE', None),
                                        ('UPDATE_DATE', None)])
def test_unknown_identity_currency_metadata_or_amount_never_becomes_zero(samples, field, value):
    sample = samples[0]
    target = next(row for row in sample['records']['profit'] if row['REPORT_DATE'].startswith('2025-12-31'))
    target[field] = value
    result = normalize_structured_financials(sample)
    cell = result['annual'][2]['metrics']['parent_profit_yuan']
    assert cell['amount_yuan'] is None
    assert result['data_complete'] is False
    assert result['ttm']['metrics']['parent_profit_yuan']['amount_yuan'] is None


def test_duplicate_revisions_do_not_choose_latest_or_positive_value(samples):
    sample = samples[0]
    target = deepcopy(next(row for row in sample['records']['profit'] if row['REPORT_DATE'].startswith('2025-12-31')))
    target['PARENT_NETPROFIT'] = '-1'
    target['UPDATE_DATE'] = '2026-10-18 12:00:00'
    sample['records']['profit'].append(target)
    result = normalize_structured_financials(sample)
    cell = result['annual'][2]['metrics']['parent_profit_yuan']
    assert cell['status'] == 'conflict'
    assert cell['amount_yuan'] is None
    assert len(cell['sources']) == 2


def test_missing_latest_quarter_not_replaced_by_older_period(samples):
    sample = samples[0]
    sample['records']['profit'] = [row for row in sample['records']['profit'] if not row['REPORT_DATE'].startswith('2026-06-30')]
    result = normalize_structured_financials(sample)
    assert result['ttm']['period_end'] == '2026-06-30'
    assert result['ttm']['metrics']['parent_profit_yuan']['status'] == 'missing'
    assert result['data_complete'] is False


def test_bank_operating_income_is_independent_of_total_operating_income(samples):
    result = normalize_structured_financials(samples[3])
    raw = next(row for row in samples[3]['records']['profit'] if row['REPORT_DATE'].startswith('2025-12-31'))
    assert Decimal(result['annual'][2]['metrics']['revenue_yuan']['amount_yuan']) == Decimal(str(raw['OPERATE_INCOME']))
    assert result['cash_flow_ratio']['applicability'] == 'requires_issuer_classification'


def test_source_metadata_and_endpoint_required(samples):
    samples[0]['sources']['profit']['source_url'] = 'https://example.com/report.pdf'
    result = normalize_structured_financials(samples[0])
    assert result['data_complete'] is False
    assert result['annual'][0]['metrics']['parent_profit_yuan']['amount_yuan'] is None


def test_revisions_after_reference_remain_labeled_current_capture(samples):
    sample = samples[0]
    row = next(row for row in sample['records']['profit'] if row['REPORT_DATE'].startswith('2025-12-31'))
    row['UPDATE_DATE'] = '2026-10-18 12:00:00'
    result = normalize_structured_financials(sample)
    assert result['point_in_time_verified'] is False
    assert result['annual'][2]['metrics']['parent_profit_yuan']['sources'][0]['updated_after_reference'] is True


def test_input_untouched_and_output_stable_under_row_order(samples):
    original = deepcopy(samples[0])
    expected = normalize_structured_financials(original)
    assert original == samples[0]
    for rows in samples[0]['records'].values():
        rows.reverse()
    assert normalize_structured_financials(samples[0]) == expected


def test_bad_cutoff_is_rejected(samples):
    samples[0]['as_of'] = date(2026, 10, 17)
    with pytest.raises(ValueError):
        normalize_structured_financials(samples[0])


def test_ttm_preserves_tiny_positive_residual_within_existing_numeric_budget(samples):
    sample = samples[0]
    for row in sample['records']['profit']:
        end = row['REPORT_DATE'][:10]
        if end in ('2025-12-31', '2025-06-30'):
            row['PARENT_NETPROFIT'] = '1'
        if end == '2026-06-30':
            row['PARENT_NETPROFIT'] = '1e-1000'
    result = normalize_structured_financials(sample)
    assert Decimal(result['ttm']['metrics']['parent_profit_yuan']['amount_yuan']) == Decimal('1e-1000')
    assert len(result['ttm']['metrics']['parent_profit_yuan']['amount_yuan']) <= 100
