"""规范财务组的原子待审保存及上游变化失效。"""

from copy import deepcopy
from datetime import UTC, date, datetime, timedelta

import pytest

from radar.stocks.research_store import (
    StockResearchError,
    StockResearchStore,
    StockRevisionConflict,
)

NOW = datetime(2026, 10, 15, tzinfo=UTC)


def revision(store):
    company = store.get_company('600900')
    assert company is not None
    return company['revision']


def prepare(tmp_path, clock=lambda: NOW):
    store = StockResearchStore(tmp_path/'facts.db', clock=clock)
    store.import_companies([{'symbol':'600900','name':'合成公司'}],source_sha256='a'*64,source_label='合成测试',data_as_of=date(2026,10,15))
    raw = add_source(store)
    fact = make_fact(store, raw)
    return store, fact, raw


def add_source(store, sha='b'*64, amount='100'):
    fields={'symbol':'600900','fiscal_year':2023,'metric':'parent_net_profit','amount_yuan':amount,'currency':'CNY','scope':'reported_parent',
            'source_url':'https://example.com/source.pdf','disclosed_on':'2024-04-01','status':'matched_template','page_number':5,'source_unit':'元','evidence_excerpt':f'合成原文{amount}'}
    payload={'fields':fields,'origin':{'source_url':fields['source_url'],'disclosed_on':fields['disclosed_on'],'file_sha256':sha,'status':'matched_template'}}
    result=store.add_evidence('600900',kind='financial_proposal',payload=payload,source_url=fields['source_url'],disclosed_on=date(2024,4,1),content_sha256=sha,expected_revision=revision(store))
    return next(row for row in store.list_evidence('600900') if row['evidence_id']==result['evidence_id'])


def make_fact(store, raw):
    from radar.stocks.financial_facts import prepare_financial_facts

    packet=prepare_financial_facts('600900',store.list_evidence('600900'),date(2026,10,15))
    return next(fact for fact in packet['facts'] if fact['kind']=='admission_profitability')


def confirm(store, evidence_id):
    store.save_review('600900', evidence_id, expected_revision=revision(store), decision='confirmed', reviewer='合成审核', reason='合成原文核验')


def test_batch_is_pending_idempotent_and_atomic_revision(tmp_path):
    store, fact, _=prepare(tmp_path)
    assert len(fact['payload']['annual'])==3
    assert fact['payload']['annual'][1]['parent_profit_yuan'] is None
    before=revision(store)
    first=store.add_financial_fact_batch('600900',[fact],expected_revision=before)
    assert first['revision']==before+1
    assert first['facts_added']==1
    rows=[row for row in store.list_evidence('600900') if row['kind']=='admission_profitability']
    assert len(rows)==1
    assert rows[0]['review_status']=='pending_verification'
    assert store.review_history('600900')==[]
    second=store.add_financial_fact_batch('600900',[fact],expected_revision=revision(store))
    assert second['facts_added']==0
    assert second['revision']==first['revision']


def test_upstream_review_change_invalidates_confirmed_group(tmp_path):
    store, fact, raw=prepare(tmp_path)
    store.add_financial_fact_batch('600900',[fact],expected_revision=revision(store))
    derived=next(row for row in store.list_evidence('600900') if row['kind']=='admission_profitability')
    confirm(store,derived['evidence_id'])
    assert next(row for row in store.list_evidence('600900') if row['kind']=='admission_profitability')['review_status']=='confirmed'
    confirm(store,raw['evidence_id'])
    stale=next(row for row in store.list_evidence('600900') if row['kind']=='admission_profitability')
    assert stale['review_status']=='needs_evidence'
    assert stale['lineage_status']=='stale'
    with pytest.raises(StockRevisionConflict):
        confirm(store,derived['evidence_id'])


def test_new_financial_source_invalidates_but_unrelated_hint_does_not(tmp_path):
    store, fact, _=prepare(tmp_path)
    store.add_financial_fact_batch('600900',[fact],expected_revision=revision(store))
    derived=next(row for row in store.list_evidence('600900') if row['kind']=='admission_profitability')
    confirm(store,derived['evidence_id'])
    store.add_evidence('600900',kind='research_hint',payload={'text':'无关线索'},source_url='https://example.com/hint.pdf',disclosed_on=date(2026,10,15),content_sha256='c'*64,expected_revision=revision(store))
    assert next(row for row in store.list_evidence('600900') if row['kind']=='admission_profitability')['review_status']=='confirmed'
    add_source(store,'d'*64)
    assert next(row for row in store.list_evidence('600900') if row['kind']=='admission_profitability')['lineage_status']=='stale'


@pytest.mark.parametrize('mutation',['inventory','origin','other_kind','duplicate','missing_dependencies','cross_company'])
def test_invalid_batch_cannot_leave_first_fact_saved(tmp_path,mutation):
    store,fact,_=prepare(tmp_path)
    bad=deepcopy(fact)
    if mutation != 'duplicate':
        bad['kind']='style_cash_flow'
        bad['payload']['issuer_type']=None
        original=bad['payload']['field_sources'].pop('parent_profit_yuan:2023')
        bad['payload']['field_sources']['operating_cash_flow_yuan:2023']=original
    if mutation=='inventory':
        bad['payload']['_financial_dependencies']['inventory']=[]
    elif mutation=='origin':
        bad['content_sha256']='c'*64
    elif mutation=='other_kind':
        bad['kind']='admission_governance'
    elif mutation=='duplicate':
        pass
    elif mutation=='missing_dependencies':
        del bad['payload']['_financial_dependencies']
    else:
        bad['payload']['_financial_dependencies']['inventory'][0]['evidence_id']='d'*64
    before=store.summary()
    with pytest.raises(StockResearchError):
        store.add_financial_fact_batch('600900',[fact,bad],expected_revision=revision(store))
    assert store.summary()==before


def test_general_evidence_entry_cannot_bypass_dependency_check(tmp_path):
    store,fact,_=prepare(tmp_path)
    fact['payload']['_financial_dependencies']['inventory']=[]
    with pytest.raises(StockResearchError):
        store.add_evidence('600900',kind=fact['kind'],payload=fact['payload'],source_url=fact['source_url'],disclosed_on=date(2024,4,1),content_sha256=fact['content_sha256'],expected_revision=revision(store))


def test_natural_source_review_expiry_invalidates_newer_group_review(tmp_path):
    current=[NOW]
    store,_,raw=prepare(tmp_path,lambda:current[0])
    confirm(store,raw['evidence_id'])
    raw=next(row for row in store.list_evidence('600900') if row['evidence_id']==raw['evidence_id'])
    fact=make_fact(store,raw)
    store.add_financial_fact_batch('600900',[fact],expected_revision=revision(store))
    current[0]=NOW+timedelta(days=80)
    derived=next(row for row in store.list_evidence('600900') if row['kind']=='admission_profitability')
    confirm(store,derived['evidence_id'])
    current[0]=NOW+timedelta(days=90)
    assert next(row for row in store.list_evidence('600900') if row['kind']=='admission_profitability')['lineage_status']=='stale'


def test_raw_field_mapping_cannot_be_relabelled_as_deducted_profit(tmp_path):
    store,fact,_=prepare(tmp_path)
    original=fact['payload']['field_sources'].pop('parent_profit_yuan:2023')
    fact['payload']['field_sources']['deducted_parent_profit_yuan:2023']=original
    with pytest.raises(StockResearchError):
        store.add_financial_fact_batch('600900',[fact],expected_revision=revision(store))


@pytest.mark.parametrize('mutation',['amount','ttm','disclosed','cash_scope'])
def test_derived_values_cannot_be_changed_while_reusing_valid_sources(tmp_path,mutation):
    store,fact,_=prepare(tmp_path)
    if mutation=='amount':
        fact['payload']['annual'][0]['parent_profit_yuan']='999999'
    elif mutation=='ttm':
        fact['payload']['ttm']={'parent_profit_yuan':'999999','latest_announced':True}
    elif mutation=='disclosed':
        fact['payload']['annual'][0]['disclosed_on']='2024-05-01'
    else:
        fact['payload']['parent_profit_scope_reconciled']=True
    before=store.summary()
    with pytest.raises(StockResearchError):
        store.add_evidence('600900',kind=fact['kind'],payload=fact['payload'],source_url=fact['source_url'],disclosed_on=date.fromisoformat(fact['disclosed_on']),content_sha256=fact['content_sha256'],expected_revision=revision(store))
    assert store.summary()==before


def test_conflicting_source_cannot_be_replaced_by_favorable_value(tmp_path):
    store,_,raw=prepare(tmp_path)
    add_source(store,sha='e'*64,amount='90')
    fact=make_fact(store,raw)
    assert fact['payload']['annual'][0]['parent_profit_yuan'] is None
    fact['payload']['annual'][0]['parent_profit_yuan']='100'
    with pytest.raises(StockResearchError):
        store.add_financial_fact_batch('600900',[fact],expected_revision=revision(store))
