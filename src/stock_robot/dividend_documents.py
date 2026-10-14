"""公告自动发现及保守字段建议命令，不自动发布财务证据。"""

from datetime import date
from pathlib import Path

import click

from radar.stocks.announcement_discovery import discover_announcements
from radar.stocks.dividend_extract import extract_archived_documents
from stock_robot.dividend_materials import _read
from stock_robot.dividend_research import _write_json


def _cutoff(value: str) -> date:
    cutoff = date.fromisoformat(value)
    if value != cutoff.isoformat() or cutoff.year < 4:
        raise ValueError("截止日必须是YYYY-MM-DD且至少能覆盖此前三个年度")
    return cutoff


@click.command("dividend-discover")
@click.option("--input", "input_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option("--as-of", required=True, help="公告查询截止日，YYYY-MM-DD")
@click.option("--cache-dir", type=click.Path(file_okay=False, path_type=Path), required=True)
@click.option("--refresh", is_flag=True)
def discover_dividends(input_path: Path, output: Path, as_of: str, cache_dir: Path, refresh: bool) -> None:
    """从公司代码发现官方实施公告URL，查询完整不等于年度分红完整。"""
    try:
        cutoff = _cutoff(as_of)
        if input_path.resolve() == output.resolve():
            raise ValueError("输出不能覆盖公司输入")
        if cache_dir.resolve() in output.resolve().parents or cache_dir.resolve() in input_path.resolve().parents:
            raise ValueError("输入与汇总输出必须位于查询原始缓存之外")
        payload, source = _read(input_path)
        companies = payload.get("companies") if isinstance(payload, dict) else payload
        if not isinstance(companies, list):
            raise TypeError("输入必须是公司列表或包含companies的JSON")
        result = discover_announcements(companies, cutoff, cache_dir, refresh=refresh)
        result["input_source"] = source
        result["rule_enabled"] = False
        result["financial_verified"] = False
        _write_json(output, result)
    except (OSError, ValueError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("公告查询结果已保存；失败与不完整范围见输出，尚未核验分红金额。")


@click.command("dividend-extract")
@click.option("--archive-result", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--archive-dir", type=click.Path(exists=True, file_okay=False, path_type=Path), required=True)
@click.option("--output", type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option("--as-of", required=True, help="证据截止日，YYYY-MM-DD")
def extract_dividends(archive_result: Path, archive_dir: Path, output: Path, as_of: str) -> None:
    """读取已留档PDF并生成字段建议，全部需审核，不自动填完整性。"""
    try:
        cutoff = _cutoff(as_of)
        if archive_result.resolve() == output.resolve() or archive_dir.resolve() in output.resolve().parents:
            raise ValueError("输出不能覆盖归档输入或位于原文目录内部")
        payload, source = _read(archive_result)
        if not isinstance(payload, dict):
            raise TypeError("归档结果必须是JSON对象")
        result = extract_archived_documents(payload, archive_dir, cutoff)
        result["input_source"] = source
        result["rule_enabled"] = False
        result["financial_verified"] = False
        _write_json(output, result)
    except (OSError, ValueError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("字段建议已保存；全部仍需审核，未自动写入标准化证据或股票池。")
