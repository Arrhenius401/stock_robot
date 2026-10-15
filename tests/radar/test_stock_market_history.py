"""原始新浪日线适配器：合成HTTP与裁切真实样本回归。"""
import hashlib
import json
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from radar.stocks import market_history as module

NOW=datetime.fromisoformat('2026-10-15T16:00:00+08:00')
WINDOW=[date(2026,8,17)+timedelta(days=i) for i in range(60)]
BODY=b'var KLC_K2_sh600900="synthetic-payload";'


def decoded():
    return [{'date':day.isoformat()+'T00:00:00.000Z','open':10,'high':12,'low':9,'close':11,
             'volume':100,'amount':1000,'postVol':2,'postAmt':22} for day in WINDOW]


class Response:
    def __init__(self,status=200,body=BODY):
        self.status_code=status
        self.body=body
        self.closed=False
    def iter_content(self,chunk_size):
        yield self.body
    def close(self):
        self.closed=True


@pytest.fixture(autouse=True)
def stable_clock(monkeypatch):
    monkeypatch.setattr(module,'_now',lambda:NOW)


def test_url_and_isolated_decoder_receives_only_string_data(monkeypatch):
    calls=[]
    def bounded(*args,**kwargs):
        calls.append((args,kwargs))
        return decoded()
    monkeypatch.setattr(module,'bounded_provider_call',bounded)
    assert module.sina_url('600900')=='https://finance.sina.com.cn/realstock/company/sh600900/hisdata_klc2/klc_kl.js'
    assert '/sz000333/' in module.sina_url('000333')
    assert module.decode_sina_body(BODY,'600900')==decoded()
    assert calls[0][1]['args']==('synthetic-payload',)
    assert calls[0][1]['timeout']==15


@pytest.mark.parametrize('body',[b'var KLC_K2_sz000333="a";',b'var KLC_K2_sh600900="a"; alert(1)',
    b'var KLC_K2_sh600900=evil()',b'var KLC_K2_sh600900="a"; var x=2;',b'bad',b'\xff'])
def test_wrong_identity_or_executable_response_rejected_before_decoder(body,monkeypatch):
    monkeypatch.setattr(module,'bounded_provider_call',lambda *a,**k:pytest.fail('不可执行无效响应'))
    with pytest.raises(ValueError):
        module.decode_sina_body(body,'600900')


def test_full_window_preserves_raw_post_fields_and_decimal_amounts():
    data=decoded()
    original=deepcopy(data)
    result=module.parse_sina_history(BODY,'600900',WINDOW,decoder=lambda *args:data)
    assert result['window_complete'] is True
    assert result['missing_dates']==[]
    assert len(result['rows'])==60
    assert result['rows'][0]['amount_yuan']=='1000'
    assert result['rows'][0]['postVol']==2
    assert result['rows'][0]['postAmt']==22
    assert result['rows'][0]['raw']==data[0]
    assert result['price_basis']=='unadjusted'
    assert result['amount_basis']=='source_raw_turnover'
    assert result['financial_verified'] is False
    assert data==original


@pytest.mark.parametrize('change',['missing','duplicate_equal','duplicate_conflict','zero_price','bad_high','bad_low','negative_amount','negative_volume','bool','nan','bad_day'])
def test_bad_or_duplicate_target_day_stays_missing(change):
    data=decoded()
    if change=='missing':
        data.pop(0)
    elif change.startswith('duplicate'):
        extra=deepcopy(data[0])
        if change=='duplicate_conflict':
            extra['amount']=999
        data.append(extra)
    else:
        field,value={'zero_price':('close',0),'bad_high':('high',10),'bad_low':('low',11.5),
            'negative_amount':('amount',-1),'negative_volume':('volume',-1),'bool':('amount',True),
            'nan':('amount',float('nan')),'bad_day':('date','2026-08-17T01:00:00.000Z')}[change]
        data[0][field]=value
    result=module.parse_sina_history(BODY,'600900',WINDOW,decoder=lambda *args:data)
    assert result['window_complete'] is False
    assert WINDOW[0].isoformat() in result['missing_dates']


def test_zero_turnover_is_real_not_missing_and_outside_data_not_used():
    data=decoded()
    data[0]['volume']=data[0]['amount']=0
    data.append({'date':'1990-01-01','garbage':True})
    result=module.parse_sina_history(BODY,'600900',WINDOW,decoder=lambda *args:data)
    assert result['window_complete'] is True
    assert result['rows'][0]['amount_yuan']=='0'


def test_real_sample_only_covers_last_three_without_backfill():
    sample=json.loads((Path(__file__).parents[1]/'resources/radar/sina-600900-2026-daily-sample.json').read_text(encoding='utf-8'))
    result=module.parse_sina_history(BODY,'600900',WINDOW,decoder=lambda *args:sample)
    assert len(result['rows'])==3
    assert len(result['missing_dates'])==57
    assert result['rows'][-1]['amount_yuan']=='4072104696'


def collect(tmp_path,monkeypatch,responses,**kwargs):
    seen=[]
    waits=[]
    monkeypatch.setattr(module,'decode_sina_body',lambda *a:decoded())
    def get(url,**options):
        seen.append((url,options))
        value=responses.pop(0)
        if isinstance(value,Exception):
            raise value
        return value
    result=module.collect_stock_history([{'symbol':'600900'}],WINDOW,tmp_path/'cache',as_of=NOW.date(),http_get=get,sleep=waits.append,**kwargs)
    return result,seen,waits


def test_download_cache_reparse_and_refresh_preserves_old_blob(tmp_path,monkeypatch):
    response=Response()
    result,calls,_=collect(tmp_path,monkeypatch,[response])
    item=result['items'][0]
    assert item['status']=='downloaded'
    assert item['liquidity_fact']['payload']['scope']=='a_share_raw_turnover'
    assert item['liquidity_fact']['disclosed_on']=='2026-10-15'
    assert calls[0][1]=={'timeout':15,'stream':True,'allow_redirects':False}
    assert response.closed
    cached,calls,_=collect(tmp_path,monkeypatch,[])
    assert cached['items'][0]['status']=='cached'
    assert calls==[]
    replacement=Response(body=b'var KLC_K2_sh600900="new";')
    refreshed,_,_=collect(tmp_path,monkeypatch,[replacement],refresh=True)
    assert refreshed['items'][0]['status']=='downloaded'
    assert (tmp_path/'cache'/f'{hashlib.sha256(BODY).hexdigest()}.js').is_file()
    assert (tmp_path/'cache'/f'{hashlib.sha256(replacement.body).hexdigest()}.js').is_file()


@pytest.mark.parametrize('status,attempts',[(400,1),(302,1),(500,3)])
def test_request_budget_status_and_response_cleanup(tmp_path,monkeypatch,status,attempts):
    responses=[Response(status) for _ in range(attempts)]
    refs=list(responses)
    result,calls,waits=collect(tmp_path,monkeypatch,responses)
    assert result['items'][0]['status']=='source_error'
    assert len(calls)==attempts
    assert all(response.closed for response in refs)
    assert len(waits)==attempts-1


def test_source_429_stops_following_companies_and_does_not_retry(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'decode_sina_body',lambda *a:decoded())
    calls=[]
    response=Response(429)
    def get(*args,**kwargs):
        calls.append(1)
        return response
    result=module.collect_stock_history([{'symbol':'600900'},{'symbol':'000333'}],WINDOW,tmp_path/'cache',as_of=NOW.date(),http_get=get,sleep=lambda _:None)
    assert len(calls)==1
    assert response.closed
    assert [item['status'] for item in result['items']]==['source_error','source_stopped']
    assert result['status']=='partial'


def test_oversize_response_closed_and_failure_refresh_never_reuses_old_success(tmp_path,monkeypatch):
    collect(tmp_path,monkeypatch,[Response()])
    huge=Response(body=b'x'*(4*1024*1024+1))
    result,calls,_=collect(tmp_path,monkeypatch,[huge],refresh=True)
    assert len(calls)==1
    assert huge.closed
    assert result['items'][0]['status']=='source_error'
    assert 'liquidity_fact' not in result['items'][0]


def test_corrupt_cached_blob_is_refetched(tmp_path,monkeypatch):
    collect(tmp_path,monkeypatch,[Response()])
    blob=tmp_path/'cache'/f'{hashlib.sha256(BODY).hexdigest()}.js'
    blob.write_bytes(b'corrupted')
    result,calls,_=collect(tmp_path,monkeypatch,[Response()])
    assert len(calls)==1
    assert result['items'][0]['status']=='downloaded'


@pytest.mark.parametrize('change',['symbol','duplicates','window_count','window_duplicate','future_asof','intraday'])
def test_input_validation_precedes_network_or_cache_creation(tmp_path,monkeypatch,change):
    companies=[{'symbol':'600900'}]
    window=list(WINDOW)
    as_of=NOW.date()
    if change=='symbol':
        companies=[{'symbol':'bad'}]
    elif change=='duplicates':
        companies*=2
    elif change=='window_count':
        window.pop()
    elif change=='window_duplicate':
        window[-1]=window[-2]
    elif change=='future_asof':
        as_of+=timedelta(days=1)
    else:
        monkeypatch.setattr(module,'_now',lambda:NOW.replace(hour=14))
    root=tmp_path/'cache'
    with pytest.raises(ValueError):
        module.collect_stock_history(companies,window,root,as_of=as_of,http_get=lambda *a,**k:pytest.fail('不可联网'))
    assert not root.exists()


def test_serial_partial_failure_keeps_success_and_spacing(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'decode_sina_body',lambda *a:decoded())
    responses=[Response(400),Response(body=b'var KLC_K2_sz000333="x";')]
    waits=[]
    result=module.collect_stock_history([{'symbol':'600900'},{'symbol':'000333'}],WINDOW,tmp_path/'cache',as_of=NOW.date(),
        http_get=lambda *a,**k:responses.pop(0),sleep=waits.append)
    assert [item['status'] for item in result['items']]==['source_error','downloaded']
    assert result['status']=='partial'
    assert result['items'][1]['liquidity_fact']['symbol']=='000333'
    assert waits==[1]


def test_missing_rows_still_downloaded_but_no_fact(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'decode_sina_body',lambda *a:decoded()[:-1])
    result=module.collect_stock_history([{'symbol':'600900'}],WINDOW,tmp_path/'cache',as_of=NOW.date(),
        http_get=lambda *a,**k:Response(),sleep=lambda _:None)
    assert result['status']=='complete'
    assert result['items'][0]['window_complete'] is False
    assert 'liquidity_fact' not in result['items'][0]


def test_stream_request_error_retries_and_closes_all_responses(tmp_path,monkeypatch):
    class Broken(Response):
        def iter_content(self,chunk_size):
            yield b'prefix'
            raise OSError('合成读取中断')
    response=Broken()
    success=Response()
    result,calls,waits=collect(tmp_path,monkeypatch,[response,success])
    assert response.closed and success.closed
    assert len(calls)==2
    assert waits==[1]
    assert result['items'][0]['status']=='downloaded'


def test_network_oserror_exhausts_exact_three_attempts(tmp_path,monkeypatch):
    result,calls,waits=collect(tmp_path,monkeypatch,[OSError('1'),OSError('2'),OSError('3')])
    assert len(calls)==3
    assert waits==[1,1]
    assert result['items'][0]['status']=='source_error'


def test_historical_snapshot_uses_real_collection_date(tmp_path,monkeypatch):
    historical=[day-timedelta(days=60) for day in WINDOW]
    monkeypatch.setattr(module,'decode_sina_body',lambda *a:[{**row,'date':historical[i].isoformat()} for i,row in enumerate(decoded())])
    result=module.collect_stock_history([{'symbol':'600900'}],historical,tmp_path/'cache',as_of=date(2026,8,16),
        http_get=lambda *a,**k:Response(),sleep=lambda _:None)
    fact=result['items'][0]['liquidity_fact']
    assert fact['disclosed_on']=='2026-10-15'
    assert fact['payload']['as_of']=='2026-08-16'


def test_symbolic_cache_refused_without_network(tmp_path,monkeypatch):
    root=tmp_path/'cache'
    root.mkdir()
    target=tmp_path/'outside.js'
    target.write_bytes(b'untouched')
    link=root/'alias.js'
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip('此Windows环境无符号链接创建权限')
    with pytest.raises(ValueError):
        module.collect_stock_history([{'symbol':'600900'}],WINDOW,root,as_of=NOW.date(),
            http_get=lambda *a,**k:pytest.fail('符号链接缓存不得触发网络'))
    assert target.read_bytes()==b'untouched'


def test_future_fetched_cache_is_not_reused(tmp_path,monkeypatch):
    collect(tmp_path,monkeypatch,[Response()])
    manifest=next((tmp_path/'cache').glob('*.json'))
    data=json.loads(manifest.read_text(encoding='utf-8'))
    data['fetched_at']='2027-01-01T00:00:00+00:00'
    manifest.write_text(json.dumps(data),encoding='utf-8')
    result,calls,_=collect(tmp_path,monkeypatch,[Response()])
    assert len(calls)==1
    assert result['items'][0]['status']=='downloaded'


def test_new_decoder_runs_cached_body_again_instead_of_trusting_old_parsed_results(tmp_path,monkeypatch):
    collect(tmp_path,monkeypatch,[Response()])
    monkeypatch.setattr(module,'decode_sina_body',lambda *args:decoded()[:-2])
    result=module.collect_stock_history([{'symbol':'600900'}],WINDOW,tmp_path/'cache',as_of=NOW.date(),
        http_get=lambda *a,**k:pytest.fail('有效原文缓存不重抓'),sleep=lambda _:None)
    assert result['items'][0]['status']=='cached'
    assert len(result['items'][0]['missing_dates'])==2
    assert 'liquidity_fact' not in result['items'][0]


def test_fetched_at_is_always_utc(tmp_path,monkeypatch):
    result,_,_=collect(tmp_path,monkeypatch,[Response()])
    assert result['items'][0]['fetched_at'].endswith('+00:00')


def test_decoder_data_string_cannot_be_executed_as_server_code(monkeypatch):
    payload='"; globalThis.compromised=true; //'
    raw=('var KLC_K2_sh600900='+json.dumps(payload)+';').encode()
    calls=[]
    monkeypatch.setattr(module,'bounded_provider_call',lambda *a,**k:calls.append(k['args']) or [])
    assert module.decode_sina_body(raw,'600900')==[]
    assert calls==[(payload,)]


@pytest.mark.parametrize('malformed',[[],None,1,'bad',{'content_sha256':[]}])
def test_malformed_cache_manifest_refetches_without_aborting_batch(tmp_path,monkeypatch,malformed):
    collect(tmp_path,monkeypatch,[Response()])
    manifest=next((tmp_path/'cache').glob('*.json'))
    manifest.write_text(json.dumps(malformed),encoding='utf-8')
    result,calls,_=collect(tmp_path,monkeypatch,[Response()])
    assert len(calls)==1
    assert result['items'][0]['status']=='downloaded'


def test_unreadable_cache_manifest_refetches(tmp_path,monkeypatch):
    collect(tmp_path,monkeypatch,[Response()])
    original=Path.open
    def read(path,*args,**kwargs):
        if path.suffix=='.json':
            raise OSError('合成缓存读取失败')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',read)
    result,calls,_=collect(tmp_path,monkeypatch,[Response()])
    assert len(calls)==1
    assert result['items'][0]['status']=='downloaded'


def test_production_download_is_in_deadline_bounded_child(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(module,'decode_sina_body',lambda *args:decoded())
    monkeypatch.setattr(module.requests,'get',lambda *a,**k:pytest.fail('主进程不得直接联网'))
    def bounded(*args,**kwargs):
        calls.append((args,kwargs))
        return {'status':200,'body':BODY,'error':None}
    monkeypatch.setattr(module,'bounded_provider_call',bounded)
    result=module.collect_stock_history([{'symbol':'600900'}],WINDOW,tmp_path/'cache',as_of=NOW.date(),sleep=lambda _:None)
    assert result['items'][0]['status']=='downloaded'
    assert calls[0][0]==('radar.stocks.market_history','_download_once')
    assert calls[0][1]['timeout']==15
    assert calls[0][1]['args']==(module.sina_url('600900'),)


def test_production_deadline_failure_retries_with_same_budget(tmp_path,monkeypatch):
    from radar.provider_request import ProviderRequestError
    calls=[]
    def bounded(*args,**kwargs):
        calls.append(1)
        raise ProviderRequestError('合成总deadline到期')
    monkeypatch.setattr(module,'bounded_provider_call',bounded)
    result=module.collect_stock_history([{'symbol':'600900'}],WINDOW,tmp_path/'cache',as_of=NOW.date(),sleep=lambda _:None)
    assert len(calls)==3
    assert result['items'][0]['status']=='source_error'


def test_production_429_stays_status_and_stops_source(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(module,'bounded_provider_call',lambda *a,**k:calls.append(1) or {'status':429,'body':b'','error':None})
    result=module.collect_stock_history([{'symbol':'600900'},{'symbol':'000333'}],WINDOW,tmp_path/'cache',as_of=NOW.date(),sleep=lambda _:None)
    assert calls==[1]
    assert result['items'][1]['status']=='source_stopped'


def test_download_once_closes_response_on_success_and_stream_error(monkeypatch):
    response=Response()
    monkeypatch.setattr(module.requests,'get',lambda *a,**k:response)
    assert module._download_once(module.sina_url('600900'))=={'status':200,'body':BODY,'error':None}
    assert response.closed
    response=Response(body=b'x'*(4*1024*1024+1))
    failed=module._download_once(module.sina_url('600900'))
    assert failed['status']==200 and failed['body']==b''
    assert '4MiB' in failed['error']
    assert response.closed


def test_single_trailing_source_block_comment_is_data_and_not_executed(monkeypatch):
    seen=[]
    monkeypatch.setattr(module,'bounded_provider_call',lambda *a,**k:seen.append(k['args']) or [])
    raw=BODY+b'\n\n/* P/srHJl7ucOX6XE68ktWKAWY0Xl/jM2gmGIM5oGrmcnxmi0c18kwI4FU0KqltWVm */\n'
    assert module.decode_sina_body(raw,'600900')==[]
    assert seen==[('synthetic-payload',)]


@pytest.mark.parametrize('suffix',[b'/* unfinished',b'/* one */ alert(1)',b'/* one /* nested */',
                                   b'/* one */ var x=2;',b'/*one*/ /*two*/',b'/*one*/ */'])
def test_malformed_or_executable_tail_after_comment_rejected(suffix,monkeypatch):
    monkeypatch.setattr(module,'bounded_provider_call',lambda *a,**k:pytest.fail('不可执行错误尾注释'))
    with pytest.raises(ValueError):
        module.decode_sina_body(BODY+suffix,'600900')


def test_production_oversize_is_nontransient_and_does_not_retry(tmp_path,monkeypatch):
    calls=[]
    def bounded(*a,**k):
        calls.append(1)
        return {'status':200,'body':b'','error':'日线响应超过4MiB预算'}
    monkeypatch.setattr(module,'bounded_provider_call',bounded)
    result=module.collect_stock_history([{'symbol':'600900'}],WINDOW,tmp_path/'cache',as_of=NOW.date(),sleep=lambda _:None)
    assert calls==[1]
    assert result['items'][0]['status']=='source_error'
    assert '4MiB' in result['items'][0]['error']


def test_invalid_assignment_200_still_archived_but_never_success_cache(tmp_path,monkeypatch):
    body=b'var KLC_K2_sz000333="wrong-company";'
    response=Response(body=body)
    result,calls,_=collect(tmp_path,monkeypatch,[response])
    item=result['items'][0]
    digest=hashlib.sha256(body).hexdigest()
    assert item['status']=='source_error'
    assert item['content_sha256']==digest
    assert item['fetched_at'].endswith('+00:00')
    assert item['raw_artifact']==f'{digest}.js'
    assert (tmp_path/'cache'/item['raw_artifact']).read_bytes()==body
    assert 'liquidity_fact' not in item
    assert response.closed
    manifest=json.loads(next((tmp_path/'cache').glob('*.json')).read_text(encoding='utf-8'))
    assert manifest['status']=='source_error'
    assert manifest['content_sha256']==digest
    retried,calls,_=collect(tmp_path,monkeypatch,[Response()])
    assert len(calls)==1
    assert retried['items'][0]['status']=='downloaded'


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),object(),{'unserializable':{1,2}}])
def test_noncore_raw_fields_must_be_strict_json_or_target_day_unknown(bad):
    data=decoded()
    data[0]['postAmt']=bad
    result=module.parse_sina_history(BODY,'600900',WINDOW,decoder=lambda *args:data)
    assert result['window_complete'] is False
    assert WINDOW[0].isoformat() in result['missing_dates']
    assert any('非JSON' in message for message in result['diagnostics'])
    json.dumps(result,allow_nan=False)


def test_bad_post_data_does_not_prevent_other_company_json_export(tmp_path,monkeypatch):
    def decode(raw,symbol):
        data=decoded()
        if symbol=='600900':
            data[0]['postVol']=float('nan')
        return data
    monkeypatch.setattr(module,'decode_sina_body',decode)
    responses=[Response(),Response(body=b'var KLC_K2_sz000333="valid";')]
    result=module.collect_stock_history([{'symbol':'600900'},{'symbol':'000333'}],WINDOW,tmp_path/'cache',as_of=NOW.date(),
        http_get=lambda *a,**k:responses.pop(0),sleep=lambda _:None)
    assert result['items'][0]['window_complete'] is False
    assert 'liquidity_fact' not in result['items'][0]
    assert result['items'][1]['window_complete'] is True
    assert 'liquidity_fact' in result['items'][1]
    json.dumps(result,allow_nan=False)


def test_cache_reads_use_explicit_maximum_lengths(tmp_path,monkeypatch):
    collect(tmp_path,monkeypatch,[Response()])
    original=Path.open
    budgets=[]
    class Reader:
        def __init__(self,handle,suffix):
            self.handle,self.suffix=handle,suffix
        def __enter__(self):
            return self
        def __exit__(self,*args):
            self.handle.close()
        def read(self,size=-1):
            budgets.append((self.suffix,size))
            assert size>0
            return self.handle.read(size)
    def open_cache(path,*args,**kwargs):
        handle=original(path,*args,**kwargs)
        return Reader(handle,path.suffix) if args and args[0]=='rb' else handle
    monkeypatch.setattr(Path,'open',open_cache)
    result,calls,_=collect(tmp_path,monkeypatch,[])
    assert calls==[]
    assert result['items'][0]['status']=='cached'
    assert ('.json',65537) in budgets
    assert ('.js',4*1024*1024+1) in budgets


def test_soft_budget_stat_race_does_not_lose_completed_companies(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'decode_sina_body',lambda *args:decoded())
    original=Path.stat
    def stat(path,*args,**kwargs):
        if path.name=='.vanished':
            raise OSError('合成并发文件删除')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'stat',stat)
    def get(*a,**k):
        (tmp_path/'cache'/'.vanished').write_bytes(b'x')
        return Response()
    result=module.collect_stock_history([{'symbol':'600900'}],WINDOW,tmp_path/'cache',as_of=NOW.date(),
        http_get=get,sleep=lambda _:None)
    assert result['items'][0]['status']=='downloaded'
    assert result['warnings']
    assert 'liquidity_fact' in result['items'][0]


def test_soft_budget_directory_scan_error_does_not_lose_completed_result(tmp_path,monkeypatch):
    monkeypatch.setattr(module,'decode_sina_body',lambda *args:decoded())
    original=Path.iterdir
    def scan(path):
        if path==tmp_path/'cache':
            raise OSError('合成软预算目录读取失败')
        return original(path)
    monkeypatch.setattr(Path,'iterdir',scan)
    result=module.collect_stock_history([{'symbol':'600900'}],WINDOW,tmp_path/'cache',as_of=NOW.date(),
        http_get=lambda *a,**k:Response(),sleep=lambda _:None)
    assert result['status']=='complete'
    assert result['items'][0]['status']=='downloaded'
    assert result['warnings']
