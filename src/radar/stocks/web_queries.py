"""股票网页只读快照查询，不初始化任何业务仓储或目录。"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from radar.stocks.financial_task import financial_progress
from radar.stocks.index_scope import INDEX_SOURCES, POOL_INDEX_CODES
from radar.stocks.update_runs import StockUpdateStore


@contextmanager
def read_connection(db: Path):
    if not db.is_file():
        yield None
        return
    with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('BEGIN')
        yield connection


def has_table(connection: sqlite3.Connection | None, name: str) -> bool:
    return connection is not None and connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def empty_page(page: int) -> dict[str, Any]:
    return {'items': [], 'total': 0, 'page': page, 'page_size': 15}


def public_task(row: Any) -> dict[str, Any]:
    task = (json.loads(json.dumps(dict(row))) if isinstance(row['pool_ids'], list) else StockUpdateStore._decode(row))
    for key in ('lease_token', 'worker_id', 'lease_until'):
        task.pop(key, None)
    if task['kind'] == 'financial_review':
        task['progress'] = financial_progress(task)
        latest = {item['scope']: item for item in task['results']}
        task['source_stopped'] = any(item.get('artifact', {}).get('source_stopped') is True for item in latest.values())
    if task['kind'] in {'financial_review', 'dividend_review', 'turnover_review', 'reference_review'}:
        # 固定范围保存在私有任务中，网页仅需范围摘要。
        task['input_snapshot'] = {key: value for key, value in task['input_snapshot'].items()
                                  if key in {'kind', 'pool_ids', 'scope_as_of', 'scope_complete', 'scope_sha256', 'source_run_id'}}
    for result in task['results']:
        artifact = result.get('artifact')
        if isinstance(artifact, dict):
            artifact.pop('path', None)
    return task


def scope_snapshot(connection: sqlite3.Connection | None, state_dir: Path, pool_id: str) -> dict[str, Any]:
    result: dict[str, Any] = {'companies': [], 'scope_status': 'not_prepared', 'scope_complete': False,
                              'source_snapshots': [], 'scope_error': None, 'scope_as_of': None}
    if pool_id not in POOL_INDEX_CODES:
        return {**result, 'scope_status': 'unsupported', 'scope_error': '该池尚未配置指数发现范围'}
    if not has_table(connection, 'stocks_update_runs'):
        return result
    assert connection is not None
    # 最新发现失败或尚在运行时不能偷偷回退到旧范围或全市场。
    row = connection.execute("SELECT * FROM stocks_update_runs WHERE kind='discover_candidates' ORDER BY created_at DESC,run_id DESC LIMIT 1").fetchone()
    if row is None:
        return result
    result.update(scope_status=row['status'], scope_error=row['error'], scope_as_of=row['target_date'])
    result['source_run_id'] = row['run_id']
    items = json.loads(row['results'])
    item = next((entry for entry in reversed(items) if entry.get('scope') == 'index_scope_v1'), None)
    if item is None:
        return result
    reference = item.get('artifact')
    if not isinstance(reference, dict):
        return result
    sha, size = reference.get('sha256'), reference.get('bytes')
    if not isinstance(sha, str) or re.fullmatch(r'[0-9a-f]{64}', sha) is None or type(size) is not int or not 0 < size <= 64 * 1024 * 1024:
        raise OSError('指数范围产物引用无效')
    root = state_dir / 'stock-updates' / 'artifacts'
    path = root / (sha + '.json')
    if (any(parent.is_symlink() for parent in (path, *path.parents)) or Path(str(reference.get('path', ''))).resolve() != path.resolve()
            or not path.is_file() or path.stat().st_size != size):
        raise OSError('指数范围产物缺失或路径异常')
    with path.open('rb') as stream:
        raw = stream.read(size + 1)
    if len(raw) != size or hashlib.sha256(raw).hexdigest() != sha:
        raise OSError('指数范围产物摘要不一致')
    payload = json.loads(raw)
    result['scope_sha256'] = sha
    scope = payload.get('pools', {}).get(pool_id)
    if not isinstance(scope, dict):
        return result
    result.update(scope_status=(row['status'] if row['status'] == 'failed' else ('complete' if scope.get('complete') else 'partial')),
                  scope_complete=scope.get('complete') is True, scope_as_of=payload.get('as_of'))
    codes = POOL_INDEX_CODES[pool_id]
    result['source_snapshots'] = [entry for entry in payload.get('items', []) if entry.get('index_code') in codes]
    # 响应只返回来源摘要；原始成份表不重复塞入每一页。
    result['source_snapshots'] = [{key: value for key, value in entry.items() if key not in {'rows', 'path', 'cache_path'}}
                                  for entry in result['source_snapshots']]
    result['companies'] = [company for company in payload.get('companies', [])
                           if pool_id in company.get('pool_ids', [])
                           and set(company.get('source_indexes', [])) & set(codes)]
    for entry in result['source_snapshots']:
        entry.setdefault('name', INDEX_SOURCES[entry['index_code']]['name'])
    return result
