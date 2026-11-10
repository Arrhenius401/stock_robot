"""只读选择最新分红任务，校验原文并重算。"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Any

from radar.stocks.admission import _BJ
from radar.stocks.dividend_collect import (
    VERSION,
    reselect_collection,
    verify_collection,
)
from radar.stocks.financial_task import read_financial_artifact
from radar.stocks.research_store import _id, _symbol

logger = logging.getLogger(__name__)


def _apply_collection(source: dict[str, Any], collection: dict[str, Any], row: sqlite3.Row,
                      symbol: str, as_of: date) -> dict[str, Any]:
    source['result'] = verify_collection(collection, symbol, date.fromisoformat(row['target_date']))
    sources = collection['sources']
    selected = collection['selected_provider']
    selected_names = ('eastmoney_page', 'eastmoney') if selected == 'eastmoney' else ('cninfo',)
    fetched_dates = {name: datetime.fromisoformat(sources[name]['fetched_at']).astimezone(_BJ).date()
                     for name in selected_names}
    source['fetched_dates'] = {name: value.isoformat() for name, value in fetched_dates.items()}
    source['status'] = ('current' if row['target_date'] == as_of.isoformat()
                        and all(value <= as_of for value in fetched_dates.values()) else 'historical')
    return source


def load_dividend_source(db: Path, symbol: str, as_of: date) -> dict[str, Any]:
    symbol = _symbol(symbol)
    source: dict[str, Any] = {'symbol': symbol, 'evaluation_as_of': as_of.isoformat(), 'status': 'unavailable',
                              'as_of': None, 'channel': 'structured_dividend', 'result': None}
    if not db.is_file():
        return source
    with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as connection:
        connection.row_factory = sqlite3.Row
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='stocks_update_runs'").fetchone() is None:
            return source
        rows = connection.execute("SELECT rowid AS creation_order,* FROM stocks_update_runs WHERE kind='dividend_review' AND target_date<=? ORDER BY target_date DESC,created_at DESC,rowid DESC", (as_of.isoformat(),)).fetchall()
    for row in rows:
        try:
            from radar.stocks.update_runs import StockUpdateStore

            snapshot = json.loads(row['input_snapshot'])
            validated = StockUpdateStore._dividend_snapshot(snapshot, json.loads(row['pool_ids']), row['target_date'])
            if snapshot != validated:
                raise ValueError('分红任务范围无效')
            if not any(company['symbol'] == symbol for company in validated['companies']):
                continue
            source.update(run_id=row['run_id'], as_of=row['target_date'],
                          snapshot_sha256=hashlib.sha256(row['input_snapshot'].encode()).hexdigest(),
                          task_identity_sha256=_id({key: row[key] for key in ('run_id', 'target_date', 'rule_version', 'pool_ids', 'created_at')}))
            if row['rule_version'] != VERSION:
                source['status'] = 'version_mismatch'
                return source
            items = json.loads(row['results'])
            item = next((item for item in reversed(items) if item.get('scope') == 'dividend:' + symbol), None)
            source['item_sha256'] = _id(item)
            if item is None:
                source['status'] = 'not_attempted'
                return source
            if item.get('status') != 'completed':
                payload = read_financial_artifact(item, db.parent/'stock-updates/artifacts')
                if payload.get('symbol') != symbol or payload.get('as_of') != row['target_date']:
                    raise ValueError('分红产物身份不符')
                reselected = reselect_collection(payload['collection'], symbol, date.fromisoformat(row['target_date']))
                if reselected is None:
                    source['status'] = 'partial'
                    return source
                return _apply_collection(source, reselected, row, symbol, as_of)
            payload = read_financial_artifact(item, db.parent/'stock-updates/artifacts')
            if payload.get('symbol') != symbol or payload.get('as_of') != row['target_date']:
                raise ValueError('分红产物身份不符')
            return _apply_collection(source, payload['collection'], row, symbol, as_of)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, StopIteration) as exc:
            logger.warning('分红来源不可用 %s: %s', symbol, exc)
            source.update(status='invalid', result=None, run_id=row['run_id'])
            return source
    return source
