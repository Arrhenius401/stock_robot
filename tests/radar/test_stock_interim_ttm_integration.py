"""年度/季度原文、覆盖审核和规范TTM的真实SQLite往返，均为合成资料。"""
from copy import deepcopy
from datetime import date, datetime

import pytest

from radar.stocks.financial_facts import prepare_financial_facts
from radar.stocks.research_store import StockResearchError, StockResearchStore

AS_OF = date(2026, 10, 15)


def raw(year, metric, amount, months=None):
    url = f'https://static.cninfo.com.cn/synthetic-{year}-{months}-{metric}.pdf'
    fields = {'symbol': '600900', 'fiscal_year': year, 'metric': metric, 'amount_yuan': amount,
              'currency': 'CNY', 'scope': 'reported_parent' if 'parent' in metric else 'consolidated',
              'status': 'matched_template', 'source_url': url, 'disclosed_on': '2026-08-31' if months else '2026-04-01'}
    origin = {'status': 'matched_template', 'source_url': url, 'disclosed_on': fields['disclosed_on'], 'file_sha256': 'a'*64}
    if months:
        fields.update(period_months=months, period_end=f'{year}-{months:02d}-30', period_basis='year_to_date', comparison_basis='current')
        origin.update(fiscal_year=year, period_months=months, period_end=fields['period_end'])
    return {'fields': fields, 'origin': origin}


def packet(quarters=False):
    company = {'symbol': '600900', 'name': '合成TTM测试公司', 'financial_proposals': [], 'quarterly_proposals': []}
    for year in (2023, 2024, 2025):
        for metric in ('parent_net_profit', 'deducted_parent_net_profit', 'operating_cash_flow'):
            company['financial_proposals'].append(raw(year, metric, '100'))
    if quarters:
        for year in (2025, 2026):
            for metric in ('parent_net_profit', 'deducted_parent_net_profit'):
                company['quarterly_proposals'].append(raw(year, metric, '60' if year == 2025 else '70', 6))
    return {'companies': [company]}


def seed(tmp_path, quarters=False):
    store = StockResearchStore(tmp_path/'ttm.db', clock=lambda: datetime.fromisoformat('2026-10-15T10:00:00+08:00'))
    store.import_review_packet(packet(quarters), source_sha256='b'*64, source_label='合成报告', data_as_of=AS_OF)
    return store


def revision(store):
    company = store.get_company('600900')
    assert company is not None
    return company['revision']


def prepare(store):
    return prepare_financial_facts('600900', store.list_evidence('600900'), AS_OF)


def fact(result, kind='admission_profitability'):
    return next(item for item in result['facts'] if item['kind'] == kind)


def save(store, facts=None):
    return store.add_financial_fact_batch('600900', prepare(store)['facts'] if facts is None else facts,
                                           expected_revision=revision(store))


def review(store, identifier):
    return store.save_review('600900', identifier, expected_revision=revision(store), decision='confirmed',
                             reviewer='合成审核员', reason='合成来源和口径核对')


def add_coverage(store):
    item = store.add_evidence('600900', kind='admission_reporting_coverage',
        payload={'period_end': '2026-06-30', 'coverage_through': AS_OF.isoformat(), 'report_types_complete': True,
                 'scope_comparable': True, 'basis': '合成全报告范围和可比口径已核对'},
        source_url='https://static.cninfo.com.cn/coverage.pdf', disclosed_on=date(2026, 8, 31),
        content_sha256='c'*64, expected_revision=revision(store))
    return item['evidence_id']


def by_id(store, identifier):
    return next(item for item in store.list_evidence('600900') if item['evidence_id'] == identifier)


def test_v1_annual_profit_becomes_stale_on_quarters_cash_remains_current(tmp_path):
    store = seed(tmp_path)
    saved = save(store)
    old_profit = next(by_id(store, identifier) for identifier in saved['evidence_ids']
                      if by_id(store, identifier)['kind'] == 'admission_profitability')
    cash = next(by_id(store, identifier) for identifier in saved['evidence_ids']
               if by_id(store, identifier)['kind'] == 'style_cash_flow')
    review(store, old_profit['evidence_id'])
    review(store, cash['evidence_id'])
    store.import_review_packet(packet(True), source_sha256='d'*64, source_label='合成季度', data_as_of=AS_OF)
    assert by_id(store, old_profit['evidence_id'])['lineage_status'] == 'stale'
    assert by_id(store, old_profit['evidence_id'])['review_status'] == 'needs_evidence'
    assert by_id(store, cash['evidence_id'])['lineage_status'] == 'current'
    assert by_id(store, cash['evidence_id'])['review_status'] == 'confirmed'
    with pytest.raises(StockResearchError):
        review(store, old_profit['evidence_id'])


def test_v2_roundtrip_coverage_review_changes_lineage_and_requires_new_fact_review(tmp_path):
    store = seed(tmp_path, True)
    prepared = prepare(store)
    assert fact(prepared)['payload']['ttm']['parent_profit_yuan'] == '110'
    assert fact(prepared)['payload']['ttm']['latest_announced'] is False
    saved = save(store)
    old_profit = next(by_id(store, identifier) for identifier in saved['evidence_ids']
                      if by_id(store, identifier)['kind'] == 'admission_profitability')
    review(store, old_profit['evidence_id'])
    identifier = add_coverage(store)
    assert fact(prepare(store))['payload']['ttm']['latest_announced'] is False
    review(store, identifier)
    prepared = prepare(store)
    assert fact(prepared)['payload']['ttm']['latest_announced'] is True
    assert by_id(store, old_profit['evidence_id'])['lineage_status'] == 'stale'
    saved = save(store, [fact(prepared)])
    assert saved['facts_added'] == 1
    latest = by_id(store, saved['evidence_ids'][0])
    assert latest['review_status'] == 'pending_verification'
    assert latest['lineage_status'] == 'current'
    review(store, latest['evidence_id'])
    assert by_id(store, latest['evidence_id'])['review_status'] == 'confirmed'
    assert store.summary()['reviews'] == 3


@pytest.mark.parametrize('change', ['latest', 'amount', 'formula', 'version', 'inventory', 'source'])
def test_generic_add_and_batch_cannot_forge_computed_ttm(tmp_path, change):
    store = seed(tmp_path, True)
    prepared = fact(prepare(store))
    forged = deepcopy(prepared)
    payload = forged['payload']
    if change == 'latest':
        payload['ttm']['latest_announced'] = True
    elif change == 'amount':
        payload['ttm']['parent_profit_yuan'] = '999'
    elif change == 'formula':
        payload['ttm_summary']['metrics']['parent_profit_yuan']['formula'] = 'current_ytd'
    elif change == 'version':
        payload['_financial_dependencies']['version'] = 1
    elif change == 'inventory':
        payload['_financial_dependencies']['inventory'].pop()
    else:
        payload['ttm_sources']['parent_profit_yuan'][0]['source_url'] = 'https://example.com/forged.pdf'
    before = store.summary()
    with pytest.raises(StockResearchError):
        save(store, [forged])
    with pytest.raises(StockResearchError):
        store.add_evidence('600900', kind=forged['kind'], payload=payload, source_url=forged['source_url'],
            disclosed_on=date.fromisoformat(forged['disclosed_on']), content_sha256=forged['content_sha256'],
            expected_revision=revision(store))
    assert store.summary() == before


def test_real_packet_import_failed_new_quarter_without_hash_blocks_previous_latest(tmp_path):
    store = seed(tmp_path, True)
    review(store, add_coverage(store))
    assert fact(prepare(store))['payload']['ttm']['latest_announced'] is True
    saved = save(store)
    document = {'symbol': '600900', 'source_url': 'https://static.cninfo.com.cn/new-failed.pdf',
                'disclosed_on': '2026-10-10', 'file_sha256': None, 'status': 'source_error',
                'fiscal_year': 2026, 'period_months': 9, 'period_end': '2026-09-30'}
    store.import_review_packet({'companies': [{'symbol': '600900', 'quarterly_documents': [document]}]},
        source_sha256='e'*64, source_label='合成失败新季度', data_as_of=AS_OF)
    failed = next(row for row in store.list_evidence('600900') if row['kind'] == 'quarterly_financial_document')
    assert failed['content_sha256'] is None
    assert failed['confirmable'] is False
    computed = fact(prepare(store))['payload']['ttm']
    assert computed['period_end'] == '2026-09-30'
    assert computed['parent_profit_yuan'] is None
    assert computed['latest_announced'] is False
    profit = next(by_id(store, identifier) for identifier in saved['evidence_ids']
                  if by_id(store, identifier)['kind'] == 'admission_profitability')
    assert profit['lineage_status'] == 'stale'


@pytest.mark.parametrize('target', ['coverage', 'quarterly'])
def test_upstream_review_version_change_stales_only_profit(tmp_path, target):
    store = seed(tmp_path, True)
    coverage_id = add_coverage(store)
    review(store, coverage_id)
    saved = save(store)
    profit = next(by_id(store, identifier) for identifier in saved['evidence_ids']
                  if by_id(store, identifier)['kind'] == 'admission_profitability')
    cash = next(by_id(store, identifier) for identifier in saved['evidence_ids']
                if by_id(store, identifier)['kind'] == 'style_cash_flow')
    if target == 'coverage':
        review(store, coverage_id)
    else:
        raw_row = next(row for row in store.list_evidence('600900') if row['kind'] == 'quarterly_financial_proposal')
        store.save_review('600900', raw_row['evidence_id'], expected_revision=revision(store), decision='rejected',
                           reviewer='合成审核员', reason='合成复核发现口径不匹配')
    assert by_id(store, profit['evidence_id'])['lineage_status'] == 'stale'
    assert by_id(store, cash['evidence_id'])['lineage_status'] == 'current'
    with pytest.raises(StockResearchError):
        review(store, profit['evidence_id'])
