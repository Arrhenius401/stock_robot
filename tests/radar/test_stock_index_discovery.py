"""指数采集器的来源隔离、证据缓存与网络预算回归。"""
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
import requests

from radar.provider_request import ProviderRequestError
from radar.stocks import index_discovery as discovery

DAY = date(2026, 10, 16)


class Response:
    def __init__(self, status: int = 200, raw: bytes = b'official-index'):
        self.status_code = status
        self.raw = raw
        self.closed = False

    def iter_content(self, **kwargs: Any):
        yield self.raw

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(discovery, '_now', lambda: datetime(2026, 10, 16, 8, tzinfo=UTC))


@pytest.fixture
def simple_parser(monkeypatch):
    def parse(raw, code, as_of):
        return {'index_code': code, 'complete': True,
                'rows': [{'symbol': '600000', 'name': '测试公司', 'exchange': 'SSE', 'source_fields': {},
                          'snapshot_date': '2026-09-30', 'effective_date': None}],
                'row_count': 1, 'expected_count': 1,
                'snapshot_date': '2026-09-30', 'effective_date': None, 'diagnostics': []}
    monkeypatch.setattr(discovery, '_parse_source', parse)
    return parse


def test_same_day_cache_and_no_qualification(tmp_path, simple_parser):
    calls, waits = [], []

    def get(url, **kwargs):
        calls.append(url)
        assert kwargs['allow_redirects'] is False
        assert kwargs['stream'] and kwargs['timeout'] == 15
        return Response()

    result = discovery.collect_index_scope(tmp_path, as_of=DAY, http_get=get, sleep=waits.append)
    assert len(calls) == 5 and waits == [1] * 4
    assert result['research_only'] and not result['financial_verified']
    assert result['members_published'] == 0
    assert result['status'] == 'complete' and len(result['companies']) == 1
    assert all(item['effective_date'] is None and item['snapshot_date'] == '2026-09-30' for item in result['items'])
    assert all(item['content_length'] == len(b'official-index') for item in result['items'])
    calls.clear()
    cached = discovery.collect_index_scope(tmp_path, as_of=DAY, http_get=get, sleep=waits.append)
    assert not calls and all(item['status'] == 'cached' for item in cached['items'])


def test_429_stops_host_but_keeps_other_provider(tmp_path, simple_parser):
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return Response(429 if 'csindex.com.cn' in url else 200)

    result = discovery.collect_index_scope(tmp_path, as_of=DAY, http_get=get, sleep=lambda _: None)
    assert len(calls) == 2
    assert sum(item['status'] == 'source_stopped' for item in result['items']) == 3
    assert result['status'] == 'partial'
    assert next(item for item in result['items'] if item['index_code'] == '399006')['status'] == 'downloaded'


def test_retry_only_network_and_5xx(tmp_path, simple_parser):
    calls, waits = {}, []

    def get(url, **kwargs):
        calls[url] = calls.get(url, 0) + 1
        if calls[url] == 1:
            raise requests.ConnectionError('暂时断网')
        return Response(503 if calls[url] == 2 else 200)

    result = discovery.collect_index_scope(tmp_path, as_of=DAY, http_get=get, sleep=waits.append)
    assert all(value == 3 for value in calls.values())
    assert waits.count(3) == 5 and waits.count(10) == 5
    assert all(item['status'] == 'downloaded' for item in result['items'])
    calls.clear()
    result = discovery.collect_index_scope(tmp_path / 'redirects', as_of=DAY,
                                           http_get=lambda url, **kw: (calls.setdefault(url, 1) and Response(302)), sleep=lambda _: None)
    assert len(calls) == 5 and all(item['status'] == 'source_error' for item in result['items'])


def test_network_and_parser_have_separate_total_budgets(tmp_path, simple_parser, monkeypatch):
    calls = []

    def bounded(module, function, *, args, timeout):
        calls.append((function, timeout))
        if function == '_download_once':
            return {'status': 200, 'raw': b'official', 'error': None}
        return simple_parser(*args)

    monkeypatch.setattr(discovery, 'bounded_provider_call', bounded)
    discovery.collect_index_scope(tmp_path, as_of=DAY, sleep=lambda _: None)
    assert calls == [('_download_once', 15), ('_parse_source', 15)] * 5
    calls.clear()

    def timeout(module, function, *, args, timeout):
        calls.append((function, timeout))
        raise ProviderRequestError('数据源请求超过15秒')

    monkeypatch.setattr(discovery, 'bounded_provider_call', timeout)
    result = discovery.collect_index_scope(tmp_path, as_of=DAY, refresh=True, sleep=lambda _: None)
    assert len(calls) == 15 and all(item['status'] == 'source_error' for item in result['items'])


@pytest.mark.parametrize('field,value', [('content_sha256', '0' * 64), ('content_length', 1),
                                        ('url', 'https://invalid.test'), ('fetched_at', '2026-10-15T08:00:00+00:00')])
def test_cache_metadata_tampering_reloads(tmp_path, simple_parser, field, value):
    import json

    discovery.collect_index_scope(tmp_path, as_of=DAY, http_get=lambda *a, **kw: Response(), sleep=lambda _: None)
    manifest = next(tmp_path.glob('*.json'))
    record = json.loads(manifest.read_text())
    record[field] = value
    manifest.write_text(json.dumps(record))
    calls = []
    discovery.collect_index_scope(tmp_path, as_of=DAY, http_get=lambda url, **kw: (calls.append(url) or Response()), sleep=lambda _: None)
    assert len(calls) == 1


def test_corrupt_raw_cache_reloads_all(tmp_path, simple_parser):
    discovery.collect_index_scope(tmp_path, as_of=DAY, http_get=lambda *a, **kw: Response(), sleep=lambda _: None)
    next(tmp_path.glob('*.bin')).write_bytes(b'corrupt')
    calls = []
    discovery.collect_index_scope(tmp_path, as_of=DAY, http_get=lambda url, **kw: (calls.append(url) or Response()), sleep=lambda _: None)
    # 首次修复内容寻址文件后，其他来源即可复用各自清单。
    assert len(calls) == 1


def test_historical_cache_only_and_future_rejected(tmp_path, simple_parser, monkeypatch):
    calls = []
    get = lambda url, **kw: (calls.append(url) or Response())
    discovery.collect_index_scope(tmp_path, as_of=DAY, http_get=get, sleep=lambda _: None)
    monkeypatch.setattr(discovery, '_now', lambda: datetime(2026, 10, 17, 8, tzinfo=UTC))
    calls.clear()
    result = discovery.collect_index_scope(tmp_path, as_of=DAY, refresh=True, http_get=get, sleep=lambda _: None)
    assert not calls and all(item['status'] == 'cached' for item in result['items'])
    result = discovery.collect_index_scope(tmp_path, as_of=date(2026, 10, 15), http_get=get, sleep=lambda _: None)
    assert not calls and all(item['status'] == 'source_error' for item in result['items'])
    with pytest.raises(ValueError, match='未来'):
        discovery.collect_index_scope(tmp_path, as_of=date(2026, 10, 18), http_get=get)


def test_symlink_cache_rejected(tmp_path, simple_parser, monkeypatch):
    original = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda path: path == tmp_path or original(path))
    with pytest.raises(ValueError, match='符号链接'):
        discovery.collect_index_scope(tmp_path, as_of=DAY)


def test_oversized_stream_not_retried(tmp_path, simple_parser):
    calls = []
    result = discovery.collect_index_scope(tmp_path, as_of=DAY,
        http_get=lambda url, **kw: (calls.append(url) or Response(raw=b'x' * (4 * 1024 * 1024 + 1))), sleep=lambda _: None)
    assert len(calls) == 5
    assert all('4MiB' in item['error'] for item in result['items'])


def test_parse_error_isolated_from_other_sources(tmp_path, simple_parser, monkeypatch):
    failing_code = next(iter(discovery.INDEX_SOURCES))

    def parse(raw, code, as_of):
        if code == failing_code:
            raise ValueError('无效来源表格')
        return simple_parser(raw, code, as_of)

    monkeypatch.setattr(discovery, '_parse_source', parse)
    result = discovery.collect_index_scope(tmp_path, as_of=DAY, http_get=lambda *a, **kw: Response(), sleep=lambda _: None)
    assert result['status'] == 'partial'
    assert sum(item['status'] == 'downloaded' for item in result['items']) == 4
    assert result['items'][0]['error'] == '无效来源表格'
