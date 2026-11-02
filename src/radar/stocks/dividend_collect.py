"""按来源保存A股分红结构化原文，采集完成不等于资格通过。"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from time import sleep as default_sleep
from typing import Any

import requests

from radar.provider_request import bounded_provider_call
from radar.stocks.research_store import _symbol
from radar.stocks.structured_collect import _atomic, _json, _safe_path

logger = logging.getLogger(__name__)
VERSION = 'stock-dividend-structured-v1'
URLS = {'cninfo': 'https://webapi.cninfo.com.cn/api/sysapi/p_sysapi1139',
        'eastmoney': 'https://datacenter-web.eastmoney.com/api/data/v1/get'}
MAX_BYTES = 8 * 1024 * 1024


def request_identity(source: str, symbol: str) -> dict[str, Any]:
    """固定请求参数同时用于网络调用与原文身份复核。"""
    symbol = _symbol(symbol)
    if source == 'cninfo':
        return {'method': 'POST', 'params': {'scode': symbol}}
    if source == 'eastmoney':
        return {'method': 'GET', 'params': {'sortColumns': 'REPORT_DATE', 'sortTypes': '-1', 'pageSize': '500',
                'pageNumber': '1', 'reportName': 'RPT_SHAREBONUS_DET', 'columns': 'ALL', 'source': 'WEB',
                'client': 'WEB', 'filter': f'(SECURITY_CODE="{symbol}")'}}
    raise ValueError('未知分红来源')


def _request(source: str, symbol: str) -> tuple[int, bytes]:
    """固定端点，子进程由外层限时；不执行第三方响应脚本。"""
    if source == 'cninfo':
        from akshare.stock.stock_dividend_cninfo import _get_file_content_ths
        from py_mini_racer import MiniRacer

        with MiniRacer() as js:
            js.eval(_get_file_content_ths('cninfo.js'), timeout=2000, max_memory=64 * 1024 * 1024)
            code = js.call('getResCode1', timeout=2000, max_memory=64 * 1024 * 1024)
        response = requests.post(URLS[source], params=request_identity(source, symbol)['params'], headers={'Accept-Enckey': code,
            'Origin': 'http://webapi.cninfo.com.cn', 'Referer': 'http://webapi.cninfo.com.cn/',
            'User-Agent': 'Mozilla/5.0'}, timeout=15, allow_redirects=False, stream=True)
    elif source == 'eastmoney':
        params = request_identity(source, symbol)['params']
        response = requests.get(URLS[source], params=params, timeout=15, allow_redirects=False, stream=True)
    else:
        raise ValueError('未知分红来源')
    try:
        if response.status_code != 200:
            return response.status_code, b''
        body = bytearray()
        for chunk in response.iter_content(65536):
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                raise ValueError('分红响应超过8MiB')
        return response.status_code, bytes(body)
    finally:
        response.close()


def _fetch(source: str, symbol: str) -> tuple[int, bytes]:
    return bounded_provider_call(__name__, '_request', args=(source, symbol), timeout=15)


def validate_source(source: str, symbol: str, raw: bytes) -> list[dict[str, Any]]:
    """必须是完整单页，存在公司代码的记录须逐条一致。"""
    if source not in URLS or not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError('来源或原始响应无效')
    data = _json(raw)
    if not isinstance(data, dict):
        raise TypeError('分红响应不是对象')
    if source == 'cninfo':
        rows = data.get('records')
    else:
        result = data.get('result')
        if data.get('success') is not True or not isinstance(result, dict) or result.get('pages') != 1:
            raise ValueError('东方财富响应缺失或分页不完整')
        rows = result.get('data')
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('分红记录不完整')
    for row in rows:
        code = row.get('SECURITY_CODE', row.get('SECCODE'))
        if code != symbol:
            raise ValueError('分红记录公司身份缺失或不一致')
    return rows


def collect_dividend(symbol: str, as_of: date, cache_dir: Path, *,
                     fetch: Callable[[str, str], tuple[int, bytes]] = _fetch,
                     sleep: Callable[[float], None] = default_sleep,
                     blocked_sources: set[str] | None = None) -> dict[str, Any]:
    """分别缓存成功来源；失败/限流不覆盖成功原文。"""
    symbol = _symbol(symbol)
    if type(as_of) is not date or as_of > datetime.now().astimezone().date():
        raise ValueError('分红研究日期无效')
    root = Path(cache_dir).absolute()
    _safe_path(root)
    root.mkdir(parents=True, exist_ok=True)
    sources: dict[str, Any] = {}
    for source, url in URLS.items():
        manifest = root / f'{source}-{symbol}-{as_of.isoformat()}.json'
        _safe_path(manifest)
        try:
            saved = _json(manifest.read_bytes())
            raw = base64.b64decode(saved['raw'], validate=True)
            fetched = datetime.fromisoformat(saved['fetched_at'])
            if (saved['version'] != VERSION or saved['url'] != url or saved['source'] != source or saved['symbol'] != symbol or saved['as_of'] != as_of.isoformat()
                    or saved['request'] != request_identity(source, symbol)
                    or saved['sha256'] != hashlib.sha256(raw).hexdigest()
                    or fetched.utcoffset() is None or fetched > datetime.now(UTC)):
                raise ValueError('缓存来源身份不符')
            validate_source(source, symbol, raw)
            sources[source] = {**saved, 'status': 'complete', 'cache_status': 'cache'}
            continue
        except (OSError, ValueError, TypeError, KeyError) as exc:
            logger.debug('分红缓存不可用，重新采集 %s %s: %s', source, symbol, exc)
        if blocked_sources is not None and source in blocked_sources:
            sources[source] = {'source': source, 'status': 'stopped', 'url': url}
            continue
        result: dict[str, Any] = {'source': source, 'status': 'failed', 'url': url}
        for attempt in range(3):
            if attempt:
                sleep((3.0, 10.0)[attempt-1])
            try:
                status, raw = fetch(source, symbol)
                if status == 429:
                    result['status'] = 'stopped'
                    if blocked_sources is not None:
                        blocked_sources.add(source)
                    break
                if status >= 500:
                    continue
                if status != 200:
                    raise ValueError(f'来源HTTP {status}')
                validate_source(source, symbol, raw)
                result = {'version': VERSION, 'source': source, 'symbol': symbol, 'as_of': as_of.isoformat(),
                          'url': url, 'request': request_identity(source, symbol), 'status': 'complete', 'fetched_at': datetime.now(UTC).isoformat(),
                          'sha256': hashlib.sha256(raw).hexdigest(), 'raw': base64.b64encode(raw).decode('ascii')}
                _atomic(manifest, json.dumps(result, ensure_ascii=False).encode())
                result['cache_status'] = 'live'
                break
            except Exception as exc:  # noqa: BLE001 第三方网络与子进程隔离，失败不覆盖成功缓存。
                logger.warning('分红来源采集失败 %s %s: %s', source, symbol, exc)
                result['reason'] = type(exc).__name__
        sources[source] = result
    return {'version': VERSION, 'symbol': symbol, 'as_of': as_of.isoformat(), 'sources': sources,
            'status': 'complete' if all(row['status'] == 'complete' for row in sources.values()) else 'partial',
            'formal_screening_enabled': False, 'members_published': 0}


def verify_collection(collection: Any, symbol: str, as_of: date) -> dict[str, Any]:
    """重新校验来源身份与原文摘要，不信任已保存的派生结论。"""
    from radar.stocks.dividend_structured import evaluate_dividend_records

    if (not isinstance(collection, dict) or collection.get('version') != VERSION
            or collection.get('symbol') != symbol or collection.get('as_of') != as_of.isoformat()):
        raise ValueError('分红产物公司、版本或截点不符')
    sources = collection.get('sources')
    if not isinstance(sources, dict):
        raise TypeError('分红来源缺失')
    decoded = {}
    for name, url in URLS.items():
        source = sources.get(name)
        if (not isinstance(source, dict) or source.get('status') != 'complete' or source.get('version') != VERSION
                or source.get('source') != name or source.get('symbol') != symbol or source.get('url') != url
                or source.get('as_of') != as_of.isoformat() or source.get('request') != request_identity(name, symbol)):
            raise ValueError('分红来源未完成或身份不符')
        fetched = datetime.fromisoformat(source['fetched_at'])
        if fetched.utcoffset() is None or fetched > datetime.now(UTC):
            raise ValueError('分红抓取时间无效')
        raw = base64.b64decode(source['raw'], validate=True)
        if hashlib.sha256(raw).hexdigest() != source.get('sha256'):
            raise ValueError('分红原文摘要不一致')
        decoded[name] = validate_source(name, symbol, raw)
    return evaluate_dividend_records(symbol, as_of, decoded['cninfo'], decoded['eastmoney'])
