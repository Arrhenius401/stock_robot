"""新浪股票原始日线研究采集；不复权、不补停牌日、不推算市值。"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from time import sleep as _sleep
from typing import Any

import requests

from radar.provider_request import ProviderRequestError, bounded_provider_call
from radar.stocks.admission import _BJ, _amount, _day
from radar.stocks.research_store import _symbol
from radar.stocks.source_archive import _close

logger = logging.getLogger(__name__)
VERSION = 'stock-sina-history-v1'
_MAX_BODY = 4 * 1024 * 1024


def _now() -> datetime:
    return datetime.now(UTC)


def sina_url(symbol: str) -> str:
    code = _symbol(symbol)
    identity = ('sh' if code.startswith('6') else 'sz') + code
    return f'https://finance.sina.com.cn/realstock/company/{identity}/hisdata_klc2/klc_kl.js'


def _encoded(raw: bytes, symbol: str) -> str:
    sina_url(symbol)
    if not isinstance(raw, bytes) or len(raw) > _MAX_BODY:
        raise ValueError('日线响应必须是预算内原始字节')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise ValueError('日线响应编码无效') from exc
    identity = ('sh' if symbol.startswith('6') else 'sz') + symbol
    match = re.fullmatch(r'\s*var\s+KLC_K2_' + identity + r'\s*=\s*("(?:[^"\\]|\\.)*")\s*;?\s*(?:/\*(?:(?!/\*|\*/)[\s\S])*\*/\s*)?', text)
    if match is None:
        raise ValueError('日线变量身份或赋值格式无效；不执行服务器脚本')
    value = json.loads(match.group(1))
    if not isinstance(value, str) or not value:
        raise ValueError('日线编码字符串为空')
    return value


def _decode_payload(encoded: str) -> list[dict[str, Any]]:
    """仅执行安装包内可信解码常量；服务器字符串作为参数数据。"""
    from akshare.stock.stock_zh_a_sina import hk_js_decode
    from py_mini_racer import MiniRacer

    with MiniRacer() as context:
        context.eval(hk_js_decode, timeout=2000, max_memory=64*1024*1024)
        result = context.call('d', encoded, timeout=2000, max_memory=64*1024*1024)
    if not isinstance(result, list) or len(result) > 100000:
        raise ValueError('解码行数或结构无效')
    return result


def decode_sina_body(raw: bytes, symbol: str) -> list[dict[str, Any]]:
    encoded = _encoded(raw, symbol)
    result = bounded_provider_call(__name__, '_decode_payload', args=(encoded,), timeout=15)
    if not isinstance(result, list) or len(result) > 100000:
        raise ValueError('解码结果不是预算内日线列表')
    return result


def _window(market_window: list[date]) -> list[date]:
    if not isinstance(market_window, list) or len(market_window) != 60:
        raise ValueError('研究窗口须为60个明确交易日')
    days = [_day(day) for day in market_window]
    if any(day is None for day in days):
        raise ValueError('研究窗口存在无效日期')
    valid = [day for day in days if day is not None]
    if any(valid[index] >= valid[index+1] for index in range(59)):
        raise ValueError('研究窗口必须递增且唯一')
    return valid


def _money(value: Decimal) -> str:
    text = format(value, 'f')
    text = text.rstrip('0').rstrip('.') if '.' in text else text
    return '0' if value == 0 else (text if len(text) <= 100 else str(value))


def _source_day(value: Any) -> date | None:
    if isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}T00:00:00\.000Z', value):
        return _day(value[:10])
    return _day(value)


def parse_sina_history(raw: bytes, symbol: str, market_window: list[date], *,
                       decoder: Callable[..., Any] | None = None) -> dict[str, Any]:
    days = _window(market_window)
    _encoded(raw, symbol)
    decoded = (decoder or decode_sina_body)(raw, symbol)
    if not isinstance(decoded, list) or len(decoded) > 100000:
        raise ValueError('日线解码结果无效')
    targets = {day.isoformat() for day in days}
    candidates: dict[str, list[dict[str, Any]]] = {}
    diagnostics = []
    for row in decoded:
        if not isinstance(row, dict):
            diagnostics.append('存在非对象日线，未采用')
            continue
        day = _source_day(row.get('date'))
        if day is None:
            diagnostics.append('存在无法识别的日线日期，未采用')
            continue
        key = day.isoformat()
        if key in targets:
            candidates.setdefault(key, []).append(row)
    rows = []
    for key in sorted(targets):
        entries = candidates.get(key, [])
        if len(entries) != 1:
            if len(entries) > 1:
                diagnostics.append(f'{key}重复原始记录，全部待核验')
            continue
        raw_row = entries[0]
        try:
            # 非核心源字段也必须可严格导出，否则该日保持未知，不能拖垮其他公司。
            json.dumps(raw_row, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError, OverflowError, RecursionError):
            diagnostics.append(f'{key}原始行含非JSON或非有限字段，未采用')
            continue
        values = {field: _amount(raw_row.get(field)) for field in ('open', 'high', 'low', 'close', 'volume', 'amount')}
        if any(value is None for value in values.values()):
            diagnostics.append(f'{key}数值缺失或无效')
            continue
        prices = [values[field] for field in ('open', 'high', 'low', 'close')]
        opening, high, low, close = prices
        volume, amount = values['volume'], values['amount']
        if (opening is None or high is None or low is None or close is None or volume is None or amount is None
                or min(opening, high, low, close) <= 0 or not low <= opening <= high or not low <= close <= high
                or volume < 0 or amount < 0):
            diagnostics.append(f'{key}OHLC范围或成交量额无效')
            continue
        rows.append({'date': key, **{field: _money(value) for field, value in values.items() if value is not None and field != 'amount'},
                     'amount_yuan': _money(amount), 'postVol': deepcopy(raw_row.get('postVol')),
                     'postAmt': deepcopy(raw_row.get('postAmt')), 'raw': deepcopy(raw_row)})
    missing = sorted(targets - {row['date'] for row in rows})
    return {'symbol': symbol, 'rows': rows, 'missing_dates': missing, 'diagnostics': diagnostics,
            'window_complete': not missing, 'currency': 'CNY', 'price_basis': 'unadjusted',
            'amount_basis': 'source_raw_turnover', 'research_only': True, 'financial_verified': False,
            'formal_screening_enabled': False}


def _atomic(path: Path, raw: bytes) -> None:
    if path.is_symlink():
        raise ValueError('缓存文件不能是符号链接')
    descriptor, temporary = tempfile.mkstemp(prefix='.history-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _cache(root: Path, manifest: Path, symbol: str, now: datetime) -> tuple[bytes, dict[str, Any]] | None:
    try:
        if not manifest.is_file():
            return None
        with manifest.open('rb') as handle:
            manifest_raw = handle.read(65536+1)
        if len(manifest_raw) > 65536:
            return None
        record = json.loads(manifest_raw.decode('utf-8'))
        if not isinstance(record, dict):
            return None
        sha = record.get('content_sha256')
        if (not isinstance(sha, str) or re.fullmatch('[0-9a-f]{64}', sha) is None
                or record.get('status') != 'downloaded' or record.get('url') != sina_url(symbol)):
            return None
        fetched = datetime.fromisoformat(record['fetched_at'])
        if fetched.tzinfo is None or fetched.utcoffset() is None or fetched > now:
            return None
        blob = root/f'{sha}.js'
        if blob.is_symlink() or not blob.is_file():
            return None
        with blob.open('rb') as handle:
            raw = handle.read(_MAX_BODY+1)
        if not 0 < len(raw) <= _MAX_BODY:
            return None
        if hashlib.sha256(raw).hexdigest() != sha:
            return None
        _encoded(raw, symbol)
        return raw, record
    except (ValueError, TypeError, KeyError, OSError):
        logger.warning('股票日线缓存损坏，重新获取: %s', manifest)
        return None


def _read_body(response: Any) -> bytes:
    chunks, size = [], 0
    for chunk in response.iter_content(chunk_size=65536):
        if not isinstance(chunk, bytes):
            raise TypeError('日线流式响应类型无效')
        size += len(chunk)
        if size > _MAX_BODY:
            raise ValueError('日线响应超过4MiB预算')
        chunks.append(chunk)
    return b''.join(chunks)


def _download_once(url: str) -> dict[str, Any]:
    """生产请求在可终止子进程内完成，socket超时之外另有总deadline。"""
    response = requests.get(url, timeout=15, stream=True, allow_redirects=False)
    try:
        status = response.status_code
        try:
            raw = _read_body(response) if status == 200 else b''
        except (ValueError, TypeError) as exc:
            return {'status': status, 'body': b'', 'error': str(exc)}
        return {'status': status, 'body': raw, 'error': None}
    finally:
        _close(response)


def collect_stock_history(companies: list[dict[str, Any]], market_window: list[date], cache_dir: Path, *,
                          as_of: date, refresh: bool = False, http_get: Callable[..., Any] | None = None,
                          sleep: Callable[[float], Any] = _sleep) -> dict[str, Any]:
    """串行采集并导出待审流动性事实；逐公司失败隔离且不写研究数据库。"""
    days, cutoff, now = _window(market_window), _day(as_of), _now()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('采集时间必须带时区')
    local = now.astimezone(_BJ)
    if (cutoff is None or cutoff > local.date() or days[-1] > cutoff
            or (days[-1] == local.date() and local.time() < time(15))):
        raise ValueError('研究截点或交易窗口包含未来/未收盘日期')
    if not isinstance(companies, list) or not companies:
        raise ValueError('companies须为非空公司列表')
    symbols = []
    for company in companies:
        if not isinstance(company, dict):
            raise TypeError('公司必须是对象')
        symbols.append(_symbol(company.get('symbol')))
    if len(set(symbols)) != len(symbols):
        raise ValueError('公司代码不能重复')
    root = Path(cache_dir).absolute()
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError('缓存目录不得经过符号链接')
    if root.exists() and any(path.is_symlink() for path in root.iterdir()):
        raise ValueError('缓存不能包含符号链接')
    root.mkdir(parents=True, exist_ok=True)
    stopped, attempted = False, False
    items = []
    for symbol in symbols:
        url = sina_url(symbol)
        identity = {'version': VERSION, 'symbol': symbol, 'url': url, 'as_of': cutoff.isoformat(), 'window': [day.isoformat() for day in days]}
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        manifest = root/f'{digest}.json'
        item: dict[str, Any] = {'symbol': symbol, 'url': url, 'status': 'source_error'}
        raw, fetched_at = None, None
        try:
            if not refresh:
                cached = _cache(root, manifest, symbol, now)
                if cached is not None:
                    raw, record = cached
                    fetched_at, item['status'] = record['fetched_at'], 'cached'
            if raw is None and stopped:
                item['status'] = 'source_stopped'
            if raw is None and not stopped:
                for attempt in range(3):
                    response = None
                    try:
                        if attempted:
                            sleep(1)
                        attempted = True
                        if http_get is None:
                            downloaded = bounded_provider_call(__name__, '_download_once', args=(url,), timeout=15)
                            if downloaded['error'] is not None:
                                raise ValueError(downloaded['error'])
                            status, body = downloaded['status'], downloaded['body']
                        else:
                            response = http_get(url, timeout=15, stream=True, allow_redirects=False)
                            status = response.status_code
                            body = _read_body(response) if status == 200 else b''
                        if status == 429:
                            stopped = True
                            raise ValueError('来源429停止后续网络请求')
                        if status >= 500:
                            raise requests.RequestException(f'HTTP{status}')
                        if status != 200:
                            raise ValueError(f'HTTP{status}不接受重定向或非成功响应')
                        raw = body
                        fetched_at = _now().astimezone(UTC).isoformat()
                        sha = hashlib.sha256(raw).hexdigest()
                        item.update(content_sha256=sha, fetched_at=fetched_at, raw_artifact=f'{sha}.js')
                        # 预算内200原文先留档；身份/解析失败仍可审计，但不成为成功缓存。
                        _atomic(root/f'{sha}.js', raw)
                        _encoded(raw, symbol)
                        item['status'] = 'downloaded'
                        break
                    except (requests.RequestException, OSError, ProviderRequestError):
                        if attempt == 2:
                            raise
                        logger.warning('股票日线请求失败，按预算重试: %s', symbol)
                    finally:
                        if response is not None:
                            _close(response)
            if raw is not None:
                sha = hashlib.sha256(raw).hexdigest()
                item.update(content_sha256=sha, fetched_at=fetched_at, raw_artifact=f'{sha}.js')
                parsed = parse_sina_history(raw, symbol, days)
                item.update(**parsed)
                if item['status'] == 'downloaded':
                    _atomic(manifest, json.dumps({'status': 'downloaded', 'url': url, 'content_sha256': sha,
                                                 'fetched_at': fetched_at}, ensure_ascii=False).encode())
                if parsed['window_complete']:
                    item['liquidity_fact'] = {'symbol': symbol, 'kind': 'style_liquidity', 'payload': {'currency': 'CNY',
                        'scope': 'a_share_raw_turnover', 'daily': [{'date': row['date'], 'amount_yuan': row['amount_yuan']} for row in parsed['rows']],
                        'price_basis': 'unadjusted', 'amount_basis': 'source_raw_turnover', 'as_of': cutoff.isoformat(), 'fetched_at': fetched_at},
                        'source_url': url, 'content_sha256': sha,
                        'disclosed_on': datetime.fromisoformat(str(fetched_at)).astimezone(_BJ).date().isoformat()}
        except Exception as exc:  # noqa: BLE001 按公司隔离第三方HTTP与解码失败，保留具体错误
            logger.warning('股票日线采集失败 %s: %s', symbol, exc)
            item.update(status='source_error', error=str(exc))
            item.pop('liquidity_fact', None)
            try:
                _atomic(manifest, json.dumps({'status': 'source_error', 'url': url, 'error': str(exc),
                                                 'content_sha256': item.get('content_sha256'), 'fetched_at': item.get('fetched_at')}, ensure_ascii=False).encode())
            except (OSError, ValueError) as cache_exc:
                logger.warning('股票日线失败记录未能保存 %s: %s', symbol, cache_exc)
        item['requires_review'] = True
        items.append(item)
    size = 0
    warnings = []
    try:
        for path in root.iterdir():
            try:
                if path.is_file():
                    size += path.stat().st_size
            except OSError as exc:
                logger.warning('股票日线缓存大小统计跳过文件 %s: %s', path, exc)
                warnings.append('部分缓存文件无法统计，软上限估算不完整')
    except OSError as exc:
        logger.warning('股票日线缓存目录无法统计 %s: %s', root, exc)
        warnings.append('缓存目录无法统计，未执行自动删除')
    if size > 1024**3:
        warnings.append('缓存超过1GiB软上限，未自动删除')
    return {'version': VERSION, 'as_of': cutoff.isoformat(), 'items': items,
            'status': 'complete' if all(item['status'] in ('downloaded', 'cached') for item in items) else 'partial',
            'research_only': True, 'financial_verified': False, 'formal_screening_enabled': False,
            'requires_review': True, 'warnings': list(dict.fromkeys(warnings))}
