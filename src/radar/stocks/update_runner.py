"""手动股票资料任务执行器，复用原文采集器并保留不可变产物。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from time import sleep
from typing import Any

from radar.stocks.capital_history import collect_capital_history_result
from radar.stocks.collection_results import collection_result
from radar.stocks.dividend_collect import collect_dividend, verify_collection
from radar.stocks.financial_task import (
    read_financial_artifact,
    verify_financial_payload,
)
from radar.stocks.index_discovery import collect_index_scope_result
from radar.stocks.market_history import collect_stock_history_result
from radar.stocks.research_store import StockResearchError, StockRevisionConflict, _sha
from radar.stocks.structured_collect import collect_structured_company_result
from radar.stocks.turnover_source import collect_turnover_payload
from radar.stocks.update_runs import StockUpdateStore

logger = logging.getLogger(__name__)
_MAX_ARTIFACT = 64 * 1024 * 1024


def validate_storage(root: Path, database: Path) -> Path:
    """产物和缓存不得包含数据库或经过符号链接。"""
    root = Path(root).absolute()
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise StockResearchError('股票更新存储路径不能经过符号链接')
    resolved = root.resolve()
    if any(Path(str(database.resolve()) + suffix).is_relative_to(resolved) for suffix in ('', '-wal', '-shm', '-journal')):
        raise StockResearchError('股票更新存储目录不能包含SQLite数据库或日志')
    return root


def _artifact(root: Path, value: dict[str, Any]) -> dict[str, Any]:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':')).encode('utf-8')
    if len(raw) > _MAX_ARTIFACT:
        raise StockResearchError('股票更新单份结构化产物超过64MiB预算')
    sha = hashlib.sha256(raw).hexdigest()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f'{sha}.json'
    if path.is_symlink():
        raise StockResearchError('股票更新产物不能是符号链接')
    if path.exists():
        with path.open('rb') as stream:
            if stream.read(len(raw) + 1) != raw:
                raise StockResearchError('同摘要产物内容已损坏，未覆盖')
    else:
        descriptor, name = tempfile.mkstemp(prefix='.stock-update-', dir=root)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
    return {'path': str(path.resolve()), 'sha256': sha, 'bytes': len(raw)}


class StockUpdateRunner:
    """资料发现完成不授予资格；所有数据库提交都重验执行租约。"""

    def __init__(self, store: StockUpdateStore, cache_dir: Path, artifact_dir: Path, *,
                 roster_collector: Callable[..., Any] = collect_index_scope_result,
                 history_collector: Callable[..., Any] = collect_stock_history_result,
                 capital_collector: Callable[..., Any] = collect_capital_history_result,
                 financial_collector: Callable[..., Any] = collect_structured_company_result,
                 dividend_collector: Callable[..., Any] = collect_dividend,
                 turnover_collector: Callable[..., Any] = collect_turnover_payload,
                 reference_factory: Callable[..., Any] | None = None,
                 market_window: list[date] | None = None,
                 max_workers: int = 4,
                 window_loader: Callable[[date], tuple[list[date] | None, Any]] | None = None):
        if type(max_workers) is not int or not 1 <= max_workers <= 8:
            raise StockResearchError('采集并发数须为1至8')
        self.max_workers = max_workers
        self.store = store
        self.cache_dir = validate_storage(cache_dir, store.research.db_path)
        self.artifact_dir = validate_storage(artifact_dir, store.research.db_path)
        if (self.cache_dir.resolve().is_relative_to(self.artifact_dir.resolve())
                or self.artifact_dir.resolve().is_relative_to(self.cache_dir.resolve())):
            raise StockResearchError('采集缓存与完成产物须使用互不包含的目录')
        self.roster_collector = roster_collector
        self.history_collector = history_collector
        self.capital_collector = capital_collector
        self.financial_collector = financial_collector
        self.dividend_collector = dividend_collector
        self.turnover_collector = turnover_collector
        self.reference_factory = reference_factory
        self.market_window = market_window
        self.window_loader = window_loader

    def _verify_artifact(self, item: dict[str, Any]) -> None:
        reference = item.get('artifact')
        if not isinstance(reference, dict):
            raise StockResearchError('已完成阶段缺少可复核产物，不能跳过采集')
        sha, size = _sha(reference.get('sha256')), reference.get('bytes')
        if type(size) is not int or not 0 < size <= _MAX_ARTIFACT or not isinstance(reference.get('path'), str):
            raise StockResearchError('已完成产物长度或路径无效')
        root = validate_storage(self.artifact_dir, self.store.research.db_path)
        path = root / f'{sha}.json'
        if path.is_symlink() or Path(reference['path']).resolve() != path.resolve() or path.stat().st_size != size:
            raise StockResearchError('已完成产物缺失、路径异常或长度不一致')
        digest, actual = hashlib.sha256(), 0
        with path.open('rb') as stream:
            while chunk := stream.read(65536):
                actual += len(chunk)
                if actual > size:
                    raise StockResearchError('已完成产物实际长度超出记录')
                digest.update(chunk)
        if actual != size or digest.hexdigest() != sha:
            raise StockResearchError('已完成产物摘要不一致，不能复用')

    def _discovery(self, task: dict[str, Any], worker: str, token: str) -> dict[str, Any]:
        cutoff = date.fromisoformat(task['target_date'])
        collected = collection_result(self.roster_collector(self.cache_dir/'index-scope', as_of=cutoff), 'index_scope')
        result = collected.value
        if not isinstance(result, dict) or result.get('as_of') != cutoff.isoformat() or not isinstance(result.get('companies'), list):
            raise StockResearchError('指数初筛结果缺少匹配研究日期和公司清单')
        result['collection_record'] = collected.record.model_dump(mode='json')
        reference = _artifact(self.artifact_dir, result)
        imported = (self.store.research.import_companies(result['companies'], source_sha256=reference['sha256'],
            source_label=reference['path'], data_as_of=cutoff,
            transaction_guard=lambda connection: self.store.assert_owner(connection, task['run_id'], worker, token)) if result['companies'] else None)
        complete = bool(result['companies']) and result.get('status') == 'complete' and result.get('scope_complete') is True
        item = {'scope': 'index_scope_v1', 'status': 'completed' if complete else ('partial' if result['companies'] else 'failed'),
                'summary': f"指数范围去重{len(result['companies'])}家公司；来源{'完整' if complete else '尚不完整'}，尚未筛选通过或发布成员",
                'artifact': {**reference, 'companies_found': len(result['companies']), 'scope_complete': complete,
                             'import': imported, 'collection_record': result['collection_record'],
                             'formal_screening_enabled': False, 'members_published': 0}}
        self.store.record_item(task['run_id'], worker, item, lease_token=token)
        return item

    def _financial(self, task: dict[str, Any], worker: str, token: str) -> str:
        latest = {item['scope']: item for item in task['results']}
        pending = False
        for company in task['input_snapshot']['companies']:
            symbol, scope = company['symbol'], 'financial:' + company['symbol']
            previous = latest.get(scope)
            if previous is not None and previous['status'] == 'completed':
                self._verify_artifact(previous)
                payload = read_financial_artifact(previous, self.artifact_dir)
                screened = verify_financial_payload(payload, task, symbol)
                if screened['financial_numeric_status'] != previous['artifact'].get('financial_numeric_status'):
                    raise StockResearchError('已完成财务产物数值结论与记录不匹配')
                continue
            stopped = False
            try:
                collected = collection_result(self.financial_collector(symbol, self.cache_dir/'structured-financial',
                                                       as_of=date.fromisoformat(task['target_date'])), 'financial')
                collection = collected.value
                stopped = isinstance(collection, dict) and collection.get('source_stopped') is True
                payload = {'symbol': symbol, 'as_of': task['target_date'], 'collection': collection,
                           'collection_record': collected.record.model_dump(mode='json')}
                screened = verify_financial_payload(payload, task, symbol)
                outcome = screened['financial_numeric_status']
            except StockRevisionConflict:
                raise
            except Exception as exc:  # noqa: BLE001 财务来源独立隔离，错误不能当作公司未盈利
                logger.warning('股票财务采集失败 %s: %s', symbol, exc)
                payload = {'symbol': symbol, 'as_of': task['target_date'], 'collection': None}
                outcome = 'pending_verification'
            incomplete = outcome == 'pending_verification' or payload.get('collection_record', {}).get('result_state', 'failed') != 'complete'
            pending = pending or incomplete
            item = {'scope': scope, 'status': 'partial' if incomplete else 'completed',
                    'summary': '财务资料待补，不能当作未盈利' if incomplete else '盈利数值复核已完成，尚未通过全部入池条件',
                    'artifact': {**_artifact(self.artifact_dir, payload), 'symbol': symbol,
                                 'financial_numeric_status': outcome, 'source_stopped': stopped,
                                 'collection_record': payload.get('collection_record')}}
            self.store.record_item(task['run_id'], worker, item, lease_token=token)
            if stopped:
                return 'partial'
        return 'partial' if pending else 'completed'

    def _dividend(self, task: dict[str, Any], worker: str, token: str) -> str:
        latest = {item['scope']: item for item in task['results']}
        pending = False
        blocked_sources: set[str] = set()
        for company in task['input_snapshot']['companies']:
            symbol, scope = company['symbol'], 'dividend:' + company['symbol']
            previous = latest.get(scope)
            if previous is not None and previous['status'] == 'completed':
                self._verify_artifact(previous)
                payload = read_financial_artifact(previous, self.artifact_dir)
                verify_collection(payload['collection'], symbol, date.fromisoformat(task['target_date']))
                continue
            collection = None
            try:
                collection = self.dividend_collector(symbol, date.fromisoformat(task['target_date']), self.cache_dir/'dividend',
                                                    blocked_sources=blocked_sources)
                verify_collection(collection, symbol, date.fromisoformat(task['target_date']))
                status = 'completed'
            except StockRevisionConflict:
                raise
            except Exception as exc:  # noqa: BLE001 分红来源逐公司隔离，保留错误并阻断旧结果。
                logger.warning('分红资料待补 %s: %s', symbol, exc)
                status = 'partial'
            pending |= status != 'completed'
            payload = {'symbol': symbol, 'as_of': task['target_date'], 'collection': collection}
            self.store.record_item(task['run_id'], worker, {'scope': scope, 'status': status,
                'summary': '分红来源已保存' if status == 'completed' else '分红来源待补',
                'artifact': _artifact(self.artifact_dir, payload)}, lease_token=token)
            collection = None
        return 'partial' if pending else 'completed'

    def _turnover(self, task: dict[str, Any], worker: str, token: str) -> str:
        from radar.stocks.turnover_source import (
            SUPPORTED_VERSIONS,
            verify_turnover_payload,
        )

        if task['rule_version'] not in SUPPORTED_VERSIONS:
            raise StockResearchError('成交额任务版本不兼容')
        cutoff = date.fromisoformat(task['target_date'])
        days, trace = self.window_loader(cutoff) if self.window_loader else (self.market_window, None)
        calendar = trace.get('calendar') if isinstance(trace, dict) else None
        if not isinstance(calendar, dict):
            from radar.stocks.update_service import load_market_window
            cached_days, cached_trace = load_market_window(self.store.research.db_path.parent, cutoff)
            if days is not None and days != cached_days:
                raise StockResearchError('手动窗口与本地交易日历不一致')
            days, trace = cached_days, cached_trace
            calendar = trace.get('calendar') if isinstance(trace, dict) else None
        if days is None or not isinstance(calendar, dict):
            raise StockResearchError('成交额任务需要已保存的60日市场日历')
        latest = {item['scope']: item for item in task['results']}
        pending = False
        cache = self.cache_dir
        remaining: list[str] = []
        for company in task['input_snapshot']['companies']:
            symbol, scope = company['symbol'], 'turnover:' + company['symbol']
            previous = latest.get(scope)
            if previous is not None and previous['status'] == 'completed':
                self._verify_artifact(previous)
                payload = read_financial_artifact(previous, self.artifact_dir)
                verify_turnover_payload(payload, symbol, task['target_date'])
                if payload['window'] != [day.isoformat() for day in days] or payload['calendar'] != calendar:
                    raise StockResearchError('原成交额任务市场日历已变化，不能混合续跑')
                continue
            remaining.append(symbol)

        def collect_one(symbol: str) -> tuple[str, dict[str, Any], bool]:
            payload: dict[str, Any] = {'symbol': symbol, 'as_of': task['target_date']}
            stopped = False
            try:
                for attempt in range(2):
                    try:
                        payload = self.turnover_collector(symbol, cutoff, cache, days, calendar)
                        break
                    except StockRevisionConflict:
                        raise
                    except Exception as exc:
                        if attempt or '429' in str(exc):
                            raise
                        logger.warning('成交额来源重试 %s: %s', symbol, exc)
                        sleep(1)
                verify_turnover_payload(payload, symbol, task['target_date'])
                if payload['window'] != [day.isoformat() for day in days] or payload['calendar'] != calendar:
                    raise ValueError('成交额来源市场日历与任务不一致')
                return 'completed', payload, False
            except StockRevisionConflict:
                raise
            except Exception as exc:  # noqa: BLE001 第三方来源逐公司隔离，失败不得当作零成交额。
                logger.warning('成交额资料待补 %s: %s', symbol, exc)
                stopped = '429' in str(exc)
                return 'partial', payload, stopped

        workers = max(1, min(self.max_workers, len(remaining) or 1))
        for start in range(0, len(remaining), workers):
            batch = remaining[start:start + workers]
            if workers == 1:
                batch_results = [collect_one(symbol) for symbol in batch]
            else:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [pool.submit(collect_one, symbol) for symbol in batch]
                    batch_results = [future.result() for future in futures]
            for symbol, (status, payload, stopped) in zip(batch, batch_results):
                pending |= status != 'completed'
                self.store.record_item(task['run_id'], worker, {'scope': 'turnover:' + symbol, 'status': status,
                    'summary': '成交额来源已保存' if status == 'completed' else '成交额资料待补',
                    'artifact': _artifact(self.artifact_dir, payload)}, lease_token=token)
                if stopped:
                    return 'partial'
        return 'partial' if pending else 'completed'

    def _refresh(self, task: dict[str, Any], worker: str, token: str) -> list[dict[str, Any]]:
        cutoff = date.fromisoformat(task['target_date'])
        companies = [{'symbol': symbol} for symbol in sorted({row['symbol'] for row in task['input_snapshot']['members']})]
        done = {item['scope'] for item in task['results'] if item['status'] == 'completed'}
        for item in task['results']:
            if item['status'] == 'completed':
                self._verify_artifact(item)
        results = []
        for scope, collector in (('market_history', self.history_collector), ('capital_history', self.capital_collector)):
            if scope in done:
                continue
            try:
                if scope == 'market_history':
                    days, trace = (self.window_loader(cutoff) if self.window_loader is not None else (self.market_window, None))
                    if days is None:
                        raise StockResearchError('更新成员行情需要明确的60个已收盘市场交易日')
                    value = collector(companies, days, self.cache_dir/scope, as_of=cutoff)
                else:
                    value = collector(companies, self.cache_dir/scope, as_of=cutoff)
                collected = collection_result(value, scope)
                value = collected.value
                if not isinstance(value, dict) or value.get('as_of') != cutoff.isoformat():
                    raise StockResearchError('成员更新结果日期或结构无效')
                if scope == 'market_history':
                    value['market_window_source'] = trace
                complete = collected.record.result_state == 'complete'
                usable = collected.record.result_state in {'complete', 'partial'}
                value['collection_record'] = collected.record.model_dump(mode='json')
                item = {'scope': scope, 'status': 'completed' if complete else ('partial' if usable else 'failed'),
                        'summary': f"{len(companies)}家公司资料采集{'完成' if complete else '尚不完整'}，未审核资格",
                        'artifact': {**_artifact(self.artifact_dir, value), 'collection_record': value['collection_record']}}
            except StockRevisionConflict:
                raise
            except Exception as exc:  # noqa: BLE001 各采集来源独立隔离，保留错误继续其他来源
                logger.warning('股票成员资料更新失败 %s: %s', scope, exc)
                item = {'scope': scope, 'status': 'failed', 'summary': str(exc)}
            self.store.record_item(task['run_id'], worker, item, lease_token=token)
            results.append(item)
        # 现有采集覆盖行情和股本，财务、分红及风险复核不能因此声称已完成。
        pending = {'scope': 'financial_and_risk_review', 'status': 'not_configured',
                   'summary': '财务、分红及风险复核尚未接入后台任务，原证据和成员状态保留'}
        self.store.record_item(task['run_id'], worker, pending, lease_token=token)
        results.append(pending)
        return results

    def _reference(self, task: dict[str, Any], worker: str, token: str) -> str:
        from data.collection_state import make_record
        from radar.stocks.reference_collect import ReferenceCollector
        from radar.stocks.reference_task import (
            REFERENCE_VERSION,
            validate_reference_snapshot,
            verify_reference_manifest,
        )

        if task['rule_version'] != REFERENCE_VERSION:
            raise StockResearchError('基础资料任务规则版本不兼容')
        frozen = validate_reference_snapshot(task['input_snapshot'], task['pool_ids'], task['target_date'])
        state = self.store.research.db_path.parent.absolute()
        root = state / 'stock-updates/reference-snapshots' / uuid.uuid4().hex
        factory = self.reference_factory or ReferenceCollector
        collector = factory(state, self.cache_dir, root, frozen, date.fromisoformat(task['target_date']))
        previous = {item['scope']: item for item in task['results']}
        channels = [('profile', None), *(('market_value', row['symbol']) for row in frozen['companies'])]
        # 行业直接复用本地表，不纳入更新；日历不足只影响市值。
        try:
            if self.window_loader is not None:
                _, trace = self.window_loader(date.fromisoformat(task['target_date']))
            else:
                trace = None
            if trace is None:
                from radar.stocks.update_service import load_market_window
                _, trace = load_market_window(state, date.fromisoformat(task['target_date']))
        except (OSError, ValueError) as exc:
            logger.warning('基础资料任务日历不可用：%s', type(exc).__name__)
            trace = None
        def collect_channel(channel: str, symbol: str | None) -> tuple[str, dict[str, Any]]:
            try:
                result = collector.collect(channel, symbol, window=trace)
                if result.value is None:
                    raise StockResearchError('基础资料来源没有可保存产物')
                state_name = {'complete': 'completed', 'partial': 'partial'}.get(result.record.result_state, 'failed')
                artifact = {**_artifact(self.artifact_dir, result.value),
                            'collection_record': result.record.model_dump(mode='json')}
            except StockRevisionConflict:
                raise
            except Exception as exc:  # noqa: BLE001 独立资料来源隔离，不能使其他来源或公司任务丢失
                logger.warning('基础资料来源失败 %s %s: %s', channel, symbol, type(exc).__name__)
                state_name = 'failed'
                artifact = {**_artifact(self.artifact_dir, {'channel': channel, 'as_of': task['target_date'], 'manifest': None}),
                            'collection_record': make_record(channel, 'failed', error_kind='fetch_failed',
                                error_message='来源请求、日历或内容校验失败，资料待补。').model_dump(mode='json')}
            return state_name, artifact

        def record(scope: str, state_name: str, artifact: dict[str, Any]) -> None:
            self.store.record_item(task['run_id'], worker, {'scope': scope, 'status': state_name,
                'summary': '基础资料来源已保存，不代表入池资格' if state_name == 'completed' else '基础资料仍待补，可查看来源诊断后继续',
                'artifact': artifact}, lease_token=token)

        statuses: list[str] = []
        collected: dict[str, tuple[str, dict[str, Any]]] = {}
        pending: list[str] = []
        for channel, symbol in channels:
            scope = 'reference:' + (('market_value:' + symbol) if symbol is not None else channel)
            old = previous.get(scope)
            if old is not None and old['status'] == 'completed':
                self._verify_artifact(old)
                # 恢复不能跳过已损坏或替换的不可变来源快照。
                from radar.stocks.financial_task import read_financial_artifact
                payload = read_financial_artifact(old, self.artifact_dir)
                verify_reference_manifest(state, payload)
                statuses.append('completed')
                continue
            if symbol is None:
                state_name, artifact = collect_channel(channel, None)
                record(scope, state_name, artifact)
                statuses.append(state_name)
            else:
                pending.append(symbol)
        if pending:
            workers = max(1, min(self.max_workers, len(pending)))
            if workers == 1:
                for symbol in pending:
                    collected[symbol] = collect_channel('market_value', symbol)
            else:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {symbol: pool.submit(collect_channel, 'market_value', symbol) for symbol in pending}
                    for symbol, future in futures.items():
                        collected[symbol] = future.result()
        for row in frozen['companies']:
            symbol = row['symbol']
            if symbol not in collected:
                continue
            state_name, artifact = collected[symbol]
            record('reference:market_value:' + symbol, state_name, artifact)
            statuses.append(state_name)
        try:
            if sum(path.stat().st_size for path in self.cache_dir.rglob('*') if path.is_file()) > 1024 ** 3:
                logger.warning('股票资料缓存超过1GiB软上限，未自动删除')
        except OSError as exc:
            logger.debug('股票资料缓存预算统计不完整：%s', type(exc).__name__)
        return 'completed' if all(status == 'completed' for status in statuses) else ('partial' if any(status in {'completed', 'partial'} for status in statuses) else 'failed')

    def execute_one(self, worker_id: str) -> dict[str, Any] | None:
        self.store.recover_expired()
        task = self.store.claim(worker_id)
        if task is None:
            return None
        token, run_id = task['lease_token'], task['run_id']
        stopped = threading.Event()

        def heartbeat() -> None:
            while not stopped.wait(15):
                try:
                    self.store.heartbeat(run_id, worker_id, lease_token=token)
                except (OSError, ValueError, sqlite3.Error) as exc:
                    logger.warning('股票任务续租失败 %s: %s', run_id, exc)
                    return

        pulse = threading.Thread(target=heartbeat, daemon=True, name='stock-update-lease')
        pulse.start()
        try:
            if task['kind'] == 'discover_candidates':
                completed = next((item for item in task['results'] if item['scope'] == 'index_scope_v1' and item['status'] == 'completed'), None)
                if completed is not None:
                    self._verify_artifact(completed)
                item = completed or self._discovery(task, worker_id, token)
                status = item['status']
            elif task['kind'] == 'financial_review':
                status = self._financial(task, worker_id, token)
            elif task['kind'] == 'dividend_review':
                status = self._dividend(task, worker_id, token)
            elif task['kind'] == 'turnover_review':
                status = self._turnover(task, worker_id, token)
            elif task['kind'] == 'reference_review':
                status = self._reference(task, worker_id, token)
            else:
                self._refresh(task, worker_id, token)
                current = self.store.get(run_id)
                latest = {item['scope']: item for item in current['results']}
                status = 'partial' if any(item['status'] in {'completed', 'partial'} for item in latest.values()) else 'failed'
            return self.store.finish(run_id, worker_id, lease_token=token, status=status,
                                     error=None if status == 'completed' else '部分资料仍缺失或待复核，详见逐项结果')
        except StockRevisionConflict as exc:
            logger.warning('股票任务已失去租约 %s: %s', run_id, exc)
            return {'run_id': run_id, 'status': 'lease_lost', 'error': str(exc)}
        except Exception as exc:
            logger.exception('股票资料任务失败 %s', run_id)
            try:
                return self.store.finish(run_id, worker_id, lease_token=token, status='failed', error=str(exc))
            except StockRevisionConflict:
                logger.warning('股票失败任务未覆盖新执行者结果 %s', run_id)
                return {'run_id': run_id, 'status': 'lease_lost', 'error': str(exc)}
        finally:
            stopped.set()
            pulse.join(timeout=1)
