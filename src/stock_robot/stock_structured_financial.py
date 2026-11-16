"""统一财务表采集入口，保存独立研究产物，不审核或发布股票池。"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any

import click

from radar.stocks.admission import _BJ, _day
from radar.stocks.research_store import _sha, _symbol
from radar.stocks.structured_collect import _safe_path
from radar.stocks.structured_financial import VERSION, normalize_structured_financials
from radar.stocks.update_runner import _artifact
from stock_robot.dividend_documents import _cutoff
from stock_robot.dividend_research import _digest, _write_json

logger = logging.getLogger(__name__)


def _companies(rows: Any) -> list[dict[str, str]]:
    if not isinstance(rows, list) or not rows:
        raise ValueError('输入必须包含非空公司清单')
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError('公司记录必须是对象')
        symbol, name = _symbol(row.get('symbol')), row.get('name')
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            raise ValueError('公司名称必须为无首尾空白的非空文本')
        company = {'symbol': symbol, 'name': name}
        if symbol in result and result[symbol] != company:
            raise ValueError(f'公司代码名称冲突：{symbol}')
        result[symbol] = company
    return [result[symbol] for symbol in sorted(result)]


def run_structured_batch(companies: list[dict[str, Any]], workspace: Path, as_of: date, *,
                         refresh: bool = False, input_sha256: str | None = None,
                         collector: Callable[..., Any] | None = None) -> dict[str, Any]:
    """复用单公司缓存续跑；每家公司立即落盘，429停止此批来源。"""
    roster = _companies(companies)
    cutoff = _day(as_of)
    if cutoff is None or cutoff > datetime.now(_BJ).date():
        raise ValueError('研究日期无效或晚于北京当前日期')
    root = Path(workspace).absolute()
    _safe_path(root)
    root.mkdir(parents=True, exist_ok=True)
    for path in root.rglob('*'):
        _safe_path(path)
    binding = {'version': VERSION, 'as_of': cutoff.isoformat(), 'companies': roster,
               'input_sha256': input_sha256, 'roster_sha256': _digest(roster)}
    manifest = root / 'manifest.json'
    if manifest.exists():
        if json.loads(manifest.read_text(encoding='utf-8')) != binding:
            raise ValueError('工作目录与输入清单、日期或版本绑定不一致')
    elif any(root.iterdir()):
        raise ValueError('拒绝复用未经绑定的非空工作目录')
    else:
        _write_json(manifest, binding)
    if collector is None:
        from radar.stocks.structured_collect import collect_structured_company

        collector = collect_structured_company
    report: dict[str, Any] = {
        'version': VERSION, 'as_of': cutoff.isoformat(), 'input_sha256': input_sha256,
        'roster_sha256': binding['roster_sha256'], 'items': [], 'source_stopped': False,
        'point_in_time_verified': False, 'financial_verified': False,
        'formal_screening_enabled': False, 'members_published': 0,
    }
    for company in roster:
        symbol = company['symbol']
        try:
            collected = collector(symbol, root / 'cache', as_of=cutoff, refresh=refresh)
            if not isinstance(collected, dict) or collected.get('symbol') != symbol or collected.get('as_of') != cutoff.isoformat():
                raise ValueError('采集结果的公司代码或研究日期不匹配')
            # 即使字段适配失败也保留429信号，后续公司不能继续请求同一来源。
            report['source_stopped'] = bool(collected.get('source_stopped'))
            normalized = normalize_structured_financials(collected)
            item = {**company, 'status': 'data_complete' if normalized['data_complete'] else 'data_incomplete',
                    'artifact': _artifact(root / 'artifacts', {'collection': collected, 'normalized': normalized}),
                    'cross_source_differences': len(normalized['cross_source_differences']),
                    'source_errors': collected.get('errors', [])}
        except Exception as exc:  # noqa: BLE001 单家公司SDK/数据执行隔离，失败不等于资格淘汰
            logger.warning('结构化财务采集失败 %s: %s', symbol, exc)
            item = {**company, 'status': 'source_error', 'error': str(exc)}
        report['items'].append(item)
        complete = sum(row['status'] == 'data_complete' for row in report['items'])
        report['summary'] = {'total': len(roster), 'attempted': len(report['items']), 'data_complete': complete,
                             'data_incomplete': len(report['items']) - complete,
                             'not_attempted': len(roster) - len(report['items'])}
        _write_json(root / 'summary.json', report)
        if report['source_stopped']:
            break
    return report


@click.command('stock-financial-collect')
@click.option('--input', 'input_path', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--workspace', type=click.Path(file_okay=False, path_type=Path), required=True)
@click.option('--as-of', required=True)
@click.option('--refresh', is_flag=True, help='显式刷新结构化缓存，无公告回退')
def structured_collect_command(input_path: Path, workspace: Path, as_of: str, refresh: bool) -> None:
    """采集显式公司清单的统一财务表，不接入旧PDF资料库、不自动审核。"""
    try:
        if input_path.resolve().is_relative_to(workspace.resolve()):
            raise ValueError('公司输入必须位于结构化工作目录之外')
        raw = input_path.read_bytes()
        payload = json.loads(raw)
        companies = payload.get('companies') if isinstance(payload, dict) else payload
        if not isinstance(companies, list):
            raise TypeError('输入必须为公司列表或包含companies的对象')
        result = run_structured_batch(companies, workspace, _cutoff(as_of), refresh=refresh,
                                      input_sha256=hashlib.sha256(raw).hexdigest())
    except (OSError, ValueError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc
    summary = result['summary']
    click.echo(f"已尝试{summary['attempted']}家，财务数据完整{summary['data_complete']}家，"
               f"尚未尝试{summary['not_attempted']}家；不代表入池。结果：{workspace / 'summary.json'}")


def screen_saved_batch(summary_path: Path) -> dict[str, Any]:
    """读取采集摘要的原子快照；从校验过的原始采集产物重算数值条件。"""
    from radar.stocks.structured_screen import screen_structured_financials

    _safe_path(summary_path.absolute())
    root = summary_path.resolve().parent
    _safe_path(root / 'manifest.json')
    summary = json.loads(summary_path.read_bytes())
    manifest = json.loads((root / 'manifest.json').read_bytes())
    if not isinstance(summary, dict) or not isinstance(manifest, dict):
        raise TypeError('采集摘要和清单必须是对象')
    for key in ('version', 'as_of', 'input_sha256', 'roster_sha256'):
        if summary.get(key) != manifest.get(key):
            raise ValueError(f'采集摘要与清单绑定不一致：{key}')
    roster = _companies(manifest.get('companies'))
    if manifest.get('version') != VERSION or _digest(roster) != manifest.get('roster_sha256'):
        raise ValueError('采集版本或公司清单摘要绑定不一致')
    names = {row['symbol']: row['name'] for row in roster}
    items = summary.get('items')
    if not isinstance(items, list):
        raise TypeError('采集摘要缺少公司结果列表')
    seen = set()
    results = []
    for item in items:
        if not isinstance(item, dict):
            raise TypeError('采集结果必须为公司对象')
        symbol = item.get('symbol')
        if (not isinstance(symbol, str) or symbol not in names
                or item.get('name') != names[symbol] or symbol in seen):
            raise ValueError('采集结果公司不属于绑定范围或重复')
        seen.add(symbol)
        try:
            ref = item.get('artifact')
            if not isinstance(ref, dict):
                raise TypeError('缺少采集产物，保留为待补')
            sha = _sha(ref.get('sha256'))
            path = root / 'artifacts' / f'{sha}.json'
            _safe_path(path)
            if not isinstance(ref.get('path'), str) or Path(ref['path']).resolve() != path.resolve():
                raise ValueError('采集产物路径未绑定到本工作目录')
            size = ref.get('bytes')
            if type(size) is not int or not 0 < size <= 64 * 1024 * 1024 or path.stat().st_size != size:
                raise ValueError('采集产物长度缺失或不一致')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != sha:
                raise ValueError('采集产物摘要不一致')
            payload = json.loads(raw)
            collection = payload.get('collection') if isinstance(payload, dict) else None
            if (not isinstance(collection, dict) or collection.get('symbol') != symbol
                    or collection.get('as_of') != manifest['as_of']):
                raise ValueError('原始采集产物公司或截点不一致')
            result = screen_structured_financials(collection)
            result.update(name=names[symbol], artifact=deepcopy(ref))
        except (OSError, ValueError, TypeError) as exc:
            logger.warning('财务初筛产物待补 %s: %s', symbol, exc)
            result = {'symbol': symbol, 'name': names[symbol], 'financial_numeric_status': 'pending_verification',
                      'error': str(exc), 'financial_verified': False, 'members_published': 0}
        results.append(result)
    return {'version': 'structured-financial-prescreen-v1', 'as_of': manifest['as_of'],
            'roster_sha256': manifest['roster_sha256'], 'input_sha256': manifest['input_sha256'],
            'summary': {'total': len(roster), 'attempted': len(items), 'not_attempted': len(roster) - len(items),
                        'numeric_passed': sum(row['financial_numeric_status'] == 'passed' for row in results),
                        'numeric_failed': sum(row['financial_numeric_status'] == 'failed' for row in results),
                        'pending_verification': sum(row['financial_numeric_status'] == 'pending_verification' for row in results)},
            'items': results, 'source_stopped': bool(summary.get('source_stopped')), 'financial_verified': False,
            'formal_screening_enabled': False, 'members_published': 0,
            'notice': '仅三年及TTM盈利的财务数值初筛，未覆盖全部资格或两池风格条件'}


@click.command('stock-financial-screen')
@click.option('--input', 'input_path', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path), required=True)
def structured_screen_command(input_path: Path, output: Path) -> None:
    """离线复算已保存财务产物，数据不足单列，不请求网络或更新成员。"""
    try:
        _safe_path(output.absolute())
        if output.resolve().is_relative_to(input_path.resolve().parent):
            raise ValueError('初筛输出不能覆盖采集工作目录中的任何文件')
        result = screen_saved_batch(input_path)
        _write_json(output, result)
    except (OSError, ValueError, TypeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"财务数值初筛：{result['summary']}；不代表入池。结果：{output}")
