"""只读日历生成股票研究市场日期窗口的命令。"""
from datetime import datetime
from pathlib import Path

import click

from radar.calendar import CalendarUnavailable, TradingCalendar
from radar.stocks.market_context import prepare_market_window
from stock_robot.dividend_documents import _cutoff
from stock_robot.stock_research import _emit, _protect_database


@click.command('stock-market-window')
@click.option('--calendar-db', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--as-of', required=True)
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path), required=True)
def market_window_command(calendar_db: Path, as_of: str, output: Path) -> None:
    """读取缓存并输出60个已收盘交易日；不会联网或确认股票准入事实。"""
    try:
        _protect_database(calendar_db, output)
        calendar = TradingCalendar(calendar_db, refresh_enabled=False)
        packet = prepare_market_window(calendar, _cutoff(as_of), now=datetime.now().astimezone())
        _emit(packet, calendar_db, output)
    except (CalendarUnavailable, OSError, ValueError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc
