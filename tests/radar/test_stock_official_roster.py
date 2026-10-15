"""官方沪深候选名录的来源范围、表格预算和只读采集回归。"""
import hashlib
import io
import json
import zipfile
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook

from radar.stocks import official_roster as module

ROOT=Path(__file__).parents[1]/'resources/radar'
NOW=datetime.fromisoformat('2026-10-15T16:00:00+08:00')


def sse(board='sse_main',complete=True):
    name='sse-600900-601318-listing.json' if board=='sse_main' else 'sse-688001-listing.json'
    value=json.loads((ROOT/name).read_text(encoding='utf-8'))
    if complete:
        value['pageHelp']['total']=len(value['result'])
    return value


def body(value):
    return json.dumps(value,ensure_ascii=False).encode()


def xlsx(rows=None,headers=None):
    workbook=Workbook()
    sheet=workbook.active
    assert sheet is not None
    sheet.title='A股列表'
    sheet.append(headers or ['A股代码','A股简称','A股上市日期','所属行业','A股总股本'])
    for row in rows or [['000001','平安银行','1991-04-03','J 金融业','19,405,918,198']]:
        sheet.append(row)
    stream=io.BytesIO()
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


class Response:
    def __init__(self,raw=b'',status=200):
        self.raw,self.status_code,self.closed=raw,status,False
    def iter_content(self,chunk_size):
        yield self.raw
    def close(self):
        self.closed=True


@pytest.fixture(autouse=True)
def stable_clock(monkeypatch):
    monkeypatch.setattr(module,'_now',lambda:NOW)


def test_real_sse_cropped_sample_preserves_total_and_is_partial():
    parsed=module.parse_sse_roster(body(sse(complete=False)))
    assert parsed['declared_total']==1702
    assert parsed['row_count']==2
    assert parsed['complete'] is False
    assert parsed['rows'][0]['symbol']=='600900'
    assert parsed['rows'][0]['a_listed_on']=='2003-11-18'
    assert parsed['rows'][1]['industry_source_code']=='J'
    assert 'issuer_type' not in parsed['rows'][1]
    assert parsed['rows'][0]['source_fields']['LIST_DATE']=='20031118'


@pytest.mark.parametrize('board',['sse_main','sse_star'])
def test_sse_explicit_synthetic_total_can_be_complete(board):
    result=module.parse_sse_roster(body(sse(board)),board=board)
    assert result['complete'] is True
    assert result['declared_total']==len(result['rows'])


@pytest.mark.parametrize('change',['sql','stock_type','list_board','market','unknown_code','duplicate','total','error'])
def test_sse_identity_and_full_count_not_guessed(change):
    data=sse()
    if change=='sql':
        data['sqlId']='wrong'
    elif change=='stock_type':
        data['result'][0]['STOCK_TYPE']='8'
    elif change=='list_board':
        data['result'][0]['LIST_BOARD']='2'
    elif change=='market':
        data['result'][0]['A_STOCK_CODE']='000001'
    elif change=='unknown_code':
        data['result'][0]['A_STOCK_CODE']='689999'
    elif change=='duplicate':
        data['result'].append(deepcopy(data['result'][0]))
        data['pageHelp']['total']=3
    elif change=='total':
        data['pageHelp']['total']=True
    else:
        data={'error':'access denied','result':[]}
    parsed=module.parse_sse_roster(body(data))
    assert parsed['complete'] is False
    assert parsed['diagnostics']
    if change=='duplicate':
        assert '600900' not in {row['symbol'] for row in parsed['rows']}


def test_explicit_cdr_exclusion_does_not_hide_unknown_codes():
    data=sse('sse_star')
    cdr=deepcopy(data['result'][0])
    cdr['A_STOCK_CODE']=cdr['COMPANY_CODE']='689009'
    cdr['SEC_NAME_CN']='合成CDR'
    data['result'].append(cdr)
    data['pageHelp']['total']=2
    result=module.parse_sse_roster(body(data),board='sse_star')
    assert result['complete'] is True
    assert [row['symbol'] for row in result['rows']]==['688001']
    assert result['scope_exclusions'][0]['symbol']=='689009'
    data['result'][1]['A_STOCK_CODE']='689008'
    assert module.parse_sse_roster(body(data),board='sse_star')['complete'] is False


def test_unknown_listing_date_is_none_not_invented():
    data=sse()
    data['result'][0]['LIST_DATE']='-'
    result=module.parse_sse_roster(body(data))
    assert result['rows'][0]['a_listed_on'] is None
    assert result['complete'] is True


def test_real_szse_dimension_a1_sample_recovers_both_rows_and_raw_shares():
    result=module.parse_szse_roster((ROOT/'szse-000001-000333-listing.xlsx').read_bytes())
    assert result['complete'] is True
    assert result['declared_total'] is None
    assert [row['symbol'] for row in result['rows']]==['000001','000333']
    assert result['rows'][0]['a_listed_on']=='1991-04-03'
    assert result['rows'][0]['industry_source_code']=='J'
    assert result['rows'][0]['source_fields']['A股总股本']=='19,405,918,198'
    assert 'company_total_shares' not in result['rows'][0]


@pytest.mark.parametrize('change',['numeric_code','unknown_code','formula','duplicate','missing_header','duplicate_header'])
def test_szse_unknown_identity_or_formula_cannot_be_complete(change):
    headers=['A股代码','A股简称','A股上市日期','所属行业','A股总股本']
    rows: list[list[Any]]=[['000001','平安银行','1991-04-03','J 金融业','100']]
    if change=='numeric_code':
        rows[0][0]=1
    elif change=='unknown_code':
        rows[0][0]='600900'
    elif change=='formula':
        rows[0][4]='=1+1'
    elif change=='duplicate':
        rows*=2
    elif change=='missing_header':
        headers[0]='代码'
    else:
        headers[1]='A股代码'
    result=module.parse_szse_roster(xlsx(rows,headers))
    assert result['complete'] is False
    assert result['diagnostics']


@pytest.mark.parametrize('kind',['path','count','expanded'])
def test_szse_zip_budget_rejected_before_workbook(kind):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        if kind=='path':
            archive.writestr('../escaped.xml','x')
        elif kind=='count':
            for index in range(65):
                archive.writestr(f'{index}.xml','x')
        else:
            archive.writestr('huge.xml',b'x'*(20*1024*1024+1))
    with pytest.raises(ValueError):
        module.parse_szse_roster(stream.getvalue())


def download(tmp_path,monkeypatch,responses,**kwargs):
    calls,waits=[],[]
    def get(url,**options):
        calls.append((url,options))
        item=responses.pop(0)
        if isinstance(item,Exception):
            raise item
        return item
    result=module.collect_stock_roster(tmp_path/'cache',as_of=NOW.date(),http_get=get,sleep=waits.append,**kwargs)
    return result,calls,waits


def responses():
    return [Response(body(sse())),Response(body(sse('sse_star'))),Response(xlsx())]


def test_all_sources_cache_and_candidate_hints_without_approval(tmp_path,monkeypatch):
    source=responses()
    refs=list(source)
    result,calls,waits=download(tmp_path,monkeypatch,source)
    assert result['universe_complete'] is True
    assert len(result['companies'])==4
    assert result['members_published']==0
    assert result['formal_screening_enabled'] is False
    assert all(item.closed for item in refs)
    assert waits==[1,1]
    assert all(call[1]['allow_redirects'] is False and call[1]['timeout']==15 and call[1]['stream'] for call in calls)
    company=next(item for item in result['companies'] if item['symbol']=='601318')
    assert company['admission_status']=='pending_verification'
    assert company['security_proposal']['payload']['issuer_type'] is None
    assert company['security_proposal']['disclosed_on']=='2026-10-15'
    assert company['research_hints'][0]['industry_source_code']=='J'
    cached,calls,_=download(tmp_path,monkeypatch,[])
    assert calls==[]
    assert all(item['status']=='cached' for item in cached['items'])
    assert cached['companies']==result['companies']


def test_429_shared_sse_host_stops_star_but_szse_continues(tmp_path,monkeypatch):
    result,calls,_=download(tmp_path,monkeypatch,[Response(status=429),Response(xlsx())])
    assert len(calls)==2
    assert [item['status'] for item in result['items']]==['source_error','source_stopped','downloaded']
    assert result['universe_complete'] is False
    assert [item['symbol'] for item in result['companies']]==['000001']


def test_historical_asof_without_that_day_cache_never_requests_current_roster(tmp_path,monkeypatch):
    result=module.collect_stock_roster(tmp_path/'cache',as_of=date(2026,10,14),http_get=lambda *a,**k:pytest.fail('不得伪造历史名录'))
    assert result['universe_complete'] is False
    assert result['companies']==[]
    assert all(item['status']=='source_error' for item in result['items'])


def test_refresh_failure_does_not_reuse_old_success_and_preserves_original_blob(tmp_path,monkeypatch):
    source=responses()
    raw=source[0].raw
    download(tmp_path,monkeypatch,source)
    result,calls,_=download(tmp_path,monkeypatch,[Response(status=400),Response(status=400),Response(status=400)],refresh=True)
    assert len(calls)==3
    assert result['companies']==[]
    assert result['universe_complete'] is False
    assert any(path.read_bytes()==raw for path in (tmp_path/'cache').glob('*.bin'))


def test_future_asof_rejected_before_directory_or_network(tmp_path):
    with pytest.raises(ValueError):
        module.collect_stock_roster(tmp_path/'cache',as_of=date(2026,10,16),http_get=lambda *a,**k:pytest.fail('不得联网'))
    assert not (tmp_path/'cache').exists()


def test_corrupt_cache_refetches_source_and_reparses(tmp_path,monkeypatch):
    source=responses()
    raw=source[0].raw
    download(tmp_path,monkeypatch,source)
    blob=tmp_path/'cache'/f'{hashlib.sha256(raw).hexdigest()}.bin'
    blob.write_bytes(b'corrupted')
    result,calls,_=download(tmp_path,monkeypatch,[Response(raw)])
    assert len(calls)==1
    assert result['universe_complete'] is True


@pytest.mark.parametrize('cell',['BN1','A20001'])
def test_szse_actual_sparse_cells_cannot_hide_outside_budget(cell):
    workbook=Workbook()
    sheet=workbook.active
    assert sheet is not None
    sheet.title='A股列表'
    sheet.append(['A股代码','A股简称','A股上市日期','所属行业'])
    sheet.append(['000001','平安银行','1991-04-03','J 金融业'])
    sheet[cell]='outside-budget'
    stream=io.BytesIO()
    workbook.save(stream)
    workbook.close()
    with pytest.raises(ValueError):
        module.parse_szse_roster(stream.getvalue())


def test_sse_company_code_cannot_disagree_with_security():
    data=sse()
    data['result'][0]['COMPANY_CODE']='601318'
    result=module.parse_sse_roster(body(data))
    assert result['complete'] is False
    assert '600900' not in {row['symbol'] for row in result['rows']}


@pytest.mark.parametrize('status,attempts',[(400,1),(302,1),(500,3)])
def test_http_status_retry_budget_and_close(tmp_path,monkeypatch,status,attempts):
    source=[Response(status=status) for _ in range(attempts)]+[Response(body(sse('sse_star'))),Response(xlsx())]
    refs=list(source)
    result,calls,waits=download(tmp_path,monkeypatch,source)
    assert len(calls)==attempts+2
    assert all(response.closed for response in refs)
    assert waits==[1]*(attempts+1)
    assert result['items'][0]['status']=='source_error'
    assert result['items'][-1]['status']=='downloaded'


def test_current_cache_can_later_be_used_as_historical_snapshot(tmp_path,monkeypatch):
    result,_,_=download(tmp_path,monkeypatch,responses())
    monkeypatch.setattr(module,'_now',lambda:datetime.fromisoformat('2026-10-16T10:00:00+08:00'))
    historic=module.collect_stock_roster(tmp_path/'cache',as_of=date(2026,10,15),http_get=lambda *a,**k:pytest.fail('历史应当复用当日缓存'))
    assert historic['universe_complete'] is True
    assert historic['companies']==result['companies']
    assert all(item['status']=='cached' for item in historic['items'])


def test_manifest_wrong_capture_day_is_not_historical_snapshot(tmp_path,monkeypatch):
    download(tmp_path,monkeypatch,responses())
    for manifest in (tmp_path/'cache').glob('*.json'):
        record=json.loads(manifest.read_text(encoding='utf-8'))
        record['fetched_at']='2026-10-14T10:00:00+00:00'
        manifest.write_text(json.dumps(record),encoding='utf-8')
    monkeypatch.setattr(module,'_now',lambda:datetime.fromisoformat('2026-10-16T10:00:00+08:00'))
    historic=module.collect_stock_roster(tmp_path/'cache',as_of=date(2026,10,15),http_get=lambda *a,**k:pytest.fail('历史不得用今天网络'))
    assert historic['universe_complete'] is False
    assert historic['companies']==[]


def test_oversize_or_parse_error_archive_does_not_become_success_cache(tmp_path,monkeypatch):
    bad=Response(b'not-json')
    result,_calls,_=download(tmp_path,monkeypatch,[bad,Response(body(sse('sse_star'))),Response(xlsx())])
    item=result['items'][0]
    assert item['status']=='source_error'
    assert item['content_sha256']==hashlib.sha256(bad.raw).hexdigest()
    assert (tmp_path/'cache'/item['raw_artifact']).read_bytes()==bad.raw
    assert result['universe_complete'] is False


def test_production_download_and_parse_have_separate_process_deadlines(tmp_path,monkeypatch):
    calls=[]
    raw_by_source={'sse_main':body(sse()),'sse_star':body(sse('sse_star')),'szse_a':xlsx()}
    def bounded(module_name,function,*,args,timeout):
        assert timeout==15
        calls.append(function)
        if function=='_download_once':
            url=args[0]
            source='szse_a' if 'www.szse.cn' in url else ('sse_star' if 'STOCK_TYPE=8' in url else 'sse_main')
            return {'status':200,'raw':raw_by_source[source],'error':None}
        return module._parse_source(*args)
    monkeypatch.setattr(module,'bounded_provider_call',bounded)
    result=module.collect_stock_roster(tmp_path/'cache',as_of=NOW.date(),sleep=lambda _:None)
    assert result['universe_complete'] is True
    assert calls==['_download_once','_parse_source']*3


def test_production_size_error_is_nontransient_but_other_host_continues(tmp_path,monkeypatch):
    calls=[]
    def bounded(*args,**kwargs):
        calls.append(args[1])
        return {'status':200,'raw':b'','error':'名录原始响应超过4MiB'}
    monkeypatch.setattr(module,'bounded_provider_call',bounded)
    result=module.collect_stock_roster(tmp_path/'cache',as_of=NOW.date(),sleep=lambda _:None)
    assert calls==['_download_once']*3
    assert result['companies']==[]


@pytest.mark.parametrize('malformed',[None,[],1,{'status':'downloaded','content_sha256':[]}])
def test_malformed_manifest_is_refetched_without_batch_failure(tmp_path,monkeypatch,malformed):
    download(tmp_path,monkeypatch,responses())
    for manifest in (tmp_path/'cache').glob('*.json'):
        manifest.write_text(json.dumps(malformed),encoding='utf-8')
    result,calls,_=download(tmp_path,monkeypatch,responses())
    assert len(calls)==3
    assert result['universe_complete'] is True


def test_szse_typed_excel_listing_date_keeps_source_and_converts_day():
    excel_day = datetime(1991,4,3,tzinfo=None)  # noqa: DTZ001 — Excel原生日期单元格禁止时区
    raw=xlsx([['000001','平安银行',excel_day,'J 金融业','100']])
    result=module.parse_szse_roster(raw)
    assert result['rows'][0]['a_listed_on']=='1991-04-03'
    assert result['rows'][0]['source_fields']['A股上市日期']=='1991-04-03T00:00:00'


def test_cache_bounded_reads_are_enforced(tmp_path,monkeypatch):
    download(tmp_path,monkeypatch,responses())
    original=Path.open
    sizes=[]
    class Reader:
        def __init__(self,handle):
            self.handle=handle
        def __enter__(self):
            return self
        def __exit__(self,*args):
            self.handle.close()
        def read(self,size=-1):
            assert size>0
            sizes.append(size)
            return self.handle.read(size)
    def open_cache(path,*args,**kwargs):
        handle=original(path,*args,**kwargs)
        return Reader(handle) if args and args[0]=='rb' else handle
    monkeypatch.setattr(Path,'open',open_cache)
    result,calls,_=download(tmp_path,monkeypatch,[])
    assert calls==[]
    assert result['universe_complete'] is True
    assert sizes.count(65537)==3
    assert sizes.count(4*1024*1024+1)==3


def test_soft_budget_failure_does_not_discard_collected_companies(tmp_path,monkeypatch):
    original=Path.stat
    def stat(path,*args,**kwargs):
        if path.name=='.vanished':
            raise OSError('合成并发删除')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'stat',stat)
    source=responses()
    def get(*args,**kwargs):
        (tmp_path/'cache'/'.vanished').write_bytes(b'x')
        return source.pop(0)
    result=module.collect_stock_roster(tmp_path/'cache',as_of=NOW.date(),http_get=get,sleep=lambda _:None)
    assert result['universe_complete'] is True
    assert len(result['companies'])==4
    assert result['warnings']


def test_symbolic_cache_is_rejected_before_network(tmp_path):
    root=tmp_path/'cache'
    root.mkdir()
    target=tmp_path/'outside'
    target.write_bytes(b'untouched')
    try:
        (root/'link').symlink_to(target)
    except OSError:
        pytest.skip('此Windows环境无符号链接创建权限')
    with pytest.raises(ValueError):
        module.collect_stock_roster(root,as_of=NOW.date(),http_get=lambda *a,**k:pytest.fail('符号链接缓存禁止联网'))
    assert target.read_bytes()==b'untouched'


@pytest.mark.parametrize('change',['name','board','type'])
def test_sse_valid_duplicate_and_bad_duplicate_both_excluded(change):
    data=sse()
    duplicate=deepcopy(data['result'][0])
    if change=='name':
        duplicate['SEC_NAME_CN']=duplicate['COMPANY_ABBR']=''
    elif change=='board':
        duplicate['LIST_BOARD']='2'
    else:
        duplicate['STOCK_TYPE']='8'
    data['result'].append(duplicate)
    data['pageHelp']['total']=3
    result=module.parse_sse_roster(body(data))
    assert result['complete'] is False
    assert '600900' not in {row['symbol'] for row in result['rows']}


@pytest.mark.parametrize('change',['name','formula'])
def test_szse_good_row_and_bad_row_same_code_not_selected(change):
    rows=[['000001','平安银行','1991-04-03','J 金融业','100']]
    duplicate=list(rows[0])
    if change=='name':
        duplicate[1]=''
    else:
        duplicate[4]='=1+1'
    rows.append(duplicate)
    result=module.parse_szse_roster(xlsx(rows))
    assert result['complete'] is False
    assert result['rows']==[]


def test_sse_cdr_raw_nan_cannot_bypass_json_safety():
    data=sse('sse_star')
    record=deepcopy(data['result'][0])
    record['A_STOCK_CODE']=record['COMPANY_CODE']='689009'
    record['unexpected']=float('nan')
    data['result'].append(record)
    data['pageHelp']['total']=2
    with pytest.raises(ValueError):
        module.parse_sse_roster(body(data),'sse_star')


@pytest.mark.parametrize('field,error', [('actionErrors',['来源报告失败']), ('fieldErrors',{'STOCK_TYPE':'查询失败'})])
def test_sse_error_envelope_never_claims_complete_even_with_result(field,error):
    data=sse()
    data[field]=error
    result=module.parse_sse_roster(body(data))
    assert result['complete'] is False
    assert result['diagnostics']


def test_sse_cdr_legal_json_exponent_overflow_is_rejected_before_scope_branch():
    data=sse('sse_star')
    record=deepcopy(data['result'][0])
    record['A_STOCK_CODE']=record['COMPANY_CODE']='689009'
    record['unexpected']='EXPONENT_PLACEHOLDER'
    data['result'].append(record)
    data['pageHelp']['total']=2
    raw=body(data).replace(b'"EXPONENT_PLACEHOLDER"',b'1e999')
    assert b'1e999' in raw
    assert b'Infinity' not in raw
    result=module.parse_sse_roster(raw,'sse_star')
    assert result['complete'] is False
    assert result['scope_exclusions']==[]
    assert result['diagnostics']
    json.dumps(result,allow_nan=False)


def test_sse_duplicate_with_overflow_raw_field_cannot_keep_good_row():
    data=sse()
    duplicate=deepcopy(data['result'][0])
    duplicate['unexpected']='EXPONENT_PLACEHOLDER'
    data['result'].append(duplicate)
    data['pageHelp']['total']=3
    raw=body(data).replace(b'"EXPONENT_PLACEHOLDER"',b'1e999')
    result=module.parse_sse_roster(raw)
    assert result['complete'] is False
    assert '600900' not in {row['symbol'] for row in result['rows']}


def test_production_sources_use_their_own_request_contract(monkeypatch):
    seen={}
    def get(url,**kwargs):
        seen[url]=kwargs
        return Response()
    monkeypatch.setattr(module.requests,'get',get)
    for source in ('sse_main','szse_a'):
        module._download_once(module._url(source))
    assert seen[module._url('sse_main')]['headers']['Referer']=='https://www.sse.com.cn/assortment/stock/list/share/'
    assert seen[module._url('szse_a')]['headers']=={}
    assert all(value['timeout']==15 and value['allow_redirects'] is False for value in seen.values())
