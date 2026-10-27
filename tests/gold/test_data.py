"""采集预算、独立缓存和失败旧值的集成回归。"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

from gold.data import GoldCollector, SourceCalendar
from gold.models import GoldObservation, GoldRules
from gold.sources import empty_series

NOW = datetime(2026, 10, 26, 10, tzinfo=UTC)


def good_executor(module, function, kwargs, *, timeout):
    series = empty_series(kwargs['key'], NOW, '')
    series.status, series.error = 'ok', None
    series.observations = [GoldObservation(observation_date=date(2026,10,23), value=100)]
    return series.model_dump(mode='json')


def test_cache_reuses_valid_and_refresh_bypasses(tmp_path):
    calls = []
    def execute(*args, **kwargs):
        calls.append(args[2]['key'])
        return good_executor(*args, **kwargs)
    collector = GoldCollector(tmp_path, request_executor=execute, sources=['dollar'])
    first = collector.collect(now=NOW)
    second = collector.collect(now=NOW+timedelta(hours=1))
    assert len(calls) == 1
    assert second.series['dollar'].as_of == first.series['dollar'].as_of
    collector.collect(now=NOW+timedelta(hours=2), refresh=True)
    assert len(calls) == 2
    assert not (tmp_path/'radar').exists()


def test_expired_failure_keeps_observation_and_fetch_date(tmp_path):
    collector = GoldCollector(tmp_path, request_executor=good_executor, sources=['dollar'])
    first = collector.collect(now=NOW)
    def fail(*args, **kwargs):
        raise RuntimeError('网络失效')
    collector.request_executor = fail
    second = collector.collect(now=NOW+timedelta(days=1))
    assert second.series['dollar'].status == 'stale'
    assert second.series['dollar'].as_of == date(2026,10,23)
    assert second.series['dollar'].fetched_at == first.series['dollar'].fetched_at
    assert '网络失效' in (second.series['dollar'].error or '')


def test_no_cache_and_global_are_explicitly_unavailable(tmp_path):
    def fail(*args, **kwargs):
        raise TimeoutError('来源硬截止')
    result = GoldCollector(tmp_path, request_executor=fail, sources=['domestic','global_reserves']).collect(now=NOW)
    assert result.series['domestic'].status == 'unavailable'
    assert result.series['global_reserves'].status == 'unavailable'
    assert '接入验收' in (result.series['global_reserves'].error or '')


def test_unknown_calendar_six_hours_and_verified_does_not_cross_update(tmp_path):
    collector = GoldCollector(tmp_path)
    assert collector.cache_expiry('domestic',NOW) == NOW+timedelta(hours=6)
    collector.calendars = {'domestic': lambda _: SourceCalendar(closed=True, next_update=NOW+timedelta(hours=8), evidence='上金所已核验发布日历')}
    assert collector.cache_expiry('domestic',NOW) == NOW+timedelta(hours=8)
    collector.calendars = {'domestic': lambda _: SourceCalendar(closed=True, next_update=NOW+timedelta(hours=2), evidence='日历')}
    assert collector.cache_expiry('domestic',NOW) == NOW+timedelta(hours=2)
    assert collector.cache_expiry('china_reserves',NOW) == NOW+timedelta(hours=24)


def test_max_three_and_total_budget_stops_starting_sources(tmp_path):
    lock = threading.Lock()
    active = maximum = started = 0
    def execute(*args, **kwargs):
        nonlocal active, maximum, started
        with lock:
            active += 1; maximum = max(maximum, active); started += 1
        try:
            time.sleep(kwargs['timeout'] + .01)
            raise TimeoutError('截止')
        finally:
            with lock:
                active -= 1
    rules = GoldRules(source_timeout_seconds=.1,total_timeout_seconds=.06)
    start = time.monotonic()
    result = GoldCollector(tmp_path,rules,request_executor=execute).collect(now=NOW)
    assert time.monotonic()-start < .3
    assert maximum <= 3 and started <= 3 and active == 0
    assert len(result.series) == 8


def test_real_process_timeout_leaves_no_child():
    import multiprocessing

    import pytest

    from radar.provider_request import ProviderRequestError, bounded_provider_call
    before = {child.pid for child in multiprocessing.active_children()}
    with pytest.raises(ProviderRequestError):
        bounded_provider_call('time','sleep',args=(10,),timeout=.05)
    assert {child.pid for child in multiprocessing.active_children()} == before


def test_future_and_wrong_identity_not_cached(tmp_path):
    def wrong(*args, **kwargs):
        result = good_executor(*args, **kwargs)
        result['key'] = 'international'
        return result
    result = GoldCollector(tmp_path,request_executor=wrong,sources=['dollar']).collect(now=NOW)
    assert result.series['dollar'].status == 'unavailable'
    assert not list(tmp_path.rglob('*.json'))


def test_corrupt_cache_isolated(tmp_path):
    collector = GoldCollector(tmp_path,request_executor=good_executor,sources=['dollar'])
    collector.collect(now=NOW)
    file = next(tmp_path.rglob('*.json'))
    file.write_text('{broken',encoding='utf8')
    result = collector.collect(now=NOW)
    assert result.series['dollar'].status == 'ok'


def test_future_observation_rejected(tmp_path):
    def future(*args, **kwargs):
        result = good_executor(*args, **kwargs)
        result['observations'][0]['observation_date'] = '2026-10-27'
        return result
    result = GoldCollector(tmp_path,request_executor=future,sources=['dollar']).collect(now=NOW)
    assert result.series['dollar'].status == 'unavailable'
    assert '未来' in (result.series['dollar'].error or '')


def test_rules_version_has_separate_cache(tmp_path):
    GoldCollector(tmp_path,request_executor=good_executor,sources=['dollar']).collect(now=NOW)
    calls = []
    def execute(*args, **kwargs):
        calls.append(True)
        return good_executor(*args, **kwargs)
    GoldCollector(tmp_path,GoldRules(version='gold-v2'),request_executor=execute,sources=['dollar']).collect(now=NOW)
    assert calls == [True]
    assert len(list(tmp_path.rglob('*.json'))) == 2


def test_no_naive_clock(tmp_path):
    import pytest
    with pytest.raises(ValueError,match='时区'):
        GoldCollector(tmp_path).collect(now=NOW.replace(tzinfo=None))


def test_parallel_reports_share_three_request_slots(tmp_path):
    lock = threading.Lock()
    active = maximum = 0
    def execute(*args, **kwargs):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        try:
            time.sleep(.03)
            return good_executor(*args, **kwargs)
        finally:
            with lock:
                active -= 1
    collectors = [GoldCollector(tmp_path / str(i), request_executor=execute,
                               sources=['dollar', 'fx', 'holdings']) for i in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda collector: collector.collect(now=NOW), collectors))
    assert len(results) == 2 and active == 0
    assert maximum <= 3
