"""季度审核包入库及命令边界，不自动确认最新报告。"""

import json
from datetime import date

import pytest
from click.testing import CliRunner

from radar.stocks.research_store import StockResearchError, StockResearchStore
from stock_robot.interim_documents import (
    build_interim_packet,
    discover_interims,
    extract_interims,
    prepare_interim_packet,
)


def extracted():
    url='https://static.cninfo.com.cn/synthetic.pdf'
    fields={'symbol':'600900','fiscal_year':2026,'period_months':3,'period_end':'2026-03-31','period_basis':'year_to_date',
            'comparison_basis':'current','metric':'parent_net_profit','amount_yuan':'100','source_unit':'元','currency':'CNY',
            'scope':'reported_parent','source_url':url,'disclosed_on':'2026-04-30','page_number':1,'evidence_excerpt':'合成归母100','status':'matched_template'}
    return {'items':[{'symbol':'600900','source_url':url,'disclosed_on':'2026-04-30','file_sha256':'a'*64,
                      'text_sha256':'b'*64,'status':'matched_template','reasons':[], 'fiscal_year':2026,'period_months':3,
                      'period_end':'2026-03-31','financial_proposals':[fields]}]}


def test_packet_import_preserves_unreviewed_quarterly_kinds(tmp_path):
    packet=build_interim_packet(extracted(),date(2026,10,15))
    store=StockResearchStore(tmp_path/'research.db')
    store.import_review_packet(packet,source_sha256='c'*64,source_label='合成季度资料',data_as_of=date(2026,10,15))
    rows=store.list_evidence('600900')
    proposal=next(row for row in rows if row['kind']=='quarterly_financial_proposal')
    document=next(row for row in rows if row['kind']=='quarterly_financial_document')
    assert proposal['review_status']=='pending_verification'
    assert proposal['payload']['fields']['period_basis']=='year_to_date'
    assert document['payload']['period_end']=='2026-03-31'
    assert store.review_history('600900')==[]
    assert packet['financial_verified'] is False


def test_failed_newer_report_keeps_discovery_period_without_proposals(tmp_path):
    data=extracted()
    item=data['items'][0]
    item.update(status='pending_verification',financial_proposals=[],disclosed_on='2026-10-12',period_end=None,period_months=None,
                discovery_period_end='2026-09-30',discovery_period_months=9,fiscal_year=None)
    packet=build_interim_packet(data,date(2026,10,15))
    assert packet['companies'][0]['quarterly_documents'][0]['period_end']=='2026-09-30'
    assert packet['companies'][0]['quarterly_documents'][0]['fiscal_year']==2026
    store=StockResearchStore(tmp_path/'research.db')
    store.import_review_packet(packet,source_sha256='c'*64,source_label='合成失败报告',data_as_of=date(2026,10,15))
    document=next(row for row in store.list_evidence('600900') if row['kind']=='quarterly_financial_document')
    assert document['confirmable'] is False


def test_packet_rejects_cross_company_fields():
    data=extracted()
    data['items'][0]['financial_proposals'][0]['symbol']='000001'
    with pytest.raises(StockResearchError):
        build_interim_packet(data,date(2026,10,15))


def test_packet_command_cannot_overwrite_source(tmp_path):
    source=tmp_path/'input.json'
    source.write_text(json.dumps(extracted()),encoding='utf-8')
    raw=source.read_bytes()
    result=CliRunner().invoke(prepare_interim_packet,['--input',str(source),'--output',str(source),'--as-of','2026-10-15'])
    assert result.exit_code!=0
    assert source.read_bytes()==raw
    output=tmp_path/'packet.json'
    result=CliRunner().invoke(prepare_interim_packet,['--input',str(source),'--output',str(output),'--as-of','2026-10-15'])
    assert result.exit_code==0,result.output
    assert json.loads(output.read_text(encoding='utf-8'))['companies'][0]['quarterly_proposals']


def test_extraction_output_cannot_overwrite_archive_directory(tmp_path):
    archive=tmp_path/'pdf'
    archive.mkdir()
    source=tmp_path/'archive.json'
    source.write_text('{"items":[]}',encoding='utf-8')
    result=CliRunner().invoke(extract_interims,['--archive-result',str(source),'--archive-dir',str(archive),
                                              '--output',str(archive/'manifest.json'),'--as-of','2026-10-15'])
    assert result.exit_code!=0
    assert not (archive/'manifest.json').exists()


def test_discovery_uses_interim_mode_and_keeps_input_source(tmp_path,monkeypatch):
    from stock_robot import interim_documents

    source=tmp_path/'companies.json'
    source.write_text('[{"symbol":"600900"}]',encoding='utf-8')
    seen=[]
    def discover(companies,as_of,cache_dir,**kwargs):
        seen.append(kwargs['mode'])
        return {'companies':companies,'documents':[],'financial_verified':False,'rule_enabled':False}
    monkeypatch.setattr(interim_documents,'discover_announcements',discover)
    output=tmp_path/'discovered.json'
    result=CliRunner().invoke(discover_interims,['--input',str(source),'--output',str(output),'--cache-dir',str(tmp_path/'cache'),'--as-of','2026-10-15'])
    assert result.exit_code==0,result.output
    assert seen==['interim_report']
    assert json.loads(output.read_text(encoding='utf-8'))['input_source']['sha256']


def test_interim_commands_registered():
    from stock_robot.cli import main

    for name in ('interim-discover','interim-extract','interim-review-packet'):
        result=CliRunner().invoke(main,['radar',name,'--help'])
        assert result.exit_code!=0,result.output


@pytest.mark.parametrize('pdf_period,discovered_period,discovered_months', [
    ('q1', '2026-06-30', 6), ('h1', '2026-03-31', 3),
])
def test_discovery_and_pdf_period_conflict_is_preserved_as_unreviewed(tmp_path,monkeypatch,pdf_period,discovered_period,discovered_months):
    import hashlib
    from pathlib import Path

    from radar.stocks import financial_extract
    from radar.stocks.interim_extract import extract_archived_interims

    url='https://static.cninfo.com.cn/q1.pdf'
    raw=b'%PDF-synthetic-period-check'
    name=hashlib.sha256(url.encode()).hexdigest()+'.pdf'
    (tmp_path/name).write_bytes(raw)
    pages=Path(f'tests/resources/radar/cninfo-600900-2026-{pdf_period}-accounting.txt').read_text(encoding='utf-8').split('\f')
    class Page:
        def __init__(self,text):
            self.text=text
        def extract_text(self):
            return self.text
    class Reader:
        def __init__(self,stream):
            self.pages=[Page(text) for text in pages]
    monkeypatch.setattr(financial_extract,'PdfReader',Reader)
    archive={'items':[{'symbol':'600900','source_url':url,'disclosed_on':'2026-08-31','status':'downloaded','file':name,
                       'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'period_end':discovered_period,'period_months':discovered_months,'fiscal_year':2026}]}
    result=extract_archived_interims(archive,tmp_path,date(2026,10,15))
    assert result['items'][0]['status']=='pending_verification'
    assert result['items'][0]['financial_proposals']==[]
    packet=build_interim_packet(result,date(2026,10,15))
    assert packet['companies'][0]['quarterly_documents'][0]['period_end']=='2026-06-30'
    assert packet['companies'][0]['quarterly_documents'][0]['parsed_period_end']==('2026-03-31' if pdf_period=='q1' else '2026-06-30')
    assert packet['companies'][0]['quarterly_documents'][0]['period_months']==6
    assert packet['companies'][0]['quarterly_proposals']==[]
