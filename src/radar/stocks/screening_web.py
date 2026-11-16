"""网页筛选快照与补全任务；只复用既有来源和确定性计算。"""
from __future__ import annotations

import csv
import ctypes
import hashlib
import json
import logging
import os
import sqlite3
import sys
import threading
import uuid
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from radar.stocks.completion_adapters import ADAPTERS
from radar.stocks.completion_apply import apply_plan
from radar.stocks.completion_plan import build_completion_plan
from radar.stocks.completion_providers import (
    annual_archive_provider,
    turnover_source_provider,
)
from radar.stocks.completion_recheck import recheck_run
from radar.stocks.financial_source import load_financial_task
from radar.stocks.formal_rules import FORMAL_RULE_VERSION
from radar.stocks.research_store import StockRevisionConflict
from radar.stocks.screening_web_cash import completion_cash_verdict

logger = logging.getLogger(__name__)


def _process_identity(pid: int) -> str | None:
    """只读查询创建时间，避免Windows的os.kill(signal=0)终止进程及PID复用。"""
    if sys.platform != 'win32':
        try:
            return Path(f'/proc/{pid}/stat').read_text().split(') ')[1].split()[19]
        except (OSError, IndexError):
            return None
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        creation, exit_time, system, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time),
                                      ctypes.byref(system), ctypes.byref(user)):
            return None
        if exit_time.dwHighDateTime or exit_time.dwLowDateTime:
            return None
        return str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
    finally:
        kernel.CloseHandle(handle)


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def project(report: dict[str, Any], pool: str) -> list[dict[str, Any]]:
    """共同硬失败优先；缺失不当作失败，提醒不当作通过全部机会。"""
    rows = []
    for item in report['items']:
        checks = [*item.get('checks', []), *item.get('pools', {}).get(pool, {}).get('checks', [])]
        outcomes = {c.get('outcome') for c in checks}
        if pool == 'growth_trend' and any((c.get('code') or c.get('name')) == 'cash_review' and c.get('outcome') == 'attention' for c in checks):
            outcomes.add('pending_verification')
        invalid = item.get('evaluation_valid') is not True or not item.get('pools', {}).get(pool)
        status = ('pending' if invalid else 'failed' if item.get('common_status') == 'failed' or 'failed' in outcomes else 'pending' if
                  outcomes & {'pending_verification', 'unavailable', 'unconfigured'} or not checks else 'passed')
        rows.append({'symbol': item['symbol'], 'name': item['name'], 'status': status,
                     'industry': (item.get('industry_source', {}).get('industry') or {}).get('level2'),
                     'reason': '；'.join(dict.fromkeys(c.get('reason', '') for c in checks
                                                   if c.get('outcome') not in {'passed', 'not_applicable'})),
                     'checks': checks, 'evaluation': item})
    return rows


def details(report: dict[str, Any], pool: str) -> list[dict[str, Any]]:
    rows = []
    for item in report['items']:
        for group, checks in [('common', item.get('checks', [])),
                              ('style', item.get('pools', {}).get(pool, {}).get('checks', []))]:
            for check in checks:
                rows.append({'代码': item['symbol'], '名称': item['name'], '池': pool, '分类': group,
                             '条件': check.get('code') or check.get('name'), '结果': check.get('outcome'),
                             '原因': check.get('reason', '')})
    return rows


def read_snapshot(state: Path, pool: str) -> dict[str, Any] | None:
    """只读读取当前池快照；不创建存储。"""
    path = Path(state) / 'stock-screening.sqlite'
    if not path.exists():
        return None
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as connection:
        row = connection.execute('SELECT payload FROM snapshots WHERE id=?', (pool,)).fetchone()
    return json.loads(row[0]) if row else None


def write_snapshot(state: Path, pool: str, payload: dict[str, Any]) -> None:
    """写入完整快照；列表、计数和详情共用同一份报告。"""
    path = Path(state) / 'stock-screening.sqlite'
    Path(state).mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=30) as connection:
        for name in ('tasks', 'snapshots'):
            connection.execute('CREATE TABLE IF NOT EXISTS ' + name + '(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        connection.execute('INSERT INTO snapshots VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                           (pool, json.dumps(payload, ensure_ascii=False)))


class ScreeningService:
    """任务持久化；重启中的任务明确失败，完整结果保留供重试。"""
    def __init__(self, state: Path, evaluate: Callable[..., Any] | None = None, collect: Callable[..., Any] | None = None, *, pipeline: Any = None):
        self.state, self.evaluate, self.collect = state, evaluate, collect
        self.pipeline = pipeline
        self.path = state / 'stock-screening.sqlite'
        self.lock = threading.Lock()

    def _read(self, table: str, key: str | None = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True) as c:
            rows = c.execute('SELECT payload FROM ' + table + (' WHERE id=?' if key else ''),
                             (key,) if key else ()).fetchall()
        return [json.loads(r[0]) for r in rows]

    def _put(self, table: str, key: str, value: Any) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=30) as c:
            for name in ('tasks', 'snapshots'):
                c.execute('CREATE TABLE IF NOT EXISTS ' + name + '(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS screening_single_active ON tasks((1)) WHERE json_extract(payload,'$.status') IN ('queued','running')")
            c.execute('INSERT INTO ' + table + ' VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                      (key, json.dumps(value, ensure_ascii=False)))

    def recover(self) -> None:
        for task in self._read('tasks'):
            if task['status'] in {'queued', 'running'}:
                owner_pid = task.get('owner_pid')
                identity = _process_identity(owner_pid) if isinstance(owner_pid, int) and owner_pid > 0 else None
                if identity is not None and identity == task.get('owner_identity'):
                    continue
                logger.debug('原筛选任务进程已退出或身份变化，标记失败以便重试')
                task.update(status='failed', error='服务重启中断任务；已完成快照保留，请重试', updated_at=_now())
                self._put('tasks', task['run_id'], task)

    def snapshot(self, pool: str) -> dict[str, Any] | None:
        rows = self._read('snapshots', pool)
        return rows[0] if rows else None

    def latest(self, pool: str) -> dict[str, Any] | None:
        rows = [t for t in self._read('tasks') if t['pool_id'] == pool]
        return max(rows, key=lambda t: t['created_at']) if rows else None

    def task(self, run_id: str) -> dict[str, Any] | None:
        rows = self._read('tasks', run_id)
        return rows[0] if rows else None

    def listing(self, pool: str, status: str, search: str, page: int, page_size: int) -> dict[str, Any]:
        snap = self.snapshot(pool)
        items = project(snap['report'], pool) if snap else []
        counts = {'screened': len(items), **{s: sum(r['status'] == s for r in items)
                                           for s in ('passed', 'pending', 'failed')}}
        selected = [r for r in items if (status == 'all' or r['status'] == status)
                    and search.casefold() in (r['symbol'] + r['name']).casefold()]
        return {'items': selected[(page-1)*page_size:page*page_size], 'total': len(selected),
                'page': page, 'page_size': page_size, 'counts': counts,
                'as_of': snap['as_of'] if snap else None, 'rule_version': FORMAL_RULE_VERSION,
                'task': self.latest(pool), 'members_published': 0}

    def preview(self, pool: str) -> dict[str, Any]:
        if self.pipeline is None:
            raise ValueError("网页流水线尚未配置")
        return self.pipeline.preview(pool)

    def plan(self, pool: str) -> dict[str, Any]:
        if self.pipeline is not None:
            return self.pipeline.plan(pool)
        snap = self.snapshot(pool)
        if snap is None:
            raise ValueError('请先更新股票池，生成完整筛选结果')
        plan = build_completion_plan(details(snap['report'], pool), self.state/'radar.db',
                                     date.fromisoformat(snap['as_of']), pools={pool})
        # 计划绑定整个快照，防止相同缺项掩盖公司结果或范围变化。
        pending = [row for row in project(snap['report'], pool) if row['status'] == 'pending']
        represented = {item['symbol'] for item in plan['items']}
        for item in plan['items']:
            item['automatic'] = (item.get('source') or {}).get('kind') in ADAPTERS
        plan['pending_total'] = len(pending)
        plan['blocked_items'] = [{'symbol': row['symbol'], 'name': row['name'],
                                  'reason': row['reason'] or '共同条件资料待补，暂无自动补全适配器'}
                                 for row in pending if row['symbol'] not in represented]
        plan['automatic_count'] = sum((item.get('source') or {}).get('kind') in ADAPTERS for item in plan['items'])
        plan['manual_count'] = len(plan['items']) - plan['automatic_count'] + len(plan['blocked_items'])
        plan['snapshot_sha256'] = _digest(snap)
        plan['plan_sha256'] = _digest({k: v for k, v in plan.items() if k != 'plan_sha256'})
        return plan

    def start(self, pool: str, kind: str, scope: dict[str, Any] | None = None,
              plan_sha256: str | None = None, preview_sha256: str | None = None) -> dict[str, Any]:
        with self.lock:
            active = next((t for t in self._read('tasks') if t['status'] in {'queued', 'running'}), None)
            if active:
                if active['pool_id'] == pool and active['kind'] == kind:
                    return active
                raise StockRevisionConflict('已有股票筛选或补全任务执行中，请等待完成')
            plan = self.plan(pool) if kind == 'completion' else None
            confirmation = None
            if self.pipeline is not None:
                confirmation = plan['execution_preview'] if plan is not None else self.preview(pool)
                if plan is None and confirmation['preview_sha256'] != preview_sha256:
                    raise StockRevisionConflict('更新预览已变化，请重新读取并确认')
                scope = confirmation
            if plan is not None and plan['plan_sha256'] != plan_sha256:
                raise StockRevisionConflict('补全计划或结果已变化，请重新读取计划')
            if plan is not None and not plan['items'] and not plan.get('shared_stages'):
                raise ValueError('当前没有待补全事项')
            task = {'run_id': uuid.uuid4().hex, 'pool_id': pool, 'kind': kind, 'status': 'queued',
                    'phase': 'snapshot', 'as_of': plan['as_of'] if plan else confirmation['as_of'] if confirmation else datetime.now().astimezone().date().isoformat(),
                    'owner_pid': os.getpid(), 'owner_identity': _process_identity(os.getpid()), 'created_at': _now(), 'updated_at': _now(), 'progress': {'attempted': 0, 'total':
                        len(plan['items']) if plan else (scope or {}).get('company_count', len((scope or {}).get('companies', [])))},
                    'results': [], 'error': None}
            try:
                self._put('tasks', task['run_id'], task)
            except sqlite3.IntegrityError as exc:
                raise StockRevisionConflict('已有股票筛选或补全任务执行中') from exc
            threading.Thread(target=self._run, args=(task, scope, plan), daemon=True).start()
            return task

    def _run(self, task: dict[str, Any], scope: Any, plan: Any) -> None:
        def progress(attempted: int, total: int, results: Any = None, phase: str = 'collect') -> None:
            task.update(status='running', phase=phase, progress={'attempted': attempted, 'total': total}, updated_at=_now())
            if results is not None:
                task['results'] = results
            self._put('tasks', task['run_id'], task)
        try:
            pool, cutoff = task['pool_id'], date.fromisoformat(task['as_of'])
            progress(0, task['progress']['total'])
            if self.pipeline is not None:
                outcome = self.pipeline.execute(task, scope, plan, progress)
                task.update(status=outcome['status'], stage_errors=outcome['stage_errors'],
                            results=outcome.get('results') or task.get('results', []), evidence=outcome.get('evidence'),
                            phase='finalizing', updated_at=_now())
                self._put('tasks', task['run_id'], task)
                return
            if plan is None:
                assert self.collect is not None and self.evaluate is not None
                self.collect(pool, scope, cutoff, progress)
                report = self.evaluate(pool, scope['companies'], cutoff, progress)
                if any(item.get('evaluation_valid') is not True for item in report['items']):
                    raise ValueError('计算期间资料无效或变化，已保留上次完整结果，请重试')
            else:
                snap = self.snapshot(pool)
                if snap is None or _digest(snap) != plan['snapshot_sha256']:
                    raise StockRevisionConflict('补全前快照变化，请重新读取计划')
                report = snap['report']
                workspace = self.state/'stock-screening'/task['run_id']
                workspace.mkdir(parents=True, exist_ok=True)
                rows = details(report, pool)
                csv_path = workspace/'details.csv'
                with csv_path.open('w', encoding='utf-8-sig', newline='') as stream:
                    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                    writer.writeheader()
                    writer.writerows(rows)
                run = apply_plan(plan, yes=True, providers={
                    'annual_report': lambda symbol, name, years: annual_archive_provider(
                        symbol, name, years, cutoff, workspace/'annual'),
                    'turnover': turnover_source_provider(self.state/'radar.db', self.state/'stock-updates/cache')},
                    on_progress=lambda done, total, items: progress(done, total, items))
                run_path = workspace/'run.json'
                run_path.write_text(json.dumps(run, ensure_ascii=False), encoding='utf-8')
                checked = recheck_run(run_path, csv_path)
                for override in checked['overrides']:
                    for item in report['items']:
                        if item['symbol'] == override['symbol']:
                            for check in item.get('pools', {}).get(pool, {}).get('checks', []):
                                if (check.get('code') or check.get('name')) == override['condition']:
                                    if check.get('outcome') == 'failed':
                                        continue
                                    outcome = override['result']
                                    if override['condition'] == 'cash_review' and outcome == 'passed':
                                        selected = load_financial_task(self.state/'radar.db', item['symbol'], cutoff)
                                        original = item.get('financial_source') or {}
                                        completed = next((r for r in run['items'] if r.get('symbol') == item['symbol']
                                                          and r.get('condition') == 'cash_review'), {})
                                        verified = {'outcome': 'pending_verification', 'reason': '原财务来源变化或不可用，需重新筛选'}
                                        if (selected.source.get('status') == 'current' and isinstance(selected.payload, dict)
                                                and all(original.get(k) == selected.source.get(k) for k in ('run_id', 'item_sha256'))):
                                            verified = completion_cash_verdict(original, selected.payload.get('collection') or {},
                                                                               completed.get('evidence') or {}, cutoff,
                                                                               item['symbol'], item.get('issuer_type'))
                                        outcome = verified['outcome']
                                        override = {**override, 'reason': verified['reason']}
                                    if override['condition'] == 'liquidity' and outcome == 'failed':
                                        outcome = 'warning' if pool == 'dividend_value' else 'observe'
                                    if override['condition'] == 'cash_review' and pool == 'growth_trend' and outcome == 'failed':
                                        outcome = 'attention'
                                    check.update(outcome=outcome, reason=override['reason'])
                task['results'] = run['items']
            self._put('snapshots', pool, {'as_of': cutoff.isoformat(), 'report': report,
                                         'run_id': task['run_id'], 'rule_version': FORMAL_RULE_VERSION})
            task.update(status='completed', phase='finalizing', updated_at=_now())
        except Exception as exc:  # noqa: BLE001, RUF100 后台任务隔离边界，异常记录并保留可重试状态
            logger.exception('股票网页筛选任务失败')
            task.update(status='failed', error=str(exc), updated_at=_now())
        self._put('tasks', task['run_id'], task)
