"""固定同源结构化财务薄采集器；不读公告、不写研究业务库。"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
import tempfile
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from time import sleep as _sleep
from typing import Any

import requests

from data.collection_state import ValueResult
from radar.provider_request import ProviderRequestError, bounded_provider_call
from radar.stocks.collection_results import collection_result
from radar.stocks.research_store import _symbol

logger = logging.getLogger(__name__)
VERSION = 'eastmoney-structured-financial-v1'
_BASE = 'https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/'
_MAIN = 'https://datacenter.eastmoney.com/securities/api/data/get'
_URLS = {_MAIN, *(_BASE + path for path in ('Index', 'xjllbDateAjaxNew', 'xjllbAjaxNew', 'lrbAjaxNew'))}
_MAX_BODY = 8 * 1024 * 1024


def _http(url: str, params: dict[str, str]) -> tuple[int, bytes]:
    """只在可终止子进程内调用，拒绝重定向及未知地址。"""
    if url not in _URLS:
        raise ValueError('结构化请求端点未获允许')
    with requests.get(url, params=params, timeout=15, allow_redirects=False, stream=True) as response:
        if response.status_code == 429:
            return 429, b''
        raw = bytearray()
        for chunk in response.iter_content(65536):
            raw.extend(chunk)
            if len(raw) > _MAX_BODY:
                raise ValueError('结构化响应超过8MiB预算')
        return response.status_code, bytes(raw)


def _fetch(url: str, params: dict[str, str]) -> tuple[int, bytes]:
    return bounded_provider_call(__name__, '_http', args=(url, params), timeout=15)


def _atomic(path: Path, raw: bytes) -> None:
    _safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.structured-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _json(raw: bytes) -> Any:
    return json.loads(raw, parse_float=str, parse_constant=_reject_constant, object_pairs_hook=_unique_pairs)


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('结构化响应包含重复键')
        result[key] = value
    return result


def _safe_path(path: Path) -> None:
    for parent in (path, *path.parents):
        if parent.is_symlink() or getattr(parent, 'is_junction', lambda: False)():
            raise ValueError('结构化缓存及父目录不能为符号链接或目录联接')
        try:
            tag = getattr(parent.lstat(), 'st_reparse_tag', 0)
        except FileNotFoundError:
            continue
        if tag == getattr(stat, 'IO_REPARSE_TAG_MOUNT_POINT', 0xA0000003):
            raise ValueError('结构化缓存及父目录不能为目录联接')


def _reject_constant(value: str) -> Any:
    raise ValueError(f'结构化响应包含非有限数值：{value}')


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


class _Requests:
    """本次公司采集共享来源停止位，缓存绑定日期和实际请求。"""

    def __init__(self, root: Path, as_of: date, fetch: Callable[..., Any], refresh: bool, sleep: Callable[..., Any]):
        self.root, self.as_of, self.fetch, self.refresh, self.sleep = root, as_of, fetch, refresh, sleep
        self.stopped = False
        self.errors: list[str] = []

    def get(self, url: str, params: dict[str, str]) -> tuple[bytes, dict[str, Any]] | None:
        if self.stopped:
            return None
        if url not in _URLS:
            raise ValueError('结构化请求端点未获允许')
        identity = {'version': VERSION, 'as_of': self.as_of.isoformat(), 'source_url': url, 'request_params': params}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        meta_path = self.root / f'{key}.json'
        _safe_path(meta_path)
        if not self.refresh:
            try:
                if meta_path.stat().st_size > 65536:
                    raise ValueError('结构化缓存索引超过预算')
                meta = _json(meta_path.read_bytes())
                if not isinstance(meta, dict) or type(meta.get('bytes')) is not int:
                    raise ValueError('结构化缓存元数据类型错误')
                sha = meta.get('content_sha256')
                if not isinstance(sha, str) or re.fullmatch(r'[0-9a-f]{64}', sha) is None:
                    raise ValueError('结构化缓存摘要格式错误')
                raw_path = self.root / f'{sha}.raw'
                _safe_path(raw_path)
                if meta.get('raw_artifact') != str(raw_path.resolve()) or raw_path.stat().st_size > _MAX_BODY:
                    raise ValueError('结构化缓存原文路径或预算无效')
                fetched = datetime.fromisoformat(meta.get('fetched_at', ''))
                if fetched.tzinfo is None or fetched.utcoffset() is None:
                    raise ValueError('结构化缓存抓取时间缺少时区')
                raw = raw_path.read_bytes()
                if all(meta.get(k) == v for k, v in identity.items()) and meta.get('bytes') == len(raw) <= _MAX_BODY and meta.get('content_sha256') == hashlib.sha256(raw).hexdigest():
                    return raw, {**meta, 'cache_status': 'cache'}
            except (OSError, ValueError, TypeError) as exc:
                logger.debug('结构化缓存不可复用：%s', exc)
        for attempt in range(3):
            if attempt:
                self.sleep((3, 10)[attempt - 1])
            try:
                status, raw = self.fetch(url, dict(params))
                if status == 429:
                    self.stopped = True
                    raise ValueError('HTTP 429，已停止整个来源')
                if 300 <= status < 400:
                    raise ValueError('来源重定向被拒绝')
                if status != 200:
                    raise ProviderRequestError(f'HTTP {status}')
                if not isinstance(raw, bytes) or len(raw) > _MAX_BODY:
                    raise ValueError('响应不是预算内原字节')
                meta = {**identity, 'content_sha256': hashlib.sha256(raw).hexdigest(),
                        'fetched_at': datetime.now(UTC).isoformat(), 'bytes': len(raw)}
                raw_path = self.root / f'{meta["content_sha256"]}.raw'
                _safe_path(raw_path)
                meta['raw_artifact'] = str(raw_path.resolve())
                if not raw_path.exists() or raw_path.stat().st_size != len(raw) or raw_path.read_bytes() != raw:
                    _atomic(raw_path, raw)
                _atomic(meta_path, json.dumps(meta, ensure_ascii=False).encode())
                return raw, {**meta, 'cache_status': 'live'}
            except (ProviderRequestError, requests.RequestException, OSError) as exc:
                if attempt == 2:
                    self.errors.append(f'{url}: {exc}')
            except ValueError as exc:
                self.errors.append(f'{url}: {exc}')
                break
        return None


def _rows(raw: bytes, *, main: bool = False) -> list[dict[str, Any]]:
    payload = _json(raw)
    if isinstance(payload, dict) and (payload.get('success') is False or ('code' in payload and payload['code'] != 0)):
        raise ValueError('结构化响应显式报告失败')
    if main:
        payload = payload.get('result') if isinstance(payload, dict) else None
    rows = payload.get('data') if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError('结构化响应缺少对象记录列表')
    return rows


def collect_structured_company(symbol: str, cache_dir: Path, *, as_of: date, refresh: bool = False,
                               fetch: Callable[..., Any] | None = None,
                               sleep: Callable[..., Any] = _sleep) -> dict[str, Any]:
    """按三个完整年度和最新/上年同期采集；缺失保留为partial，不降级为旧季度。"""
    _symbol(symbol)
    if type(as_of) is not date or as_of.year < 4 or as_of > datetime.now(UTC).astimezone().date():
        raise ValueError('采集日期类型无效、早于三年度计算边界或晚于今日')
    _safe_path(Path(cache_dir).absolute())
    market = 'SH' if symbol.startswith('6') else 'SZ'
    code = market + symbol
    client = _Requests(Path(cache_dir), as_of, fetch or _fetch, refresh, sleep)
    result: dict[str, Any] = {'version': VERSION, 'symbol': symbol, 'as_of': as_of.isoformat(),
        'captured_at': datetime.now(UTC).isoformat(), 'point_in_time_verified': False,
        'status': 'partial', 'records': {'main': [], 'cashflow': [], 'profit': []},
        'sources': {}, 'metadata': {}, 'company_type': None, 'targets': [],
        'source_stopped': False, 'errors': client.errors, 'main_scope': '接口单页全历史，上限200条；仅本地筛选目标期'}
    periods: set[date] = set()
    params = {'type': 'RPT_F10_FINANCE_MAINFINADATA', 'sty': 'APP_F10_MAINFINADATA', 'quoteColumns': '',
              'filter': f'(SECUCODE="{symbol}.{market}")', 'p': '1', 'ps': '200', 'sr': '-1',
              'st': 'REPORT_DATE', 'source': 'HSF10', 'client': 'PC'}
    response = client.get(_MAIN, params)
    if response:
        try:
            main = _rows(response[0], main=True)
            result['records']['main'] = main
            result['sources']['main'] = response[1]
            periods.update(d for row in main if (d := _day(row.get('REPORT_DATE'))) is not None
                           and d <= as_of and (notice := _day(row.get('NOTICE_DATE'))) is not None and notice <= as_of)
        except (ValueError, TypeError, KeyError) as exc:
            client.errors.append(f'main: {exc}')
    response = client.get(_BASE + 'Index', {'type': 'web', 'code': code.lower()})
    if response:
        from bs4 import BeautifulSoup
        node = BeautifulSoup(response[0], 'html.parser').find(id='hidctype')
        company_type = str(node.get('value', '')) if node is not None else ''
        if company_type in {'1', '2', '3', '4'}:
            result['company_type'] = company_type
            result['metadata']['company_type'] = response[1]
        else:
            client.errors.append('company_type: hidctype缺失或未知')
    company_type = result['company_type']
    if company_type:
        response = client.get(_BASE + 'xjllbDateAjaxNew', {'companyType': company_type, 'reportDateType': '0', 'code': code})
        if response:
            try:
                date_rows = _rows(response[0])
                result['metadata']['report_dates'] = {**response[1], 'records': date_rows}
                periods.update(d for row in date_rows if (d := _day(row.get('REPORT_DATE'))) is not None and d <= as_of)
            except (ValueError, TypeError, KeyError) as exc:
                client.errors.append(f'report_dates: {exc}')
    periods = {d for d in periods if (d.month, d.day) in {(3, 31), (6, 30), (9, 30), (12, 31)}}
    if periods and company_type:
        latest = max(periods)
        result['latest_available_period'] = latest.isoformat()
        targets = {date(as_of.year - offset, 12, 31) for offset in (1, 2, 3)}
        targets.update((latest, latest.replace(year=latest.year - 1)))
        result['targets'] = [d.isoformat() for d in sorted(targets, reverse=True)]
        for kind, endpoint in (('cashflow', 'xjllbAjaxNew'), ('profit', 'lrbAjaxNew')):
            dates = result['targets']
            response = client.get(_BASE + endpoint, {'companyType': company_type, 'reportDateType': '0',
                'reportType': '1', 'dates': ','.join(dates), 'code': code})
            if response:
                try:
                    rows = _rows(response[0])
                    result['records'][kind] = [row for row in rows if str(row.get('REPORT_DATE', ''))[:10] in dates]
                    result['sources'][kind] = response[1]
                except (ValueError, TypeError, KeyError) as exc:
                    client.errors.append(f'{kind}: {exc}')
            found = {str(row.get('REPORT_DATE', ''))[:10] for row in result['records'][kind]}
            missing = set(result['targets']) - found
            if missing:
                client.errors.append(f'{kind}: 缺少目标期{sorted(missing)}')
        result['records']['main'] = [row for row in result['records']['main'] if str(row.get('REPORT_DATE', ''))[:10] in result['targets']]
        if not client.errors and all(kind in result['sources'] for kind in ('main', 'cashflow', 'profit')):
            result['status'] = 'complete'
    else:
        client.errors.append('必要报告期或公司类型未知，未请求财务表')
    result['source_stopped'] = client.stopped
    total = sum(path.stat().st_size for path in Path(cache_dir).glob('*') if path.is_file())
    if total > 1024 ** 3:
        result['warnings'] = ['结构化缓存超过1GiB软上限，未自动删除']
    return result


def collect_structured_company_result(*args: Any, **kwargs: Any) -> ValueResult[dict[str, Any]]:
    """共享结果入口，保留原采集器的请求和缓存语义。"""
    return collection_result(collect_structured_company(*args, **kwargs), "financial")
