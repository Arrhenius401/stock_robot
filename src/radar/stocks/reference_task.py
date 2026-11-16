"""基础资料任务的冻结范围、只读选择和不可变来源引用。"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any

from radar.stocks.financial_task import read_financial_artifact
from radar.stocks.index_scope import POOL_INDEX_CODES
from radar.stocks.market_value_source import _scope
from radar.stocks.research_store import StockResearchError, _id, _sha, _symbol, _text

logger = logging.getLogger(__name__)
REFERENCE_VERSION = 'stock-reference-review-v1'


def verify_reference_manifest(state: Path, payload: dict[str, Any]) -> Path:
    """恢复执行前复核完整原文链，不只检查外层登记JSON。"""
    from radar.stocks.market_value_registry import _reference
    ref = payload['manifest']
    if not ref['path'].startswith('stock-updates/reference-snapshots/'):
        raise StockResearchError('基础资料快照位置无效')
    path, raw = _reference(state, ref, need_size=True)
    manifest = json.loads(raw)
    refs = [manifest['scope']]
    refs.extend(manifest[key] for key in ('window', 'history', 'taxonomy') if key in manifest)
    refs.extend(manifest.get('rosters', []))
    refs.extend(ref for entries in manifest.get('companies', {}).values() for ref in entries)
    for entry in refs:
        _reference(path.parent, entry, need_size=True)
    return path


def reference_snapshot(scope: Any, pools: list[str], as_of: date, *, source_run_id: str,
                       source_sha256: str, force_refresh: bool = False) -> dict[str, Any]:
    """保留两个池的完整范围证据，仅选取本次请求池的公司。"""
    if (not isinstance(pools, list) or not pools or len(set(pools)) != len(pools)
            or any(pool not in POOL_INDEX_CODES for pool in pools) or type(force_refresh) is not bool):
        raise StockResearchError('基础资料池范围或刷新参数无效')
    if not isinstance(scope, dict) or not isinstance(scope.get('companies'), list) or not scope['companies']:
        raise StockResearchError('基础资料复核前须准备完整候选范围')
    _scope(scope, scope['companies'][0]['symbol'], as_of, required_pools=pools)
    compact = {key: scope[key] for key in ('version', 'as_of', 'status', 'scope_complete', 'pools')}
    compact['companies'] = [{key: row[key] for key in ('symbol', 'name', 'source_indexes', 'pool_ids')} for row in scope['companies']]
    compact = json.loads(json.dumps(compact))
    if scope.get('version') == 'stock-reference-scope-v1' or any(scope['pools'][pool].get('complete') is not True for pool in POOL_INDEX_CODES):
        compact.update(version='stock-reference-scope-v1', selected_pool_ids=sorted(pools), status='complete', scope_complete=True)
        compact['companies'] = [row for row in compact['companies'] if set(row['pool_ids']) & set(pools)]
        for pool, value in compact['pools'].items():
            value['companies'] = [row['symbol'] for row in compact['companies'] if pool in row['pool_ids']]
    companies = [{'symbol': row['symbol'], 'name': row['name']} for row in compact['companies'] if set(row['pool_ids']) & set(pools)]
    if not companies:
        raise StockResearchError('请求池没有候选公司')
    return {'kind': 'reference_review', 'pool_ids': sorted(pools), 'scope_complete': True,
            'scope_as_of': compact['as_of'], 'scope_sha256': _id(compact), 'scope_document': compact,
            'source_scope_sha256': _sha(source_sha256), 'source_run_id': _text(source_run_id, '范围任务标识'),
            'companies': sorted(companies, key=lambda c: c['symbol']), 'force_refresh': force_refresh,
            'formal_screening_enabled': False}


def validate_reference_snapshot(snapshot: Any, pools: list[str], target: str) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise StockResearchError('基础资料任务须冻结候选范围')
    try:
        expected = reference_snapshot(snapshot['scope_document'], pools, date.fromisoformat(target),
            source_run_id=snapshot['source_run_id'], source_sha256=snapshot['source_scope_sha256'],
            force_refresh=snapshot['force_refresh'])
    except (KeyError, TypeError, ValueError) as exc:
        raise StockResearchError('基础资料冻结范围无效') from exc
    if snapshot != expected:
        raise StockResearchError('基础资料冻结范围内容或摘要不一致')
    return expected


def prepare_reference_snapshot(db: Path, state: Path, pools: list[str], as_of: date, *, force_refresh: bool = False) -> dict[str, Any]:
    from radar.stocks.web_queries import has_table, read_connection, scope_snapshot

    with read_connection(db) as connection:
        if not has_table(connection, 'stocks_update_runs'):
            raise StockResearchError('请先准备完整指数候选范围')
        for pool in pools:
            scope = scope_snapshot(connection, state, pool)
            if scope.get('scope_complete') is not True or scope.get('scope_status') != 'complete':
                raise StockResearchError('基础资料更新需要所选池完整指数范围')
        assert connection is not None
        row = connection.execute("SELECT * FROM stocks_update_runs WHERE kind='discover_candidates' ORDER BY created_at DESC,run_id DESC LIMIT 1").fetchone()
        assert row is not None
        results = json.loads(row['results'])
        item = next(entry for entry in reversed(results) if entry.get('scope') == 'index_scope_v1')
        document = read_financial_artifact(item, state / 'stock-updates/artifacts')
    return reference_snapshot(document, pools, as_of, source_run_id=row['run_id'], source_sha256=item['artifact']['sha256'], force_refresh=force_refresh)


def select_reference_manifest(state: Path, symbol: str, as_of: date, channel: str, *, db: Path | None = None) -> tuple[Path | None, dict[str, Any] | None]:
    """适用新任务阻断旧登记；原文位置只供内部加载器使用。"""
    from radar.stocks.market_value_registry import _reference

    if type(as_of) is not date:
        raise ValueError('评估日期无效')
    symbol = _symbol(symbol)
    database = db if db is not None else state / 'radar.db'
    if not database.is_file():
        return None, None
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as connection:
        connection.row_factory = sqlite3.Row
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='stocks_update_runs'").fetchone() is None:
            return None, None
        rows = connection.execute("SELECT * FROM stocks_update_runs WHERE kind='reference_review' AND target_date<=? ORDER BY target_date DESC,created_at DESC,rowid DESC", (as_of.isoformat(),)).fetchall()
    for row in rows:
        identity = {'run_id': row['run_id'], 'task_identity_sha256': _id({key: row[key] for key in ('run_id', 'target_date', 'rule_version', 'pool_ids', 'created_at')}),
                    'snapshot_sha256': _id(row['input_snapshot']), 'status': 'unavailable', 'as_of': row['target_date'],
                    'reason': '最新基础资料任务尚未处理或来源校验失败，不能回退旧登记'}
        try:
            frozen = validate_reference_snapshot(json.loads(row['input_snapshot']), json.loads(row['pool_ids']), row['target_date'])
            if not any(c['symbol'] == symbol for c in frozen['companies']):
                continue
            if row['rule_version'] != REFERENCE_VERSION:
                return None, identity
            scope = 'reference:' + (('market_value:' + symbol) if channel == 'market_value' else channel)
            item = next((item for item in reversed(json.loads(row['results'])) if item.get('scope') == scope), None)
            identity['item_sha256'] = _id(item)
            if item is None or item.get('status') not in {'completed', 'partial'}:
                return None, identity
            payload = read_financial_artifact(item, state / 'stock-updates/artifacts')
            if payload.get('channel') != channel or payload.get('as_of') != row['target_date']:
                raise StockResearchError('基础资料来源身份或日期不符')
            ref = payload['manifest']
            if not ref['path'].startswith('stock-updates/reference-snapshots/'):
                raise StockResearchError('基础资料快照位置无效')
            path, raw = _reference(state, ref, need_size=True)
            manifest = json.loads(raw)
            if manifest.get('as_of') != row['target_date'] or manifest.get('scope', {}).get('sha256') != frozen['scope_sha256']:
                raise StockResearchError('基础资料快照范围或截点不符')
            identity.update(status='available', reason='已校验基础资料任务的不可变来源产物')
            return path, identity
        except (OSError, ValueError, TypeError, KeyError, AttributeError, StopIteration, RecursionError, OverflowError) as exc:
            logger.debug('基础资料任务不可用 %s: %s', symbol, exc)
            return None, identity
    return None, None
