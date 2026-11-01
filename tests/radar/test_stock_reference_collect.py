"""资料请求预算、成功缓存保护及来源独立任务回归。"""

import pytest

from data.collection_state import ValueResult, make_record
from radar.stocks.reference_collect import (
    INDUSTRY_URLS,
    MARKET_URL,
    ReferenceClient,
    ReferenceUnavailable,
)
from radar.stocks.reference_task import REFERENCE_VERSION
from radar.stocks.update_runner import StockUpdateRunner
from tests.radar.test_stock_financial_source import DAY, environment
from tests.radar.test_stock_reference_task import snapshot


def test_cache_keeps_original_time_and_force_failure_does_not_replace_index(tmp_path):
    calls = []
    client = ReferenceClient(tmp_path, DAY, fetch=lambda *args: (calls.append(args) or (200, b'original')))
    raw, first = client.get(MARKET_URL, {'x': '1'})
    client.commit(first)
    client.refresh = True
    client.fetch = lambda *args: (400, b'')
    with pytest.raises(ReferenceUnavailable):
        client.get(MARKET_URL, {'x': '1'})
    client.refresh = False
    raw, cached = client.get(MARKET_URL, {'x': '1'})
    assert raw == b'original' and cached['fetched_at'] == first['fetched_at'] and cached['cache_status'] == 'cache'
    assert len(calls) == 1


def test_limit_stops_same_source_but_not_other_source(tmp_path):
    calls = []
    def fetch(url, params):
        calls.append(url)
        return (429, b'') if url == MARKET_URL else (200, b'independent')
    client = ReferenceClient(tmp_path, DAY, fetch=fetch, wait=lambda _: pytest.fail('429不可重试'))
    for symbol in ('600900', '000001'):
        with pytest.raises(ReferenceUnavailable):
            client.get(MARKET_URL, {'symbol': symbol})
    client.get(INDUSTRY_URLS['history'], {})
    assert calls == [MARKET_URL, INDUSTRY_URLS['history']]


def test_retry_budget_is_three_and_no_outer_retry(tmp_path):
    calls, waits = [], []
    client = ReferenceClient(tmp_path, DAY, fetch=lambda *args: (calls.append(1) or (500, b'')), wait=waits.append)
    with pytest.raises(ReferenceUnavailable):
        client.get(MARKET_URL, {})
    assert len(calls) == 3 and waits == [3, 10]


def test_industry_capture_is_shared_between_pools_and_loaded_from_task(tmp_path):
    from radar.stocks.industry_source import _registered_load_industry_source
    from radar.stocks.reference_collect import ReferenceCollector
    from radar.stocks.update_runner import _artifact
    from tests.radar.test_stock_industry_source import seed
    resource = tmp_path / 'resource'
    resource.mkdir()
    source, manifest = seed(resource)
    payloads = {INDUSTRY_URLS[key]: (source / manifest[key]['path']).read_bytes() for key in ('history', 'taxonomy')}
    calls = []
    def fetch(url, params):
        calls.append(url)
        return 200, payloads[url]
    _, _, tasks, _ = environment(tmp_path)
    for pool in ('growth_trend', 'dividend_value'):
        frozen = snapshot([pool])
        run = tasks.enqueue(kind='reference_review', pool_ids=[pool], target_date=DAY, rule_version=REFERENCE_VERSION, snapshot=frozen)
        claimed = tasks.claim('worker')
        assert claimed is not None
        collector = ReferenceCollector(tmp_path, tmp_path / 'stock-updates/cache',
            tmp_path / 'stock-updates/reference-snapshots' / pool, frozen, DAY, fetch=fetch)
        result = collector.collect('industry')
        assert result.value is not None
        ref = _artifact(tmp_path / 'stock-updates/artifacts', result.value)
        tasks.record_item(run['run_id'], 'worker', {'scope': 'reference:industry', 'status': 'completed',
            'summary': '行业来源完成', 'artifact': ref}, lease_token=claimed['lease_token'])
        tasks.finish(run['run_id'], 'worker', lease_token=claimed['lease_token'], status='partial')
        actual = _registered_load_industry_source(tmp_path, '600900', DAY, tmp_path / result.value['manifest']['path'])
        assert actual['status'] == 'current' and actual['industry']['level1'] == '一级'
    assert len(calls) == 2


def test_custom_database_and_source_change_invalidate_archive(tmp_path):
    from radar.stocks.company_profile_source import load_company_profile_source
    from radar.stocks.research_store import StockRevisionConflict
    from stock_robot.stock_admission import evaluate_store
    research, archive, tasks, _ = environment(tmp_path)
    frozen = snapshot()
    source = load_company_profile_source(tmp_path, '600900', DAY, db=research.db_path)
    inputs = archive.capture_inputs(symbol='600900')
    inputs['companies'][0].update(company_profile_source=source, profile_as_of=DAY.isoformat())
    report = evaluate_store(research, DAY, symbol='600900', include_styles=True, profile_sources={'600900': source})
    saved = archive.save_evaluation(report, inputs)
    tasks.enqueue(kind='reference_review', pool_ids=frozen['pool_ids'], target_date=DAY, rule_version=REFERENCE_VERSION, snapshot=frozen)
    with pytest.raises(StockRevisionConflict):
        archive.assert_current(saved['evaluation_id'])


@pytest.mark.parametrize('empty', [True, False])
def test_empty_or_partial_industry_does_not_become_success_cache(tmp_path, empty):
    import io

    import pandas as pd

    from radar.stocks.reference_collect import ReferenceCollector
    from radar.stocks.reference_task import reference_snapshot
    from tests.radar.test_stock_industry_source import seed
    from tests.radar.test_stock_reference_task import scope_document
    resource = tmp_path / 'resource'
    resource.mkdir()
    source, manifest = seed(resource)
    history = (source / manifest['history']['path']).read_bytes()
    if empty:
        buf = io.BytesIO()
        pd.DataFrame(columns=['股票代码', '计入日期', '更新日期', '行业代码']).to_excel(buf, index=False)
        history = buf.getvalue()
    payloads = {INDUSTRY_URLS['history']: history, INDUSTRY_URLS['taxonomy']: (source / manifest['taxonomy']['path']).read_bytes()}
    scope = scope_document()
    scope['companies'].append({**scope['companies'][0], 'symbol': '000001', 'name': '平安银行'})
    for pool in scope['pools'].values():
        pool['companies'].append('000001')
    frozen = reference_snapshot(scope, ['growth_trend'], DAY, source_run_id='scope', source_sha256='a' * 64)
    collector = ReferenceCollector(tmp_path, tmp_path / 'cache', tmp_path / 'stock-updates/reference-snapshots/test',
        frozen, DAY, fetch=lambda url, params: (200, payloads[url]))
    if empty:
        with pytest.raises(ReferenceUnavailable):
            collector.collect('industry')
    else:
        assert collector.collect('industry').record.result_state == 'partial'
    assert not list((tmp_path / 'cache/reference').glob('*.json'))


def test_task_does_not_update_industry_and_resume_skips_success(tmp_path):
    _, _, tasks, _ = environment(tmp_path)
    calls = []
    class FakeCollector:
        def __init__(self, state, cache, root, frozen, day):
            self.state, self.root, self.frozen, self.day = state, root, frozen, day
        def collect(self, channel, symbol, *, window):
            calls.append(channel)
            if channel == 'market_value':
                raise ReferenceUnavailable('日历待补')
            import hashlib

            from radar.stocks.research_store import _json
            from radar.stocks.structured_collect import _atomic
            self.root.mkdir(parents=True, exist_ok=True)
            scope_raw = _json(self.frozen['scope_document']).encode()
            _atomic(self.root / 'scope.json', scope_raw)
            raw = _json({'as_of': self.day.isoformat(), 'scope': {'path': 'scope.json',
                        'sha256': hashlib.sha256(scope_raw).hexdigest(), 'bytes': len(scope_raw)}}).encode()
            path = self.root / (channel + '.json')
            _atomic(path, raw)
            return ValueResult({'channel': channel, 'as_of': self.day.isoformat(), 'manifest': {
                'path': path.relative_to(self.state).as_posix(), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}}, make_record(channel, 'complete'))
    frozen = snapshot()
    run = tasks.enqueue(kind='reference_review', pool_ids=frozen['pool_ids'], target_date=DAY, rule_version=REFERENCE_VERSION, snapshot=frozen)
    claimed = tasks.claim('legacy-worker')
    assert claimed is not None
    from radar.stocks.update_runner import _artifact
    tasks.record_item(run['run_id'], 'legacy-worker', {'scope': 'reference:industry', 'status': 'failed',
        'summary': '旧行业失败记录', 'artifact': _artifact(tmp_path / 'stock-updates/artifacts', {'legacy': True})},
        lease_token=claimed['lease_token'])
    tasks.finish(run['run_id'], 'legacy-worker', lease_token=claimed['lease_token'], status='partial')
    tasks.resume_reference(run['run_id'], frozen['pool_ids'])
    runner = StockUpdateRunner(tasks, tmp_path / 'stock-updates/cache', tmp_path / 'stock-updates/artifacts', reference_factory=FakeCollector)
    first = runner.execute_one('worker')
    assert first is not None and first['status'] == 'partial'
    tasks.resume_reference(run['run_id'], frozen['pool_ids'])
    resumed = runner.execute_one('worker')
    assert resumed is not None and resumed['status'] == 'partial'
    assert calls == ['profile', 'market_value', 'market_value']
    assert 'reference:industry' not in resumed['pending_scopes']
