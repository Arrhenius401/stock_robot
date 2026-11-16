"""基础资料专用采集；复用计算器，成功缓存与最新尝试分离。"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from time import sleep
from typing import Any

import requests

from data.collection_state import ValueResult, make_record
from radar.provider_request import ProviderRequestError, bounded_provider_call
from radar.stocks.official_roster import collect_stock_roster
from radar.stocks.research_store import _json, _sha
from radar.stocks.structured_collect import _atomic, _safe_path

logger = logging.getLogger(__name__)
MARKET_URL = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
INDUSTRY_URLS = {
    'history': 'https://www.swsresearch.com/swindex/pdf/SwClass2021/StockClassifyUse_stock.xls',
    'taxonomy': 'https://www.swsresearch.com/swindex/pdf/SwClass2021/2014to2021.xlsx',
}
_LIMIT = 8 * 1024 * 1024


def _http(url: str, params: dict[str, str]) -> tuple[int, bytes]:
    if url not in {MARKET_URL, *INDUSTRY_URLS.values()}:
        raise ValueError('基础资料端点未获允许')
    with requests.get(url, params=params, timeout=15, allow_redirects=False, stream=True) as response:
        if response.status_code != 200:
            return response.status_code, b''
        raw = bytearray()
        for chunk in response.iter_content(65536):
            raw.extend(chunk)
            if len(raw) > _LIMIT:
                raise ValueError('基础资料原文超过8MiB')
        return response.status_code, bytes(raw)


def _fetch(url: str, params: dict[str, str]) -> tuple[int, bytes]:
    return bounded_provider_call(__name__, '_http', args=(url, params), timeout=15)


class ReferenceUnavailable(ValueError):
    """受控的来源失败，不把第三方异常原文传入页面。"""


class ReferenceClient:
    """缓存命中保留原时间；完整校验后才更新成功索引。"""
    def __init__(self, cache: Path, as_of: date, *, refresh: bool = False,
                 fetch: Callable[..., Any] = _fetch, wait: Callable[[float], Any] = sleep):
        self.cache, self.as_of, self.refresh, self.fetch, self.wait = cache.absolute(), as_of, refresh, fetch, wait
        self.stopped: set[str] = set()

    def get(self, url: str, params: dict[str, str]) -> tuple[bytes, dict[str, Any]]:
        if url not in {MARKET_URL, *INDUSTRY_URLS.values()}:
            raise ReferenceUnavailable('资料端点未获允许')
        identity = {'version': 'stock-reference-cache-v1', 'as_of': self.as_of.isoformat(), 'source_url': url, 'request_params': params}
        key = hashlib.sha256(_json(identity).encode()).hexdigest()
        index = self.cache / (key + '.json')
        _safe_path(index)
        if not self.refresh:
            try:
                if index.stat().st_size > 65536:
                    raise ValueError('缓存索引超限')
                meta = json.loads(index.read_bytes())
                digest = _sha(meta['sha256'])
                raw_path = self.cache / (digest + '.raw')
                _safe_path(raw_path)
                if any(meta.get(k) != v for k, v in identity.items()) or raw_path.stat().st_size > _LIMIT:
                    raise ValueError('缓存身份无效')
                raw = raw_path.read_bytes()
                fetched = datetime.fromisoformat(meta['fetched_at'])
                if (fetched.utcoffset() is None or fetched > datetime.now(UTC) or len(raw) != meta['bytes']
                        or hashlib.sha256(raw).hexdigest() != digest):
                    raise ValueError('缓存长度、摘要或时间无效')
                return raw, {**meta, 'cache_status': 'cache'}
            except (OSError, ValueError, TypeError, KeyError) as exc:
                logger.debug('基础资料缓存待补：%s', type(exc).__name__)
        if url in self.stopped:
            raise ReferenceUnavailable('来源限流，已停止本批后续请求')
        for attempt in range(3):
            if attempt:
                self.wait((3, 10)[attempt - 1])
            try:
                status, raw = self.fetch(url, dict(params))
                if status == 429:
                    self.stopped.add(url)
                    raise ReferenceUnavailable('来源限流，已停止本批后续请求')
                if status >= 500:
                    raise ProviderRequestError('来源暂时不可用')
                if status != 200 or not isinstance(raw, bytes) or not 0 < len(raw) <= _LIMIT:
                    raise ReferenceUnavailable('来源响应状态、类型或大小无效')
                meta = {**identity, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
                        'fetched_at': datetime.now(UTC).isoformat(), 'cache_status': 'live', 'cache_key': key}
                _atomic(self.cache / (meta['sha256'] + '.raw'), raw)
                return raw, meta
            except (requests.RequestException, ProviderRequestError, OSError) as exc:
                logger.warning('基础资料请求失败：%s', type(exc).__name__)
                if attempt == 2:
                    break
            except ReferenceUnavailable:
                break
        _atomic(index.with_suffix('.attempt'), _json({'status': 'failed', 'attempted_at': datetime.now(UTC).isoformat()}).encode())
        raise ReferenceUnavailable('来源请求失败或限流，资料保留待补')

    def commit(self, meta: dict[str, Any]) -> None:
        identity = {key: meta[key] for key in ('version', 'as_of', 'source_url', 'request_params')}
        key = hashlib.sha256(_json(identity).encode()).hexdigest()
        _atomic(self.cache / (key + '.json'), _json({key: value for key, value in meta.items() if key not in {'cache_status', 'cache_key'}}).encode())
        _atomic(self.cache / (key + '.attempt'), _json({'status': 'complete', 'attempted_at': datetime.now(UTC).isoformat()}).encode())

    def reject(self, meta: dict[str, Any]) -> None:
        identity = {key: meta[key] for key in ('version', 'as_of', 'source_url', 'request_params')}
        key = hashlib.sha256(_json(identity).encode()).hexdigest()
        _atomic(self.cache / (key + '.attempt'), _json({'status': 'partial', 'attempted_at': datetime.now(UTC).isoformat()}).encode())


class ReferenceCollector:
    """一次任务共享来源停止位、公共表与冻结范围文件。"""
    def __init__(self, state: Path, cache: Path, root: Path, snapshot: dict[str, Any], as_of: date, *,
                 fetch: Callable[..., Any] = _fetch, roster_collector: Callable[..., Any] = collect_stock_roster):
        self.state, self.root, self.snapshot, self.as_of = state, root, snapshot, as_of
        self.client = ReferenceClient(cache / 'reference', as_of, refresh=snapshot['force_refresh'], fetch=fetch)
        self.cache, self.roster_collector = cache, roster_collector
        self.scope = self.write('scope.json', _json(snapshot['scope_document']).encode())
        if self.scope['sha256'] != snapshot['scope_sha256']:
            raise ValueError('基础资料冻结范围摘要不一致')

    def write(self, name: str, raw: bytes) -> dict[str, Any]:
        path = self.root / name
        _atomic(path, raw)
        return {'path': name, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}

    def manifest(self, channel: str, name: str, content: dict[str, Any], *, cached: bool = False, complete: bool = True) -> ValueResult[dict[str, Any]]:
        ref = self.write(name, _json(content).encode())
        ref['path'] = (self.root / name).relative_to(self.state).as_posix()
        return ValueResult({'channel': channel, 'as_of': self.as_of.isoformat(), 'manifest': ref},
            make_record({'profile': '官方上市名录', 'industry': '申万官方行业', 'market_value': '东方财富供应商市值'}[channel],
                        'complete' if complete else 'partial', cache_status='cache' if cached else 'live'))

    def collect(self, channel: str, symbol: str | None = None, *, window: Any = None) -> ValueResult[dict[str, Any]]:
        base = {'as_of': self.as_of.isoformat(), 'scope': self.scope}
        if channel == 'profile':
            result = self.roster_collector(self.cache / 'official-roster', as_of=self.as_of, refresh=self.snapshot['force_refresh'])
            refs = []
            for item in result['items']:
                if item.get('complete') is not True or item.get('status') not in {'downloaded', 'cached'}:
                    continue
                raw = (self.cache / 'official-roster' / item['raw_artifact']).read_bytes()
                if hashlib.sha256(raw).hexdigest() != item['content_sha256']:
                    raise ValueError('名录原文摘要不符')
                ref = self.write(item['content_sha256'] + '.bin', raw)
                refs.append({**ref, 'source': item['source'], 'source_url': item['url'], 'fetched_at': item['fetched_at']})
            if not refs:
                raise ReferenceUnavailable('官方上市名录未取得可用数据')
            symbols = [row['symbol'] for item in result['items'] if item.get('complete') is True for row in item.get('rows', [])]
            covered = all(symbols.count(row['symbol']) == 1 for row in self.snapshot['companies'])
            return self.manifest(channel, 'profile.json', {**base, 'version': 'stock-company-profile-v1', 'rosters': refs},
                                 cached=all(i.get('status') == 'cached' for i in result['items']), complete=result['status'] == 'complete' and covered)
        if channel == 'industry':
            from radar.stocks.industry_source import _classifications
            captures = {key: self.client.get(url, {}) for key, url in INDUSTRY_URLS.items()}
            try:
                classifications = _classifications(captures['history'][0], captures['taxonomy'][0], self.as_of)
                covered = sum(len(classifications.get(row['symbol'], [])) == 1 for row in self.snapshot['companies'])
                if not covered:
                    raise ReferenceUnavailable('行业表没有覆盖本次冻结公司')
            except (ValueError, TypeError, KeyError):
                for _, meta in captures.values():
                    self.client.reject(meta)
                raise
            for key, (raw, meta) in captures.items():
                base[key] = {**self.write(meta['sha256'] + '.bin', raw), 'source_url': meta['source_url'], 'fetched_at': meta['fetched_at']}
            complete = covered == len(self.snapshot['companies'])
            for _, meta in captures.values():
                if complete:
                    self.client.commit(meta)
                else:
                    self.client.reject(meta)
            return self.manifest(channel, 'industry.json', {**base, 'version': 'stock-company-industry-v1'},
                                 cached=all(meta['cache_status'] == 'cache' for _, meta in captures.values()), complete=complete)
        if channel != 'market_value' or symbol is None or not isinstance(window, dict) or len(window.get('days', [])) != 60:
            raise ReferenceUnavailable('市值更新缺少明确的60个已收盘交易日')
        from radar.stocks.market_value_registry import (
            _registered_load_market_value_source,
        )
        params = {'sortColumns': 'TRADE_DATE', 'sortTypes': '-1', 'pageSize': '80', 'pageNumber': '1',
                  'reportName': 'RPT_VALUEANALYSIS_DET', 'columns': 'ALL', 'quoteColumns': '', 'source': 'WEB', 'client': 'WEB',
                  'filter': f'(SECURITY_CODE="{symbol}")(TRADE_DATE>=\'{window["days"][0]}\')(TRADE_DATE<=\'{window["days"][-1]}\')'}
        raw, meta = self.client.get(MARKET_URL, params)
        base.update(version='stock-market-value-registry-v1', window=self.write('window.json', _json(window).encode()),
                    companies={symbol: [{**self.write(meta['sha256'] + '.bin', raw), 'source_url': MARKET_URL, 'fetched_at': meta['fetched_at']}]})
        result = self.manifest(channel, 'market-' + symbol + '.json', base, cached=meta['cache_status'] == 'cache')
        computed = _registered_load_market_value_source(self.state, symbol, self.as_of, self.root / ('market-' + symbol + '.json'))
        if computed['status'] != 'current' or any(check['outcome'] == 'pending_verification' for check in computed['checks']):
            self.client.reject(meta)
            result = self.manifest(channel, 'market-' + symbol + '.json', base, cached=meta['cache_status'] == 'cache', complete=False)
        else:
            self.client.commit(meta)
        return result
