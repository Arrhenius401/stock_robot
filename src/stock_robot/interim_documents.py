"""季度与半年报的发现、字段提取及待审资料包。"""

from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

import click

from radar.stocks.admission import _day
from radar.stocks.announcement_discovery import discover_announcements
from radar.stocks.research_store import StockResearchError, _id, _symbol
from stock_robot.dividend_documents import _cutoff
from stock_robot.dividend_materials import _read
from stock_robot.dividend_research import _write_json


def build_interim_packet(extraction: dict[str, Any], as_of: date) -> dict[str, Any]:
    """只包装已留档建议，失败文档保留期间，不自动审核或认定最新。"""
    if not isinstance(extraction, dict) or not isinstance(extraction.get('items'), list):
        raise StockResearchError('季度提取结果必须包含items列表')
    companies: dict[str, dict[str, Any]] = {}
    for item in extraction['items']:
        if not isinstance(item, dict):
            raise StockResearchError('季度提取条目必须是对象')
        symbol = _symbol(item.get('symbol'))
        company = companies.setdefault(symbol, {'symbol': symbol, 'quarterly_proposals': [], 'quarterly_documents': [], 'unresolved': []})
        origin = {key: deepcopy(item.get(key)) for key in ('source_url', 'disclosed_on', 'file_sha256', 'text_sha256', 'status', 'reasons', 'period_months', 'period_end', 'fiscal_year')}
        parsed_period, discovered_period = origin.get('period_end'), item.get('discovery_period_end')
        conflict = parsed_period is not None and discovered_period is not None and parsed_period != discovered_period
        origin['parsed_period_end'] = parsed_period
        origin['discovery_period_end'] = discovered_period
        # 错链时仅保留合法较新期末作阻断线索，任何金额均不采用。
        disclosed_day = _day(origin.get('disclosed_on'))
        valid_periods = [day for value in (parsed_period, discovered_period)
                         if (day := _day(value)) is not None and disclosed_day is not None and day < disclosed_day and day <= as_of
                         and (day.month, day.day) in {(3, 31), (6, 30), (9, 30)}]
        hint = max(valid_periods) if conflict and valid_periods else None
        origin['period_end'] = hint.isoformat() if hint is not None else (parsed_period or discovered_period)
        origin['period_months'] = hint.month if hint is not None else (origin.get('period_months') or item.get('discovery_period_months'))
        period_day = _day(origin['period_end'])
        if period_day is not None and (origin.get('fiscal_year') is None or conflict):
            origin['fiscal_year'] = period_day.year
        if conflict:
            origin['status'] = 'pending_verification'
        company['quarterly_documents'].append(origin)
        proposals = [] if conflict else item.get('financial_proposals', [])
        if not isinstance(proposals, list) or any(not isinstance(row, dict) for row in proposals):
            raise StockResearchError('季度字段建议必须是对象列表')
        for fields in proposals:
            if fields.get('symbol') != symbol:
                raise StockResearchError('季度字段建议不能跨公司')
            proposal = {'fields': deepcopy(fields), 'origin': deepcopy(origin), 'requires_review': True, 'financial_verified': False}
            proposal['proposal_id'] = _id({'fields': fields, 'origin': origin})
            company['quarterly_proposals'].append(proposal)
        reasons = item.get('reasons', [])
        if not isinstance(reasons, list) or any(not isinstance(reason, str) for reason in reasons):
            raise StockResearchError('季度诊断必须是字符串列表')
        company['unresolved'].extend(reasons)
        company['unresolved'].append('季度原文/比较口径/最新报告范围仍须核验')
    result = {'packet_version': 'interim-review-v1', 'as_of': as_of.isoformat(),
              'companies': [companies[code] for code in sorted(companies)], 'requires_review': True,
              'financial_verified': False, 'rule_enabled': False, 'research_only': True}
    result['revision_sha256'] = _id(result)
    return result


@click.command('interim-discover')
@click.option('--input', 'input_path', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option('--as-of', required=True)
@click.option('--cache-dir', type=click.Path(file_okay=False, path_type=Path), required=True)
@click.option('--refresh', is_flag=True)
def discover_interims(input_path: Path, output: Path, as_of: str, cache_dir: Path, refresh: bool) -> None:
    """发现Q1/H1/Q3官方完整报告，不将查询完成当最新核验。"""
    try:
        if input_path.resolve() == output.resolve() or input_path.resolve().is_relative_to(cache_dir.resolve()) or output.resolve().is_relative_to(cache_dir.resolve()):
            raise ValueError('输入与输出不能互相覆盖或位于查询缓存内')
        payload, trace = _read(input_path)
        companies = payload.get('companies') if isinstance(payload, dict) else payload
        if not isinstance(companies, list):
            raise TypeError('输入须为companies公司列表')
        result = discover_announcements(companies, _cutoff(as_of), cache_dir, refresh=refresh, mode='interim_report')
        result['input_source'] = trace
        _write_json(output, result)
    except (OSError, ValueError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo('季度公告发现结果已保存，最新范围及可比性尚未核实。')


@click.command('interim-extract')
@click.option('--archive-result', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--archive-dir', type=click.Path(exists=True, file_okay=False, path_type=Path), required=True)
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option('--as-of', required=True)
def extract_interims(archive_result: Path, archive_dir: Path, output: Path, as_of: str) -> None:
    """从已归档季度PDF提取明确累计列，全部保留待审。"""
    from radar.stocks.interim_extract import extract_archived_interims

    try:
        if output.resolve() == archive_result.resolve() or output.resolve().is_relative_to(archive_dir.resolve()):
            raise ValueError('输出不能覆盖归档输入或写入PDF目录')
        payload, trace = _read(archive_result)
        if not isinstance(payload, dict):
            raise TypeError('归档结果须为对象')
        result = extract_archived_interims(payload, archive_dir, _cutoff(as_of))
        result['input_source'] = trace
        _write_json(output, result)
    except (OSError, ValueError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo('季度字段建议已保存，未审核或启用正式筛选。')


@click.command('interim-review-packet')
@click.option('--input', 'input_path', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option('--as-of', required=True)
def prepare_interim_packet(input_path: Path, output: Path, as_of: str) -> None:
    """把季度建议包装为可导入研究库的待审资料包。"""
    try:
        if input_path.resolve() == output.resolve():
            raise ValueError('输出不能覆盖季度提取输入')
        payload, _ = _read(input_path)
        packet = build_interim_packet(payload, _cutoff(as_of))
        _write_json(output, packet)
    except (OSError, ValueError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo('季度审核资料包已保存；尚未导入或确认任何事实。')
