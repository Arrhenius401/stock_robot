"""东财日线成交额来源：URL、超时边界、原始哈希、60 日窗口和停牌日语义。"""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import requests

from radar.provider_request import bounded_provider_call

VERSION = 'stock-eastmoney-kline-v1'
URL = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'
FIELDS = 'f51,f52,f53,f54,f55,f56,f57'


def _secid(symbol: str) -> str:
    if symbol.startswith('6'):
        return '1.' + symbol
    return '0.' + symbol


def eastmoney_url(symbol: str) -> str:
    return URL + '?' + '&'.join([
        'secid=' + _secid(symbol), 'fields1=f1,f2,f3,f4,f5,f6',
        'fields2=' + FIELDS, 'klt=101', 'fqt=0', 'lmt=200',
    ])


def _fetch(url: str, params: dict[str, str]) -> bytes:
    response = requests.get(url, params=params, timeout=20,
                            headers={'User-Agent': 'StockRobot/1.0',
                                     'Referer': 'https://quote.eastmoney.com/'})
    if response.status_code != 200:
        raise ValueError(f'东财日线 HTTP {response.status_code}')
    return response.content


def _rows(raw: bytes) -> dict[str, Decimal]:
    payload = json.loads(raw)
    data = payload.get('data') if isinstance(payload, dict) else None
    klines = data.get('klines') if isinstance(data, dict) else None
    if not isinstance(klines, list):
        raise TypeError('东财日线响应缺少 klines')
    rows: dict[str, Decimal] = {}
    for line in klines:
        parts = line.split(',') if isinstance(line, str) else []
        if len(parts) < 7 or parts[6] in {'', '-'}:
            continue
        rows[parts[0]] = Decimal(parts[6])
    return rows


def fetch_eastmoney_turnover(symbol: str, as_of: date, cache_dir: Path, window: list[date],
                             calendar: dict[str, Any]) -> dict[str, Any]:
    """拉取窗口内日线成交额；窗口内无成交的交易日按停牌 0 成交，不推算。"""
    if len(window) != 60 or window[-1] > as_of:
        raise ValueError('成交额窗口必须是截至截点的60日')
    params = {'secid': _secid(symbol), 'fields1': 'f1,f2,f3,f4,f5,f6', 'fields2': FIELDS,
              'klt': '101', 'fqt': '0', 'beg': window[0].strftime('%Y%m%d'),
              'end': window[-1].strftime('%Y%m%d'), 'lmt': '200'}
    raw = bounded_provider_call('radar.stocks.eastmoney_history', '_fetch',
                                kwargs={'url': URL, 'params': params}, timeout=30)
    if not isinstance(raw, bytes) or not raw:
        raise ValueError('东财日线响应为空')
    sha = hashlib.sha256(raw).hexdigest()
    root = Path(cache_dir)
    root.mkdir(parents=True, exist_ok=True)
    (root / (sha + '.js')).write_bytes(raw)
    amounts = _rows(raw)
    daily = [{'date': day.isoformat(), 'amount_yuan': str(amounts.get(day.isoformat(), Decimal(0)))}
             for day in window]
    payload = {'symbol': symbol, 'as_of': as_of.isoformat(), 'provider': 'eastmoney',
               'url': eastmoney_url(symbol), 'window': [day.isoformat() for day in window],
               'calendar': calendar, 'fetched_at': datetime.now(UTC).isoformat(),
               'sha256': sha, 'raw': base64.b64encode(raw).decode('ascii'),
               'daily': daily, 'backfill': True}
    return verify_eastmoney_turnover_payload(payload, symbol, as_of)


def verify_eastmoney_turnover_payload(payload: Any, symbol: str, as_of: date | str) -> dict[str, Any]:
    """重新解码东财原始响应，校验 URL、窗口、日历和哈希，不信任保存的 daily。"""
    cutoff = as_of if isinstance(as_of, date) else date.fromisoformat(as_of)
    if (not isinstance(payload, dict) or payload.get('symbol') != symbol
            or payload.get('as_of') != cutoff.isoformat() or payload.get('provider') != 'eastmoney'
            or payload.get('url') != eastmoney_url(symbol)):
        raise ValueError('东财成交额来源身份不符')
    window = payload.get('window')
    if (not isinstance(window, list) or len(window) != 60
            or [date.fromisoformat(day) for day in window][-1] > cutoff):
        raise ValueError('东财成交额窗口不完整或晚于截点')
    if not isinstance(payload.get('calendar'), dict):
        raise TypeError('缺少市场日历快照')
    raw = base64.b64decode(payload['raw'], validate=True)
    if hashlib.sha256(raw).hexdigest() != payload.get('sha256'):
        raise ValueError('东财成交额原文摘要不一致')
    amounts = _rows(raw)
    daily = [{'date': day, 'amount_yuan': str(amounts.get(day, Decimal(0)))} for day in window]
    return {**payload, 'daily': daily}


__all__ = ['VERSION', 'eastmoney_url', 'fetch_eastmoney_turnover', 'verify_eastmoney_turnover_payload']
