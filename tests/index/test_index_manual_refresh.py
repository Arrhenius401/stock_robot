from datetime import date

from data.base import CollectionResult
from data.schemas import IndexPriceData
from index.request_cache import IndexRequestCache


def test_refresh_bypasses_cache_and_keeps_success_on_failure(tmp_path):
    cache = IndexRequestCache(tmp_path / 'index.db')
    row = IndexPriceData(symbol='000300', trade_date=date(2026, 10, 29), open=10, high=11, low=9, close=10, volume=100)
    class Adapter:
        calls = 0
        def fetch_result(self, *_args, **_kwargs):
            self.calls += 1
            return CollectionResult([row]) if self.calls == 1 else CollectionResult([])
    adapter = Adapter()
    assert cache.fetch_cached(adapter, '000300', 'index_price', index_style='broad').rows
    assert cache.fetch_cached(adapter, '000300', 'index_price', index_style='broad').rows
    assert adapter.calls == 1
    refreshed = cache.fetch_cached(adapter, '000300', 'index_price', index_style='broad', refresh=True)
    assert adapter.calls == 2
    assert refreshed.record is not None
    assert refreshed.rows and refreshed.record.cache_status == 'stale'


def test_pipeline_and_collector_pass_refresh_to_bounded(monkeypatch):
    from data.schemas import AnalysisTarget, IndexAnalysisContext
    from index.collector import IndexDataCollector
    from index.pipeline import IndexPipeline

    target = AnalysisTarget(target_type='index', symbol='HSI:00001.00', name='恒指', market='hk', index_style='overseas')
    calls = []
    def bounded(item, **kwargs):
        calls.append(kwargs.get('refresh_cache', False))
        return IndexAnalysisContext(target=item)
    monkeypatch.setattr('index.collection_budget.collect_bounded', bounded)
    collector = IndexDataCollector()
    collector.collect(target)
    collector.collect(target, refresh_cache=True)
    assert calls == [False, True]
    pipeline = IndexPipeline(llm_enabled=False)
    pipeline._collector = collector
    pipeline.run([target], refresh_cache=True)
    assert calls[-1] is True


def test_bounded_worker_accepts_refresh_flag(monkeypatch, tmp_path):
    import io
    import json
    import sys

    from data.schemas import AnalysisTarget, IndexAnalysisContext
    from index.collection_budget import main

    target = AnalysisTarget(target_type='index', symbol='HSI', name='恒指', market='hk', index_style='overseas')
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps({'target': target.model_dump(), 'output': str(tmp_path / 'out'), 'refresh_cache': True})))
    calls = []
    def collect(_self, value, **kwargs):
        calls.append(kwargs.get('refresh_cache'))
        return IndexAnalysisContext(target=value)
    monkeypatch.setattr('index.collector.IndexDataCollector.collect', collect)
    monkeypatch.setattr('index.collector.IndexDataCollector._checkpoint', lambda *_: None)
    main()
    assert calls == [True]


def test_strategy_force_refresh_keeps_cached_prices_on_failure(tmp_path, monkeypatch):
    from datetime import date

    from index.strategy_data import StrategyDataProvider

    provider = StrategyDataProvider(cache_path=tmp_path / 'strategy.db')
    today = date(2026, 10, 29)
    item = IndexPriceData(symbol='930740', trade_date=today, open=10, high=11, low=9, close=10, volume=100)
    calls = []
    def source(*_args):
        calls.append(True)
        return [item] if len(calls) == 1 else []
    monkeypatch.setattr(provider, '_official_prices', source)
    monkeypatch.setattr(provider, '_fallback_prices', lambda *_: [])
    assert provider.fetch_prices('930740', end=today)
    assert provider.fetch_prices('930740', end=today)
    assert len(calls) == 1
    provider.refresh_cache = True
    assert provider.fetch_prices('930740', end=today)
    assert len(calls) == 2
    assert provider._read_cached('strategy_price', f'csi:930740:{today}')
    assert provider.refresh_notes


def test_refresh_does_not_reuse_another_style_or_ambiguous_legacy(tmp_path):
    cache = IndexRequestCache(tmp_path / 'index.db')
    row = IndexPriceData(symbol='000300', trade_date=date(2026, 10, 29), open=10, high=11, low=9, close=10, volume=100)
    class Adapter:
        def fetch_result(self, *_args, **kwargs):
            return CollectionResult([row]) if kwargs.get('index_style') == 'broad' else CollectionResult([])
    adapter = Adapter()
    cache.fetch_cached(adapter, '000300', 'index_price', index_style='broad')
    assert not cache.fetch_cached(adapter, '000300', 'index_price', index_style='sector', refresh=True).rows
    import json
    cache.cache.put('index_price', '000300', 'last-success', json.dumps({'rows':[row.model_dump(mode='json')], 'record':None}), -1)
    assert not cache.fetch_cached(adapter, '000300', 'index_price', index_style='sector', refresh=True).rows


def test_legacy_cache_only_migrates_when_style_status_matches(tmp_path):
    import json
    cache = IndexRequestCache(tmp_path / 'index.db')
    row = IndexPriceData(symbol='000300', trade_date=date(2026, 10, 29), open=10, high=11, low=9, close=10, volume=100)
    raw = json.dumps({'rows': [row.model_dump(mode='json')], 'record': None})
    cache.cache.put('index_price', '000300', 'last-success', raw, -1)
    cache.cache.put('index_price', '000300:broad', 'status', raw, 86400)
    class Adapter:
        def fetch_result(self, *_args, **_kwargs):
            return CollectionResult([])
    assert cache.fetch_cached(Adapter(), '000300', 'index_price', index_style='broad', refresh=True).rows
    assert not cache.fetch_cached(Adapter(), '000300', 'index_price', index_style='sector', refresh=True).rows


def test_strategy_financial_refresh_failure_keeps_previous_cache(tmp_path, monkeypatch):
    from index.strategy_data import StrategyDataProvider

    provider = StrategyDataProvider(cache_path=tmp_path / 'strategy.db')
    today = date(2026, 10, 29)
    calls = []
    def get(*_args):
        calls.append(True)
        if len(calls) == 1:
            return {'success': True, 'result': {'data': [{'SECURITY_CODE': '600000'}], 'pages': 1}}
        raise OSError('offline')
    monkeypatch.setattr(provider, '_get_json', get)
    rows, errors = provider._cached_report('RPT_DMSK_FN_CASHFLOW', ['600000'], today)
    assert rows and not errors
    provider.refresh_cache = True
    restored, errors = provider._cached_report('RPT_DMSK_FN_CASHFLOW', ['600000'], today)
    assert restored == rows and any('使用原日期缓存' in item for item in errors)
    provider.refresh_cache = False
    assert provider._cached_report('RPT_DMSK_FN_CASHFLOW', ['600000'], today)[0] == rows
    assert len(calls) == 2
