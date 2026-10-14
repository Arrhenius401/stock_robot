"""留档年报字段建议命令，不自动核实普通股利润或准入资格。"""

from pathlib import Path

import click

from radar.stocks.financial_extract import extract_archived_financials
from stock_robot.dividend_documents import _cutoff
from stock_robot.dividend_materials import _read
from stock_robot.dividend_research import _write_json


@click.command("financial-extract")
@click.option("--archive-result", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--archive-dir", type=click.Path(exists=True, file_okay=False, path_type=Path), required=True)
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option("--as-of", required=True)
def financial_extract(archive_result: Path, archive_dir: Path, output: Path, as_of: str) -> None:
    """从已留档年报提取会计数据建议，全部保留审核状态。"""
    try:
        cutoff = _cutoff(as_of)
        if archive_result.resolve() == output.resolve() or archive_dir.resolve() in output.resolve().parents:
            raise ValueError("输出不能覆盖归档输入或位于原文目录内")
        payload, trace = _read(archive_result)
        if not isinstance(payload, dict):
            raise TypeError("归档结果必须是JSON对象")
        result = extract_archived_financials(payload, archive_dir, cutoff)
        result.update(input_source=trace, requires_review=True, financial_verified=False, rule_enabled=False)
        _write_json(output, result)
    except (OSError, ValueError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("财务字段建议已保存；普通股口径、股类及年度分红完整性仍需核验，正式筛选未启用。")
