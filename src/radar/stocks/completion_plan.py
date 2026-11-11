"""待核验补全计划：只读、确定性生成，不联网、不写库、不调用模型。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import Any

from radar.stocks.dividend_collect import _decode_source
from radar.stocks.dividend_source import load_dividend_source
from radar.stocks.dividend_structured import (
    evaluate_cninfo_records,
    evaluate_eastmoney_records,
)
from radar.stocks.financial_source import load_financial_source
from radar.stocks.financial_task import read_financial_artifact
from radar.stocks.turnover_source import load_turnover_source

PLAN_VERSION = 'completion-plan-v1'
PENDING = {'pending_verification', 'unavailable'}
CASH_FIELDS = ('netcash_operate', 'construct_long_asset', 'assign_dividend_profit', 'end_cash')


def _sha(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _years(as_of: date) -> list[int]:
    return list(range(as_of.year - 3, as_of.year))


def _dividend_annual(db: Path, symbol: str, as_of: date) -> list[dict[str, Any]]:
    source = load_dividend_source(db, symbol, as_of)
    result = source.get('result') or {}
    annual = result.get('annual')
    if isinstance(annual, list) and annual:
        return annual
    if not db.is_file():
        return []
    try:
        with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM stocks_update_runs WHERE kind='dividend_review' AND target_date<=? "
                "ORDER BY target_date DESC,created_at DESC,rowid DESC", (as_of.isoformat(),)).fetchall()
        for row in rows:
            items = json.loads(row['results'])
            item = next((entry for entry in reversed(items) if entry.get('scope') == 'dividend:' + symbol), None)
            if item is None:
                continue
            payload = read_financial_artifact(item, db.parent / 'stock-updates/artifacts')
            sources = payload['collection']['sources']
            candidates = []
            try:
                candidates.append(evaluate_eastmoney_records(
                    symbol, as_of,
                    _decode_source(sources.get('eastmoney_page'), 'eastmoney_page', symbol, as_of),
                    _decode_source(sources.get('eastmoney'), 'eastmoney', symbol, as_of)))
            except (ValueError, TypeError, KeyError):
                pass
            try:
                candidates.append(evaluate_cninfo_records(
                    symbol, as_of, _decode_source(sources.get('cninfo'), 'cninfo', symbol, as_of)))
            except (ValueError, TypeError, KeyError):
                pass
            if candidates:
                best = max(candidates, key=lambda value: sum(
                    1 for item in value.get('annual', []) if item.get('outcome') == 'passed'))
                return best.get('annual') or []
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return []
    return []


def _dividend_item(symbol: str, name: str, pools: list[str], as_of: date, db: Path) -> dict[str, Any]:
    annual = _dividend_annual(db, symbol, as_of)
    years = _years(as_of)
    passed = {int(row['year']) for row in annual
              if row.get('outcome') == 'passed' and isinstance(row.get('year'), int)}
    missing = [year for year in years if year not in passed]
    no_record = not annual or not any(row.get('outcome') == 'passed' for row in annual)
    return {
        'symbol': symbol, 'name': name, 'pools': pools, 'condition': 'dividend_history',
        'current': 'pending_verification', 'gap': {
            'years': missing,
            'missing': [f'{year} 年度 A 股现金分红实施记录' for year in missing],
            'existing_passed_years': sorted(passed),
            'no_record_all_years': no_record,
        },
        'source': {'kind': 'annual_report_profit_distribution',
                   'documents': [f'{year} 年年度报告 利润分配章节' for year in missing],
                   'classifier_rule': 'dividend-plan-2026-11-10-v1',
                   'disclosed_before': as_of.isoformat()},
        'expected': ('failed_if_confirmed_no_dividend_else_pending' if no_record
                     else 'passed_if_positive_else_failed'),
        'risk': ('三年均无实施记录，必须从年报利润分配章节独立判定；明确不分配应转 failed，'
                 '没有年报或表述不明保持 pending' if no_record else
                 '只补缺失年度；以年报利润分配章节独立判定，已有年度不回退，来源冲突保持 pending'),
        'proof_required': ['source_url', 'disclosed_on', 'content_sha256'],
    }


def _cash_item(symbol: str, name: str, pools: list[str], as_of: date, db: Path) -> dict[str, Any]:
    source = load_financial_source(db, symbol, as_of)
    financial = source.get('financial') or {}
    reason = str((financial.get('cash_special') or {}).get('reason') or '现金专项字段缺失')
    if '利息保障' in reason:
        fields = ['interest_coverage']
    else:
        fields = list(CASH_FIELDS)
    return {
        'symbol': symbol, 'name': name, 'pools': pools, 'condition': 'cash_review',
        'current': 'pending_verification', 'gap': {'years': _years(as_of), 'fields': fields, 'reason': reason},
        'source': {'kind': 'annual_report_cashflow',
                   'documents': [f'{year} 年年度报告现金流量表/主要财务指标' for year in _years(as_of)],
                   'disclosed_before': as_of.isoformat()},
        'expected': 'passed_or_failed_after_completion',
        'risk': '只补缺失字段；已有字段冲突或来源日期晚于截点则保持 pending',
        'proof_required': ['source_url', 'disclosed_on', 'content_sha256'],
    }


def _liquidity_item(symbol: str, name: str, pools: list[str], as_of: date, db: Path) -> dict[str, Any]:
    source = load_turnover_source(db, symbol, as_of)
    return {
        'symbol': symbol, 'name': name, 'pools': pools, 'condition': 'liquidity',
        'current': 'unavailable', 'gap': {'window': '60 个交易日（截至截点）', 'reason': source.get('reason') or '成交额来源缺失'},
        'source': {'kind': 'market_turnover_60d', 'documents': ['截至截点的 60 日成交额窗口'],
                   'disclosed_before': as_of.isoformat()},
        'expected': 'passed_or_unavailable',
        'risk': '补不到窗口就保持 unavailable，不猜',
        'proof_required': ['source_url', 'window_end', 'content_sha256'],
    }


ADAPTERS = {
    'dividend_history': _dividend_item,
    'cash_review': _cash_item,
    'liquidity': _liquidity_item,
}


def build_completion_plan(details: list[dict[str, Any]], db: Path, as_of: date, *,
                          pools: set[str] | None = None,
                          symbols: set[str] | None = None) -> dict[str, Any]:
    """从评估明细生成只读补全计划；相同输入输出相同 plan_sha256。"""
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    common_status: dict[str, str] = {}
    priority = {'passed': 0, 'pending_verification': 1, 'failed': 2}
    for row in details:
        if row.get('分类') != 'common' or not isinstance(row.get('代码'), str):
            continue
        outcome = row.get('结果')
        if outcome not in priority:
            outcome = 'pending_verification'
        current = common_status.setdefault(row['代码'], 'passed')
        if priority[outcome] > priority[current]:
            common_status[row['代码']] = outcome
    common_ok = {symbol for symbol, outcome in common_status.items() if outcome == 'passed'}
    failed_pairs = {(row.get('代码'), row.get('池')) for row in details
                    if row.get('分类') == 'style' and row.get('结果') == 'failed'}
    for row in details:
        if row.get('分类') != 'style' or row.get('结果') not in PENDING:
            continue
        pool, symbol, condition = row.get('池'), row.get('代码'), row.get('条件')
        if not isinstance(pool, str) or not isinstance(symbol, str) or not isinstance(condition, str):
            continue
        if symbol not in common_ok:
            continue
        # 同一池已有硬失败时，不再为这个池生成补全项；其他池仍可处理。
        if (symbol, pool) in failed_pairs:
            continue
        if pools and pool not in pools:
            continue
        if symbols and symbol not in symbols:
            continue
        key = (symbol, condition)
        item = grouped.setdefault(key, {'symbol': symbol, 'name': row.get('名称') or symbol,
                                        'pools': [], 'condition': condition})
        if pool not in item['pools']:
            item['pools'].append(pool)
    items = []
    for key in sorted(grouped):
        item = grouped[key]
        adapter = ADAPTERS.get(item['condition'])
        if adapter is None:
            items.append({**item, 'current': 'pending_verification',
                          'gap': {'reason': '暂无可自动补全的适配器'},
                          'source': {'kind': 'manual_review'}, 'expected': 'pending_verification',
                          'risk': '需要人工提供来源', 'proof_required': ['source_url', 'disclosed_on', 'content_sha256']})
            continue
        items.append(adapter(item['symbol'], item['name'], sorted(item['pools']), as_of, db))
    plan = {'version': PLAN_VERSION, 'as_of': as_of.isoformat(), 'pools': sorted(pools) if pools else [],
            'items': items, 'summary': {
                'items': len(items), 'symbols': len({item['symbol'] for item in items}),
                'by_condition': {condition: sum(1 for item in items if item['condition'] == condition)
                                 for condition in sorted({item['condition'] for item in items})}}}
    plan['plan_sha256'] = _sha(plan)
    return plan
