"""从年报建议准备规范事实的真实SQLite和CLI往返。"""

import json
from datetime import date

import pytest
from click.testing import CliRunner

from radar.stocks.research_store import StockResearchStore
from stock_robot.stock_admission import evaluate_store


def seed(tmp_path):
    store=StockResearchStore(tmp_path/'research.db')
    store.import_companies([{'symbol':'600900','name':'合成财务公司'}],source_sha256='a'*64,source_label='仅合成测试',data_as_of=date(2026,10,15))
    for year in (2023,2024,2025):
        for metric,amount in (('parent_net_profit','100'),('deducted_parent_net_profit','90'),('operating_cash_flow','80')):
            company=store.get_company('600900')
            assert company is not None
            fields={'symbol':'600900','fiscal_year':year,'metric':metric,'amount_yuan':amount,'currency':'CNY','scope':'reported_parent' if 'parent' in metric else 'consolidated',
                    'source_unit':'元','status':'matched_template','page_number':5,'evidence_excerpt':f'合成{year} {metric} {amount}',
                    'source_url':f'https://example.com/{year}.pdf','disclosed_on':f'{year+1}-04-01'}
            sha=str(year%10)*64
            payload={'fields':fields,'origin':{'status':'matched_template','source_url':fields['source_url'],'disclosed_on':fields['disclosed_on'],'file_sha256':sha}}
            store.add_evidence('600900',kind='financial_proposal',payload=payload,source_url=fields['source_url'],disclosed_on=date(year+1,4,1),content_sha256=sha,expected_revision=company['revision'])
    return store


def invoke(store,*extra):
    from stock_robot.cli import main

    return CliRunner().invoke(main,['radar','stock-financial-facts-prepare','--db',str(store.db_path),'--symbol','600900','--as-of','2026-10-15',*extra])


def test_default_prepare_is_read_only_and_does_not_supply_ttm(tmp_path):
    store=seed(tmp_path)
    before=store.summary()
    result=invoke(store)
    assert result.exit_code==0,result.output
    packet=json.loads(result.output)
    assert len(packet['facts'])==2
    assert packet['requires_review'] is True
    assert packet['financial_verified'] is False
    profit=next(fact for fact in packet['facts'] if fact['kind']=='admission_profitability')
    cash=next(fact for fact in packet['facts'] if fact['kind']=='style_cash_flow')
    assert profit['payload']['ttm'] is None
    assert cash['payload']['parent_profit_scope_reconciled'] is False
    assert cash['payload']['issuer_type'] is None
    assert store.summary()==before


def test_saved_groups_need_review_then_only_annual_can_pass(tmp_path):
    store=seed(tmp_path)
    company=store.get_company('600900')
    assert company is not None
    result=invoke(store,'--save','--expected-revision',str(company['revision']))
    assert result.exit_code==0,result.output
    rows=store.list_evidence('600900')
    derived=[row for row in rows if row['kind'] in {'admission_profitability','style_cash_flow'}]
    assert len(derived)==2
    assert all(row['review_status']=='pending_verification' for row in derived)
    assert store.review_history('600900')==[]
    before=evaluate_store(store,date(2026,10,15),include_styles=True)['items'][0]
    assert next(check for check in before['checks'] if check['code']=='annual_parent_profit_2023')['outcome']=='pending_verification'
    profit=next(row for row in derived if row['kind']=='admission_profitability')
    current=store.get_company('600900')
    assert current is not None
    store.save_review('600900',profit['evidence_id'],expected_revision=current['revision'],decision='confirmed',reviewer='合成审核',reason='合成字段核对')
    after=evaluate_store(store,date(2026,10,15),include_styles=True)['items'][0]
    assert next(check for check in after['checks'] if check['code']=='annual_parent_profit_2023')['outcome']=='passed'
    assert next(check for check in after['checks'] if check['code']=='ttm_parent_profit')['outcome']=='pending_verification'
    assert after['admission_status']=='pending_verification'


@pytest.mark.parametrize('extra',[['--save'],['--save','--expected-revision','0']])
def test_missing_or_stale_revision_cannot_save(tmp_path,extra):
    store=seed(tmp_path)
    before=store.summary()
    result=invoke(store,*extra)
    assert result.exit_code!=0
    assert store.summary()==before


@pytest.mark.parametrize('suffix',['','-wal','-shm','-journal'])
def test_output_cannot_overwrite_database_family(tmp_path,suffix):
    store=seed(tmp_path)
    result=invoke(store,'--output',str(store.db_path)+suffix)
    assert result.exit_code!=0
    assert 'SQLite' in result.output


def test_new_command_registered(tmp_path):
    from stock_robot.cli import main

    result=CliRunner().invoke(main,['radar','stock-financial-facts-prepare','--help'])
    assert result.exit_code==0,result.output
    assert '--expected-revision' in result.output


def test_derived_cash_scope_cannot_be_automatically_checked(tmp_path):
    from radar.stocks.financial_facts import prepare_financial_facts
    from radar.stocks.research_store import StockResearchError

    store=seed(tmp_path)
    packet=prepare_financial_facts('600900',store.list_evidence('600900'),date(2026,10,15))
    cash=next(fact for fact in packet['facts'] if fact['kind']=='style_cash_flow')
    cash['payload']['parent_profit_scope_reconciled']=True
    company=store.get_company('600900')
    assert company is not None
    with pytest.raises(StockResearchError):
        store.add_financial_fact_batch('600900',[cash],expected_revision=company['revision'])
