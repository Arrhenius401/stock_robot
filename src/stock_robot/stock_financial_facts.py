"""将年度及季度建议整理为带完整依赖的待审规范财务组。"""

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import click

from radar.stocks.financial_facts import prepare_financial_facts
from radar.stocks.research_store import (
    StockResearchError,
    StockResearchStore,
    StockRevisionConflict,
)
from stock_robot.dividend_documents import _cutoff
from stock_robot.stock_research import _emit, _protect_database


def prepare_from_store(store: StockResearchStore, symbol: str, as_of: date, *, save: bool = False,
                       expected_revision: int | None = None) -> dict[str, Any]:
    """同一次读取准备资料，保存仍需显式CAS且不创建审核记录。"""
    if save and expected_revision is None:
        raise StockResearchError('保存待审事实必须提供expected-revision')
    before = store.get_company(symbol)
    if before is None:
        raise StockResearchError('公司不存在')
    evidence = store.list_evidence(symbol)
    packet = prepare_financial_facts(symbol, evidence, as_of)
    after = store.get_company(symbol)
    if after is None or before['revision'] != after['revision']:
        raise StockRevisionConflict('准备财务事实期间资料已变化，请重试')
    packet.update(source_revision=before['revision'], saved=None)
    if save:
        if expected_revision != before['revision']:
            raise StockRevisionConflict('公司版本已变化，请重新查看准备资料')
        if expected_revision is None:
            raise StockResearchError('缺少保存版本')
        packet['saved'] = store.add_financial_fact_batch(symbol, packet['facts'], expected_revision=expected_revision)
    return packet


@click.command('stock-financial-facts-prepare')
@click.option('--db', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--symbol', required=True)
@click.option('--as-of', required=True)
@click.option('--save', is_flag=True, help='显式保存为待审事实，不自动确认')
@click.option('--expected-revision', type=click.IntRange(min=0))
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path))
def prepare_facts(db: Path, symbol: str, as_of: str, save: bool, expected_revision: int | None, output: Path | None) -> None:
    """整理三年财务及有来源的季度TTM建议，不自动审核或批准资格。"""
    try:
        _protect_database(db, output)
        packet = prepare_from_store(StockResearchStore(db), symbol, _cutoff(as_of), save=save, expected_revision=expected_revision)
        _emit(packet, db, output)
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc
