"""导出原始股票日线研究资料，不改动正式成员或审核记录。"""
from datetime import date
from pathlib import Path

import click

from radar.stocks.market_history import collect_stock_history
from radar.stocks.research_store import _symbol
from stock_robot.dividend_documents import _cutoff
from stock_robot.dividend_materials import _read
from stock_robot.dividend_research import _write_json
from stock_robot.stock_admission import _read_market_window


@click.command('stock-market-history')
@click.option('--input', 'input_path', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--market-window', 'window_path', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--as-of', required=True)
@click.option('--cache-dir', type=click.Path(file_okay=False, path_type=Path), required=True)
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option('--refresh', is_flag=True)
def market_history_command(input_path: Path, window_path: Path, as_of: str, cache_dir: Path, output: Path, refresh: bool) -> None:
    """采集未复权日线与原始成交额，保留缺口及待审建议，不更新数据库。"""
    try:
        paths = [path.resolve() for path in (input_path, window_path, output)]
        root = cache_dir.resolve()
        if len(set(paths)) != 3 or any(path == root or path.is_relative_to(root) for path in paths):
            raise ValueError('输入、窗口和输出不能互相覆盖或位于采集缓存内')
        cutoff = _cutoff(as_of)
        payload, source = _read(input_path)
        companies = payload.get('companies') if isinstance(payload, dict) else payload
        if not isinstance(companies, list) or not companies or any(not isinstance(item, dict) for item in companies):
            raise ValueError('输入必须包含非空公司对象列表')
        codes = [_symbol(item.get('symbol')) for item in companies]
        if len(set(codes)) != len(codes):
            raise ValueError('公司代码不能重复')
        days, trace = _read_market_window(cutoff, window_path)
        if days is None or any(not isinstance(day, date) for day in days):
            raise ValueError('必须提供完整市场日期窗口')
        result = collect_stock_history(companies, days, cache_dir, as_of=cutoff, refresh=refresh)
        result.update(input_source=source, market_window_source=trace)
        _write_json(output, result)
    except (OSError, ValueError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo('股票日线研究资料已导出；未审核财务事实或发布股票池成员。')
