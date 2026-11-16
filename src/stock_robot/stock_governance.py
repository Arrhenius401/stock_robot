"""按需更新交易所上市公司监管列表研究来源，不修改审核或成员。"""
from pathlib import Path

import click

from radar.stocks.exchange_governance import (
    collect_exchange_governance,
    rebuild_exchange_governance_index,
)


@click.command('exchange-governance-update')
@click.option('--state-dir', type=click.Path(file_okay=False, path_type=Path), default=Path('.stock_robot'), show_default=True)
def exchange_governance_update(state_dir: Path) -> None:
    """采集沪深交易所监管措施与纪律处分列表作为研究线索。"""
    result = collect_exchange_governance(state_dir)
    if result.get('status') != 'complete':
        raise click.ClickException('交易所监管列表采集失败，来源待核验：' + str(result.get('reason', '未知原因')))
    click.echo('交易所监管列表研究来源已更新；未生成审核或正式成员。')


@click.command('exchange-governance-rebuild')
@click.option('--state-dir', type=click.Path(file_okay=False, path_type=Path), default=Path('.stock_robot'), show_default=True)
def exchange_governance_rebuild(state_dir: Path) -> None:
    """从保存的原文分页重建监管派生索引，保留原抓取和快照日期。"""
    result = rebuild_exchange_governance_index(state_dir)
    if result.get('status') not in {'rebuilt', 'current'}:
        raise click.ClickException('交易所监管列表重建失败：' + str(result.get('reason', '未知原因')))
    click.echo('交易所监管列表派生索引已复核；未抓取新数据，未生成审核或正式成员。')
