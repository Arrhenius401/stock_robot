"""正式股票池成员与追加式生命周期事件；研究资料不能直接发布成员。"""

from __future__ import annotations

import json
from contextlib import closing
from typing import Any

from radar.stocks.evaluation_store import StockEvaluationStore
from radar.stocks.pools import StockPoolRepository
from radar.stocks.research_store import (
    StockResearchError,
    StockRevisionConflict,
    _json,
    _sha,
    _symbol,
    _text,
)

_ACTIONS = {'add', 'suspend', 'exit', 'restore'}
_STATUSES = {'active', 'suspended', 'exited'}


class StockMembershipStore:
    """池成员按池独立保存；所有状态变化都有不可变事件。"""

    def __init__(self, evaluations: StockEvaluationStore, pools: StockPoolRepository):
        self.evaluations = evaluations
        self.research = evaluations.research
        self.pools = pools
        with self.research._write() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS stocks_membership_schema_versions(version INTEGER NOT NULL)')
            versions = connection.execute('SELECT version FROM stocks_membership_schema_versions').fetchall()
            if versions and (len(versions) != 1 or versions[0][0] != 1):
                raise StockResearchError('不支持的股票成员生命周期版本')
            connection.execute('CREATE TABLE IF NOT EXISTS stocks_pool_revisions(pool_id TEXT PRIMARY KEY, revision INTEGER NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS stocks_memberships(pool_id TEXT NOT NULL, symbol TEXT NOT NULL REFERENCES stocks_companies(symbol), status TEXT NOT NULL, source_evaluation_id TEXT, source_revision INTEGER, reason TEXT NOT NULL, updated_at TEXT NOT NULL, revision INTEGER NOT NULL, PRIMARY KEY(pool_id,symbol))')
            connection.execute('CREATE TABLE IF NOT EXISTS stocks_membership_events(event_id INTEGER PRIMARY KEY AUTOINCREMENT, pool_id TEXT NOT NULL, symbol TEXT NOT NULL, operation TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL, evaluation_id TEXT, source_revision INTEGER, occurred_at TEXT NOT NULL, pool_revision INTEGER NOT NULL, membership_revision INTEGER NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS stocks_membership_requests(request_id TEXT PRIMARY KEY, payload TEXT NOT NULL, result TEXT NOT NULL)')
            if not versions:
                connection.execute('INSERT INTO stocks_membership_schema_versions VALUES(1)')

    @staticmethod
    def _page(value: Any, label: str) -> int:
        if type(value) is not int or value < 1:
            raise StockResearchError(f'{label}须为正整数')
        return value

    @staticmethod
    def _changes(value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list) or not value:
            raise StockResearchError('成员变更至少需要一项')
        result: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for change in value:
            if not isinstance(change, dict) or set(change) != {'pool_id', 'symbol', 'action', 'reason'}:
                raise StockResearchError('成员变更仅允许pool_id、symbol、action、reason')
            pool_id, symbol = _text(change['pool_id'], '池标识'), _symbol(change['symbol'])
            action, reason = _text(change['action'], '变更动作'), _text(change['reason'], '变更理由')
            if action not in _ACTIONS:
                raise StockResearchError('成员变更动作无效')
            if (pool_id, symbol) in seen:
                raise StockResearchError('同一池同一公司不能在同批次重复变更')
            seen.add((pool_id, symbol))
            result.append({'pool_id': pool_id, 'symbol': symbol, 'action': action, 'reason': reason})
        return sorted(result, key=lambda item: (item['pool_id'], item['symbol']))

    @staticmethod
    def _expected(value: Any, pools: set[str]) -> dict[str, int]:
        if not isinstance(value, dict) or set(value) != pools:
            raise StockResearchError('每个涉及股票池都须提供且仅提供预期版本')
        result = {}
        for pool_id, revision in value.items():
            if not isinstance(pool_id, str) or type(revision) is not int or revision < 0 or revision >= 2**63 - 1:
                raise StockResearchError('股票池预期版本无效')
            result[pool_id] = revision
        return result

    def _pool_revision(self, connection: Any, pool_id: str) -> int:
        row = connection.execute('SELECT revision FROM stocks_pool_revisions WHERE pool_id=?', (pool_id,)).fetchone()
        return 0 if row is None else row['revision']

    def _current_evaluation(self, evaluation_id: str, changes: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
        if not any(change['action'] in {'add', 'restore'} for change in changes):
            if evaluation_id:
                return _sha(evaluation_id), self.evaluations.assert_current(evaluation_id)
            return '', {}
        evaluation_id = _sha(evaluation_id)
        return evaluation_id, self.evaluations.assert_current(evaluation_id)

    def _eligible(self, snapshot: dict[str, Any], change: dict[str, str]) -> int:
        report = snapshot.get('report')
        if not isinstance(report, dict) or report.get('formal_screening_enabled') is not True:
            raise StockResearchError('正式筛选尚未启用，不能发布或恢复成员')
        item = next((row for row in report.get('items', []) if isinstance(row, dict) and row.get('symbol') == change['symbol']), None)
        if item is None:
            raise StockResearchError('公司不属于当前评估档案')
        source_revision = item.get('source_revision')
        pool = item.get('pools', {}).get(change['pool_id']) if isinstance(item.get('pools'), dict) else None
        if (type(source_revision) is not int or item.get('common_status') != 'passed' or not isinstance(pool, dict)
                or pool.get('approved_conditions_status') != 'passed' or pool.get('unconfigured_rules') != []):
            raise StockResearchError('共同准入、池条件或规则配置未满足，不能发布或恢复成员')
        current = self.research.get_company(change['symbol'])
        if current is None or current['revision'] != source_revision:
            raise StockRevisionConflict('公司资料版本已变化，请重新评估')
        return source_revision

    def _result(self, connection: Any, changes: list[dict[str, str]], revisions: dict[str, int]) -> dict[str, Any]:
        rows = []
        for change in changes:
            row = connection.execute('SELECT * FROM stocks_memberships WHERE pool_id=? AND symbol=?', (change['pool_id'], change['symbol'])).fetchone()
            if row is not None:
                rows.append(dict(row))
        return {'changes': rows, 'pool_revisions': revisions,
                'members_published': sum(change['action'] in {'add', 'restore'} for change in changes),
                'formal_screening_enabled': False, 'requires_review': True}

    def apply(self, *, evaluation_id: str | None, changes: list[dict[str, Any]], expected_pool_revisions: dict[str, Any], request_id: str, actor: str) -> dict[str, Any]:
        request_id, actor = _text(request_id, '请求标识'), _text(actor, '操作人')
        if len(request_id) > 200:
            raise StockResearchError('请求标识不能超过200字')
        prepared = self._changes(changes)
        pool_ids = {change['pool_id'] for change in prepared}
        expected = self._expected(expected_pool_revisions, pool_ids)
        evaluation = _sha(evaluation_id) if evaluation_id is not None else ''
        payload = _json({'evaluation_id': evaluation or None, 'changes': prepared, 'expected_pool_revisions': expected, 'actor': actor})
        with self.research._write() as connection:
            replay = connection.execute('SELECT payload,result FROM stocks_membership_requests WHERE request_id=?', (request_id,)).fetchone()
            if replay is not None:
                if replay['payload'] != payload:
                    raise StockRevisionConflict('请求标识已用于不同成员变更')
                return json.loads(replay['result'])
            for pool_id in pool_ids:
                if self.pools.get(pool_id) is None:
                    raise StockResearchError('股票池不存在或已禁用')
            evaluation, snapshot = self._current_evaluation(evaluation, prepared)
            for pool_id, revision in expected.items():
                if self._pool_revision(connection, pool_id) != revision:
                    raise StockRevisionConflict('股票池版本已变化，请重新读取')
            sources: dict[tuple[str, str], int | None] = {}
            for change in prepared:
                row = connection.execute('SELECT * FROM stocks_memberships WHERE pool_id=? AND symbol=?', (change['pool_id'], change['symbol'])).fetchone()
                current = None if row is None else row['status']
                action = change['action']
                if action == 'add':
                    if current is not None:
                        raise StockResearchError('公司已有成员记录，不能重复新增')
                    sources[(change['pool_id'], change['symbol'])] = self._eligible(snapshot, change)
                elif action == 'restore':
                    if current not in {'suspended', 'exited'}:
                        raise StockResearchError('仅暂停或已退出成员可以恢复')
                    sources[(change['pool_id'], change['symbol'])] = self._eligible(snapshot, change)
                elif action == 'suspend' and current != 'active':
                    raise StockResearchError('仅当前有效成员可以暂停')
                elif action == 'exit' and current not in {'active', 'suspended'}:
                    raise StockResearchError('仅有效或暂停成员可以退出')
            now = self.research._now().isoformat()
            revised = {pool_id: revision + 1 for pool_id, revision in expected.items()}
            for pool_id, revision in revised.items():
                connection.execute('INSERT INTO stocks_pool_revisions(pool_id,revision) VALUES(?,?) ON CONFLICT(pool_id) DO UPDATE SET revision=excluded.revision', (pool_id, revision))
            for change in prepared:
                pool_id, symbol, action = change['pool_id'], change['symbol'], change['action']
                status = {'add': 'active', 'restore': 'active', 'suspend': 'suspended', 'exit': 'exited'}[action]
                old = connection.execute('SELECT revision,source_evaluation_id,source_revision FROM stocks_memberships WHERE pool_id=? AND symbol=?', (pool_id, symbol)).fetchone()
                member_revision = 1 if old is None else old['revision'] + 1
                source_revision = sources.get((pool_id, symbol))
                event_source_revision = source_revision
                source_evaluation = evaluation or None
                if old is not None and source_revision is None:
                    source_revision = old['source_revision']
                    source_evaluation = old['source_evaluation_id']
                if old is None:
                    connection.execute('INSERT INTO stocks_memberships VALUES(?,?,?,?,?,?,?,?)', (pool_id, symbol, status, source_evaluation, source_revision, change['reason'], now, member_revision))
                else:
                    connection.execute('UPDATE stocks_memberships SET status=?,source_evaluation_id=?,source_revision=?,reason=?,updated_at=?,revision=? WHERE pool_id=? AND symbol=?', (status, source_evaluation, source_revision, change['reason'], now, member_revision, pool_id, symbol))
                connection.execute('INSERT INTO stocks_membership_events(pool_id,symbol,operation,actor,reason,evaluation_id,source_revision,occurred_at,pool_revision,membership_revision) VALUES(?,?,?,?,?,?,?,?,?,?)', (pool_id, symbol, {'add': 'publish_add', 'restore': 'restore', 'suspend': 'suspend', 'exit': 'exit'}[action], actor, change['reason'], evaluation or None, event_source_revision, now, revised[pool_id], member_revision))
            result = self._result(connection, prepared, revised)
            connection.execute('INSERT INTO stocks_membership_requests VALUES(?,?,?)', (request_id, payload, _json(result)))
        return result

    def get_member(self, pool_id: str, symbol: str) -> dict[str, Any] | None:
        pool_id, symbol = _text(pool_id, '池标识'), _symbol(symbol)
        with closing(self.research._connect()) as connection:
            row = connection.execute('SELECT * FROM stocks_memberships WHERE pool_id=? AND symbol=?', (pool_id, symbol)).fetchone()
            if row is None:
                return None
            result = dict(row)
            events = connection.execute('SELECT * FROM stocks_membership_events WHERE pool_id=? AND symbol=? ORDER BY event_id', (pool_id, symbol)).fetchall()
        result['events'] = [dict(event) for event in events]
        return result

    def list_members(self, pool_id: str, *, status: str | None = 'active', page: int = 1, page_size: int = 15) -> dict[str, Any]:
        pool_id = _text(pool_id, '池标识')
        page, page_size = self._page(page, '页码'), self._page(page_size, '页面大小')
        if status is not None and status not in _STATUSES:
            raise StockResearchError('成员状态无效')
        clause, parameters = (' WHERE pool_id=?', [pool_id])
        if status is not None:
            clause += ' AND status=?'
            parameters.append(status)
        with closing(self.research._connect()) as connection:
            total = connection.execute('SELECT COUNT(*) FROM stocks_memberships' + clause, parameters).fetchone()[0]
            revision = self._pool_revision(connection, pool_id)
            offset = (page - 1) * page_size
            rows = connection.execute('SELECT * FROM stocks_memberships' + clause + ' ORDER BY symbol LIMIT ? OFFSET ?', [*parameters, page_size, offset]).fetchall() if offset < total else []
        return {'pool_id': pool_id, 'revision': revision, 'items': [dict(row) for row in rows], 'total': total, 'page': page, 'page_size': page_size,
                'status': status, 'formal_screening_enabled': False}
