"""财务建议到待审核规范组的来源与数值回归。"""

from datetime import date

import pytest

from radar.stocks.financial_facts import prepare_financial_facts

AS_OF = date(2026, 10, 15)


def proposal(year=2023, metric='parent_net_profit', amount='100', index=1, *, disclosed='2026-04-01'):
    url = f'https://static.cninfo.com.cn/{index}.pdf'
    sha = f'{index:064x}'
    fields = {'symbol': '600900', 'fiscal_year': year, 'metric': metric, 'amount_yuan': amount,
              'source_unit': '万元', 'currency': 'CNY', 'scope': 'reported_parent' if 'parent' in metric else 'consolidated',
              'source_url': url, 'disclosed_on': disclosed, 'page_number': 5, 'evidence_excerpt': '合成原文', 'status': 'matched_template'}
    origin = {'source_url': url, 'disclosed_on': disclosed, 'file_sha256': sha, 'status': 'matched_template'}
    return {'symbol': '600900', 'kind': 'financial_proposal', 'payload': {'fields': fields, 'origin': origin},
            'source_url': url, 'disclosed_on': disclosed, 'content_sha256': sha, 'confirmable': True,
            'evidence_id': f'{1000+index:064x}', 'latest_review': None, 'review_status': 'pending_verification'}


def facts():
    return [proposal(year, metric, index=index) for index, (year, metric) in enumerate(
        [(year, metric) for year in (2023, 2024, 2025) for metric in
         ('parent_net_profit', 'deducted_parent_net_profit', 'operating_cash_flow', 'revenue')], 1)]


def prepared(rows=None):
    return prepare_financial_facts('600900', facts() if rows is None else rows, AS_OF)


def by_kind(result, kind):
    return next(fact for fact in result['facts'] if fact['kind'] == kind)


def test_complete_annual_facts_still_pending_and_no_ttm_or_cash_scope_claim():
    result = prepared()
    profit = by_kind(result, 'admission_profitability')['payload']
    cash = by_kind(result, 'style_cash_flow')['payload']
    assert [row['year'] for row in profit['annual']] == [2023, 2024, 2025]
    assert profit['ttm'] is None
    assert cash['issuer_type'] is None
    assert cash['parent_profit_scope_reconciled'] is False
    assert all(row['parent_profit_yuan'] == '100' for row in profit['annual'])
    assert result['requires_review'] is True
    assert result['financial_verified'] is False
    assert result['formal_screening_enabled'] is False
    assert result['members_published'] == 0
    assert len(profit['_financial_dependencies']['inventory']) == 12
    assert profit['_financial_dependencies']['security'] is None
    assert profit['_financial_dependencies']['as_of'] == AS_OF.isoformat()
    assert all(row['review_id'] is None for row in profit['_financial_dependencies']['inventory'])


def test_equal_decimal_sources_merge_and_different_values_conflict():
    rows = [proposal(amount='1e2'), proposal(amount='100.00', index=2)]
    result = prepared(rows)
    profit = by_kind(result, 'admission_profitability')['payload']
    assert profit['annual'][0]['parent_profit_yuan'] == '100'
    assert len(profit['field_sources']['parent_profit_yuan:2023']) == 2
    rows.append(proposal(amount='101', index=3))
    profit = by_kind(prepared(rows), 'admission_profitability')['payload']
    assert profit['annual'][0]['parent_profit_yuan'] is None
    assert len(profit['field_sources']['parent_profit_yuan:2023']) == 3


def test_primary_pdf_is_real_latest_dependency_not_generated_json():
    rows = [proposal(year=2023, disclosed='2024-03-01'), proposal(year=2024, index=2, disclosed='2025-03-01')]
    result = by_kind(prepared(rows), 'admission_profitability')
    assert result['source_url'] == rows[1]['source_url']
    assert result['content_sha256'] == rows[1]['content_sha256']
    assert result['disclosed_on'] == '2025-03-01'


@pytest.mark.parametrize('amount', ['0', '-1', '-0', '0.00000000000000000001'])
def test_signs_and_precision_preserved(amount):
    result = by_kind(prepared([proposal(amount=amount)]), 'admission_profitability')
    from decimal import Decimal
    assert Decimal(result['payload']['annual'][0]['parent_profit_yuan']) == Decimal(amount)


@pytest.mark.parametrize('change', ['currency', 'scope', 'symbol', 'origin_url', 'origin_sha', 'field_sha',
                                    'future', 'annual_end', 'nan', 'extreme', 'unconfirmable', 'status'])
def test_invalid_proposal_does_not_become_normalized_fact(change):
    row = proposal()
    fields, origin = row['payload']['fields'], row['payload']['origin']
    if change == 'currency':
        fields['currency'] = 'USD'
    elif change == 'scope':
        fields['scope'] = 'consolidated'
    elif change == 'symbol':
        fields['symbol'] = '000333'
    elif change == 'origin_url':
        origin['source_url'] = 'https://static.cninfo.com.cn/other.pdf'
    elif change == 'origin_sha':
        origin['file_sha256'] = 'b'*64
    elif change == 'field_sha':
        fields['file_sha256'] = 'b'*64
    elif change in {'future', 'annual_end'}:
        day = '2027-01-01' if change == 'future' else '2023-12-31'
        fields['disclosed_on'] = origin['disclosed_on'] = row['disclosed_on'] = day
    elif change == 'nan':
        fields['amount_yuan'] = float('nan')
    elif change == 'extreme':
        fields['amount_yuan'] = '1e-999999999'
    elif change == 'unconfirmable':
        row['confirmable'] = False
    else:
        fields['status'] = 'pending_verification'
    assert prepared([row])['facts'] == []


@pytest.mark.parametrize('status', ['rejected', 'needs_evidence', 'expired'])
def test_explicit_negative_review_or_expiration_not_used_but_in_inventory(status):
    row = proposal()
    row['review_status'] = status
    result = prepared([row])
    assert result['facts'] == []
    # 仍保留上游集合，不能隐去后续驳回或新增建议。
    assert result['inventory'][0]['evidence_id'] == row['evidence_id']


def with_review(row, *, decision='confirmed', when='2026-10-14T00:00:00+00:00', review_id=1):
    row['review_status'] = decision
    row['latest_review'] = {'review_id': review_id, 'decision': decision, 'reviewed_at': when,
                            'symbol': row['symbol'], 'evidence_id': row['evidence_id']}
    return row


@pytest.mark.parametrize('change', ['future', 'naive', 'expired', 'wrong_evidence', 'wrong_company', 'missing_review_id', 'rejected'])
def test_invalid_or_expired_review_is_not_used(change):
    row = with_review(proposal())
    review = row['latest_review']
    if change == 'future':
        review['reviewed_at'] = '2027-01-01T00:00:00+00:00'
    elif change == 'naive':
        review['reviewed_at'] = '2026-10-01T00:00:00'
    elif change == 'expired':
        review['reviewed_at'] = '2026-01-01T00:00:00+00:00'
    elif change == 'wrong_evidence':
        review['evidence_id'] = 'e'*64
    elif change == 'wrong_company':
        review['symbol'] = '000333'
    elif change == 'missing_review_id':
        del review['review_id']
    else:
        review['decision'] = 'rejected'
    assert prepared([row])['facts'] == []


def test_security_issuer_source_and_review_are_bound_only_to_cash():
    security = with_review({'symbol': '600900', 'kind': 'admission_security', 'payload': {
        'exchange': 'SSE', 'security_type': 'ordinary_a', 'issuer_type': 'bank', 'a_listed_on': '2000-01-01'},
        'evidence_id': 'd'*64, 'confirmable': True, 'source_url': 'https://static.cninfo.com.cn/security.pdf',
        'content_sha256': 'e'*64, 'disclosed_on': '2026-10-01'}, review_id=100)
    result = prepared([*facts(), security])
    cash = by_kind(result, 'style_cash_flow')['payload']
    profit = by_kind(result, 'admission_profitability')['payload']
    assert cash['issuer_type'] == 'bank'
    assert cash['_financial_dependencies']['security'] == {'evidence_id': 'd'*64, 'review_id': 100}
    assert profit['_financial_dependencies']['security'] is None
    security['review_status'] = 'expired'
    cash = by_kind(prepared([*facts(), security]), 'style_cash_flow')['payload']
    assert cash['issuer_type'] is None
    assert cash['_financial_dependencies']['security'] is None


def test_all_raw_inventory_includes_unknown_rejected_other_year_and_unusable_metric():
    rows = facts()
    old = proposal(2022, index=100)
    rejected = with_review(proposal(index=101), decision='rejected', review_id=101)
    unsupported = proposal(metric='unsupported', index=102)
    result = prepared([*rows, old, rejected, unsupported])
    expected = sorted(row['evidence_id'] for row in [*rows, old, rejected, unsupported])
    for fact in result['facts']:
        assert [row['evidence_id'] for row in fact['payload']['_financial_dependencies']['inventory']] == expected
    assert next(row for row in result['inventory'] if row['evidence_id'] == rejected['evidence_id'])['review_id'] == 101


def test_original_ref_amount_and_normalized_annual_amount_are_separate():
    row = proposal(amount='1e2')
    profit = by_kind(prepared([row]), 'admission_profitability')['payload']
    ref = profit['field_sources']['parent_profit_yuan:2023'][0]
    assert ref['amount_yuan'] == '1e2'
    assert ref['canonical_amount_yuan'] == '100'
    assert ref['source_unit'] == '万元'
    assert ref['page_number'] == 5
    assert ref['evidence_excerpt'] == '合成原文'
    assert profit['annual'][0]['parent_profit_yuan'] == '100'


@pytest.mark.parametrize('change', ['url_credentials', 'url_ip', 'url_http', 'hash', 'origin_symbol', 'outer_symbol',
                                    'bool_year', 'out_year', 'bad_amount_type', 'bad_origin_type'])
def test_identity_and_bad_types_never_produce_fact(change):
    row = proposal()
    fields, origin = row['payload']['fields'], row['payload']['origin']
    if change.startswith('url_'):
        url = {'url_credentials': 'https://user:password@example.com/a.pdf', 'url_ip': 'https://127.0.0.1/a.pdf',
               'url_http': 'http://example.com/a.pdf'}[change]
        fields['source_url'] = origin['source_url'] = row['source_url'] = url
    elif change == 'hash':
        origin['file_sha256'] = row['content_sha256'] = 'no-sha'
    elif change == 'origin_symbol':
        origin['symbol'] = '000333'
    elif change == 'outer_symbol':
        row['payload']['symbol'] = '000333'
    elif change == 'bool_year':
        fields['fiscal_year'] = True
    elif change == 'out_year':
        fields['fiscal_year'] = 2022
    elif change == 'bad_amount_type':
        fields['amount_yuan'] = []
    else:
        row['payload']['origin'] = []
    assert prepared([row])['facts'] == []


def test_revenue_alone_is_summary_not_profit_or_cash_fact():
    result = prepared([proposal(metric='revenue')])
    assert result['facts'] == []
    assert result['annual_summary'][0]['revenue_yuan']['amount_yuan'] == '100'


def test_missing_year_is_unknown_and_comparative_source_keeps_own_fiscal_year():
    row = proposal(year=2023, disclosed='2026-04-01')
    profit = by_kind(prepared([row]), 'admission_profitability')['payload']
    assert profit['annual'][0]['year'] == 2023
    assert profit['annual'][0]['parent_profit_yuan'] == '100'
    assert profit['annual'][1]['parent_profit_yuan'] is None
    assert profit['annual'][1]['disclosed_on'] is None
    assert profit['ttm'] is None


def test_bad_inventory_cannot_omit_source_and_emit_saveable_facts():
    row = proposal(index=100)
    row['evidence_id'] = None
    assert prepared([*facts(), row])['facts'] == []


def test_preparation_does_not_change_inputs_or_use_supplier_approval():
    import copy

    rows = facts()
    before = copy.deepcopy(rows)
    for row in rows:
        row['payload']['financial_verified'] = True
    result = prepared(rows)
    assert result['financial_verified'] is False
    assert all(fact['payload']['ttm'] is None for fact in result['facts'] if fact['kind'] == 'admission_profitability')
    for row in rows:
        del row['payload']['financial_verified']
    assert rows == before


@pytest.mark.parametrize('amount', ['1e-100', '1e-1000'])
def test_normalized_scientific_values_remain_accepted_by_admission(amount):
    from radar.stocks.admission import _amount

    fact = by_kind(prepared([proposal(amount=amount)]), 'admission_profitability')
    normalized = fact['payload']['annual'][0]['parent_profit_yuan']
    assert len(normalized) <= 100
    assert _amount(normalized) == _amount(amount)
    assert _amount(normalized) is not None


def quarterly_rows():
    result = []
    for index, (year, metric) in enumerate([(year, metric) for year in (2025, 2026)
                                          for metric in ('parent_net_profit', 'deducted_parent_net_profit')], 101):
        row = proposal(year=year, metric=metric, amount='60' if year == 2025 else '70', index=index, disclosed='2026-08-31')
        row['kind'] = 'quarterly_financial_proposal'
        row['payload']['fields'].update(period_months=6, period_end=f'{year}-06-30', period_basis='year_to_date', comparison_basis='current')
        row['payload']['origin'].update(fiscal_year=year, period_months=6, period_end=f'{year}-06-30')
        result.append(row)
    return result


def test_quarterly_ttm_integrated_only_profit_v2_and_cash_stays_identical():
    original = prepared()
    result = prepared([*facts(), *quarterly_rows()])
    profit = by_kind(result, 'admission_profitability')['payload']
    cash = by_kind(result, 'style_cash_flow')
    assert profit['ttm']['parent_profit_yuan'] == '110'
    assert profit['ttm']['deducted_parent_profit_yuan'] == '110'
    assert profit['ttm']['latest_announced'] is False
    assert profit['_financial_dependencies']['version'] == 2
    assert len(profit['_financial_dependencies']['inventory']) == 16
    assert profit['ttm_summary']['requires_review'] is True
    assert len(profit['ttm_sources']['parent_profit_yuan']) == 3
    assert cash == by_kind(original, 'style_cash_flow')


def test_quarterly_profits_can_create_partial_fact_without_annual_profit():
    result = prepared(quarterly_rows())
    profit = by_kind(result, 'admission_profitability')['payload']
    assert all(row['parent_profit_yuan'] is None for row in profit['annual'])
    assert profit['ttm']['parent_profit_yuan'] is None
    assert profit['_financial_dependencies']['version'] == 2


def test_invalid_quarter_inventory_prevents_profit_but_not_annual_cash():
    row = quarterly_rows()[0]
    row['evidence_id'] = None
    result = prepared([*facts(), row])
    assert [fact['kind'] for fact in result['facts']] == ['style_cash_flow']


def test_coverage_alone_switches_profit_dependency_v2_without_fabricating_ttm():
    row = quarterly_rows()[0]
    row['kind'] = 'admission_reporting_coverage'
    row['payload'] = {'period_end': '2026-06-30', 'coverage_through': AS_OF.isoformat()}
    profit = by_kind(prepared([*facts(), row]), 'admission_profitability')['payload']
    assert profit['_financial_dependencies']['version'] == 2
    assert profit['ttm']['parent_profit_yuan'] is None
    assert profit['ttm']['latest_announced'] is False
