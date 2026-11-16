"""导出官方股本变动原文研究资料，未知单位不生成市值事实。"""
from datetime import datetime
from pathlib import Path

import click

from radar.stocks.admission import _BJ
from radar.stocks.capital_history import collect_capital_history
from radar.stocks.research_store import _symbol
from stock_robot.dividend_documents import _cutoff
from stock_robot.dividend_materials import _read
from stock_robot.dividend_research import _write_json


@click.command('stock-capital-history')
@click.option('--input', 'input_path', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--as-of', required=True)
@click.option('--start-date', default='1990-01-01', show_default=True)
@click.option('--cache-dir', type=click.Path(file_okay=False, path_type=Path), required=True)
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option('--refresh', is_flag=True)
def capital_history_command(input_path: Path, as_of: str, start_date: str, cache_dir: Path, output: Path, refresh: bool) -> None:
    """保存双日期股本记录；不猜单位、不计算市值、不修改审核或成员。"""
    try:
        source_path, target_path, root = input_path.resolve(), output.resolve(), cache_dir.resolve()
        if source_path == target_path or any(path == root or path.is_relative_to(root) for path in (source_path, target_path)):
            raise ValueError('输入和输出不能互相覆盖或位于采集缓存内')
        cutoff, start = _cutoff(as_of), _cutoff(start_date)
        if cutoff > datetime.now(_BJ).date() or start > cutoff:
            raise ValueError('研究截点不能在未来，起始日期不能晚于截点')
        payload, source = _read(input_path)
        companies = payload.get('companies') if isinstance(payload, dict) else payload
        if not isinstance(companies, list) or not companies or any(not isinstance(item, dict) for item in companies):
            raise ValueError('输入必须包含非空公司对象列表')
        codes = [_symbol(item.get('symbol')) for item in companies]
        if len(set(codes)) != len(codes):
            raise ValueError('公司代码不能重复')
        result = collect_capital_history(companies, cache_dir, as_of=cutoff, start_date=start, refresh=refresh)
        result['input_source'] = source
        _write_json(output, result)
    except (OSError, ValueError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo('股本变动研究资料已导出；单位及全股类覆盖仍待核实，未计算市值或发布成员。')
