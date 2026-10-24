"""股票研究管理接口；只读访问不创建业务存储或任务。"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from radar.stocks.admission import RULE_VERSION
from radar.stocks.evaluation_store import StockEvaluationStore
from radar.stocks.financial_source import load_financial_source
from radar.stocks.financial_task import financial_results
from radar.stocks.membership_drafts import StockMembershipDraftStore
from radar.stocks.membership_store import StockMembershipStore
from radar.stocks.pools import StockPoolConfigError, StockPoolRepository
from radar.stocks.research_store import (
    StockResearchError,
    StockResearchStore,
    StockRevisionConflict,
    _symbol,
)
from radar.stocks.structured_screen import RULE_VERSION as FINANCIAL_RULE_VERSION
from radar.stocks.style import STYLE_RULE_VERSION
from radar.stocks.update_runner import StockUpdateRunner
from radar.stocks.update_runs import StockUpdateStore
from radar.stocks.update_service import StockUpdateService, load_market_window
from radar.stocks.web_queries import (
    empty_page,
    has_table,
    public_task,
    read_connection,
    scope_snapshot,
)
from stock_robot.stock_admission import evaluate_store

logger = logging.getLogger(__name__)
Revision = Annotated[StrictInt, Field(ge=0, lt=2**63 - 1)]
Text = Annotated[str, Field(min_length=1, max_length=2000)]


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class ReviewInput(Input):
    evidence_id: Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]
    expected_revision: Revision
    decision: Literal['confirmed', 'rejected', 'needs_evidence']
    reviewer: Text
    reason: Text


class DraftInput(Input):
    expected_revision: Revision
    request_id: Annotated[str, Field(min_length=1, max_length=200)]
    actor: Text
    reason: Text
    pool_id: Text


class DiscoverInput(Input):
    pool_id: Text
    as_of: date | None = None


class UpdateInput(Input):
    as_of: date | None = None


class FinancialInput(Input):
    resume_run_id: Text | None = None


class ChangeInput(Input):
    pool_id: Text
    symbol: Annotated[str, Field(pattern=r'^\d{6}$')]
    action: Literal['add', 'restore', 'suspend', 'exit']
    reason: Text


class MembershipInput(Input):
    evaluation_id: str | None = None
    changes: Annotated[list[ChangeInput], Field(min_length=1)]
    expected_pool_revisions: dict[str, Revision]
    request_id: Annotated[str, Field(min_length=1, max_length=200)]
    actor: Text


class StockRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            try:
                return await original(request)
            except StockRevisionConflict as exc:
                raise HTTPException(409, str(exc)) from exc
            except StockPoolConfigError as exc:
                logger.exception('股票池配置读取失败')
                raise HTTPException(500, str(exc)) from exc
            except (sqlite3.Error, OSError, RuntimeError) as exc:
                logger.exception('股票管理存储或服务不可用')
                raise HTTPException(503, '股票资料服务暂不可用，请重试') from exc
            except (StockResearchError, ValueError, TypeError) as exc:
                raise HTTPException(422, str(exc)) from exc
        return handle


def create_stock_router(*, state_dir: Path | None = None, pool_dir: Path | None = None,
                        service_factory: Callable[[StockUpdateRunner], Any] = StockUpdateService) -> APIRouter:
    """延迟写初始化，现有待处理队列在应用生命周期恢复。"""
    state = Path(state_dir) if state_dir is not None else Path.cwd() / '.stock_robot'
    db = state / 'radar.db'
    pools = StockPoolRepository(pool_dir or Path(__file__).parents[2] / 'config' / 'radar_stock_pools')
    service: Any = None
    lock = threading.Lock()

    def pool(pool_id: str):
        item = pools.get(pool_id)
        if item is None:
            raise HTTPException(404, '股票池不存在或未启用')
        return item

    def research(*, writable: bool = False) -> StockResearchStore:
        if not writable:
            with read_connection(db) as connection:
                if not has_table(connection, 'stocks_companies'):
                    raise HTTPException(404, '公司不存在')
        return StockResearchStore(db, read_only=not writable)

    def company(store: StockResearchStore, symbol: str):
        row = store.get_company(_symbol(symbol))
        if row is None:
            raise HTTPException(404, '公司不存在')
        return row

    def stores():
        store = StockResearchStore(db)
        evaluations = StockEvaluationStore(store)
        memberships = StockMembershipStore(evaluations, pools)
        return store, evaluations, memberships

    def updates() -> StockUpdateStore:
        return StockUpdateStore(stores()[2])

    def wake(tasks: StockUpdateStore) -> None:
        nonlocal service
        with lock:
            if service is None:
                runner = StockUpdateRunner(tasks, state / 'stock-updates' / 'cache', state / 'stock-updates' / 'artifacts',
                                           window_loader=lambda day: load_market_window(state, day))
                service = service_factory(runner)
            service.wake()

    @asynccontextmanager
    async def lifespan(_app):
        try:
            with read_connection(db) as connection:
                pending = (has_table(connection, 'stocks_update_runs') and connection is not None
                           and connection.execute("SELECT 1 FROM stocks_update_runs WHERE status IN ('queued','running') LIMIT 1").fetchone() is not None)
            if pending:
                wake(updates())
        except (sqlite3.Error, OSError, RuntimeError, ValueError):
            logger.exception('股票手动任务恢复失败，保留接口错误与重试入口')
        try:
            yield
        finally:
            if service is not None:
                service.stop()

    router = APIRouter(prefix='/stocks', route_class=StockRoute, lifespan=lifespan)

    def candidates_data(pool_id: str, search: str = '', page: int = 1):
        pool(pool_id)
        with read_connection(db) as connection:
            scope = scope_snapshot(connection, state, pool_id)
            scoped = scope.pop('companies')
            items = []
            for entry in scoped:
                if search.casefold() not in (entry['symbol'] + ' ' + entry['name']).casefold():
                    continue
                row = (connection.execute('SELECT * FROM stocks_companies WHERE symbol=?', (entry['symbol'],)).fetchone()
                       if has_table(connection, 'stocks_companies') and connection is not None else None)
                if row is not None:
                    items.append({**dict(row), 'admission_status': 'pending_verification',
                                  'source_indexes': entry['source_indexes'], 'source_records': entry.get('source_records', [])})
            offset = (page - 1) * 15
            return {**scope, 'pool_id': pool_id, 'items': items[offset:offset + 15], 'total': len(items),
                    'page': page, 'page_size': 15, 'formal_screening_enabled': False}

    @router.get('/candidates')
    def candidates(pool_id: str = 'dividend_value', search: str = '', page: int = Query(1, ge=1)):
        return candidates_data(pool_id, search, page)

    @router.get('/companies/{symbol}')
    def detail(symbol: str):
        store = research()
        row = company(store, symbol)
        cutoff = datetime.now().astimezone().date()
        window, trace = load_market_window(state, cutoff)
        financial = load_financial_source(db, symbol, cutoff)
        report = evaluate_store(store, cutoff, symbol=symbol, market_window=window, include_styles=True,
                                financial_sources={symbol: financial})
        report['market_window_source'] = trace
        evidence, reviews = store.list_evidence(symbol), store.review_history(symbol)
        if company(store, symbol)['revision'] != row['revision']:
            raise StockRevisionConflict('读取详情期间公司资料变化，请重新读取')
        if load_financial_source(db, symbol, cutoff) != financial:
            raise StockRevisionConflict('读取详情期间财务来源变化，请重新读取')
        return {'company': row, 'evidence': evidence, 'reviews': reviews, 'evaluation': report, 'publication_enabled': False}

    @router.post('/companies/{symbol}/reviews')
    def save_review(symbol: str, body: ReviewInput):
        company(research(), symbol)
        store = research(writable=True)
        if not any(item['evidence_id'] == body.evidence_id for item in store.list_evidence(symbol)):
            raise HTTPException(404, '证据不存在或不属于该公司')
        return store.save_review(symbol, **body.model_dump())

    def replay_draft(symbol: str, body: DraftInput):
        # 请求标识绑定用户输入，不随重新取日历的当前时间变化。
        with read_connection(db) as connection:
            if not has_table(connection, 'stocks_member_drafts'):
                return None
            assert connection is not None
            old = connection.execute('SELECT * FROM stocks_member_drafts WHERE request_id=?',
                                     (body.request_id,)).fetchone()
        if old is None:
            return None
        changes = json.loads(old['changes'])
        expected = {'pool_id': body.pool_id, 'symbol': symbol, 'action': 'add',
                    'reason': body.reason, 'source_revision': body.expected_revision}
        if (old['actor'] != body.actor or len(changes) != 1
                or any(changes[0].get(key) != value for key, value in expected.items())):
            raise StockRevisionConflict('请求标识已用于其他成员草案')
        _store, evaluations, _ = stores()
        return StockMembershipDraftStore(evaluations, pools).get(old['draft_id'])

    @router.post('/companies/{symbol}/draft')
    def save_draft(symbol: str, body: DraftInput):
        symbol = _symbol(symbol)
        replay = replay_draft(symbol, body)
        if replay is not None:
            return replay
        pool(body.pool_id)
        row = company(research(), symbol)
        if row['revision'] != body.expected_revision:
            raise StockRevisionConflict('公司版本已变化，请重新读取')
        store, evaluations, _ = stores()
        inputs = evaluations.capture_inputs(symbol=symbol)
        if inputs['companies'][0]['company']['revision'] != body.expected_revision:
            raise StockRevisionConflict('公司版本已变化，请重新读取')
        cutoff = datetime.now().astimezone().date()
        window, trace = load_market_window(state, cutoff)
        financial = load_financial_source(db, symbol, cutoff)
        inputs['companies'][0].update(financial_source=financial, financial_as_of=cutoff.isoformat())
        report = evaluate_store(store, cutoff, symbol=symbol, market_window=window, include_styles=True,
                                financial_sources={symbol: financial})
        report['market_window_source'] = trace
        saved = evaluations.save_evaluation(report, inputs, market_window=window)
        drafts = StockMembershipDraftStore(evaluations, pools)
        try:
            return drafts.create(evaluation_id=saved['evaluation_id'], changes=[{'pool_id': body.pool_id, 'symbol': symbol,
                                 'action': 'add', 'reason': body.reason}], request_id=body.request_id, actor=body.actor)
        except StockRevisionConflict:
            # 两个相同请求并发到达时，回放另一请求已经提交的结果。
            replay = replay_draft(symbol, body)
            if replay is not None:
                return replay
            raise

    @router.get('/drafts')
    def drafts(page: int = Query(1, ge=1)):
        result = empty_page(page)
        with read_connection(db) as connection:
            if has_table(connection, 'stocks_member_drafts'):
                assert connection is not None
                result['total'] = connection.execute('SELECT COUNT(*) FROM stocks_member_drafts').fetchone()[0]
                rows = connection.execute('SELECT * FROM stocks_member_drafts ORDER BY created_at DESC,draft_id LIMIT 15 OFFSET ?', ((page - 1) * 15,)).fetchall()
                for row in rows:
                    item = dict(row)
                    item.pop('payload', None)
                    item['changes'] = json.loads(item['changes'])
                    item['publication_enabled'] = False
                    result['items'].append(item)
        return {**result, 'publication_enabled': False, 'members_published': 0}

    @router.get('/memberships/events')
    def events(pool_id: str | None = None, page: int = Query(1, ge=1)):
        if pool_id is not None:
            pool(pool_id)
        result = empty_page(page)
        with read_connection(db) as connection:
            if has_table(connection, 'stocks_membership_events'):
                assert connection is not None
                clause, args = (' WHERE pool_id=?', [pool_id]) if pool_id else ('', [])
                result['total'] = connection.execute('SELECT COUNT(*) FROM stocks_membership_events' + clause, args).fetchone()[0]
                rows = connection.execute('SELECT * FROM stocks_membership_events' + clause + ' ORDER BY event_id DESC LIMIT 15 OFFSET ?', [*args, (page - 1) * 15]).fetchall()
                result['items'] = [dict(row) for row in rows]
        return result

    @router.post('/membership-changes')
    def membership_changes(body: MembershipInput):
        for change in body.changes:
            pool(change.pool_id)
            company(research(), change.symbol)
        return stores()[2].apply(**body.model_dump())

    @router.post('/discover', status_code=202)
    def discover(body: DiscoverInput):
        pool(body.pool_id)
        tasks = updates()
        run = tasks.enqueue(kind='discover_candidates', pool_ids=[item.id for item in pools.load_all() if item.enabled],
                            target_date=body.as_of or datetime.now().astimezone().date(), rule_version=RULE_VERSION + '+' + STYLE_RULE_VERSION + '+index_scope_v1')
        wake(tasks)
        return public_task(run)

    @router.post('/{pool_id}/update', status_code=202)
    def update(pool_id: str, body: UpdateInput | None = None):
        pool(pool_id)
        tasks = updates()
        run = tasks.enqueue(kind='refresh_members', pool_ids=[pool_id],
                            target_date=(body.as_of if body else None) or datetime.now().astimezone().date(), rule_version=RULE_VERSION + '+' + STYLE_RULE_VERSION)
        wake(tasks)
        return public_task(run)

    @router.get('/{pool_id}/financial-review/scope')
    def financial_scope(pool_id: str):
        pool(pool_id)
        with read_connection(db) as connection:
            result = scope_snapshot(connection, state, pool_id)
        return {**{key: value for key, value in result.items() if key != 'companies'},
                'pool_id': pool_id, 'total': len(result['companies']), 'as_of': result['scope_as_of']}

    @router.post('/{pool_id}/financial-review', status_code=202)
    def start_financial(pool_id: str, body: FinancialInput | None = None):
        pool(pool_id)
        if body is not None and body.resume_run_id is not None:
            # 先只读确认身份，拒绝错误请求时不创建存储。
            old = next((StockUpdateStore._decode(row) for row in task_rows() if row['run_id'] == body.resume_run_id), None)
            if old is None or old['kind'] != 'financial_review' or old['pool_ids'] != [pool_id]:
                raise HTTPException(404, '当前池的财务任务不存在')
            tasks = updates()
            run = tasks.resume_financial(body.resume_run_id, pool_id)
        else:
            with read_connection(db) as connection:
                scope = scope_snapshot(connection, state, pool_id)
            if not scope['scope_complete'] or scope['scope_status'] != 'complete' or not scope['companies']:
                raise StockResearchError('请先准备当前池完整指数候选范围')
            snapshot = {**scope, 'kind': 'financial_review', 'pool_ids': [pool_id]}
            tasks = updates()
            run = tasks.enqueue(kind='financial_review', pool_ids=[pool_id],
                                target_date=datetime.now().astimezone().date(), rule_version=FINANCIAL_RULE_VERSION,
                                snapshot=snapshot)
        wake(tasks)
        return public_task(run)

    def task_rows():
        with read_connection(db) as connection:
            if not has_table(connection, 'stocks_update_runs'):
                return []
            assert connection is not None
            return connection.execute('SELECT * FROM stocks_update_runs ORDER BY created_at DESC,run_id DESC').fetchall()

    @router.get('/updates/latest')
    def latest(pool_id: str | None = None, kind: Literal['financial_review', 'refresh_members', 'discover_candidates'] | None = None):
        if pool_id is not None:
            pool(pool_id)
        for row in task_rows():
            value = public_task(row)
            if (pool_id is None or pool_id in value['pool_ids']) and (kind is None or value['kind'] == kind):
                return value
        return None

    @router.get('/updates')
    def list_updates(page: int = Query(1, ge=1)):
        rows = task_rows()
        return {**empty_page(page), 'items': [public_task(row) for row in rows[(page - 1) * 15:page * 15]],
                'total': len(rows), 'automatic_enabled': False}

    @router.get('/updates/{run_id}')
    def get_update(run_id: str):
        for row in task_rows():
            if row['run_id'] == run_id:
                return public_task(row)
        raise HTTPException(404, '股票更新任务不存在')

    @router.get('/updates/{run_id}/financial-results')
    def financial_page(run_id: str, page: int = Query(1, ge=1)):
        for row in task_rows():
            if row['run_id'] == run_id:
                return financial_results(StockUpdateStore._decode(row), state/'stock-updates'/'artifacts', page)
        raise HTTPException(404, '股票更新任务不存在')

    @router.get('/{pool_id}/members')
    def members(pool_id: str):
        pool(pool_id)
        scoped = candidates_data(pool_id)
        items, revision = [], 0
        with read_connection(db) as connection:
            if has_table(connection, 'stocks_memberships'):
                assert connection is not None
                rows = connection.execute("SELECT m.*,c.name FROM stocks_memberships m JOIN stocks_companies c ON c.symbol=m.symbol WHERE pool_id=? AND status IN ('active','suspended') ORDER BY symbol", (pool_id,)).fetchall()
                items = [dict(row) for row in rows]
                current = connection.execute('SELECT revision FROM stocks_pool_revisions WHERE pool_id=?', (pool_id,)).fetchone()
                revision = current['revision'] if current else 0
        return {'pool_id': pool_id, 'revision': revision, 'items': items,
                'effective': [item for item in items if item['status'] == 'active'],
                'suspended': [item for item in items if item['status'] == 'suspended'],
                'industries': [], 'status': 'available' if items else 'empty',
                'unavailable_reason': None if items else '正式筛选规则尚未配置，当前没有正式成员',
                'candidate_count': scoped['total'], 'scope_status': scoped['scope_status'],
                'scope_complete': scoped['scope_complete'], 'source_snapshots': scoped['source_snapshots'],
                'formal_screening_enabled': False, 'publication_enabled': False}

    return router
