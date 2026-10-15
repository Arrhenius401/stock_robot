"""发现当前官方证券名录，只导出共享候选研究线索。"""
from datetime import datetime
from pathlib import Path

import click

from radar.stocks.admission import _BJ
from radar.stocks.official_roster import collect_stock_roster
from stock_robot.dividend_documents import _cutoff
from stock_robot.dividend_research import _write_json


@click.command('stock-universe-discover')
@click.option('--as-of', required=True)
@click.option('--cache-dir', type=click.Path(file_okay=False, path_type=Path), required=True)
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option('--refresh', is_flag=True)
def official_roster_command(as_of: str, cache_dir: Path, output: Path, refresh: bool) -> None:
    """发现沪深普通A股候选；当前日联网，历史日只复用对应缓存，不写数据库。"""
    try:
        target, root = output.resolve(), cache_dir.resolve()
        if target == root or target.is_relative_to(root):
            raise ValueError('输出不能覆盖采集缓存或位于缓存内')
        cutoff = _cutoff(as_of)
        if cutoff > datetime.now().astimezone(_BJ).date():
            raise ValueError('官方名录研究日期不能晚于北京当前日期')
        result = collect_stock_roster(cache_dir, as_of=cutoff, refresh=refresh)
        _write_json(output, result)
    except (OSError, ValueError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo('官方名录研究候选已导出；不是合格成员，未保存审核或发布。')
