"""股票手动更新任务命令；自动调度保持关闭。"""

import sqlite3
from datetime import date, datetime
from pathlib import Path

import click

from radar.stocks.evaluation_store import StockEvaluationStore
from radar.stocks.membership_store import StockMembershipStore
from radar.stocks.pools import StockPoolRepository
from radar.stocks.research_store import StockResearchStore
from radar.stocks.update_runner import StockUpdateRunner, validate_storage
from radar.stocks.update_runs import StockUpdateStore
from stock_robot.dividend_documents import _cutoff
from stock_robot.dividend_materials import _read
from stock_robot.stock_admission import _read_market_window
from stock_robot.stock_archive import _protect_database, _protect_pool_directory
from stock_robot.stock_research import _emit


@click.command('stock-reference-update')
@click.option('--db', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--pool', 'pool_ids', multiple=True, required=True)
@click.option('--as-of')
@click.option('--force-refresh', is_flag=True)
@click.option('--resume-run-id')
@click.option('--pool-dir', type=click.Path(exists=True, file_okay=False, path_type=Path), default='config/radar_stock_pools')
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path))
def reference_update(db: Path, pool_ids: tuple[str, ...], as_of: str | None, force_refresh: bool,
                     resume_run_id: str | None, pool_dir: Path, output: Path | None) -> None:
    """冻结已保存候选，入队一次基础资料更新；用stock-update-run-one执行。"""
    from radar.stocks.reference_task import (
        REFERENCE_VERSION,
        prepare_reference_snapshot,
    )
    try:
        _protect_database(db, output)
        _protect_pool_directory(pool_dir, output)
        if resume_run_id is not None:
            if as_of is not None or force_refresh:
                raise ValueError('继续原任务不能更改日期或强制刷新参数')
            result = _updates(db, pool_dir).resume_reference(resume_run_id, list(pool_ids))
        else:
            target = _cutoff(as_of) if as_of is not None else datetime.now().astimezone().date()
            frozen = prepare_reference_snapshot(db, db.parent, list(pool_ids), target, force_refresh=force_refresh)
            result = _updates(db, pool_dir).enqueue(kind='reference_review', pool_ids=list(pool_ids), target_date=target,
                                                   rule_version=REFERENCE_VERSION, snapshot=frozen)
        _emit(result, db, output)
    except (OSError, TypeError, ValueError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc


def _updates(db: Path, pool_dir: Path) -> StockUpdateStore:
    research = StockResearchStore(db)
    memberships = StockMembershipStore(StockEvaluationStore(research), StockPoolRepository(pool_dir))
    return StockUpdateStore(memberships)


@click.command('stock-dividend-update')
@click.option('--db', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--pool', 'pool_id', required=True)
@click.option('--as-of')
@click.option('--resume-run-id')
@click.option('--pool-dir', type=click.Path(exists=True, file_okay=False, path_type=Path), default='config/radar_stock_pools')
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path))
def dividend_update(db: Path, pool_id: str, as_of: str | None, resume_run_id: str | None,
                    pool_dir: Path, output: Path | None) -> None:
    """冻结候选并入队分红资料任务，或继续已有任务。"""
    from radar.stocks.dividend_collect import VERSION
    from radar.stocks.web_queries import read_connection, scope_snapshot

    try:
        _protect_database(db, output)
        _protect_pool_directory(pool_dir, output)
        tasks = _updates(db, pool_dir)
        if resume_run_id is not None:
            if as_of is not None:
                raise ValueError('继续原任务不能更改日期')
            result = tasks.resume_dividend(resume_run_id, pool_id)
        else:
            with read_connection(db) as connection:
                scope = scope_snapshot(connection, db.parent, pool_id)
            if not scope['scope_complete'] or scope['scope_status'] != 'complete' or not scope['companies']:
                raise ValueError('请先准备当前池完整指数候选范围')
            snapshot = {**scope, 'kind': 'dividend_review', 'pool_ids': [pool_id]}
            target = _cutoff(as_of) if as_of is not None else datetime.now().astimezone().date()
            result = tasks.enqueue(kind='dividend_review', pool_ids=[pool_id], target_date=target,
                                   rule_version=VERSION, snapshot=snapshot)
        _emit(result, db, output)
    except (OSError, TypeError, ValueError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc


@click.command('stock-turnover-update')
@click.option('--db', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--pool', 'pool_id', required=True)
@click.option('--as-of')
@click.option('--resume-run-id')
@click.option('--pool-dir', type=click.Path(exists=True, file_okay=False, path_type=Path), default='config/radar_stock_pools')
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path))
def turnover_update(db: Path, pool_id: str, as_of: str | None, resume_run_id: str | None,
                    pool_dir: Path, output: Path | None) -> None:
    """冻结候选并入队成交额资料任务，或继续已有任务。"""
    from radar.stocks.turnover_source import VERSION
    from radar.stocks.web_queries import read_connection, scope_snapshot

    try:
        _protect_database(db, output)
        _protect_pool_directory(pool_dir, output)
        tasks = _updates(db, pool_dir)
        if resume_run_id is not None:
            if as_of is not None:
                raise ValueError('继续原任务不能更改日期')
            result = tasks.resume_turnover(resume_run_id, pool_id)
        else:
            with read_connection(db) as connection:
                scope = scope_snapshot(connection, db.parent, pool_id)
            if not scope['scope_complete'] or scope['scope_status'] != 'complete' or not scope['companies']:
                raise ValueError('请先准备当前池完整指数候选范围')
            snapshot = {**scope, 'kind': 'turnover_review', 'pool_ids': [pool_id]}
            target = _cutoff(as_of) if as_of is not None else datetime.now().astimezone().date()
            result = tasks.enqueue(kind='turnover_review', pool_ids=[pool_id], target_date=target,
                                   rule_version=VERSION, snapshot=snapshot)
        _emit(result, db, output)
    except (OSError, TypeError, ValueError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc


@click.command('stock-update-enqueue')
@click.option('--db', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--input', 'input_path', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--pool-dir', type=click.Path(exists=True, file_okay=False, path_type=Path), default='config/radar_stock_pools')
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path))
def enqueue_update(db: Path, input_path: Path, pool_dir: Path, output: Path | None) -> None:
    """入队一次手动股票资料更新或候选发现，不发布成员。"""
    try:
        _protect_database(db, input_path, output)
        _protect_pool_directory(pool_dir, output)
        if output is not None and output.resolve() == input_path.resolve():
            raise ValueError('输出不能覆盖更新任务输入')
        payload, _ = _read(input_path)
        if not isinstance(payload, dict) or set(payload) != {'kind', 'pool_ids', 'target_date', 'rule_version'}:
            raise ValueError('输入仅允许kind、pool_ids、target_date、rule_version')
        result = _updates(db, pool_dir).enqueue(kind=payload['kind'], pool_ids=payload['pool_ids'], target_date=_cutoff(payload['target_date']), rule_version=payload['rule_version'])
        _emit(result, db, output)
    except (OSError, TypeError, ValueError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc


@click.command('stock-update-list')
@click.option('--db', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--pool-dir', type=click.Path(exists=True, file_okay=False, path_type=Path), default='config/radar_stock_pools')
@click.option('--page', type=click.IntRange(min=1), default=1)
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path))
def list_updates(db: Path, pool_dir: Path, page: int, output: Path | None) -> None:
    """查看手动股票更新任务；自动更新固定关闭。"""
    try:
        _protect_database(db, output)
        _protect_pool_directory(pool_dir, output)
        _emit(_updates(db, pool_dir).list_runs(page=page), db, output)
    except (OSError, TypeError, ValueError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc


@click.command('stock-update-run-one')
@click.option('--db', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--pool-dir', type=click.Path(exists=True, file_okay=False, path_type=Path), default='config/radar_stock_pools')
@click.option('--worker-id', default='manual-cli')
@click.option('--cache-dir', type=click.Path(file_okay=False, path_type=Path))
@click.option('--artifact-dir', type=click.Path(file_okay=False, path_type=Path))
@click.option('--market-window', 'window_path', type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path))
def run_one_update(db: Path, pool_dir: Path, worker_id: str, cache_dir: Path | None, artifact_dir: Path | None, window_path: Path | None, output: Path | None) -> None:
    """执行指数初筛范围发现或成员资料采集，保留待核验结果和逐项失败。"""
    try:
        cache = validate_storage(cache_dir or db.parent/'stock-updates'/'cache', db)
        artifacts = validate_storage(artifact_dir or db.parent/'stock-updates'/'artifacts', db)
        for root in (cache, artifacts):
            if root.resolve().is_relative_to(pool_dir.resolve()) or pool_dir.resolve().is_relative_to(root.resolve()):
                raise ValueError('任务缓存及产物不能包含股票池配置目录或位于其中')
            if window_path is not None and window_path.resolve().is_relative_to(root.resolve()):
                raise ValueError('任务存储不能包含市场窗口输入')
            if output is not None and output.resolve().is_relative_to(root.resolve()):
                raise ValueError('任务结果导出不能位于采集缓存或原文产物目录')
        _protect_database(db, window_path, output)
        _protect_pool_directory(pool_dir, output)
        if output is not None and window_path is not None and output.resolve() == window_path.resolve():
            raise ValueError('任务结果导出不能覆盖市场窗口输入')

        def window_loader(cutoff: date):
            return _read_market_window(cutoff, window_path)

        store = _updates(db, pool_dir)
        runner = StockUpdateRunner(store, cache, artifacts, window_loader=window_loader)
        result = runner.execute_one(worker_id)
        if result is None:
            click.echo('没有待执行的股票更新任务。')
            return
        _emit(result, db, output)
    except (OSError, TypeError, ValueError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc
