"""官方指数成分采集：仅形成研究范围，不审核或发布正式资格。"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from time import sleep as _sleep
from typing import Any
from urllib.parse import urlsplit

import requests

from radar.provider_request import ProviderRequestError, bounded_provider_call
from radar.stocks.admission import _BJ, _day
from radar.stocks.index_scope import (
    INDEX_SOURCES,
    build_index_scope,
    parse_index_constituents,
)
from radar.stocks.official_roster import _atomic, _soft_budget
from radar.stocks.source_archive import _close

logger = logging.getLogger(__name__)
VERSION = 'official-index-scope-v1'
_MAX_BODY = 4 * 1024 * 1024


def _now() -> datetime:
    return datetime.now(UTC)


def _headers(url: str) -> dict[str, str]:
    if urlsplit(url).hostname not in {'oss-ch.csindex.com.cn', 'www.cnindex.com.cn'}:
        raise ValueError('不支持的官方指数来源主机')
    return {'User-Agent': 'Mozilla/5.0'}


def _read_response(response: Any) -> dict[str, Any]:
    if response.status_code != 200:
        return {'status': response.status_code, 'raw': b'', 'error': None}
    chunks, size = [], 0
    for chunk in response.iter_content(chunk_size=65536):
        if not isinstance(chunk, bytes):
            raise TypeError('指数原始响应流类型无效')
        size += len(chunk)
        if size > _MAX_BODY:
            raise ValueError('指数原始响应超过4MiB')
        chunks.append(chunk)
    raw = b''.join(chunks)
    if not raw:
        raise ValueError('指数原始响应为空')
    return {'status': 200, 'raw': raw, 'error': None}


def _download_once(url: str) -> dict[str, Any]:
    """由隔离进程覆盖请求及整个响应流，防止慢流突破总预算。"""
    response = requests.get(url, timeout=15, stream=True, allow_redirects=False, headers=_headers(url))
    try:
        try:
            return _read_response(response)
        except (ValueError, TypeError) as exc:
            return {'status': response.status_code, 'raw': b'', 'error': str(exc)}
    finally:
        _close(response)


def _parse_source(raw: bytes, code: str, as_of: date) -> dict[str, Any]:
    return parse_index_constituents(raw, code, as_of)


def _cache(root: Path, manifest: Path, url: str, cutoff: date, now: datetime) -> tuple[bytes, dict[str, Any]] | None:
    try:
        if manifest.is_symlink() or not manifest.is_file():
            return None
        with manifest.open('rb') as handle:
            encoded = handle.read(65537)
        if len(encoded) > 65536:
            return None
        record = json.loads(encoded)
        if not isinstance(record, dict) or record.get('status') != 'downloaded' or record.get('url') != url:
            return None
        sha = record.get('content_sha256')
        if not isinstance(sha, str) or re.fullmatch('[0-9a-f]{64}', sha) is None:
            return None
        size = record.get('content_length')
        if type(size) is not int or not 0 < size <= _MAX_BODY:
            return None
        fetched = datetime.fromisoformat(record['fetched_at'])
        if fetched.tzinfo is None or fetched.utcoffset() is None or fetched > now or fetched.astimezone(_BJ).date() != cutoff:
            return None
        path = root / f'{sha}.bin'
        if path.is_symlink() or not path.is_file():
            return None
        with path.open('rb') as handle:
            raw = handle.read(_MAX_BODY + 1)
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != sha:
            return None
        return raw, record
    except (ValueError, TypeError, KeyError, OSError):
        logger.warning('指数缓存不可用，按日期限制重新获取: %s', manifest)
        return None


def collect_index_scope(cache_dir: Path, *, as_of: date, refresh: bool = False,
                        http_get: Callable[..., Any] | None = None,
                        sleep: Callable[[float], Any] = _sleep) -> dict[str, Any]:
    """采集已确认的五个指数，历史日只复用该日抓取且完整可校验的缓存。"""
    now, cutoff = _now(), _day(as_of)
    if now.tzinfo is None or now.utcoffset() is None or cutoff is None or cutoff > now.astimezone(_BJ).date():
        raise ValueError('采集时间或研究日期无效/未来')
    root = Path(cache_dir).absolute()
    if any(path.is_symlink() for path in (root, *root.parents)) or (root.exists() and any(path.is_symlink() for path in root.iterdir())):
        raise ValueError('缓存目录或文件不得经过符号链接')
    root.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    stopped: set[str | None] = set()
    attempted = False
    for code, source in INDEX_SOURCES.items():
        url = source['url']
        host = urlsplit(url).hostname
        key = hashlib.sha256(json.dumps({'version': VERSION, 'index_code': code, 'as_of': cutoff.isoformat()}, sort_keys=True).encode()).hexdigest()
        manifest = root / f'{key}.json'
        item: dict[str, Any] = {'index_code': code, 'source_metadata': deepcopy(source), 'url': url,
                                'status': 'source_error', 'complete': False}
        raw = None
        try:
            # 历史查询即使要求刷新，也不能忽略既有证据后再访问当前接口。
            cached = _cache(root, manifest, url, cutoff, now) if not refresh or cutoff < now.astimezone(_BJ).date() else None
            if cached is not None:
                raw, metadata = cached
                item.update({field: metadata[field] for field in ('content_sha256', 'content_length', 'fetched_at')})
                item['status'] = 'cached'
            if raw is None:
                if cutoff != now.astimezone(_BJ).date():
                    raise ValueError('当前接口不提供历史成员，缺少研究日当日抓取缓存，未联网')
                if host in stopped:
                    item.update(status='source_stopped', error='来源429，停止该主机后续请求')
                else:
                    for attempt in range(3):
                        response = None
                        try:
                            if attempted:
                                sleep((3 if attempt == 1 else 10) if attempt else 1)
                            attempted = True
                            if http_get is None:
                                downloaded = bounded_provider_call(__name__, '_download_once', args=(url,), timeout=15)
                            else:
                                response = http_get(url, timeout=15, stream=True, allow_redirects=False, headers=_headers(url))
                                downloaded = _read_response(response)
                            status = downloaded['status']
                            if downloaded.get('error'):
                                raise ValueError(downloaded['error'])
                            if status == 429:
                                stopped.add(host)
                                raise ValueError('来源429，停止该主机后续请求')
                            if 500 <= status < 600:
                                raise requests.RequestException(f'HTTP{status}')
                            if status != 200:
                                raise ValueError(f'HTTP{status}不接受非成功响应或重定向')
                            raw = downloaded['raw']
                            if not isinstance(raw, bytes) or not 0 < len(raw) <= _MAX_BODY:
                                raise ValueError('指数响应类型或长度无效')
                            item.update(content_sha256=hashlib.sha256(raw).hexdigest(), content_length=len(raw),
                                        fetched_at=_now().astimezone(UTC).isoformat(), status='downloaded')
                            _atomic(root / f'{item["content_sha256"]}.bin', raw)
                            break
                        except (requests.RequestException, ProviderRequestError):
                            if attempt == 2:
                                raise
                            logger.warning('官方指数网络失败，按预算重试: %s', code)
                        finally:
                            if response is not None:
                                _close(response)
            if raw is not None:
                item['raw_artifact'] = f'{item["content_sha256"]}.bin'
                parsed = (bounded_provider_call(__name__, '_parse_source', args=(raw, code, cutoff), timeout=15)
                          if http_get is None else _parse_source(raw, code, cutoff))
                item.update(parsed)
                if item['status'] == 'downloaded':
                    metadata = {field: item[field] for field in ('status', 'url', 'content_sha256', 'content_length', 'fetched_at')}
                    _atomic(manifest, json.dumps(metadata, ensure_ascii=False).encode())
        except Exception as exc:  # noqa: BLE001 各指数来源独立隔离网络、解析和缓存故障
            logger.warning('官方指数采集失败 %s: %s', code, exc)
            item.update(status='source_error', complete=False, error=str(exc))
        items.append(item)
    result = build_index_scope(items, cutoff)
    result.update(version=VERSION, warnings=_soft_budget(root))
    return result
