"""巨潮官方股本变动原文研究；单位和全股类覆盖未知时不推算市值。"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, date, datetime
from pathlib import Path
from time import sleep as _sleep
from typing import Any

import requests

from data.collection_state import ValueResult
from radar.provider_request import ProviderRequestError, bounded_provider_call
from radar.stocks.admission import _BJ, _day
from radar.stocks.atomic import replace_with_retry
from radar.stocks.collection_results import collection_result
from radar.stocks.research_store import _symbol
from radar.stocks.source_archive import _close

logger = logging.getLogger(__name__)
VERSION = 'cninfo-capital-history-v1'
_MAX_BODY = 4*1024*1024
_STATIC_HEADERS = {'Accept': '*/*', 'Origin': 'https://webapi.cninfo.com.cn',
                   'Referer': 'https://webapi.cninfo.com.cn/', 'User-Agent': 'Mozilla/5.0', 'X-Requested-With': 'XMLHttpRequest'}


def _now() -> datetime:
    return datetime.now(UTC)


def capital_url(symbol: str) -> str:
    _symbol(symbol)
    return 'https://webapi.cninfo.com.cn/api/stock/p_stock2215'


def _reject(value: str) -> Any:
    raise ValueError(f'原文JSON含非标准非有限数值{value}')


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('原文JSON包含重复键')
        result[key] = value
    return result


class _NumberToken(str):
    """只用于另存原始数字字面量，不冒充已核实单位金额。"""


def _strict_json(raw: bytes) -> tuple[Any, Any]:
    try:
        text = raw.decode('utf-8-sig')
        value = json.loads(text, parse_constant=_reject, object_pairs_hook=_pairs)
        json.dumps(value, allow_nan=False)
        tokens = json.loads(text, parse_int=_NumberToken, parse_float=_NumberToken,
                            parse_constant=_reject, object_pairs_hook=_pairs)
        return value, tokens
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise ValueError('原文JSON格式、重复键、数字或嵌套预算无效') from exc


def _tokens(value: Any) -> Any:
    if isinstance(value, _NumberToken):
        return str(value)
    if isinstance(value, dict):
        return {key: result for key, item in value.items() if (result := _tokens(item)) is not None}
    if isinstance(value, list):
        return {str(index): result for index, item in enumerate(value) if (result := _tokens(item)) is not None}
    return None


def _result(symbol: str, cutoff: date, start: date) -> dict[str, Any]:
    return {'symbol': symbol, 'as_of': cutoff.isoformat(), 'start_date': start.isoformat(), 'rows': [],
            'excluded_rows': [], 'declared_total': None, 'declared_count': None, 'row_count': 0,
            'diagnostics': [], 'complete': False, 'challenge_detected': False,
            'unit': None, 'unit_verified': False, 'all_share_classes_verified': False,
            'research_only': True, 'financial_verified': False, 'formal_screening_enabled': False}


def _challenge(message: str) -> bool:
    lower = message.lower()
    return any(word in lower for word in ('验证码', '权限', '登录', '登陆', '账号', '人机', '访问频繁',
                                           'unauthorized', 'forbidden', 'captcha', 'challenge', 'access denied', 'rate limit'))


def parse_capital_history(raw: bytes, symbol: str, *, as_of: date, start_date: date) -> dict[str, Any]:
    """保留公告/变动双日期及所有版本，完整不等于单位、股类或历史覆盖已核实。"""
    capital_url(symbol)
    cutoff, start = _day(as_of), _day(start_date)
    if cutoff is None or start is None or start > cutoff:
        raise ValueError('研究日期或变动起点无效/倒序')
    if not isinstance(raw, bytes) or len(raw) > _MAX_BODY:
        raise ValueError('股本原文必须为4MiB预算内字节')
    result = _result(symbol, cutoff, start)
    if raw.lstrip().startswith(b'<') and _challenge(raw[:65536].decode('utf-8', errors='replace')):
        result.update(challenge_detected=True)
        result['diagnostics'].append('来源返回权限或验证码挑战，停止来源')
        return result
    payload, token_payload = _strict_json(raw)
    if not isinstance(payload, dict):
        raise TypeError('官方股本响应不是对象')
    message = ' '.join(str(payload.get(key, '')) for key in ('resultmsg', 'message', 'msg', 'error', 'errmsg'))
    challenged = payload.get('resultcode') in (401, 403, 429, '401', '403', '429') or _challenge(message)
    if challenged:
        result['challenge_detected'] = True
        result['diagnostics'].append('来源返回权限或验证码挑战，停止来源')
        return result
    records = payload.get('records')
    if (type(payload.get('resultcode')) is not int or payload['resultcode'] != 200 or not isinstance(records, list)
            or any(word in message.lower() for word in ('error', '失败', '错误'))):
        result['diagnostics'].append('来源resultcode、records或错误消息未通过校验')
        return result
    if len(records) > 20000:
        raise ValueError('股本原始记录超过20000行预算')
    total, count = payload.get('total'), payload.get('count')
    valid = type(total) is int and type(count) is int and 0 <= total == count == len(records) <= 20000
    result.update(declared_total=total if type(total) is int else None, declared_count=count if type(count) is int else None,
                  row_count=len(records))
    if not valid:
        result['diagnostics'].append('count/total缺失、异常或与本次原文数量不一致，不能认完整')
    pairs: dict[tuple[str, str], int] = {}
    for record in records:
        if not isinstance(record, dict) or record.get('SECCODE') != symbol:
            continue
        announced, effective = _day(record.get('DECLAREDATE')), _day(record.get('VARYDATE'))
        if announced is not None and effective is not None:
            pair = (announced.isoformat(), effective.isoformat())
            pairs[pair] = pairs.get(pair, 0)+1
    raw_tokens = token_payload.get('records', []) if isinstance(token_payload, dict) else []
    for index, record in enumerate(records):
        reasons = []
        if not isinstance(record, dict):
            valid = False
            result['excluded_rows'].append({'source_fields': record, 'reasons': ['原始记录不是对象']})
            continue
        announced, effective = _day(record.get('DECLAREDATE')), _day(record.get('VARYDATE'))
        if record.get('SECCODE') != symbol:
            reasons.append('证券身份与请求公司不匹配')
            valid = False
        if announced is None or effective is None:
            reasons.append('公告或变动日期无效')
            valid = False
        if announced is not None and announced > cutoff:
            reasons.append('公告日期晚于截点')
        if effective is not None and effective > cutoff:
            reasons.append('变动日期晚于截点')
        if effective is not None and effective < start:
            reasons.append('变动日期早于请求起点')
        if announced is not None and effective is not None and pairs.get((announced.isoformat(), effective.isoformat()), 0) > 1:
            reasons.append('重复公告/变动双日期')
            valid = False
        row = {'announced_on': announced.isoformat() if announced else None,
               'effective_on': effective.isoformat() if effective else None,
               'source_fields': deepcopy(record), 'source_numeric_tokens': _tokens(raw_tokens[index])}
        if reasons:
            result['excluded_rows'].append({**row, 'reasons': reasons})
        else:
            result['rows'].append(row)
    revisions: dict[str, set[str]] = {}
    for row in result['rows']:
        revisions.setdefault(row['effective_on'], set()).add(row['announced_on'])
    if any(len(announcements) > 1 for announcements in revisions.values()):
        result['diagnostics'].append('同一变动日存在不同公告版本，全部保留，不选latest或合成股数序列')
    if any(count > 1 for count in pairs.values()):
        result['diagnostics'].append('重复双日期记录全部排除，未选择数值版本')
    result['complete'] = valid
    return result


def _headers() -> dict[str, str]:
    """仅执行安装版SDK的本地纯时间编码，不记录或返回动态头。"""
    from akshare.datasets import get_ths_js
    from py_mini_racer import MiniRacer

    script = Path(get_ths_js('cninfo.js')).read_text(encoding='utf-8')
    with MiniRacer() as context:
        context.eval(script, timeout=2000, max_memory=64*1024*1024)
        key = context.call('getResCode1', timeout=2000, max_memory=64*1024*1024)
    if not isinstance(key, str) or not key:
        raise ValueError('SDK本地时间编码未产生合法请求头')
    return {**_STATIC_HEADERS, 'Accept-Enckey': key}


def _read_response(response: Any) -> dict[str, Any]:
    status = response.status_code
    if status != 200:
        return {'status': status, 'raw': b'', 'error': None}
    chunks, size = [], 0
    for chunk in response.iter_content(chunk_size=65536):
        if not isinstance(chunk, bytes):
            return {'status': status, 'raw': b'', 'error': '原始股本响应流类型无效'}
        size += len(chunk)
        if size > _MAX_BODY:
            return {'status': status, 'raw': b'', 'error': '原始股本响应超过4MiB'}
        chunks.append(chunk)
    return {'status': status, 'raw': b''.join(chunks), 'error': None}


def _post_once(symbol: str, start: str, cutoff: str) -> dict[str, Any]:
    response = requests.post(capital_url(symbol), params={'scode': symbol, 'sdate': start, 'edate': cutoff},
                             headers=_headers(), timeout=15, stream=True, allow_redirects=False)
    try:
        return _read_response(response)
    finally:
        _close(response)


def _atomic(path: Path, raw: bytes) -> None:
    if path.is_symlink():
        raise ValueError('缓存文件不能是符号链接')
    descriptor, name = tempfile.mkstemp(prefix='.capital-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        replace_with_retry(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _cache(root: Path, manifest: Path, symbol: str, start: date, cutoff: date, now: datetime) -> tuple[bytes, dict[str, Any]] | None:
    try:
        if not manifest.is_file():
            return None
        with manifest.open('rb') as handle:
            raw_manifest = handle.read(65537)
        if len(raw_manifest) > 65536:
            return None
        record, _tokens_record = _strict_json(raw_manifest)
        if (not isinstance(record, dict) or record.get('status') != 'downloaded' or record.get('url') != capital_url(symbol)
                or record.get('symbol') != symbol or record.get('start_date') != start.isoformat()
                or record.get('as_of') != cutoff.isoformat() or record.get('version') != VERSION):
            return None
        sha = record.get('content_sha256')
        if not isinstance(sha, str) or len(sha) != 64 or any(char not in '0123456789abcdef' for char in sha):
            return None
        fetched = datetime.fromisoformat(record['fetched_at'])
        if fetched.tzinfo is None or fetched.utcoffset() is None or fetched > now:
            return None
        blob = root/f'{sha}.jsonbody'
        if blob.is_symlink() or not blob.is_file():
            return None
        with blob.open('rb') as handle:
            raw = handle.read(_MAX_BODY+1)
        if not 0 < len(raw) <= _MAX_BODY or hashlib.sha256(raw).hexdigest() != sha:
            return None
        return raw, record
    except (ValueError, TypeError, KeyError, OSError):
        logger.warning('官方股本缓存损坏或时间无效，重新获取: %s', manifest)
        return None


def _soft_budget(root: Path) -> list[str]:
    total, warnings = 0, []
    try:
        for path in root.iterdir():
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError as exc:
                logger.warning('官方股本缓存大小统计跳过文件 %s: %s', path, exc)
                warnings.append('部分缓存文件无法统计')
    except OSError as exc:
        logger.warning('官方股本缓存目录统计失败 %s: %s', root, exc)
        warnings.append('缓存目录无法统计，未自动删除')
    if total > 1024**3:
        warnings.append('缓存超过1GiB软上限，未自动删除')
    return list(dict.fromkeys(warnings))


def collect_capital_history(companies: list[dict[str, Any]], cache_dir: Path, *, as_of: date,
                            start_date: date = date(1990, 1, 1), refresh: bool = False,
                            http_post: Callable[..., Any] | None = None, sleep: Callable[[float], Any] = _sleep) -> dict[str, Any]:
    """原文采集和缓存重解析，不写研究数据库、不生成市值事实。"""
    cutoff, start, now = _day(as_of), _day(start_date), _now()
    if (now.tzinfo is None or now.utcoffset() is None or cutoff is None or start is None
            or start > cutoff or cutoff > now.astimezone(_BJ).date()):
        raise ValueError('研究日期/起点/采集时区无效、倒序或未来')
    if not isinstance(companies, list) or not companies:
        raise ValueError('companies须为非空公司对象列表')
    symbols = []
    for company in companies:
        if not isinstance(company, dict):
            raise TypeError('公司必须是对象')
        symbols.append(_symbol(company.get('symbol')))
    if len(set(symbols)) != len(symbols):
        raise ValueError('公司代码不能重复')
    root = Path(cache_dir).absolute()
    if any(path.is_symlink() for path in (root, *root.parents)) or (root.exists() and any(path.is_symlink() for path in root.iterdir())):
        raise ValueError('缓存root、祖先或文件不能经过符号链接')
    root.mkdir(parents=True, exist_ok=True)
    stopped, attempted, items = False, False, []
    for symbol in symbols:
        url = capital_url(symbol)
        identity = {'version': VERSION, 'symbol': symbol, 'start_date': start.isoformat(), 'as_of': cutoff.isoformat(), 'url': url}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        manifest = root/f'{key}.json'
        item: dict[str, Any] = {'symbol': symbol, 'url': url, 'status': 'source_error', 'complete': False}
        raw, fetched_at = None, None
        try:
            cached = _cache(root, manifest, symbol, start, cutoff, now) if not refresh else None
            if cached is not None:
                raw, record = cached
                fetched_at, item['status'] = record['fetched_at'], 'cached'
            if raw is None:
                if stopped:
                    item['status'] = 'source_stopped'
                else:
                    for attempt in range(3):
                        response = None
                        try:
                            if attempted:
                                sleep((3 if attempt == 1 else 10) if attempt else 1)
                            attempted = True
                            if http_post is None:
                                downloaded = bounded_provider_call(__name__, '_post_once', args=(symbol, start.isoformat(), cutoff.isoformat()), timeout=15)
                            else:
                                response = http_post(url, params={'scode': symbol, 'sdate': start.isoformat(), 'edate': cutoff.isoformat()},
                                                     headers=dict(_STATIC_HEADERS), timeout=15, stream=True, allow_redirects=False)
                                downloaded = _read_response(response)
                            status = downloaded['status']
                            if status in (401, 403, 429):
                                stopped = True
                                raise ValueError(f'来源HTTP{status}停止后续网络')
                            if downloaded['error'] is not None:
                                raise ValueError(downloaded['error'])
                            if status >= 500:
                                raise requests.RequestException(f'HTTP{status}')
                            if status != 200:
                                raise ValueError(f'HTTP{status}不接受非成功响应或重定向')
                            raw = downloaded['raw']
                            fetched_at = _now().astimezone(UTC).isoformat()
                            sha = hashlib.sha256(raw).hexdigest()
                            item.update(content_sha256=sha, fetched_at=fetched_at, raw_artifact=f'{sha}.jsonbody')
                            item['status'] = 'downloaded'
                            break
                        except (requests.RequestException, OSError, ProviderRequestError):
                            if attempt == 2:
                                raise
                            logger.warning('官方股本请求失败，按预算重试: %s', symbol)
                        finally:
                            if response is not None:
                                _close(response)
            if raw is not None:
                sha = hashlib.sha256(raw).hexdigest()
                if item['status'] == 'downloaded':
                    _atomic(root/f'{sha}.jsonbody', raw)
                item.update(content_sha256=sha, fetched_at=fetched_at, raw_artifact=f'{sha}.jsonbody')
                parsed = (bounded_provider_call(__name__, 'parse_capital_history', args=(raw, symbol),
                          kwargs={'as_of': cutoff, 'start_date': start}, timeout=15) if http_post is None
                          else parse_capital_history(raw, symbol, as_of=cutoff, start_date=start))
                item.update(**parsed)
                if parsed['challenge_detected']:
                    stopped = True
                    raise ValueError('官方来源返回权限或验证码挑战，停止后续网络')
                if item['status'] == 'downloaded':
                    metadata = {**identity, 'status': 'downloaded' if item['complete'] else 'partial',
                                'content_sha256': sha, 'fetched_at': fetched_at}
                    _atomic(manifest.with_suffix('.attempt'), json.dumps(metadata, ensure_ascii=False).encode())
                    if item['complete']:
                        _atomic(manifest, json.dumps(metadata, ensure_ascii=False).encode())
        except Exception as exc:  # noqa: BLE001 每家公司独立隔离官方网络、解析和缓存失败
            logger.warning('官方股本研究失败 %s: %s', symbol, exc)
            item.update(status='source_error', error=str(exc), complete=False)
            try:
                _atomic(manifest.with_suffix('.attempt'), json.dumps({**identity, 'status': 'source_error', 'error': str(exc),
                    'content_sha256': item.get('content_sha256'), 'fetched_at': item.get('fetched_at')}, ensure_ascii=False).encode())
            except (OSError, ValueError) as cache_exc:
                logger.warning('官方股本失败记录未保存 %s: %s', symbol, cache_exc)
        items.append(item)
    complete = all(item['status'] in ('downloaded', 'cached') and item.get('complete') is True for item in items)
    return {'version': VERSION, 'as_of': cutoff.isoformat(), 'start_date': start.isoformat(), 'items': items,
            'status': 'complete' if complete else 'partial',
            'unit': None, 'unit_verified': False, 'all_share_classes_verified': False,
            'retrospective': cutoff < now.astimezone(_BJ).date(), 'point_in_time_verified': False,
            'research_only': True, 'financial_verified': False, 'formal_screening_enabled': False,
            'members_published': 0, 'requires_review': True, 'warnings': _soft_budget(root)}


def collect_capital_history_result(*args: Any, **kwargs: Any) -> ValueResult[dict[str, Any]]:
    """共享结果入口，保留原采集器的请求和缓存语义。"""
    return collection_result(collect_capital_history(*args, **kwargs), "capital_history")
