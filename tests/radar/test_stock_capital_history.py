"""官方股本变动双日期、未知单位及有预算原文采集回归。"""
import hashlib
import json
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path

import pytest

from radar.stocks import capital_history as module

ROOT=Path(__file__).parents[1]/'resources/radar'
NOW=datetime.fromisoformat('2026-10-15T16:00:00+08:00')
START=date(1990,1,1)
AS_OF=NOW.date()


def sample(symbol='600900',complete=True):
    value=json.loads((ROOT/f'cninfo-{symbol}-capital-history.json').read_text(encoding='utf-8'))
    if complete:
        value['count']=value['total']=len(value['records'])
    return value


def body(value):
    return json.dumps(value,ensure_ascii=False).encode()


def parse(value=None,symbol='600900',as_of=AS_OF,start=START):
    return module.parse_capital_history(body(sample(symbol) if value is None else value),symbol,as_of=as_of,start_date=start)


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


@pytest.mark.parametrize('symbol,total',[('600900',69),('601318',52)])
def test_cropped_real_sample_keeps_unknown_units_and_real_total(symbol,total):
    result=parse(sample(symbol,False),symbol)
    assert result['complete'] is False
    assert result['declared_total']==total
    assert result['row_count']==3
    assert result['unit'] is None
    assert result['unit_verified'] is False
    assert result['all_share_classes_verified'] is False
    assert 'capitalization_fact' not in result
    assert all('source_fields' in row and 'announced_on' in row and 'effective_on' in row for row in result['rows'])
    assert result['rows'][0]['source_fields']['F003N']==sample(symbol)['records'][0]['F003N']
    assert result['rows'][0]['source_numeric_tokens']['F003N']


def test_f022_is_preserved_without_becoming_total_a_or_inferred_share_class():
    result=parse(symbol='601318')
    assert result['complete'] is True
    assert all('a_total_shares' not in row and 'total_shares' not in row for row in result['rows'])
    assert any(row['source_fields']['F028N'] for row in result['rows'])
    assert parse()['rows'][-1]['source_fields']['F002V']=='GDR上市'


@pytest.mark.parametrize('change',['late_announcement','late_effective','before_start','bad_date','identity'])
def test_both_dates_and_identity_enforced_without_future_leak(change):
    value=sample()
    first=value['records'][0]
    if change=='late_announcement':
        first['DECLAREDATE']='2026-10-16'
    elif change=='late_effective':
        first['VARYDATE']='2026-10-16'
    elif change=='before_start':
        first['VARYDATE']='1989-12-31'
    elif change=='bad_date':
        first['DECLAREDATE']='2026-02-30'
    else:
        first['SECCODE']='000333'
    result=parse(value)
    assert len(result['rows'])==2
    assert len(result['excluded_rows'])==1
    assert result['excluded_rows'][0]['reasons']
    if change in ('bad_date','identity'):
        assert result['complete'] is False


def test_announcement_can_precede_effective_date_without_guessing_latest():
    value=sample()
    result=parse(value,as_of=date(2020,9,29))
    assert all(row['effective_on']<='2020-09-29' for row in result['rows'])
    assert any('变动日期晚于截点' in row['reasons'] for row in result['excluded_rows'])


@pytest.mark.parametrize('conflict',[False,True])
def test_duplicate_pair_all_excluded_not_selected(conflict):
    value=sample()
    duplicate=deepcopy(value['records'][0])
    if conflict:
        duplicate['F003N']=999
    value['records'].append(duplicate)
    value['count']=value['total']=4
    result=parse(value)
    assert result['complete'] is False
    assert len(result['rows'])==2
    assert len([row for row in result['excluded_rows'] if '重复公告/变动双日期' in row['reasons']])==2


def test_different_announcement_same_effective_retained_as_revisions():
    value=sample()
    revision=deepcopy(value['records'][0])
    revision['DECLAREDATE']='2003-11-14'
    revision['F003N']=999
    value['records'].append(revision)
    value['count']=value['total']=4
    result=parse(value)
    assert len(result['rows'])==4
    assert result['complete'] is True
    assert any('不同公告版本' in message for message in result['diagnostics'])


@pytest.mark.parametrize('raw',[b'{"resultcode":200,"resultcode":200,"records":[]}',b'{"value":NaN}',
    b'{"value":1e999}',b'[]',b'not-json'])
def test_nonstandard_or_overflow_json_rejected(raw):
    with pytest.raises((ValueError,TypeError)):
        module.parse_capital_history(raw,'600900',as_of=NOW.date(),start_date=START)


@pytest.mark.parametrize('change',['count','total','resultcode','records'])
def test_error_or_truncated_packet_not_complete(change):
    value=sample()
    if change=='count':
        value['count']=True
    elif change=='total':
        value['total']=100
    elif change=='resultcode':
        value['resultcode']=500
    else:
        value['records']='bad'
    result=parse(value)
    assert result['complete'] is False
    assert result['diagnostics']


def test_numeric_token_precision_kept_separately_from_json_field_representation():
    value=sample()
    value['records'][0]['F003N']='EXACT_NUMBER'
    raw=body(value).replace(b'"EXACT_NUMBER"',b'1234567890.1234567890123456789')
    result=module.parse_capital_history(raw,'600900',as_of=NOW.date(),start_date=START)
    assert result['rows'][0]['source_numeric_tokens']['F003N']=='1234567890.1234567890123456789'
    json.dumps(result,allow_nan=False)


def collect(tmp_path,monkeypatch,responses,**kwargs):
    calls,waits=[],[]
    def post(url,**options):
        calls.append((url,options))
        response=responses.pop(0)
        if isinstance(response,Exception):
            raise response
        return response
    result=module.collect_capital_history([{'symbol':'600900'},{'symbol':'601318'}],tmp_path/'cache',as_of=NOW.date(),http_post=post,sleep=waits.append,**kwargs)
    return result,calls,waits


def responses():
    return [Response(body(sample())),Response(body(sample('601318')))]


def test_download_cache_and_double_date_research_only(tmp_path,monkeypatch):
    source=responses()
    refs=list(source)
    result,calls,waits=collect(tmp_path,monkeypatch,source)
    assert [item['status'] for item in result['items']]==['downloaded','downloaded']
    assert result['status']=='complete'
    assert result['unit_verified'] is False
    assert result['members_published']==0
    assert result['formal_screening_enabled'] is False
    assert result['point_in_time_verified'] is False
    assert all(response.closed for response in refs)
    assert waits==[1]
    assert all(options['timeout']==15 and options['allow_redirects'] is False and options['stream'] for _url,options in calls)
    cached,calls,_=collect(tmp_path,monkeypatch,[])
    assert calls==[]
    assert all(item['status']=='cached' for item in cached['items'])
    assert cached['items'][0]['rows']==result['items'][0]['rows']


@pytest.mark.parametrize('status',[401,403,429])
def test_permissions_and_rate_limit_stop_source_no_retries(tmp_path,monkeypatch,status):
    response=Response(status=status)
    result,calls,_=collect(tmp_path,monkeypatch,[response])
    assert len(calls)==1
    assert response.closed
    assert [item['status'] for item in result['items']]==['source_error','source_stopped']


def test_200_challenge_body_stops_following_network(tmp_path,monkeypatch):
    challenged=Response(body({'resultcode':403,'resultmsg':'权限不足/验证码','records':[]}))
    result,calls,_=collect(tmp_path,monkeypatch,[challenged])
    assert len(calls)==1
    assert result['items'][0]['status']=='source_error'
    assert result['items'][1]['status']=='source_stopped'
    assert result['items'][0]['content_sha256']==hashlib.sha256(challenged.raw).hexdigest()


@pytest.mark.parametrize('status,attempts',[(400,1),(302,1),(500,3)])
def test_status_budget_nontransient_and_close(tmp_path,monkeypatch,status,attempts):
    source=[Response(status=status) for _ in range(attempts)]+[Response(body(sample('601318')))]
    refs=list(source)
    result,calls,waits=collect(tmp_path,monkeypatch,source)
    assert len(calls)==attempts+1
    assert len(waits)==attempts
    assert all(response.closed for response in refs)
    assert result['items'][1]['status']=='downloaded'


def test_refresh_failure_retains_blob_but_does_not_reuse_success(tmp_path,monkeypatch):
    source=responses()
    raw=source[0].raw
    collect(tmp_path,monkeypatch,source)
    result,calls,_=collect(tmp_path,monkeypatch,[Response(status=400),Response(status=400)],refresh=True)
    assert len(calls)==2
    assert all(item['status']=='source_error' for item in result['items'])
    assert (tmp_path/'cache'/f'{hashlib.sha256(raw).hexdigest()}.jsonbody').read_bytes()==raw


def test_invalid_dates_or_company_inputs_rejected_before_cache_or_network(tmp_path):
    for companies,as_of,start in [([{'symbol':'bad'}],NOW.date(),START),
         ([{'symbol':'600900'}]*2,NOW.date(),START),([{'symbol':'600900'}],date(2026,10,16),START),
         ([{'symbol':'600900'}],NOW.date(),date(2026,10,16))]:
        with pytest.raises(ValueError):
            module.collect_capital_history(companies,tmp_path/'cache',as_of=as_of,start_date=start,http_post=lambda *a,**k:pytest.fail('不得联网'))
    assert not (tmp_path/'cache').exists()


def test_historical_requests_are_explicitly_retrospective(tmp_path,monkeypatch):
    source=responses()
    result=module.collect_capital_history([{'symbol':'600900'}],tmp_path/'cache',as_of=date(2020,9,29),
        http_post=lambda *a,**k:source.pop(0),sleep=lambda _:None)
    assert result['retrospective'] is True
    assert result['point_in_time_verified'] is False
    assert all(row['announced_on']<='2020-09-29' and row['effective_on']<='2020-09-29' for row in result['items'][0]['rows'])
    assert result['items'][0]['fetched_at'].startswith('2026-10-15')


def test_retry_backoff_is_three_then_ten_and_next_company_one_second(tmp_path,monkeypatch):
    result,calls,waits=collect(tmp_path,monkeypatch,[Response(status=500),Response(status=500),Response(body(sample())),Response(body(sample('601318')))])
    assert waits==[3,10,1]
    assert len(calls)==4
    assert result['status']=='complete'


def test_production_post_and_parser_each_have_total_deadline(tmp_path,monkeypatch):
    calls=[]
    def bounded(module_name,function,*,args,kwargs=None,timeout):
        assert timeout==15
        calls.append((function,args,kwargs))
        if function=='_post_once':
            return {'status':200,'raw':body(sample(args[0])),'error':None}
        return module.parse_capital_history(*args,**(kwargs or {}))
    monkeypatch.setattr(module,'bounded_provider_call',bounded)
    monkeypatch.setattr(module.requests,'post',lambda *a,**k:pytest.fail('主进程不得直接HTTP'))
    result=module.collect_capital_history([{'symbol':'600900'}],tmp_path/'cache',as_of=NOW.date(),sleep=lambda _:None)
    assert result['status']=='complete'
    assert [entry[0] for entry in calls]==['_post_once','parse_capital_history']
    assert calls[0][1]==('600900','1990-01-01','2026-10-15')
    assert 'Accept-Enckey' not in json.dumps(result,allow_nan=False)


def test_production_nontransient_size_error_not_retried(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(module,'bounded_provider_call',lambda *a,**k:calls.append(1) or {'status':200,'raw':b'','error':'原文超过4MiB'})
    result=module.collect_capital_history([{'symbol':'600900'}],tmp_path/'cache',as_of=NOW.date(),sleep=lambda _:None)
    assert calls==[1]
    assert result['items'][0]['status']=='source_error'


def test_blob_archive_error_does_not_trigger_repeated_network(tmp_path,monkeypatch):
    original=module._atomic
    def save(path,raw):
        if path.suffix=='.jsonbody':
            raise OSError('合成缓存存储失败')
        return original(path,raw)
    monkeypatch.setattr(module,'_atomic',save)
    result,calls,_=collect(tmp_path,monkeypatch,responses())
    assert len(calls)==2
    assert all(item['status']=='source_error' for item in result['items'])
    assert all(item['content_sha256'] for item in result['items'])


def test_oversize_response_closed_and_not_retried(tmp_path,monkeypatch):
    huge=Response(b'x'*(4*1024*1024+1))
    result,calls,_=collect(tmp_path,monkeypatch,[huge,Response(body(sample('601318')))])
    assert huge.closed
    assert len(calls)==2
    assert result['items'][0]['status']=='source_error'
    assert result['items'][1]['status']=='downloaded'


def test_stream_oserror_retries_closes_and_respects_backoff(tmp_path,monkeypatch):
    class Broken(Response):
        def iter_content(self,chunk_size):
            yield b'prefix'
            raise OSError('合成流中断')
    broken=Broken()
    result,calls,waits=collect(tmp_path,monkeypatch,[broken,*responses()])
    assert len(calls)==3
    assert broken.closed
    assert waits==[3,1]
    assert result['status']=='complete'


@pytest.mark.parametrize('manifest',[None,[],1,{'status':'downloaded','content_sha256':[]}])
def test_bad_manifest_refetches_without_aborting_other_company(tmp_path,monkeypatch,manifest):
    collect(tmp_path,monkeypatch,responses())
    for path in (tmp_path/'cache').glob('*.json'):
        path.write_text(json.dumps(manifest),encoding='utf-8')
    result,calls,_=collect(tmp_path,monkeypatch,responses())
    assert len(calls)==2
    assert result['status']=='complete'


def test_hash_corrupt_cache_refetches_and_old_valid_blob_preserved(tmp_path,monkeypatch):
    source=responses()
    raw=source[0].raw
    collect(tmp_path,monkeypatch,source)
    path=tmp_path/'cache'/f'{hashlib.sha256(raw).hexdigest()}.jsonbody'
    path.write_bytes(b'corrupt')
    result,calls,_=collect(tmp_path,monkeypatch,[Response(raw)])
    assert len(calls)==1
    assert result['items'][0]['status']=='downloaded'
    assert result['items'][1]['status']=='cached'
    assert path.read_bytes()==raw


def test_future_cached_capture_is_refetched(tmp_path,monkeypatch):
    collect(tmp_path,monkeypatch,responses())
    for manifest in (tmp_path/'cache').glob('*.json'):
        record=json.loads(manifest.read_text(encoding='utf-8'))
        record['fetched_at']='2027-01-01T00:00:00+00:00'
        manifest.write_text(json.dumps(record),encoding='utf-8')
    result,calls,_=collect(tmp_path,monkeypatch,responses())
    assert len(calls)==2
    assert all(item['status']=='downloaded' for item in result['items'])


def test_cache_still_usable_after_source_stops(tmp_path,monkeypatch):
    collect(tmp_path,monkeypatch,responses())
    # 只破坏首家缓存；第二家同日有效缓存仍可用，不因前一HTTP403丢失。
    for manifest in (tmp_path/'cache').glob('*.json'):
        record=json.loads(manifest.read_text(encoding='utf-8'))
        if record['symbol']=='600900':
            manifest.write_text('null',encoding='utf-8')
    result,calls,_=collect(tmp_path,monkeypatch,[Response(status=403)])
    assert len(calls)==1
    assert [item['status'] for item in result['items']]==['source_error','cached']


def test_cache_reads_have_real_byte_limits(tmp_path,monkeypatch):
    collect(tmp_path,monkeypatch,responses())
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
    result,calls,_=collect(tmp_path,monkeypatch,[])
    assert calls==[]
    assert result['status']=='complete'
    assert sizes.count(65537)==2
    assert sizes.count(4*1024*1024+1)==2


def test_declared_count_mismatch_is_partial_even_when_transport_succeeds(tmp_path, monkeypatch):
    raw = sample('600900')
    raw['count'] = raw['count'] + 1
    result, calls, _ = collect(tmp_path, monkeypatch, [Response(body(raw)), Response(body(sample('601318')))])
    assert len(calls) == 2
    assert result['items'][0]['status'] == 'downloaded'
    assert result['items'][0]['complete'] is False
    assert result['status'] == 'partial'
