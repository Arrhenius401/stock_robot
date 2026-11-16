"""股票池证据准备与筛选流水线：共享证据 → 公司资料 → 复算 → 计划 → 补全 → 单次落盘。

CLI 与网页后续共用同一实现；这里只复用既有来源、任务队列和确定性复算。
"""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Callable
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from typing import Any

from radar.stocks.company_profile_source import load_company_profile_source
from radar.stocks.completion_apply import apply_plan
from radar.stocks.completion_plan_v2 import (
    build_completion_plan_v2,
    plan_firm_items,
)
from radar.stocks.completion_providers import (
    annual_archive_provider,
    turnover_source_provider,
)
from radar.stocks.completion_recheck import recheck_run
from radar.stocks.dividend_collect import VERSION as DIVIDEND_VERSION
from radar.stocks.dividend_source import load_dividend_source
from radar.stocks.evaluation_store import StockEvaluationStore
from radar.stocks.exchange_governance import load_exchange_governance_sources
from radar.stocks.financial_source import load_financial_source, load_financial_task
from radar.stocks.formal_rules import FORMAL_RULE_VERSION
from radar.stocks.growth_source import load_growth_source
from radar.stocks.industry_source import load_industry_source
from radar.stocks.leadership_source import load_leadership_source
from radar.stocks.market_value_registry import load_market_value_source
from radar.stocks.membership_store import StockMembershipStore
from radar.stocks.peer_universe import (
    frozen_reference_snapshot,
    load_peer_universe,
)
from radar.stocks.pools import StockPoolRepository
from radar.stocks.reference_task import REFERENCE_VERSION, prepare_reference_snapshot
from radar.stocks.research_store import StockResearchStore
from radar.stocks.screening_web import details, project, read_snapshot, write_snapshot
from radar.stocks.screening_web_cash import completion_cash_verdict
from radar.stocks.shared_evidence import (
    build_scale,
    collect_governance,
    evaluation_identity,
    governance_ready,
    scale_ready,
)
from radar.stocks.structured_screen import RULE_VERSION as FINANCIAL_RULE_VERSION
from radar.stocks.turnover_source import VERSION as TURNOVER_VERSION
from radar.stocks.turnover_source import load_turnover_source
from radar.stocks.update_runner import StockUpdateRunner
from radar.stocks.update_runs import StockUpdateStore
from radar.stocks.update_service import load_market_window
from radar.stocks.web_queries import read_connection, scope_snapshot
from stock_robot.stock_admission import evaluate_store

PIPELINE_VERSION = 'stock-screening-pipeline-v1'
_CACHE = 'stock-updates/cache'
_ARTIFACTS = 'stock-updates/artifacts'


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _plan_sha(plan: dict[str, Any]) -> str:
    """与 completion-apply 的计划校验保持同一规范形式。"""
    body = {key: value for key, value in plan.items() if key not in {'plan_sha256', 'members_published'}}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def snapshot_digest(payload: dict[str, Any]) -> str:
    """快照摘要不含任务标识，计划与复核共用同一绑定。"""
    return _digest({key: payload.get(key) for key in ('as_of', 'report', 'rule_version', 'evidence')})


def _window_days(window: list[date] | None) -> list[str] | None:
    return None if window is None else [day.isoformat() for day in window]


def _peer_summary(peer: dict[str, Any]) -> dict[str, Any]:
    payload = peer.get('payload') or {}
    return {'status': peer.get('status'), 'file_sha256': peer.get('file_sha256'),
            'company_count': payload.get('company_count') or peer.get('company_count'),
            'as_of': payload.get('as_of'), 'scope_sha256': payload.get('scope_sha256'),
            'reason': peer.get('reason')}


def _check_window(source: dict[str, Any], window: list[date] | None, trace: Any) -> None:
    if source.get('status') != 'current':
        return
    calendar = trace.get('calendar') if isinstance(trace, dict) else None
    if (window is None or source.get('window_days') != _window_days(window)
            or not isinstance(calendar, dict) or source.get('calendar_sha256') != calendar.get('snapshot_sha256')):
        raise ValueError('市场日历来源已变化，请重新采集')


class ScreeningPipeline:
    """一次构建一份快照；列表、计数和详情都从同一份快照派生。"""

    def __init__(self, state_dir: Path, *, pool_dir: Path | None = None) -> None:
        self.state = Path(state_dir).absolute()
        self.db = self.state / 'radar.db'
        self.pool_dir = Path(pool_dir) if pool_dir is not None else Path(__file__).parents[3] / 'config' / 'radar_stock_pools'
        self.pools = StockPoolRepository(self.pool_dir)
        self.cache = self.state / _CACHE
        self.artifacts = self.state / _ARTIFACTS
        self._tasks: StockUpdateStore | None = None
        self._runner: StockUpdateRunner | None = None

    def service(self) -> tuple[StockUpdateStore, StockUpdateRunner]:
        """按需创建任务队列；只读命令不创建业务存储。"""
        if self._tasks is None or self._runner is None:
            research = StockResearchStore(self.db)
            memberships = StockMembershipStore(StockEvaluationStore(research), self.pools)
            self._tasks = StockUpdateStore(memberships)
            self._runner = StockUpdateRunner(self._tasks, self.cache, self.artifacts,
                                             window_loader=lambda day: load_market_window(self.state, day))
        return self._tasks, self._runner

    # ---------- 范围与任务 ----------

    def scope(self, pool: str) -> dict[str, Any]:
        if self.pools.get(pool) is None:
            raise ValueError('股票池不存在或未启用')
        with read_connection(self.db) as connection:
            scope = scope_snapshot(connection, self.state, pool)
        if not scope['scope_complete'] or not scope['companies']:
            raise ValueError('请先准备当前池完整候选范围')
        return scope

    def _completed(self, kind: str, pool_ids: list[str], cutoff: date, version: str) -> list[dict[str, Any]]:
        if not self.db.is_file():
            return []
        with closing(sqlite3.connect(self.db.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM stocks_update_runs WHERE kind=? AND target_date=? AND status='completed' "
                "AND rule_version=? ORDER BY created_at DESC,rowid DESC",
                (kind, cutoff.isoformat(), version)).fetchall()
        result = []
        for row in rows:
            task = StockUpdateStore._decode(row)
            if task['pool_ids'] == sorted(pool_ids):
                result.append(task)
        return result

    def _reusable(self, kind: str, pool: str, cutoff: date, version: str, scope_sha: str | None) -> dict[str, Any] | None:
        for task in self._completed(kind, [pool], cutoff, version):
            snapshot = task['input_snapshot']
            if scope_sha is None or snapshot.get('scope_sha256') == scope_sha:
                return task
        return None

    def _drain(self, run_id: str, progress: Callable[..., None] | None) -> dict[str, Any]:
        tasks, runner = self.service()
        last = ''
        while True:
            task = tasks.get(run_id)
            if task['status'] not in {'queued', 'running'}:
                return task
            scope = last or task['phase']
            if progress is not None:
                latest = {item['scope']: item for item in task['results']}
                progress(len(latest), len(latest) + 1, [{'scope': r['scope'], 'status': r['status'],
                                                         'summary': r.get('summary', '')}
                                                        for r in latest.values()], scope)
            executed = runner.execute_one('stock-screening-cli')
            if executed is None:
                time.sleep(1)
            else:
                last = task['phase']

    def _run_task(self, kind: str, pool_ids: list[str], cutoff: date, version: str,
                  snapshot: dict[str, Any], progress: Callable[..., None] | None) -> dict[str, Any]:
        tasks, _runner = self.service()
        task = tasks.enqueue(kind=kind, pool_ids=pool_ids, target_date=cutoff, rule_version=version,
                             snapshot=snapshot)
        return self._drain(task['run_id'], progress)

    # ---------- 共享证据 ----------

    def prepare_shared(self, cutoff: date, progress: Callable[..., None] | None = None) -> dict[str, Any]:
        """治理快照 → 同业市值覆盖 → 同业排名；失败明确记录，不降级为小范围。"""
        window, trace = load_market_window(self.state, cutoff)
        if window is None:
            raise ValueError('市场日历不可用，无法准备共享证据')
        governance = collect_governance(self.state, cutoff, progress)
        if progress is not None:
            progress(1, 3, [{'scope': 'governance', 'status': 'completed' if governance.get('current') else 'failed',
                             'summary': governance.get('reason', '')}], 'governance')
        peer = load_peer_universe(self.state)
        if peer['status'] != 'current':
            return {'governance': governance, 'peer_universe': _peer_summary(peer),
                    'industry_scale': {'current': False, 'reason': '同业范围未准备'},
                    'window_sha256': _digest(trace)}
        payload = peer['payload']
        symbols = [row['symbol'] for row in payload['companies']]
        missing = [symbol for symbol in symbols if load_market_value_source(self.state, symbol, cutoff).get('status') != 'current']
        if missing:
            frozen = frozen_reference_snapshot(payload, cutoff)
            task = self._run_task('reference_review', ['dividend_value', 'growth_trend'], cutoff,
                                  REFERENCE_VERSION, frozen, progress)
            if task['status'] != 'completed':
                return {'governance': governance, 'peer_universe': _peer_summary(peer),
                        'industry_scale': {'current': False, 'reason': f'同业市值采集未完成：{task.get("error") or task["status"]}'},
                        'window_sha256': _digest(trace)}
        still = [symbol for symbol in symbols if load_market_value_source(self.state, symbol, cutoff).get('status') != 'current']
        if progress is not None:
            progress(2, 3, [{'scope': 'peer_market_value', 'status': 'completed' if not still else 'failed',
                             'summary': f'缺{len(still)}家' if still else f'{len(symbols)}家已覆盖'}], 'peer_market_value')
        if still:
            return {'governance': governance, 'peer_universe': _peer_summary(peer),
                    'industry_scale': {'current': False, 'reason': f'同业市值覆盖不足 {len(still)}/{len(symbols)}'},
                    'window_sha256': _digest(trace)}
        ready = scale_ready(self.state, cutoff, peer_universe_sha256=str(peer['file_sha256']),
                            company_count=len(symbols), sample_symbol=symbols[0])
        if ready is None:
            ready = build_scale(self.state, self.db, cutoff, peer_universe_sha256=str(peer['file_sha256']),
                                scope_sha256=str(payload['scope_sha256']), symbols=symbols,
                                window_sha256=_digest(trace), mapping_path=self._mapping_path())
        if progress is not None:
            progress(3, 3, [{'scope': 'industry_scale', 'status': 'completed' if ready.get('current') else 'failed',
                             'summary': ready.get('reason', '')}], 'industry_scale')
        return {'governance': governance, 'peer_universe': _peer_summary(peer), 'industry_scale': ready,
                'window_sha256': _digest(trace)}

    def _mapping_path(self) -> Path:
        from utils.paths import industry_mapping_path
        return industry_mapping_path()

    # ---------- 公司资料与复算 ----------

    def _collect_firm(self, pool: str, scope: dict[str, Any], cutoff: date,
                      progress: Callable[..., None] | None) -> list[dict[str, Any]]:
        versions = {'financial_review': FINANCIAL_RULE_VERSION, 'dividend_review': DIVIDEND_VERSION,
                    'turnover_review': TURNOVER_VERSION}
        errors = []
        for kind, version in versions.items():
            reusable = self._reusable(kind, pool, cutoff, version, scope.get('scope_sha256'))
            if reusable is not None:
                continue
            snapshot = {**scope, 'kind': kind, 'pool_ids': [pool]}
            task = self._run_task(kind, [pool], cutoff, version, snapshot, progress)
            if task['status'] != 'completed':
                errors.append({'stage': kind, 'error': task.get('error') or task['status']})
        reference = self._reusable_reference(pool, cutoff, scope)
        if reference is None:
            frozen = prepare_reference_snapshot(self.db, self.state, [pool], cutoff)
            task = self._run_task('reference_review', [pool], cutoff, REFERENCE_VERSION, frozen, progress)
            if task['status'] != 'completed':
                errors.append({'stage': 'reference_review', 'error': task.get('error') or task['status']})
        return errors

    def _reusable_reference(self, pool: str, cutoff: date, scope: dict[str, Any]) -> dict[str, Any] | None:
        symbols = {row['symbol'] for row in scope['companies']}
        for task in self._completed('reference_review', [pool], cutoff, REFERENCE_VERSION):
            companies = {row['symbol'] for row in task['input_snapshot'].get('companies', [])}
            if symbols <= companies:
                return task
        return None

    def evaluate(self, companies: list[dict[str, Any]], cutoff: date,
                 progress: Callable[..., None] | None = None) -> dict[str, Any]:
        store = StockResearchStore(self.db)
        window, trace = load_market_window(self.state, cutoff)
        if window is None:
            raise ValueError('市场日历不可用，无法复核筛选条件')
        report: dict[str, Any] = {'as_of': cutoff.isoformat(), 'items': [], 'members_published': 0}
        governance = load_exchange_governance_sources(self.state, [c['symbol'] for c in companies], cutoff)
        loaders = {'financial_sources': lambda s: load_financial_source(self.db, s, cutoff),
                   'dividend_sources': lambda s: load_dividend_source(self.db, s, cutoff),
                   'turnover_sources': lambda s: load_turnover_source(self.db, s, cutoff),
                   'profile_sources': lambda s: load_company_profile_source(self.state, s, cutoff),
                   'industry_sources': lambda s: load_industry_source(self.state, s, cutoff),
                   'growth_sources': lambda s: load_growth_source(self.state, s, cutoff),
                   'market_value_sources': lambda s: load_market_value_source(self.state, s, cutoff),
                   'governance_sources': lambda s: governance[s],
                   'leadership_sources': lambda s: load_leadership_source(self.state, s, cutoff)}
        for index, company in enumerate(companies):
            symbol = company['symbol']
            sources = {key: {symbol: loader(symbol)} for key, loader in loaders.items()}
            _check_window(sources['market_value_sources'][symbol], window, trace)
            _check_window(sources['turnover_sources'][symbol], window, trace)
            evaluated = evaluate_store(store, cutoff, symbol=symbol, market_window=window,
                                       include_styles=True, structured_only=True, **sources)
            if any(item.get('evaluation_valid') is not True for item in evaluated['items']):
                raise ValueError(f'{symbol} 计算期间资料无效或变化')
            report['items'].extend(evaluated['items'])
            if progress is not None:
                progress(index + 1, len(companies), phase='review')
        return report

    # ---------- 快照 ----------

    def build(self, pool: str, cutoff: date, *, shared: bool = True,
              progress: Callable[..., None] | None = None) -> dict[str, Any]:
        scope = self.scope(pool)
        shared_result = self.prepare_shared(cutoff, progress) if shared else {}
        errors = self._collect_firm(pool, scope, cutoff, progress)
        report = self.evaluate(scope['companies'], cutoff, progress)
        evidence = self._evidence(cutoff, shared_result)
        payload = {'as_of': cutoff.isoformat(), 'report': report, 'rule_version': FORMAL_RULE_VERSION,
                   'evidence': evidence}
        payload['shared'] = shared_result
        payload['stage_errors'] = errors
        payload['scope'] = {'pool_id': pool, 'company_count': len(scope['companies']),
                            'scope_sha256': scope.get('scope_sha256'), 'scope_as_of': scope.get('scope_as_of')}
        payload['snapshot_sha256'] = snapshot_digest(payload)
        return payload

    def _evidence(self, cutoff: date, shared: dict[str, Any]) -> dict[str, Any]:
        governance = shared.get('governance') or {}
        scale = shared.get('industry_scale') or {}
        peer = shared.get('peer_universe') or {}
        return {'version': PIPELINE_VERSION, 'as_of': cutoff.isoformat(),
                'governance': {'status': 'current' if governance.get('current') else 'unavailable',
                               'as_of': governance.get('as_of'), 'manifest_sha256': governance.get('manifest_sha256'),
                               'records': governance.get('records')},
                'industry_scale': {'status': 'current' if scale.get('current') else 'unavailable',
                                   'file_sha256': scale.get('file_sha256'),
                                   'company_count': scale.get('company_count'),
                                   'peer_universe_sha256': peer.get('file_sha256')},
                'evaluation': evaluation_identity(cutoff), 'generated_at': _now()}

    def commit(self, pool: str, payload: dict[str, Any], *, run_id: str | None = None) -> dict[str, Any]:
        value = {**payload, 'run_id': run_id or uuid.uuid4().hex}
        write_snapshot(self.state, pool, value)
        return value

    def plan(self, pool: str) -> dict[str, Any]:
        snapshot = read_snapshot(self.state, pool)
        if snapshot is None:
            raise ValueError('请先更新股票池，生成完整筛选结果')
        shared = self._shared_status(snapshot)
        rows = details(snapshot['report'], pool)
        plan = build_completion_plan_v2(rows, self.db, date.fromisoformat(snapshot['as_of']),
                                        pools={pool}, shared=shared)
        plan.update(snapshot_sha256=snapshot_digest(snapshot),
                    evidence=snapshot.get('evidence'),
                    as_of=snapshot['as_of'], rule_version=snapshot.get('rule_version'))
        plan['plan_sha256'] = _plan_sha(plan)
        return plan

    def _shared_status(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        evidence = snapshot.get('evidence') or {}
        governance = evidence.get('governance') or {}
        scale = evidence.get('industry_scale') or {}
        return {'governance': {'current': governance.get('status') == 'current'
                               and governance.get('as_of') == snapshot.get('as_of'),
                               'reason': '快照已绑定同日监管证据' if governance.get('status') == 'current' else '缺少同日监管证据'},
                'industry_scale': {'current': scale.get('status') == 'current',
                                   'reason': '快照已绑定同业排名' if scale.get('status') == 'current' else '缺少同业排名'}}

    # ---------- 补全 ----------

    def complete(self, pool: str, plan: dict[str, Any], *, yes: bool = True,
                 progress: Callable[..., None] | None = None) -> dict[str, Any]:
        snapshot = read_snapshot(self.state, pool)
        if snapshot is None:
            raise ValueError('当前池没有筛选快照')
        if plan.get('snapshot_sha256') != snapshot_digest(snapshot):
            raise ValueError('补全计划与当前快照不一致，请重新生成计划')
        if not yes:
            return {'dry_run': True, 'items': plan_firm_items(plan)}
        report, overrides = self._apply(pool, snapshot['report'], plan, date.fromisoformat(snapshot['as_of']),
                                        snapshot.get('run_id') or uuid.uuid4().hex, progress)
        payload = {**snapshot, 'report': report}
        payload['snapshot_sha256'] = snapshot_digest(payload)
        committed = self.commit(pool, payload)
        return {'as_of': payload['as_of'], 'overrides': overrides, 'run_id': committed['run_id'],
                'counts': self._counts(payload['report'], pool)}

    def _apply(self, pool: str, report: dict[str, Any], plan: dict[str, Any], cutoff: date, run_id: str,
               progress: Callable[..., None] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        workspace = self.state / 'stock-screening' / run_id
        items = plan_firm_items(plan)
        if not items:
            return report, []
        workspace.mkdir(parents=True, exist_ok=True)
        rows = details(report, pool)
        if not rows:
            return report, []
        csv_path = workspace / 'details.csv'
        with csv_path.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        run = apply_plan({**plan, 'items': items}, yes=True, providers={
            'annual_report': lambda symbol, name, years: annual_archive_provider(
                symbol, name, years, cutoff, workspace / 'annual'),
            'turnover': turnover_source_provider(self.db, self.state / 'stock-updates/cache')},
            on_progress=lambda done, total, result: progress(done, total, result, 'completion')
            if progress is not None else None)
        run_path = workspace / 'run.json'
        run_path.write_text(json.dumps(run, ensure_ascii=False), encoding='utf-8')
        checked = recheck_run(run_path, csv_path)
        for override in checked['overrides']:
            for item in report['items']:
                if item['symbol'] != override['symbol']:
                    continue
                for check in item.get('pools', {}).get(pool, {}).get('checks', []):
                    if (check.get('code') or check.get('name')) != override['condition']:
                        continue
                    if check.get('outcome') == 'failed':
                        continue
                    outcome = override['result']
                    if override['condition'] == 'cash_review' and outcome == 'passed':
                        selected = load_financial_task(self.db, item['symbol'], cutoff)
                        original = item.get('financial_source') or {}
                        completed = next((row for row in run['items'] if row.get('symbol') == item['symbol']
                                          and row.get('condition') == 'cash_review'), {})
                        verified: dict[str, Any] = {'outcome': 'pending_verification',
                                                    'reason': '原财务来源变化或不可用，需重新筛选'}
                        if (selected.source.get('status') == 'current' and isinstance(selected.payload, dict)
                                and all(original.get(key) == selected.source.get(key) for key in ('run_id', 'item_sha256'))):
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
        return report, checked['overrides']

    def _counts(self, report: dict[str, Any], pool: str) -> dict[str, int]:
        items = project(report, pool)
        return {'screened': len(items), **{status: sum(row['status'] == status for row in items)
                                           for status in ('passed', 'pending', 'failed')}}

    # ---------- 编排 ----------

    def update(self, pool: str, cutoff: date, *, shared: bool = True,
               progress: Callable[..., None] | None = None) -> dict[str, Any]:
        payload = self.build(pool, cutoff, shared=shared, progress=progress)
        committed = self.commit(pool, payload)
        return {'run_id': committed['run_id'], 'as_of': payload['as_of'], 'evidence': payload['evidence'],
                'stage_errors': payload['stage_errors'], 'scope': payload['scope'],
                'counts': self._counts(payload['report'], pool)}

    def close_loop(self, pool: str, cutoff: date, progress: Callable[..., None] | None = None) -> dict[str, Any]:
        payload = self.build(pool, cutoff, shared=True, progress=progress)
        plan = build_completion_plan_v2(details(payload['report'], pool), self.db, cutoff,
                                        pools={pool}, shared=self._shared_status(payload))
        plan.update(snapshot_sha256=snapshot_digest(payload), evidence=payload['evidence'],
                    as_of=payload['as_of'], rule_version=payload['rule_version'])
        plan['plan_sha256'] = _plan_sha(plan)
        run_id = uuid.uuid4().hex
        if plan_firm_items(plan):
            report, overrides = self._apply(pool, payload['report'], plan, cutoff, run_id, progress)
            payload['report'] = report
        else:
            overrides = []
        payload['snapshot_sha256'] = snapshot_digest(payload)
        committed = self.commit(pool, payload, run_id=run_id)
        return {'run_id': committed['run_id'], 'as_of': payload['as_of'], 'counts': self._counts(payload['report'], pool),
                'overrides': overrides, 'plan': plan, 'stage_errors': payload['stage_errors'],
                'evidence': payload['evidence']}

    def evidence(self, cutoff: date) -> dict[str, Any]:
        governance = governance_ready(self.state, cutoff)
        peer = load_peer_universe(self.state)
        scale = scale_ready(self.state, cutoff, peer_universe_sha256=str(peer.get('file_sha256')),
                            company_count=peer.get('company_count') or 0,
                            sample_symbol=(peer['payload']['companies'][0]['symbol'] if peer.get('payload') else ''))
        return {'governance': governance, 'industry_scale': scale, 'peer_universe': _peer_summary(peer),
                'evaluation': evaluation_identity(cutoff)}
