"""将统一流水线接入网页确认与持久化任务，不另建采集路径。"""
from datetime import date, datetime
from typing import Any

from radar.stocks.completion_plan_v2 import build_completion_plan_v2, plan_firm_items
from radar.stocks.research_store import StockRevisionConflict
from radar.stocks.screening_pipeline import (
    ScreeningPipeline,
    _plan_sha,
    snapshot_digest,
)
from radar.stocks.screening_web import _digest, details, project


class WebPipeline:
    """执行前摘要绑定范围、截点、规则及共享缓存。"""
    def __init__(self, pipeline: ScreeningPipeline):
        self.pipeline = pipeline

    def preview(self, pool: str, cutoff: date | None = None) -> dict[str, Any]:
        cutoff = cutoff or datetime.now().astimezone().date()
        scope = self.pipeline.scope(pool)
        evidence = self.pipeline.evidence(cutoff)
        peer = evidence.get('peer_universe') or {}
        stages = []
        for stage, label, cost, count in (
            ('governance', '交易所监管快照', '全市场4类监管列表，约240页；有效同日缓存可复用', 0),
            ('industry_scale', '同业市值与规模排名', '完整同业范围；不会缩小为当前池样本', peer.get('company_count') or 0),
        ):
            ready = (evidence.get(stage) or {}).get('current') is True
            stages.append({'stage': stage, 'label': label, 'cost_hint': cost, 'symbol_count': count,
                           'status': 'cached' if ready else 'refresh_required'})
        from radar.stocks.screening_web import read_snapshot
        snapshot = read_snapshot(self.pipeline.state, pool)
        value = {'pool_id': pool, 'as_of': cutoff.isoformat(), 'company_count': len(scope['companies']),
                 'scope_sha256': _digest(scope), 'shared_stages': stages, 'evidence': evidence,
                 'snapshot_sha256': snapshot_digest(snapshot) if snapshot else None}
        value['preview_sha256'] = _digest(value)
        return value

    def plan(self, pool: str) -> dict[str, Any]:
        plan = self.pipeline.plan(pool)
        from radar.stocks.screening_web import read_snapshot
        snapshot = read_snapshot(self.pipeline.state, pool)
        if snapshot is None:
            raise ValueError('请先更新股票池')
        plan['pending_total'] = sum(row['status'] == 'pending' for row in project(snapshot['report'], pool))
        for item in plan['items']:
            item['automatic'] = (item.get('executable') or {}).get('mode') in {'firm', 'shared_stage'}
        plan['automatic_count'] = sum(item['automatic'] for item in plan['items'])
        plan['manual_count'] = len(plan['items']) - plan['automatic_count'] + len(plan.get('blocked_items') or [])
        plan['execution_preview'] = self.preview(pool, date.fromisoformat(plan['as_of']))
        plan['plan_sha256'] = _plan_sha(plan)
        return plan

    def execute(self, task: dict[str, Any], confirmation: dict[str, Any], plan: Any, progress: Any) -> dict[str, Any]:
        pool, cutoff = task['pool_id'], date.fromisoformat(task['as_of'])
        expected = confirmation['preview_sha256']
        if self.preview(pool, cutoff)['preview_sha256'] != expected:
            raise StockRevisionConflict('执行范围、共享缓存或快照已变化，请重新确认')
        payload = self.pipeline.build(pool, cutoff, shared=True, progress=progress)
        errors = list(payload.get('stage_errors') or [])
        for stage in ('governance', 'industry_scale'):
            ready = (payload.get('shared') or {}).get(stage) or {}
            if ready.get('current') is not True:
                errors.append({'stage': stage, 'error': ready.get('reason') or '共享证据未完成'})
        if any(item.get('evaluation_valid') is not True for item in payload['report']['items']):
            errors.append({'stage': 'review', 'error': '公司计算期间来源无效或变化'})
        overrides = []
        completion_results: list[dict[str, Any]] = []

        def completion_progress(attempted: int, total: int, results: Any = None, phase: str = "completion") -> None:
            if results is not None:
                completion_results[:] = results
            progress(attempted, total, results, phase)

        if not errors and plan is not None:
            remaining = build_completion_plan_v2(details(payload['report'], pool), self.pipeline.db, cutoff,
                                                 pools={pool}, shared=self.pipeline._shared_status(payload))
            # 不扩大用户确认的公司范围；新出现的风格缺项仍限制于原确认公司。
            symbols = {item['symbol'] for item in plan['items']}
            remaining['items'] = [item for item in remaining['items'] if item['symbol'] in symbols]
            remaining['plan_sha256'] = _plan_sha(remaining)
            if plan_firm_items(remaining):
                payload['report'], overrides = self.pipeline._apply(pool, payload['report'], remaining, cutoff,
                                                                  task['run_id'], completion_progress)
                for result in completion_results:
                    if result.get('status') != 'complete':
                        errors.append({'stage': 'completion', 'error': result.get('reason') or result.get('error') or '补全来源未完成'})
        if _digest(self.pipeline.scope(pool)) != confirmation['scope_sha256']:
            raise StockRevisionConflict('执行中候选范围发生变化，保留旧快照')
        from radar.stocks.screening_web import read_snapshot
        snapshot = read_snapshot(self.pipeline.state, pool)
        if (snapshot_digest(snapshot) if snapshot else None) != confirmation['snapshot_sha256']:
            raise StockRevisionConflict('执行中结果快照发生变化，保留旧快照')
        if not errors:
            payload['snapshot_sha256'] = snapshot_digest(payload)
            self.pipeline.commit(pool, payload, run_id=task['run_id'])
        return {'stage_errors': errors, 'overrides': overrides, 'results': completion_results, 'evidence': payload.get('evidence'),
                'status': 'partial' if errors else 'completed'}
