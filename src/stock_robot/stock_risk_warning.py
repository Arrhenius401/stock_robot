"""按需更新风险名单研究来源，不修改审核或成员。"""
from pathlib import Path

import click

from radar.stocks.risk_warning import collect_risk_warning


@click.command('risk-warning-update')
@click.option('--state-dir', type=click.Path(file_okay=False, path_type=Path), default=Path('.stock_robot'), show_default=True)
def risk_warning_update(state_dir: Path) -> None:
    """采集当日风险警示板原文作为研究线索。"""
    result = collect_risk_warning(state_dir)
    if result.get('status') != 'complete':
        raise click.ClickException('风险名单采集失败，来源待核验：' + str(result.get('reason', '未知原因')))
    click.echo('风险名单研究来源已更新；未生成审核或正式成员。')
