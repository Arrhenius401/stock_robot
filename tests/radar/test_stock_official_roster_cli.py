"""官方名录命令与共享候选导入互通，使用合成公司。"""
import hashlib
import json
from datetime import date, datetime

import pytest
from click.testing import CliRunner

from radar.stocks.research_store import StockResearchStore
from stock_robot import stock_official_roster
from stock_robot.stock_official_roster import official_roster_command

AS_OF=date(2026,10,15)


def test_export_then_existing_candidate_import_remains_unreviewed(tmp_path,monkeypatch):
    seen=[]
    def collect(root,**kwargs):
        seen.append((root,kwargs))
        return {'companies':[{'symbol':'600900','name':'合成沪股','admission_status':'pending_verification',
                    'research_hints':{'a_listed_on':'2003-11-18','issuer_type':None}},
                {'symbol':'000001','name':'合成深股','admission_status':'pending_verification'}],
                'status':'partial','universe_complete':False,'research_only':True,
                'formal_screening_enabled':False,'members_published':0}
    monkeypatch.setattr(stock_official_roster,'collect_stock_roster',collect)
    output=tmp_path/'roster.json'
    result=CliRunner().invoke(official_roster_command,['--as-of',AS_OF.isoformat(),'--cache-dir',str(tmp_path/'cache'),
        '--output',str(output),'--refresh'])
    assert result.exit_code==0,result.output
    assert seen[0][1]=={'as_of':AS_OF,'refresh':True}
    data=json.loads(output.read_text(encoding='utf-8'))
    assert data['status']=='partial' and data['formal_screening_enabled'] is False
    store=StockResearchStore(tmp_path/'research.db',clock=lambda:datetime.fromisoformat('2026-10-15T10:00:00+08:00'))
    report=store.import_companies(data['companies'],source_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
        source_label='合成名录',data_as_of=AS_OF)
    assert report['companies_added']==2
    assert store.summary()['reviews']==0
    assert all(row['review_status']=='pending_verification' for row in store.list_evidence('600900'))


@pytest.mark.parametrize('inside',['same','child'])
def test_output_cannot_overwrite_cache_before_collection(tmp_path,monkeypatch,inside):
    cache=tmp_path/'cache'
    output=cache if inside=='same' else cache/'roster.json'
    monkeypatch.setattr(stock_official_roster,'collect_stock_roster',lambda *a,**k:pytest.fail('拒绝后不得采集'))
    result=CliRunner().invoke(official_roster_command,['--as-of',AS_OF.isoformat(),'--cache-dir',str(cache),'--output',str(output)])
    assert result.exit_code!=0 and not cache.exists()


@pytest.mark.parametrize('cutoff',['not-date','9999-12-31'])
def test_bad_cutoff_does_not_start_collection(tmp_path,monkeypatch,cutoff):
    monkeypatch.setattr(stock_official_roster,'collect_stock_roster',lambda *a,**k:pytest.fail('日期拒绝后不得采集'))
    result=CliRunner().invoke(official_roster_command,['--as-of',cutoff,'--cache-dir',str(tmp_path/'cache'),
        '--output',str(tmp_path/'roster.json')])
    assert result.exit_code!=0 and not (tmp_path/'cache').exists()


def test_roster_command_registered():
    from stock_robot.cli import main
    result=CliRunner().invoke(main,['radar','stock-universe-discover','--help'])
    assert result.exit_code==0,result.output
