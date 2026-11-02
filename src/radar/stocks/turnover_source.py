"""读取候选成交额任务并从保存的原始日线重算。"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Any

from radar.provider_request import ProviderRequestError
from radar.stocks import market_history
from radar.stocks.admission import _BJ
from radar.stocks.financial_task import read_financial_artifact
from radar.stocks.research_store import _id, _sha, _symbol

VERSION = 'stock-turnover-review-v1'
logger = logging.getLogger(__name__)


def verify_turnover_payload(payload: Any, symbol: str, as_of: str) -> dict[str, Any]:
    """重新解码原文，不信任任务中保存的派生金额。"""
    if (not isinstance(payload, dict) or payload.get('symbol') != symbol or payload.get('as_of') != as_of
            or payload.get('url') != market_history.sina_url(symbol)):
        raise ValueError('成交额来源身份或日期不符')
    window = market_history._window(payload['window'])
    if window[-1] > date.fromisoformat(as_of):
        raise ValueError('成交额窗口晚于研究截点')
    fetched = datetime.fromisoformat(payload['fetched_at'])
    if fetched.utcoffset() is None or fetched > datetime.now().astimezone():
        raise ValueError('成交额抓取日期无效')
    calendar = payload.get('calendar')
    if not isinstance(calendar, dict):
        raise TypeError('缺少日历快照摘要')
    _sha(calendar.get('snapshot_sha256'))
    raw = base64.b64decode(payload['raw'], validate=True)
    if hashlib.sha256(raw).hexdigest() != payload.get('sha256'):
        raise ValueError('成交额原文摘要不符')
    result = market_history.parse_sina_history(raw, symbol, window)
    if not result['window_complete']:
        raise ValueError('成交额交易日不完整')
    return {'currency': 'CNY', 'scope': 'a_share_raw_turnover',
            'daily': [{'date': row['date'], 'amount_yuan': row['amount_yuan']} for row in result['rows']]}


def load_turnover_source(db: Path, symbol: str, as_of: date) -> dict[str, Any]:
    symbol = _symbol(symbol)
    source: dict[str, Any] = {'symbol': symbol, 'evaluation_as_of': as_of.isoformat(), 'status': 'unavailable',
                              'as_of': None, 'channel': 'structured_turnover', 'payload': None}
    if not db.is_file():
        return source
    with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as connection:
        connection.row_factory = sqlite3.Row
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='stocks_update_runs'").fetchone() is None:
            return source
        rows = connection.execute("SELECT rowid,* FROM stocks_update_runs WHERE kind='turnover_review' AND target_date<=? ORDER BY target_date DESC,created_at DESC,rowid DESC", (as_of.isoformat(),)).fetchall()
    for row in rows:
        try:
            from radar.stocks.update_runs import StockUpdateStore
            snapshot = json.loads(row['input_snapshot'])
            validated = StockUpdateStore._turnover_snapshot(snapshot, json.loads(row['pool_ids']), row['target_date'])
            if snapshot != validated:
                raise ValueError('成交额任务范围无效')
            if not any(company['symbol'] == symbol for company in validated['companies']):
                continue
            source.update(run_id=row['run_id'], as_of=row['target_date'], snapshot_sha256=hashlib.sha256(row['input_snapshot'].encode()).hexdigest(),
                          task_identity_sha256=_id({key: row[key] for key in ('run_id', 'target_date', 'rule_version', 'pool_ids', 'created_at')}))
            if row['rule_version'] != VERSION:
                source['status'] = 'version_mismatch'
                return source
            item = next((item for item in reversed(json.loads(row['results'])) if item.get('scope') == 'turnover:' + symbol), None)
            source['item_sha256'] = _id(item)
            if item is None:
                source['status'] = 'not_attempted'
                return source
            if item.get('status') != 'completed':
                source['status'] = 'partial'
                return source
            payload = read_financial_artifact(item, db.parent/'stock-updates/artifacts')
            source['payload'] = verify_turnover_payload(payload, symbol, row['target_date'])
            source['window_days'] = payload['window']
            source['calendar_sha256'] = payload['calendar']['snapshot_sha256']
            fetched = datetime.fromisoformat(payload['fetched_at']).astimezone(_BJ).date()
            source['fetched_on'] = fetched.isoformat()
            source['status'] = 'current' if row['target_date'] == as_of.isoformat() and fetched <= as_of else 'historical'
            if source['status'] == 'current':
                from radar.stocks.update_service import load_market_window
                days, trace = load_market_window(db.parent, as_of)
                calendar = trace.get('calendar') if isinstance(trace, dict) else None
                if (days is None or [day.isoformat() for day in days] != payload['window']
                        or not isinstance(calendar, dict) or calendar.get('snapshot_sha256') != source['calendar_sha256']):
                    source.update(status='invalid', payload=None, reason='市场日历已变化或不可用，成交额待核验')
            return source
        except (OSError, ValueError, TypeError, KeyError, AttributeError, StopIteration, sqlite3.Error, ProviderRequestError) as exc:
            logger.warning('成交额来源不可用 %s: %s', symbol, exc)
            source.update(status='invalid', payload=None, run_id=row['run_id'])
            return source
    return source
