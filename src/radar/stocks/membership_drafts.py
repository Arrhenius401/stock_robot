"""保存版本化成员新增草案；研究档案不具备正式发布权限。"""

from __future__ import annotations

import json
from contextlib import closing
from typing import Any

from radar.stocks.evaluation_store import StockEvaluationStore
from radar.stocks.pools import StockPoolRepository
from radar.stocks.research_store import (
    StockResearchError,
    StockRevisionConflict,
    _id,
    _json,
    _sha,
    _symbol,
    _text,
)


class StockMembershipDraftStore:
    """草案与追加事件独立留档，正式成员始终由后续发布流程管理。"""

    def __init__(self, evaluations: StockEvaluationStore, pools: StockPoolRepository):
        self.evaluations = evaluations
        self.research = evaluations.research
        self.pools = pools
        with self.research._write() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS stocks_member_draft_versions(version INTEGER NOT NULL)')
            versions = connection.execute('SELECT version FROM stocks_member_draft_versions').fetchall()
            if versions and (len(versions) != 1 or versions[0][0] != 1):
                raise StockResearchError('不支持的成员草案版本')
            connection.execute('CREATE TABLE IF NOT EXISTS stocks_member_drafts(draft_id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, evaluation_id TEXT NOT NULL REFERENCES stocks_evaluations(evaluation_id), payload TEXT NOT NULL, changes TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL)')
            connection.execute('CREATE TABLE IF NOT EXISTS stocks_member_draft_events(event_id INTEGER PRIMARY KEY AUTOINCREMENT, draft_id TEXT NOT NULL REFERENCES stocks_member_drafts(draft_id), operation TEXT NOT NULL, actor TEXT NOT NULL, reason TEXT NOT NULL, occurred_at TEXT NOT NULL, revision INTEGER NOT NULL)')
            if not versions:
                connection.execute('INSERT INTO stocks_member_draft_versions VALUES(1)')

    @staticmethod
    def _changes(changes: Any) -> list[dict[str, str]]:
        if not isinstance(changes, list) or not changes:
            raise StockResearchError('成员草案至少需要一项变更')
        result = []
        seen = set()
        for change in changes:
            if not isinstance(change, dict) or set(change) != {'pool_id', 'symbol', 'action', 'reason'}:
                raise StockResearchError('变更仅允许pool_id、symbol、action、reason字段')
            if change['action'] != 'add':
                raise StockResearchError('当前只支持新增关注草案，恢复和移除须等成员生命周期接通')
            pool_id, symbol = _text(change['pool_id'], '池标识'), _symbol(change['symbol'])
            reason = _text(change['reason'], '关注理由')
            if (pool_id, symbol) in seen:
                raise StockResearchError('同一池同一家公司不能重复变更')
            seen.add((pool_id, symbol))
            result.append({'pool_id': pool_id, 'symbol': symbol, 'action': 'add', 'reason': reason})
        return sorted(result, key=lambda item: (item['pool_id'], item['symbol']))

    def create(self, *, evaluation_id: str, changes: list[dict[str, Any]], request_id: str, actor: str) -> dict[str, Any]:
        evaluation_id, request_id, actor = _sha(evaluation_id), _text(request_id, '请求标识'), _text(actor, '操作人')
        if len(request_id) > 200:
            raise StockResearchError('请求标识不能超过200字')
        normalized = self._changes(changes)
        payload = _json({'evaluation_id': evaluation_id, 'changes': normalized, 'actor': actor})
        draft_id = _id({'request_id': request_id, 'payload': payload})
        with self.research._write() as connection:
            old = connection.execute('SELECT draft_id,payload FROM stocks_member_drafts WHERE request_id=?', (request_id,)).fetchone()
            if old:
                if old['payload'] != payload:
                    raise StockRevisionConflict('请求标识已用于其他成员草案')
                draft_id = old['draft_id']
            else:
                evaluation = self.evaluations.assert_current(evaluation_id)
                items = {item['symbol']: item for item in evaluation['report']['items']}
                prepared = []
                for change in normalized:
                    if self.pools.get(change['pool_id']) is None:
                        raise StockResearchError('股票池不存在或已禁用')
                    item = items.get(change['symbol'])
                    if item is None:
                        raise StockResearchError('公司不属于该评估档案')
                    pool = item.get('pools', {}).get(change['pool_id'])
                    if not isinstance(pool, dict):
                        raise StockResearchError('该股票池尚无对应风格计算结果')
                    blockers = ['formal_screening_disabled']
                    if item.get('common_status') != 'passed':
                        blockers.append('common_' + str(item.get('common_status', 'pending_verification')))
                    if pool.get('approved_conditions_status') != 'passed':
                        blockers.append('pool_' + str(pool.get('approved_conditions_status', 'pending_verification')))
                    if pool.get('unconfigured_rules'):
                        blockers.append('rules_unconfigured')
                    prepared.append({**change, 'source_revision': item['source_revision'], 'blockers': blockers,
                                     'unconfigured_rules': pool.get('unconfigured_rules', [])})
                now = self.research._now().isoformat()
                connection.execute('INSERT INTO stocks_member_drafts VALUES(?,?,?,?,?,?,?,?,?)',
                                   (draft_id, request_id, evaluation_id, payload, _json(prepared), actor, now, 'blocked', 1))
                connection.execute('INSERT INTO stocks_member_draft_events(draft_id,operation,actor,reason,occurred_at,revision) VALUES(?,?,?,?,?,?)',
                                   (draft_id, 'created', actor, '保存关注草案，正式发布关闭', now, 1))
        result = self.get(draft_id)
        if result is None:
            raise StockResearchError('成员草案保存后读取失败')
        return result

    def get(self, draft_id: str) -> dict[str, Any] | None:
        draft_id = _sha(draft_id)
        with closing(self.research._connect()) as connection:
            connection.execute('BEGIN')
            row = connection.execute('SELECT * FROM stocks_member_drafts WHERE draft_id=?', (draft_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            events = connection.execute('SELECT * FROM stocks_member_draft_events WHERE draft_id=? ORDER BY event_id', (draft_id,)).fetchall()
        result.pop('payload')
        result['changes'] = json.loads(result['changes'])
        result['events'] = [dict(event) for event in events]
        evaluation = self.evaluations.get(result['evaluation_id'])
        freshness = evaluation['freshness'] if evaluation else {'current': False, 'reasons': ['evaluation_missing']}
        result.update(evaluation_current=freshness['current'], evaluation_freshness=freshness,
                      publication_enabled=False, members_published=0)
        return result

    def list_drafts(self, *, page: int = 1, page_size: int = 15) -> dict[str, Any]:
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in (page, page_size)):
            raise StockResearchError('页码和页面大小须为正整数')
        with closing(self.research._connect()) as connection:
            connection.execute('BEGIN')
            total = connection.execute('SELECT COUNT(*) FROM stocks_member_drafts').fetchone()[0]
            offset = (page - 1) * page_size
            rows = (connection.execute('SELECT draft_id FROM stocks_member_drafts ORDER BY created_at DESC,draft_id LIMIT ? OFFSET ?',
                                       (min(total, page_size), offset)).fetchall() if offset < total else [])
        items = [self.get(row['draft_id']) for row in rows]
        return {'items': items, 'total': total, 'page': page, 'page_size': page_size,
                'publication_enabled': False, 'members_published': 0}

    def cancel(self, draft_id: str, *, expected_revision: int, actor: str, reason: str) -> dict[str, Any]:
        draft_id, actor, reason = _sha(draft_id), _text(actor, '操作人'), _text(reason, '撤销理由')
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or not 1 <= expected_revision < 2**63 - 1:
            raise StockResearchError('草案版本须为可递增的正整数')
        with self.research._write() as connection:
            row = connection.execute('SELECT revision,status FROM stocks_member_drafts WHERE draft_id=?', (draft_id,)).fetchone()
            if row is None:
                raise StockResearchError('成员草案不存在')
            if row['revision'] != expected_revision:
                raise StockRevisionConflict('成员草案版本变化，请重新读取')
            if row['status'] == 'cancelled':
                raise StockResearchError('成员草案已撤销')
            revision = expected_revision + 1
            connection.execute('UPDATE stocks_member_drafts SET status=?,revision=? WHERE draft_id=?', ('cancelled', revision, draft_id))
            connection.execute('INSERT INTO stocks_member_draft_events(draft_id,operation,actor,reason,occurred_at,revision) VALUES(?,?,?,?,?,?)',
                               (draft_id, 'cancelled', actor, reason, self.research._now().isoformat(), revision))
        result = self.get(draft_id)
        if result is None:
            raise StockResearchError('成员草案撤销后读取失败')
        return result
