"""计算结果不可变归档与受阻成员草案的本地CLI入口。"""

import sqlite3
from pathlib import Path
from typing import Any

import click

from radar.stocks.evaluation_store import StockEvaluationStore
from radar.stocks.membership_drafts import StockMembershipDraftStore
from radar.stocks.pools import StockPoolRepository
from radar.stocks.research_store import StockResearchStore
from stock_robot.dividend_documents import _cutoff
from stock_robot.dividend_materials import _read
from stock_robot.stock_admission import _read_market_window, evaluate_store
from stock_robot.stock_research import _emit, _protect_database


def _archive(db: Path) -> StockEvaluationStore:
    return StockEvaluationStore(StockResearchStore(db))


def _protect_pool_directory(pool_dir: Path, output: Path | None) -> None:
    if output is not None and output.resolve().is_relative_to(pool_dir.resolve()):
        raise ValueError('导出文件不能写入读取中的股票池配置目录')


def _drafts(db: Path, pool_dir: Path) -> StockMembershipDraftStore:
    return StockMembershipDraftStore(_archive(db), StockPoolRepository(pool_dir))


@click.command('stock-evaluation-capture')
@click.option('--db', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--as-of', required=True)
@click.option('--symbol')
@click.option('--market-window', 'window_path', type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path))
def capture_evaluation(db: Path, as_of: str, symbol: str | None, window_path: Path | None, output: Path | None) -> None:
    """重新计算并留档，不能导入外部passed结论。"""
    try:
        _protect_database(db, window_path, output)
        if output is not None and window_path is not None and output.resolve() == window_path.resolve():
            raise ValueError('输出不能覆盖市场窗口输入')
        cutoff = _cutoff(as_of)
        window, trace = _read_market_window(cutoff, window_path)
        archive = _archive(db)
        inputs = archive.capture_inputs(symbol=symbol)
        report = evaluate_store(archive.research, cutoff, symbol=symbol, market_window=window, include_styles=True)
        report['market_window_source'] = trace
        saved = archive.save_evaluation(report, inputs, market_window=window)
        summary = {key: saved[key] for key in ('evaluation_id', 'status', 'created_at', 'as_of', 'scope', 'freshness')}
        summary.update(companies_total=len(saved['report']['items']), formal_screening_enabled=False, members_published=0)
        _emit(summary, db, output)
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc


@click.command('stock-evaluation-list')
@click.option('--db', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--symbol')
@click.option('--page', type=click.IntRange(min=1), default=1)
@click.option('--evaluation-id')
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path))
def list_evaluations(db: Path, symbol: str | None, page: int, evaluation_id: str | None, output: Path | None) -> None:
    """按页查看档案摘要，指定ID时导出完整历史来源。"""
    try:
        _protect_database(db, output)
        archive = _archive(db)
        result = archive.get(evaluation_id) if evaluation_id else archive.list_evaluations(symbol=symbol, page=page)
        if result is None:
            raise ValueError('评估档案不存在')
        _emit(result, db, output)
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc


@click.command('stock-member-draft-create')
@click.option('--db', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--input', 'input_path', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--pool-dir', type=click.Path(exists=True, file_okay=False, path_type=Path), default='config/radar_stock_pools')
@click.option('--request-id', required=True)
@click.option('--actor', required=True)
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path))
def create_draft(db: Path, input_path: Path, pool_dir: Path, request_id: str, actor: str, output: Path | None) -> None:
    """保存新增关注草案及阻碍，不发布正式成员。"""
    try:
        _protect_database(db, input_path, output)
        _protect_pool_directory(pool_dir, output)
        if output is not None and output.resolve() == input_path.resolve():
            raise ValueError('输出不能覆盖成员变更输入')
        payload, _ = _read(input_path)
        if not isinstance(payload, dict) or set(payload) != {'evaluation_id', 'changes'}:
            raise ValueError('输入仅允许evaluation_id和changes，不接受外部资格结论')
        result = _drafts(db, pool_dir).create(evaluation_id=payload['evaluation_id'], changes=payload['changes'], request_id=request_id, actor=actor)
        _emit(result, db, output)
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc


@click.command('stock-member-draft-list')
@click.option('--db', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--pool-dir', type=click.Path(exists=True, file_okay=False, path_type=Path), default='config/radar_stock_pools')
@click.option('--page', type=click.IntRange(min=1), default=1)
@click.option('--draft-id')
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path))
def list_drafts(db: Path, pool_dir: Path, page: int, draft_id: str | None, output: Path | None) -> None:
    """查看草案和撤销历史，附当前评估来源是否失效。"""
    try:
        _protect_database(db, output)
        _protect_pool_directory(pool_dir, output)
        store = _drafts(db, pool_dir)
        result: dict[str, Any] | None = store.get(draft_id) if draft_id else store.list_drafts(page=page)
        if result is None:
            raise ValueError('成员草案不存在')
        _emit(result, db, output)
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc


@click.command('stock-member-draft-cancel')
@click.option('--db', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--pool-dir', type=click.Path(exists=True, file_okay=False, path_type=Path), default='config/radar_stock_pools')
@click.option('--draft-id', required=True)
@click.option('--expected-revision', type=click.IntRange(min=1), required=True)
@click.option('--actor', required=True)
@click.option('--reason', required=True)
def cancel_draft(db: Path, pool_dir: Path, draft_id: str, expected_revision: int, actor: str, reason: str) -> None:
    """按版本撤销草案，保留追加式历史。"""
    try:
        result = _drafts(db, pool_dir).cancel(draft_id, expected_revision=expected_revision, actor=actor, reason=reason)
        _emit(result, db)
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc
