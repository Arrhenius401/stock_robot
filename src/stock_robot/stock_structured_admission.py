"""将校验后的结构化盈利数值接入独立只读共同资格报告。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from datetime import datetime, time
from pathlib import Path
from typing import Any

import click

from radar.stocks.admission import _BJ, _day, evaluate_common_admission
from radar.stocks.index_scope import POOL_INDEX_CODES
from radar.stocks.research_store import StockResearchStore
from radar.stocks.structured_collect import _safe_path
from stock_robot.dividend_research import _write_json
from stock_robot.stock_research import _protect_database
from stock_robot.stock_structured_financial import _companies, screen_saved_batch


def merge_profitability(manual: list[dict[str, Any]], financial: dict[str, Any], codes: set[str]) -> list[dict[str, Any]]:
    """只替换指定盈利条件；确定失败优先，缺失不能回退人工通过。"""
    numeric = {row['code']: row for row in financial.get('financial_numeric_checks', [])}
    checks = deepcopy(manual)
    for check in checks:
        code = check['code']
        if code not in codes:
            continue
        old = deepcopy(check)
        structured = deepcopy(numeric.get(code, {'code': code, 'outcome': 'pending_verification', 'reason': '采集产物缺失、损坏或尚未处理'}))
        # 两渠道确定冲突也保留已知失败，不能利用另一渠道通过将失败抹去。
        outcomes = {old['outcome'], structured['outcome']}
        outcome = ('pending_verification' if outcomes == {'passed', 'failed'}
                   else ('failed' if 'failed' in outcomes else structured['outcome']))
        check.clear()
        check.update(code=code, outcome=outcome, channel='structured_financial',
                     reason='两方正负冲突待核验；其他确定失败优先，缺失不回退人工通过',
                     manual_check=old, structured_check=structured)
    return checks


def _counts(items: list[dict[str, Any]]) -> dict[str, int]:
    return {'total': len(items), **{status: sum(row['common_status'] == status for row in items)
                                  for status in ('passed', 'failed', 'pending_verification')}}


def build_structured_admission(scope_path: Path, summary_path: Path, db: Path) -> dict[str, Any]:
    """固定范围及截点；不会写研究库、旧评估存储或成员。"""
    for path in (scope_path, summary_path, db):
        _safe_path(path.absolute())
    raw = scope_path.read_bytes()
    scope = json.loads(raw)
    manifest_path = summary_path.parent/'manifest.json'
    _safe_path(manifest_path.absolute())
    summary_raw, manifest_raw = summary_path.read_bytes(), manifest_path.read_bytes()
    financial = screen_saved_batch(summary_path)
    if summary_path.read_bytes() != summary_raw or manifest_path.read_bytes() != manifest_raw:
        raise ValueError('财务摘要或清单快照在校验期间发生变化，请重试')
    manifest = json.loads(manifest_raw)
    scope_sha = hashlib.sha256(raw).hexdigest()
    if scope_sha != financial['input_sha256']:
        raise ValueError('指数范围原始SHA与财务输入绑定不一致')
    if not isinstance(scope, dict) or scope.get('scope_complete') is not True or scope.get('status') != 'complete':
        raise ValueError('指数范围不完整')
    cutoff, scope_day = _day(financial['as_of']), _day(scope.get('as_of'))
    if cutoff is None or scope_day is None or scope_day > cutoff:
        raise ValueError('范围日期必须不晚于财务截点')
    companies = scope.get('companies')
    roster = _companies(companies)
    if roster != _companies(manifest.get('companies')) or not isinstance(companies, list) or len(companies) != len(roster):
        raise ValueError('范围与财务绑定公司集合或名称不一致')
    pools = scope.get('pools')
    if not isinstance(pools, dict) or set(pools) != set(POOL_INDEX_CODES):
        raise ValueError('指数池配置不一致')
    allowed = set().union(*map(set, POOL_INDEX_CODES.values()))
    for company in companies:
        sources, memberships = company.get('source_indexes'), company.get('pool_ids')
        if (not isinstance(sources, list) or not sources or any(not isinstance(code, str) or code not in allowed for code in sources)
                or len(sources) != len(set(sources)) or not isinstance(memberships, list)):
            raise ValueError('公司指数来源无效')
        expected = {pool for pool, codes in POOL_INDEX_CODES.items() if set(codes).intersection(sources)}
        if len(memberships) != len(expected) or set(memberships) != expected:
            raise ValueError('公司池归属与指数配置不一致')
    for pool, codes in POOL_INDEX_CODES.items():
        row = pools[pool]
        expected_symbols = {c['symbol'] for c in companies if pool in c['pool_ids']}
        if (not isinstance(row, dict) or row.get('complete') is not True or row.get('status') != 'complete'
                or row.get('index_codes') != list(codes) or not isinstance(row.get('companies'), list)
                or len(row['companies']) != len(expected_symbols) or set(row['companies']) != expected_symbols):
            raise ValueError('池范围状态、指数组合或公司清单不一致')
    store = StockResearchStore(db, read_only=True, clock=lambda: datetime.combine(cutoff, time.max, tzinfo=_BJ))
    numeric = {row['symbol']: row for row in financial['items']}
    codes = {f'annual_{metric}_{year}' for year in range(cutoff.year-3, cutoff.year)
             for metric in ('parent_profit', 'deducted_profit')} | {'ttm_parent_profit', 'ttm_deducted_profit'}
    items = []
    for company in companies:
        symbol = company['symbol']
        before = store.get_company(symbol)
        evidence = store.list_evidence(symbol) if before else []
        manual = evaluate_common_admission(symbol, evidence, cutoff)
        after = store.get_company(symbol)
        if before != after:
            raise ValueError('评估期间研究公司资料发生变化，请重试')
        computed = numeric.get(symbol, {'symbol': symbol, 'financial_numeric_status': 'pending_verification', 'not_attempted': True})
        checks = merge_profitability(manual['checks'], computed, codes)
        outcomes = {row['outcome'] for row in checks}
        status = 'failed' if 'failed' in outcomes or manual['common_status'] == 'failed' else ('pending_verification' if 'pending_verification' in outcomes else 'passed')
        items.append({'symbol': symbol, 'name': company['name'], 'pool_ids': deepcopy(company['pool_ids']),
                      'source_indexes': deepcopy(company['source_indexes']), 'manual_common': manual,
                      'structured_financial': computed, 'checks': checks, 'common_status': status,
                      'admission_status': 'excluded' if status == 'failed' else 'pending_verification',
                      'financial_verified': False, 'formal_screening_enabled': False, 'members_published': 0})
    if summary_path.read_bytes() != summary_raw or manifest_path.read_bytes() != manifest_raw:
        raise ValueError('财务摘要或清单快照在报告构建期间发生变化，请重试')
    return {'version': 'structured-common-research-v1', 'calculation_channel': 'verified_structured_profitability_plus_manual_common',
            'as_of': cutoff.isoformat(), 'scope_as_of': scope_day.isoformat(), 'scope_sha256': scope_sha,
            'financial_roster_sha256': financial['roster_sha256'], 'financial_summary_sha256': hashlib.sha256(summary_raw).hexdigest(),
            'summary': _counts(items), 'pools': {pool: _counts([row for row in items if pool in row['pool_ids']]) for pool in POOL_INDEX_CODES},
            'items': items, 'research_only': True, 'financial_verified': False,
            'formal_screening_enabled': False, 'members_published': 0}


def _protect_output(scope: Path, summary: Path, db: Path, output: Path) -> None:
    _safe_path(output.absolute())
    _protect_database(db, output)
    target = output.resolve()
    project = Path(__file__).resolve().parents[2]
    if (target == scope.resolve() or target.is_relative_to(summary.resolve().parent)
            or any(target.is_relative_to(project/name) for name in ('src', 'scripts', 'tests', '.git', '.venv', '.stock_robot'))
            or target.suffix.lower() != '.json'):
        raise ValueError('输出不能覆盖关键输入、财务工作目录或项目代码/缓存，须为独立JSON文件')


@click.command('stock-structured-admission')
@click.option('--scope', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--financial-summary', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--db', type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option('--output', type=click.Path(dir_okay=False, path_type=Path), required=True)
def structured_admission_command(scope: Path, financial_summary: Path, db: Path, output: Path) -> None:
    """只读既定指数范围与财务产物，生成独立共同资格研究报告。"""
    try:
        _protect_output(scope, financial_summary, db, output)
        result = build_structured_admission(scope, financial_summary, db)
        _write_json(output, result)
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"共同资格研究：{result['summary']}；未发布成员。结果：{output}")
