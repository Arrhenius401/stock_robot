"""海外跟踪指数采集、独立缓存及基于 ETF 实际日期的派生视图。"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
from contextlib import closing
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
import yaml

from radar.models import RadarUniverse

logger = logging.getLogger(__name__)


def close_at(day: str, mapping: dict[str, Any]) -> datetime:
    """由市场日期与当地收盘时间生成带夏令时规则的时点。"""
    return datetime.combine(
        datetime.fromisoformat(day).date(), time.fromisoformat(mapping['close_time']),
        tzinfo=ZoneInfo(mapping['timezone']),
    )


def parse_daily(mapping: dict[str, Any], payload: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    """验证准确指数身份并仅采用已完成的日线，不猜测节假日。"""
    _aware(now)
    rows: list[tuple[str, Any]] = []
    if mapping['source'] == 'eastmoney':
        data = payload['data']
        if (data['code'] != mapping['code'] or data['market'] != 100
                or data['name'] != mapping['expected_name']):
            raise ValueError('指数身份与审核映射不一致')
        for line in data['klines']:
            parts = line.split(',')
            rows.append((parts[0], parts[2]))
    elif mapping['source'] == 'yahoo':
        data = payload['chart']['result'][0]
        meta = data['meta']
        if (meta.get('symbol') != mapping['code'] or meta.get('instrumentType') != 'INDEX'
                or meta.get('currency') != mapping['currency']
                or meta.get('exchangeTimezoneName') != mapping['timezone']
                or meta.get('longName') != mapping['expected_name']
                or meta.get('dataGranularity') != '1d'):
            raise ValueError('指数身份、币种、时区或日线口径不一致')
        stamps = data['timestamp']
        prices = data['indicators']['quote'][0]['close']
        if len(stamps) != len(prices):
            raise ValueError('指数日期与收盘数量不一致')
        for stamp, price in zip(stamps, prices, strict=True):
            observed = datetime.fromtimestamp(stamp, ZoneInfo(mapping['timezone']))
            if observed > now:
                continue
            rows.append((observed.date().isoformat(), price))
    else:
        raise ValueError('未审核的数据源')
    parsed: dict[str, dict[str, Any]] = {}
    for day, raw in rows:
        ending = close_at(day, mapping)
        if ending > now or raw is None:
            continue
        value = float(raw)
        if not math.isfinite(value) or value <= 0:
            raise ValueError('指数收盘必须为有限正数')
        if day in parsed:
            raise ValueError('指数日线日期重复')
        parsed[day] = {'date': day, 'close': value, 'closed_at': ending.isoformat()}
    if not parsed:
        raise ValueError('暂无已完成的指数收盘记录')
    return [parsed[day] for day in sorted(parsed)]


def _aware(now: datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('海外动态必须使用带时区时间')


class OverseasService:
    """采集与读取严格分离；网络失败保留上次已完成记录。"""

    def __init__(self, db_path: Path, mapping_path: Path | None = None):
        self.db_path = Path(db_path)
        path = mapping_path or Path(__file__).resolve().parents[2] / 'config/radar_overseas_v1.yaml'
        config = yaml.safe_load(path.read_text(encoding='utf-8'))
        self.version = config['version']
        self.mappings: dict[str, dict[str, Any]] = config['mappings']
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS overseas_cache (
                cache_key TEXT PRIMARY KEY, checked_at TEXT NOT NULL,
                rows_json TEXT NOT NULL, error TEXT
            )''')

    def refresh(self, universe: RadarUniverse, snapshot: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
        """每个准确指数只请求一次，逐指数隔离失败。"""
        _aware(now)
        if universe.id != 'overseas_etf':
            return self.view(universe, snapshot, now)
        mappings = {self._key(mapping): mapping for item in universe.instruments
                    if (mapping := self.mappings.get(item.symbol)) and mapping['source'] != 'unavailable'}
        for key, mapping in mappings.items():
            previous = self._cached(key)
            try:
                rows = parse_daily(mapping, self._fetch(mapping), now)
                # 保存完整有效历史，以便旧 ETF 快照只读派生。
                merged = {row['date']: row for row in previous['rows']}
                merged.update({row['date']: row for row in rows})
                self._save(key, now, [merged[day] for day in sorted(merged)], None)
            except Exception as exc:  # noqa: BLE001 — 第三方网络及响应隔离，不影响其他指数
                logger.warning('海外指数 %s 更新失败: %s', mapping['code'], exc)
                if isinstance(exc, (requests.RequestException, OSError)):
                    reason = '海外行情源连接失败，请稍后重试'
                elif isinstance(exc, (ValueError, KeyError, TypeError, IndexError)):
                    reason = '海外行情源返回的数据未通过日期或指数口径校验'
                else:
                    reason = '海外行情源暂不可用，请稍后重试'
                self._save(key, now, previous['rows'], reason)
        return self.view(universe, snapshot, now)

    def view(self, universe: RadarUniverse, snapshot: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
        """仅读取本地缓存，旧快照回溯来源运行的行情日期。"""
        _aware(now)
        if universe.id != 'overseas_etf':
            return {'checked_at': None, 'etf_as_of_date': (snapshot or {}).get('as_of_date'),
                    'status': 'not_applicable', 'reason': '首期海外动态仅适用于海外市场 ETF 池', 'items': []}
        etf_items = {row['symbol']: row for row in (snapshot or {}).get('items', [])}
        items = []
        checked = []
        for instrument in universe.instruments:
            mapping = self.mappings.get(instrument.symbol)
            item = {'symbol': instrument.symbol, 'name': instrument.name,
                    'index_name': None, 'currency': None, 'timezone': None, 'series': None,
                    'status': 'unmapped', 'error': '尚未审核准确跟踪关系', 'source': None,
                    'as_of_date': None, 'base_date': None, 'daily_change': None,
                    'cumulative_change': None, 'checked_at': None, 'closed_at': None,
                    'base_closed_at': None, 'etf_as_of_date': None, 'has_new_close': False}
            if mapping is None:
                items.append(item)
                continue
            item.update({k: mapping[k] for k in ('index_name', 'currency', 'timezone', 'series', 'source')})
            item['status'] = 'unavailable'
            if mapping['source'] == 'unavailable':
                item['error'] = mapping['unavailable_reason']
                items.append(item)
                continue
            cached = self._cached(self._key(mapping))
            item['checked_at'] = cached['checked_at']
            if cached['checked_at']:
                checked.append(cached['checked_at'])
            rows = [row for row in cached['rows'] if datetime.fromisoformat(row['closed_at']) <= now]
            if not rows:
                item['error'] = cached['error'] or '尚未采集已完成的海外指数数据'
                items.append(item)
                continue
            last = rows[-1]
            item.update(status='stale' if cached['error'] else 'fresh', error=cached['error'],
                        as_of_date=last['date'], closed_at=last['closed_at'])
            reasons = []
            if len(rows) > 1:
                item['daily_change'] = last['close'] / rows[-2]['close'] - 1
            else:
                reasons.append('缺少上一有效交易日收盘，无法计算最近一日涨跌')
            cutoff = self._cutoff(etf_items.get(instrument.symbol), snapshot)
            if cutoff and cutoff > now:
                cutoff = None
            if cutoff:
                item['etf_as_of_date'] = cutoff.date().isoformat()
                baseline = [row for row in rows if datetime.fromisoformat(row['closed_at']) <= cutoff]
                if baseline:
                    base = baseline[-1]
                    item.update(base_date=base['date'], base_closed_at=base['closed_at'],
                                cumulative_change=last['close'] / base['close'] - 1,
                                has_new_close=last['date'] > base['date'])
                else:
                    reasons.append('缺少 ETF 实际收盘之前的指数起点，无法计算累计变化')
            else:
                reasons.append('缺少 ETF 实际行情日期，无法计算累计变化')
            if reasons:
                item['error'] = '；'.join(filter(None, [item['error'], *reasons]))
            items.append(item)
        return {'checked_at': max(checked, key=datetime.fromisoformat) if checked else None,
                'etf_as_of_date': (snapshot or {}).get('as_of_date'), 'status': 'available', 'items': items}

    @staticmethod
    def _cutoff(item: dict[str, Any] | None, snapshot: dict[str, Any] | None) -> datetime | None:
        if not item or item.get('status') == 'failed':
            return None
        raw = item.get('market_date') or item.get('source_as_of_date')
        if not raw and item.get('status') == 'fresh':
            raw = (snapshot or {}).get('as_of_date')
        if not raw:
            return None
        try:
            day = date.fromisoformat(raw) if isinstance(raw, str) else raw
            if not isinstance(day, date) or isinstance(day, datetime):
                return None
        except (ValueError, TypeError):
            return None
        # observed_at 是采集时间；stale 必须使用来源行情日期，不能使用本次目标日期。
        return datetime.combine(day, time(15), tzinfo=ZoneInfo('Asia/Shanghai'))

    def _fetch(self, mapping: dict[str, Any]) -> dict[str, Any]:
        if mapping['source'] == 'eastmoney':
            url = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'
            params = {'secid': f"100.{mapping['code']}", 'klt': '101', 'fqt': '0',
                      'lmt': '320', 'end': '20500101', 'fields1': 'f1,f2,f3,f4,f5,f6',
                      'fields2': 'f51,f52,f53,f54,f55,f56,f57'}
        else:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{mapping['code']}"
            params = {'range': '1y', 'interval': '1d'}
        response = requests.get(url, params=params, timeout=12, headers={'User-Agent': 'Mozilla/5.0'})
        response.raise_for_status()
        return response.json()

    def _key(self, mapping: dict[str, Any]) -> str:
        payload = json.dumps({'version': self.version, 'mapping': mapping}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    def _cached(self, key: str) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            row = conn.execute('SELECT * FROM overseas_cache WHERE cache_key=?', (key,)).fetchone()
        return {'rows': json.loads(row['rows_json']), 'checked_at': row['checked_at'], 'error': row['error']} if row else {
            'rows': [], 'checked_at': None, 'error': None}

    def _save(self, key: str, now: datetime, rows: list[dict[str, Any]], error: str | None) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute('INSERT OR REPLACE INTO overseas_cache VALUES (?,?,?,?)',
                         (key, now.isoformat(), json.dumps(rows), error))

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        return conn
