"""导出按投资风格划分的指数初筛范围，不写入正式股票池。"""

from datetime import datetime
from pathlib import Path

import click

from radar.stocks.admission import _BJ
from radar.stocks.index_discovery import collect_index_scope
from stock_robot.dividend_documents import _cutoff
from stock_robot.dividend_research import _write_json


@click.command('stock-index-discover')
@click.option('--as-of', required=True, help='研究日期，YYYY-MM-DD；历史日只读取对应抓取缓存')
@click.option('--cache-dir', type=click.Path(file_okay=False, path_type=Path), required=True)
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option('--refresh', is_flag=True)
def index_scope_command(as_of: str, cache_dir: Path, output: Path, refresh: bool) -> None:
    """采集五个官方指数并导出两池去重范围；不等于通过盈利、龙头或风险审核。"""
    try:
        target, root = output.resolve(), cache_dir.resolve()
        if target == root or target.is_relative_to(root):
            raise ValueError('输出不能覆盖采集缓存或位于缓存内')
        if output.is_symlink() or any(path.is_symlink() for path in (cache_dir, *cache_dir.parents)):
            raise ValueError('输出或缓存路径不能是符号链接')
        cutoff = _cutoff(as_of)
        if cutoff > datetime.now().astimezone(_BJ).date():
            raise ValueError('指数研究日期不能晚于北京当前日期')
        result = collect_index_scope(cache_dir, as_of=cutoff, refresh=refresh)
        _write_json(output, result)
    except (OSError, ValueError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo('指数初筛范围已导出；请查看各来源日期与完整性，尚未筛选通过或发布成员。')
