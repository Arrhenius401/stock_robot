"""股票资料更新与候选发现的独立手动任务；不复用ETF快照语义。"""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from typing import Any

from radar.stocks.membership_store import StockMembershipStore
from radar.stocks.research_store import (
    StockResearchError,
    StockRevisionConflict,
    _json,
    _sha,
    _symbol,
    _text,
)

_KINDS = {'refresh_members', 'discover_candidates', 'financial_review', 'reference_review', 'dividend_review', 'turnover_review'}
_TERMINAL = {'completed', 'partial', 'failed', 'cancelled'}
_ITEM_STATUSES = {'completed', 'partial', 'failed', 'not_configured'}
_PHASES = {'snapshot', 'collect', 'review', 'finalizing'}
_CHINA = timezone(timedelta(hours=8))


class StockUpdateStore:
    """股票任务有独立队列、租约和不可变输入，不触发正式成员变更。"""

    def __init__(self, memberships: StockMembershipStore):
        self.memberships = memberships
        self.research = memberships.research
        with self.research._write() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS stocks_update_schema_versions(version INTEGER NOT NULL)')
            versions = connection.execute('SELECT version FROM stocks_update_schema_versions').fetchall()
            if versions and (len(versions) != 1 or versions[0][0] != 1):
                raise StockResearchError('不支持的股票更新任务版本')
            connection.execute('CREATE TABLE IF NOT EXISTS stocks_update_runs(run_id TEXT PRIMARY KEY, kind TEXT NOT NULL, pool_ids TEXT NOT NULL, target_date TEXT NOT NULL, rule_version TEXT NOT NULL, input_snapshot TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL, results TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, worker_id TEXT, lease_until TEXT, lease_token TEXT, attempt INTEGER NOT NULL)')
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS stocks_update_active ON stocks_update_runs(kind,pool_ids,target_date,rule_version) WHERE status IN ('queued','running')")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS stocks_update_single_running ON stocks_update_runs(status) WHERE status='running'")
            if not versions:
                connection.execute('INSERT INTO stocks_update_schema_versions VALUES(1)')

    @staticmethod
    def _date(value: Any) -> str:
        if isinstance(value, date) and not isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, str):
            try:
                parsed = date.fromisoformat(value).isoformat()
                if parsed != value:
                    raise ValueError('日期须使用规范形式')
                return parsed
            except ValueError as exc:
                raise StockResearchError('目标日期须为YYYY-MM-DD') from exc
        raise StockResearchError('目标日期须为YYYY-MM-DD')

    def _pools(self, pool_ids: Any) -> list[str]:
        if not isinstance(pool_ids, list) or not pool_ids:
            raise StockResearchError('股票更新至少须指定一个股票池')
        ids = sorted({_text(pool_id, '池标识') for pool_id in pool_ids})
        for pool_id in ids:
            if self.memberships.pools.get(pool_id) is None:
                raise StockResearchError('股票池不存在或已禁用')
        return ids

    def _snapshot(self, connection: sqlite3.Connection, kind: str, pool_ids: list[str]) -> dict[str, Any]:
        members, revisions = [], {}
        for pool_id in pool_ids:
            row = connection.execute('SELECT revision FROM stocks_pool_revisions WHERE pool_id=?', (pool_id,)).fetchone()
            revisions[pool_id] = 0 if row is None else row['revision']
            if kind == 'refresh_members':
                rows = connection.execute("SELECT symbol,revision,status FROM stocks_memberships WHERE pool_id=? AND status IN ('active','suspended') ORDER BY symbol", (pool_id,)).fetchall()
                members.extend({'pool_id': pool_id, 'symbol': row['symbol'], 'membership_revision': row['revision'], 'status': row['status']} for row in rows)
        if kind == 'refresh_members' and not members:
            raise StockResearchError('当前池没有有效或暂停成员，不能更新成员资料')
        return {'kind': kind, 'pool_ids': pool_ids, 'members': sorted(members, key=lambda row: (row['pool_id'], row['symbol'])),
                'symbols': sorted({row['symbol'] for row in members}), 'pool_revisions': revisions, 'formal_screening_enabled': False}

    @staticmethod
    def _decode(row: Any) -> dict[str, Any]:
        result = dict(row)
        for key in ('pool_ids', 'input_snapshot', 'results'):
            result[key] = json.loads(result[key])
        latest = {item['scope']: item['status'] for item in result['results']}
        if result['kind'] == 'reference_review':
            # 旧行业尝试保留为历史记录，不再影响基础资料任务的当前进度。
            latest.pop('reference:industry', None)
        result['completed_scopes'] = sorted(scope for scope, status in latest.items() if status == 'completed')
        result['pending_scopes'] = sorted(scope for scope, status in latest.items() if status != 'completed')
        result['automatic_enabled'] = False
        return result

    @staticmethod
    def _financial_snapshot(snapshot: Any, pools: list[str], target: str) -> dict[str, Any]:
        if (not isinstance(snapshot, dict) or snapshot.get('kind') != 'financial_review'
                or snapshot.get('pool_ids') != pools or len(pools) != 1
                or snapshot.get('scope_complete') is not True):
            raise StockResearchError('财务复核须固定当前池完整指数范围')
        scope_date = StockUpdateStore._date(snapshot.get('scope_as_of'))
        if scope_date > target:
            raise StockResearchError('指数范围日期不能晚于财务研究日期')
        companies = snapshot.get('companies')
        if not isinstance(companies, list) or not companies:
            raise StockResearchError('财务复核范围为空，请先准备候选')
        prepared = []
        for row in companies:
            if not isinstance(row, dict):
                raise StockResearchError('财务范围公司须为对象')
            prepared.append({'symbol': _symbol(row.get('symbol')), 'name': _text(row.get('name'), '公司名称')})
        if len({row['symbol'] for row in prepared}) != len(prepared):
            raise StockResearchError('财务范围公司代码重复')
        return {'kind': 'financial_review', 'pool_ids': pools, 'scope_complete': True,
                'scope_as_of': scope_date, 'scope_sha256': _sha(snapshot.get('scope_sha256')),
                'source_run_id': _text(snapshot.get('source_run_id'), '范围任务标识'),
                'source_snapshots': json.loads(_json(snapshot.get('source_snapshots', []))),
                'companies': sorted(prepared, key=lambda row: row['symbol']),
                'formal_screening_enabled': False}

    @staticmethod
    def _dividend_snapshot(snapshot: Any, pools: list[str], target: str) -> dict[str, Any]:
        if not isinstance(snapshot, dict) or snapshot.get('kind') != 'dividend_review':
            raise StockResearchError('分红任务须冻结候选范围')
        frozen = StockUpdateStore._financial_snapshot({**snapshot, 'kind': 'financial_review'}, pools, target)
        return {**frozen, 'kind': 'dividend_review'}

    @staticmethod
    def _turnover_snapshot(snapshot: Any, pools: list[str], target: str) -> dict[str, Any]:
        if not isinstance(snapshot, dict) or snapshot.get('kind') != 'turnover_review':
            raise StockResearchError('成交额任务须冻结候选范围')
        frozen = StockUpdateStore._financial_snapshot({**snapshot, 'kind': 'financial_review'}, pools, target)
        return {**frozen, 'kind': 'turnover_review'}

    def enqueue(self, *, kind: str, pool_ids: list[str], target_date: date | str, rule_version: str,
                snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(kind, str) or kind not in _KINDS:
            raise StockResearchError('股票更新任务类型无效')
        pools, target, rules = self._pools(pool_ids), self._date(target_date), _text(rule_version, '规则版本')
        if date.fromisoformat(target) > self.research._now().astimezone(_CHINA).date():
            raise StockResearchError('目标日期不能晚于当前北京时间日期')
        if kind == 'financial_review':
            snapshot = self._financial_snapshot(snapshot, pools, target)
        elif kind == 'dividend_review':
            snapshot = self._dividend_snapshot(snapshot, pools, target)
        elif kind == 'turnover_review':
            snapshot = self._turnover_snapshot(snapshot, pools, target)
        elif kind == 'reference_review':
            from radar.stocks.reference_task import validate_reference_snapshot
            snapshot = validate_reference_snapshot(snapshot, pools, target)
        elif snapshot is not None:
            raise StockResearchError('该任务不接受外部范围快照')
        identity = (kind, _json(pools), target, rules)
        with self.research._write() as connection:
            existing = connection.execute("SELECT * FROM stocks_update_runs WHERE kind=? AND pool_ids=? AND target_date=? AND rule_version=? AND status IN ('queued','running')", identity).fetchone()
            if existing is not None:
                return self._decode(existing)
            snapshot = snapshot if snapshot is not None else self._snapshot(connection, kind, pools)
            now = self.research._now().isoformat()
            run_id = uuid.uuid4().hex
            connection.execute('INSERT INTO stocks_update_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (run_id, kind, _json(pools), target, rules, _json(snapshot), 'queued', 'queued', '[]', None, now, now, None, None, None, 0))
            row = connection.execute('SELECT * FROM stocks_update_runs WHERE run_id=?', (run_id,)).fetchone()
        return self._decode(row)

    def get(self, run_id: str) -> dict[str, Any]:
        run_id = _text(run_id, '任务标识')
        with closing(self.research._connect()) as connection:
            connection.execute('BEGIN')
            row = connection.execute('SELECT * FROM stocks_update_runs WHERE run_id=?', (run_id,)).fetchone()
        if row is None:
            raise StockResearchError('股票更新任务不存在')
        return self._decode(row)

    def resume_financial(self, run_id: str, pool_id: str) -> dict[str, Any]:
        """用户显式重排原任务；不读取新范围，不改写已经成功的公司。"""
        with self.research._write() as connection:
            row = connection.execute('SELECT * FROM stocks_update_runs WHERE run_id=?',
                                     (_text(run_id, '任务标识'),)).fetchone()
            if row is None:
                raise StockResearchError('财务任务不存在')
            task = self._decode(row)
            if task['kind'] != 'financial_review' or task['pool_ids'] != [pool_id]:
                raise StockResearchError('只能继续当前池的财务任务')
            if task['status'] in {'queued', 'running'}:
                return task
            if task['status'] not in {'partial', 'failed'}:
                raise StockResearchError('该财务任务无需继续')
            self._financial_snapshot(task['input_snapshot'], [pool_id], task['target_date'])
            active = connection.execute("SELECT 1 FROM stocks_update_runs WHERE kind=? AND pool_ids=? AND target_date=? AND rule_version=? AND status IN ('queued','running')",
                                        (task['kind'], _json(task['pool_ids']), task['target_date'], task['rule_version'])).fetchone()
            if active is not None:
                raise StockRevisionConflict('同一范围日期已有排队或执行中的财务任务')
            connection.execute("UPDATE stocks_update_runs SET status='queued',phase='queued',error=NULL,worker_id=NULL,lease_until=NULL,lease_token=NULL,attempt=0,updated_at=? WHERE run_id=?",
                               (self.research._now().isoformat(), task['run_id']))
            return self._decode(connection.execute('SELECT * FROM stocks_update_runs WHERE run_id=?', (task['run_id'],)).fetchone())

    def resume_dividend(self, run_id: str, pool_id: str) -> dict[str, Any]:
        """继续原分红任务，保留冻结范围和已完成的公司。"""
        with self.research._write() as connection:
            row = connection.execute('SELECT * FROM stocks_update_runs WHERE run_id=?',
                                     (_text(run_id, '任务标识'),)).fetchone()
            if row is None:
                raise StockResearchError('分红任务不存在')
            task = self._decode(row)
            if task['kind'] != 'dividend_review' or task['pool_ids'] != [pool_id]:
                raise StockResearchError('只能继续当前池的分红任务')
            if task['status'] in {'queued', 'running'}:
                return task
            if task['status'] not in {'partial', 'failed'}:
                raise StockResearchError('该分红任务无需继续')
            self._dividend_snapshot(task['input_snapshot'], [pool_id], task['target_date'])
            active = connection.execute("SELECT 1 FROM stocks_update_runs WHERE kind=? AND pool_ids=? AND target_date=? AND rule_version=? AND status IN ('queued','running')",
                                        (task['kind'], _json(task['pool_ids']), task['target_date'], task['rule_version'])).fetchone()
            if active is not None:
                raise StockRevisionConflict('同一范围日期已有排队或执行中的分红任务')
            connection.execute("UPDATE stocks_update_runs SET status='queued',phase='queued',error=NULL,worker_id=NULL,lease_until=NULL,lease_token=NULL,attempt=0,updated_at=? WHERE run_id=?",
                               (self.research._now().isoformat(), task['run_id']))
            return self._decode(connection.execute('SELECT * FROM stocks_update_runs WHERE run_id=?', (task['run_id'],)).fetchone())

    def resume_turnover(self, run_id: str, pool_id: str) -> dict[str, Any]:
        """继续原成交额任务并保留已完成公司。"""
        with self.research._write() as connection:
            row = connection.execute('SELECT * FROM stocks_update_runs WHERE run_id=?', (_text(run_id, '任务标识'),)).fetchone()
            if row is None:
                raise StockResearchError('成交额任务不存在')
            task = self._decode(row)
            if task['kind'] != 'turnover_review' or task['pool_ids'] != [pool_id]:
                raise StockResearchError('只能继续当前池的成交额任务')
            if task['status'] in {'queued', 'running'}:
                return task
            if task['status'] not in {'partial', 'failed'}:
                raise StockResearchError('该成交额任务无需继续')
            self._turnover_snapshot(task['input_snapshot'], [pool_id], task['target_date'])
            active = connection.execute("SELECT 1 FROM stocks_update_runs WHERE kind=? AND pool_ids=? AND target_date=? AND rule_version=? AND status IN ('queued','running')",
                                        (task['kind'], _json(task['pool_ids']), task['target_date'], task['rule_version'])).fetchone()
            if active is not None:
                raise StockRevisionConflict('同一范围日期已有排队或执行中的成交额任务')
            connection.execute("UPDATE stocks_update_runs SET status='queued',phase='queued',error=NULL,worker_id=NULL,lease_until=NULL,lease_token=NULL,attempt=0,updated_at=? WHERE run_id=?",
                               (self.research._now().isoformat(), task['run_id']))
            return self._decode(connection.execute('SELECT * FROM stocks_update_runs WHERE run_id=?', (task['run_id'],)).fetchone())

    def resume_reference(self, run_id: str, pool_ids: list[str]) -> dict[str, Any]:
        """只继续原基础资料任务，不重新冻结或替换研究日期。"""
        from radar.stocks.reference_task import validate_reference_snapshot
        pools = self._pools(pool_ids)
        with self.research._write() as connection:
            row = connection.execute('SELECT * FROM stocks_update_runs WHERE run_id=?', (_text(run_id, '任务标识'),)).fetchone()
            if row is None:
                raise StockResearchError('基础资料任务不存在')
            task = self._decode(row)
            if task['kind'] != 'reference_review' or task['pool_ids'] != pools:
                raise StockResearchError('只能继续原池范围的基础资料任务')
            if task['status'] in {'queued', 'running'}:
                return task
            if task['status'] not in {'partial', 'failed'}:
                raise StockResearchError('该基础资料任务无需继续')
            validate_reference_snapshot(task['input_snapshot'], pools, task['target_date'])
            active = connection.execute("SELECT 1 FROM stocks_update_runs WHERE kind=? AND pool_ids=? AND target_date=? AND rule_version=? AND status IN ('queued','running')",
                (task['kind'], _json(pools), task['target_date'], task['rule_version'])).fetchone()
            if active is not None:
                raise StockRevisionConflict('同一范围日期已有待执行的基础资料任务')
            connection.execute("UPDATE stocks_update_runs SET status='queued',phase='queued',error=NULL,worker_id=NULL,lease_until=NULL,lease_token=NULL,attempt=0,updated_at=? WHERE run_id=?",
                (self.research._now().isoformat(), task['run_id']))
            return self._decode(connection.execute('SELECT * FROM stocks_update_runs WHERE run_id=?', (task['run_id'],)).fetchone())

    def claim(self, worker_id: str) -> dict[str, Any] | None:
        worker_id = _text(worker_id, '执行者标识')
        with self.research._write() as connection:
            if connection.execute("SELECT 1 FROM stocks_update_runs WHERE status='running' LIMIT 1").fetchone() is not None:
                return None
            row = connection.execute("SELECT * FROM stocks_update_runs WHERE status='queued' ORDER BY created_at,run_id LIMIT 1").fetchone()
            if row is None:
                return None
            now = self.research._now()
            connection.execute("UPDATE stocks_update_runs SET status='running',phase='snapshot',worker_id=?,lease_until=?,lease_token=?,updated_at=?,error=NULL,attempt=attempt+1 WHERE run_id=? AND status='queued'", (worker_id, (now + timedelta(seconds=90)).isoformat(), uuid.uuid4().hex, now.isoformat(), row['run_id']))
            claimed = connection.execute('SELECT * FROM stocks_update_runs WHERE run_id=?', (row['run_id'],)).fetchone()
        return self._decode(claimed)

    def assert_owner(self, connection: sqlite3.Connection, run_id: str, worker_id: str, lease_token: str) -> None:
        """供同库原子导入使用；调用者须持有写事务。"""
        row = connection.execute('SELECT status,worker_id,lease_token,lease_until FROM stocks_update_runs WHERE run_id=?', (_text(run_id, '任务标识'),)).fetchone()
        if (row is None or row['status'] != 'running' or row['worker_id'] != _text(worker_id, '执行者标识')
                or row['lease_token'] != _text(lease_token, '租约令牌') or row['lease_until'] is None
                or datetime.fromisoformat(row['lease_until']) <= self.research._now()):
            raise StockRevisionConflict('股票更新任务租约已失效')

    def heartbeat(self, run_id: str, worker_id: str, *, lease_token: str) -> None:
        run_id = _text(run_id, '任务标识')
        with self.research._write() as connection:
            self.assert_owner(connection, run_id, worker_id, lease_token)
            now = self.research._now()
            connection.execute('UPDATE stocks_update_runs SET updated_at=?,lease_until=? WHERE run_id=?', (now.isoformat(), (now + timedelta(seconds=90)).isoformat(), run_id))

    @staticmethod
    def _item(item: Any) -> dict[str, Any]:
        if (not isinstance(item, dict) or not {'scope', 'status', 'summary'} <= set(item)
                or set(item) - {'scope', 'status', 'summary', 'artifact'}
                or not isinstance(item['status'], str) or item['status'] not in _ITEM_STATUSES):
            raise StockResearchError('逐项更新结果格式无效')
        prepared = {**item, 'scope': _text(item['scope'], '资料范围'), 'summary': _text(item['summary'], '结果摘要')}
        if 'artifact' in item and not isinstance(item['artifact'], dict):
            raise StockResearchError('资料原文引用须为JSON对象')
        # 通用研究序列化会把非有限数字转换成文本；任务结果明确拒绝这种数据。
        def validate(value: Any) -> None:
            if isinstance(value, float) and not math.isfinite(value):
                raise StockResearchError('任务结果不允许非有限数字')
            if isinstance(value, dict):
                for child in value.values():
                    validate(child)
            elif isinstance(value, list):
                for child in value:
                    validate(child)
        try:
            validate(prepared)
            return json.loads(_json(prepared))
        except RecursionError as exc:
            raise StockResearchError('任务结果JSON嵌套过深') from exc

    def record_item(self, run_id: str, worker_id: str, item: dict[str, Any], *, lease_token: str, phase: str = 'collect') -> None:
        run_id = _text(run_id, '任务标识')
        item = self._item(item)
        if not isinstance(phase, str) or phase not in _PHASES:
            raise StockResearchError('股票更新阶段无效')
        with self.research._write() as connection:
            self.assert_owner(connection, run_id, worker_id, lease_token)
            row = connection.execute('SELECT results FROM stocks_update_runs WHERE run_id=?', (run_id,)).fetchone()
            results = json.loads(row['results'])
            previous = next((result for result in reversed(results) if result['scope'] == item['scope']), None)
            if previous is not None and previous['status'] == 'completed':
                if previous == item:
                    return
                raise StockRevisionConflict('已成功的资料范围不能改写')
            results.append(item)
            now = self.research._now()
            connection.execute('UPDATE stocks_update_runs SET phase=?,results=?,updated_at=?,lease_until=? WHERE run_id=?', (phase, _json(results), now.isoformat(), (now + timedelta(seconds=90)).isoformat(), run_id))

    def finish(self, run_id: str, worker_id: str, *, lease_token: str, status: str, error: str | None = None) -> dict[str, Any]:
        run_id = _text(run_id, '任务标识')
        if not isinstance(status, str) or status not in _TERMINAL:
            raise StockResearchError('股票更新终态无效')
        if error is not None:
            error = _text(error, '任务错误')
        with self.research._write() as connection:
            self.assert_owner(connection, run_id, worker_id, lease_token)
            row = connection.execute('SELECT * FROM stocks_update_runs WHERE run_id=?', (run_id,)).fetchone()
            current = self._decode(row)
            if status == 'completed' and (not current['completed_scopes'] or current['pending_scopes'] or error is not None):
                raise StockResearchError('完整完成须有成功资料且没有失败、待配置或错误')
            connection.execute('UPDATE stocks_update_runs SET status=?,phase=?,error=?,updated_at=?,lease_until=NULL,lease_token=NULL WHERE run_id=?', (status, status, error, self.research._now().isoformat(), run_id))
            finished = connection.execute('SELECT * FROM stocks_update_runs WHERE run_id=?', (run_id,)).fetchone()
            return self._decode(finished)

    def recover_expired(self) -> int:
        now = self.research._now()
        with self.research._write() as connection:
            changed = connection.execute("UPDATE stocks_update_runs SET status=CASE WHEN attempt>=3 THEN 'failed' ELSE 'queued' END,phase=CASE WHEN attempt>=3 THEN 'failed' ELSE 'queued' END,worker_id=NULL,lease_until=NULL,lease_token=NULL,error='执行进程中断，租约已过期',updated_at=? WHERE status='running' AND lease_until<=?", (now.isoformat(), now.isoformat()))
        return changed.rowcount

    def list_runs(self, *, page: int = 1, page_size: int = 15) -> dict[str, Any]:
        if type(page) is not int or type(page_size) is not int or page < 1 or not 1 <= page_size <= 100:
            raise StockResearchError('页码须为正整数，页面大小须为1至100')
        with closing(self.research._connect()) as connection:
            connection.execute('BEGIN')
            total = connection.execute('SELECT COUNT(*) FROM stocks_update_runs').fetchone()[0]
            rows = connection.execute('SELECT * FROM stocks_update_runs ORDER BY created_at DESC,run_id DESC LIMIT ? OFFSET ?', (page_size, (page-1)*page_size)).fetchall() if (page-1)*page_size < total else []
        return {'items': [self._decode(row) for row in rows], 'total': total, 'page': page, 'page_size': page_size, 'automatic_enabled': False}
