"""手动股票资料任务执行器，复用原文采集器并保留不可变产物。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import threading
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from radar.stocks.capital_history import collect_capital_history
from radar.stocks.financial_task import (
    read_financial_artifact,
    verify_financial_payload,
)
from radar.stocks.index_discovery import collect_index_scope
from radar.stocks.market_history import collect_stock_history
from radar.stocks.research_store import StockResearchError, StockRevisionConflict, _sha
from radar.stocks.structured_collect import collect_structured_company
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
                 roster_collector: Callable[..., Any] = collect_index_scope,
                 history_collector: Callable[..., Any] = collect_stock_history,
                 capital_collector: Callable[..., Any] = collect_capital_history,
                 financial_collector: Callable[..., Any] = collect_structured_company,
                 market_window: list[date] | None = None,
                 window_loader: Callable[[date], tuple[list[date] | None, Any]] | None = None):
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
        result = self.roster_collector(self.cache_dir/'index-scope', as_of=cutoff)
        if not isinstance(result, dict) or result.get('as_of') != cutoff.isoformat() or not isinstance(result.get('companies'), list):
            raise StockResearchError('指数初筛结果缺少匹配研究日期和公司清单')
        reference = _artifact(self.artifact_dir, result)
        imported = (self.store.research.import_companies(result['companies'], source_sha256=reference['sha256'],
            source_label=reference['path'], data_as_of=cutoff,
            transaction_guard=lambda connection: self.store.assert_owner(connection, task['run_id'], worker, token)) if result['companies'] else None)
        complete = bool(result['companies']) and result.get('status') == 'complete' and result.get('scope_complete') is True
        item = {'scope': 'index_scope_v1', 'status': 'completed' if complete else ('partial' if result['companies'] else 'failed'),
                'summary': f"指数范围去重{len(result['companies'])}家公司；来源{'完整' if complete else '尚不完整'}，尚未筛选通过或发布成员",
                'artifact': {**reference, 'companies_found': len(result['companies']), 'scope_complete': complete,
                             'import': imported, 'formal_screening_enabled': False, 'members_published': 0}}
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
                collection = self.financial_collector(symbol, self.cache_dir/'structured-financial',
                                                       as_of=date.fromisoformat(task['target_date']))
                stopped = isinstance(collection, dict) and collection.get('source_stopped') is True
                payload = {'symbol': symbol, 'as_of': task['target_date'], 'collection': collection}
                screened = verify_financial_payload(payload, task, symbol)
                outcome = screened['financial_numeric_status']
            except StockRevisionConflict:
                raise
            except Exception as exc:  # noqa: BLE001 财务来源独立隔离，错误不能当作公司未盈利
                logger.warning('股票财务采集失败 %s: %s', symbol, exc)
                payload = {'symbol': symbol, 'as_of': task['target_date'], 'collection': None}
                outcome = 'pending_verification'
            incomplete = outcome == 'pending_verification'
            pending = pending or incomplete
            item = {'scope': scope, 'status': 'partial' if incomplete else 'completed',
                    'summary': '财务资料待补，不能当作未盈利' if incomplete else '盈利数值复核已完成，尚未通过全部入池条件',
                    'artifact': {**_artifact(self.artifact_dir, payload), 'symbol': symbol,
                                 'financial_numeric_status': outcome, 'source_stopped': stopped}}
            self.store.record_item(task['run_id'], worker, item, lease_token=token)
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
                    if isinstance(value, dict):
                        value = {**value, 'market_window_source': trace}
                else:
                    value = collector(companies, self.cache_dir/scope, as_of=cutoff)
                if not isinstance(value, dict) or value.get('as_of') != cutoff.isoformat():
                    raise StockResearchError('成员更新结果日期或结构无效')
                complete = value.get('status') == 'complete'
                usable = complete or any(row.get('status') in {'downloaded', 'cached'} and row.get('rows')
                                         for row in value.get('items', []) if isinstance(row, dict))
                item = {'scope': scope, 'status': 'completed' if complete else ('partial' if usable else 'failed'),
                        'summary': f"{len(companies)}家公司资料采集{'完成' if complete else '尚不完整'}，未审核资格",
                        'artifact': _artifact(self.artifact_dir, value)}
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
