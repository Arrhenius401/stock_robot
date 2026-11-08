"""只读选择已保存财务任务，重算并绑定可审计的公司来源。"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from radar.stocks.financial_task import (
    read_financial_artifact,
    safe_financial_result,
    verify_financial_payload,
)
from radar.stocks.research_store import StockResearchError, _id, _symbol
from radar.stocks.structured_screen import RULE_VERSION

logger = logging.getLogger(__name__)


def financial_status_columns(financial: Any) -> dict[str, Any]:
    """交付表分别取盈利状态和现金比率状态，不能互相顶替。"""
    view = financial if isinstance(financial, dict) else {}
    cash = view.get('cash_flow_reference')
    cash = cash if isinstance(cash, dict) else {}
    return {'financial_numeric': view.get('financial_numeric_status', ''),
            'cash_ratio': cash.get('ratio'),
            'cash_ratio_status': cash.get('reference_condition_status')}


def type_specific_metrics(collection: Any, as_of: date) -> dict[str, Any]:
    """从已保存财务原文提取发行人类型分支需要的年度指标。"""
    records = collection.get('records') if isinstance(collection, dict) else None
    main = records.get('main') if isinstance(records, dict) else None
    rows = [row for row in main if isinstance(row, dict) and isinstance(row.get('REPORT_DATE'), str)] if isinstance(main, list) else []
    annual = []
    for row in rows:
        try:
            day = date.fromisoformat(row['REPORT_DATE'][:10])
        except ValueError:
            continue
        if day.month == 12 and day.day == 31 and day <= as_of:
            annual.append((day, row))
    if not annual:
        return {'status': 'pending_verification', 'reason': '缺少截点前完整年度财务指标'}
    day, row = max(annual, key=lambda item: item[0])

    def number(key: str) -> float | None:
        value = row.get(key)
        try:
            return float(value) if value not in (None, '') else None
        except (TypeError, ValueError):
            return None

    npl = number('NON_PERFORMING_LOAN')
    loans = number('GROSSLOANS')
    return {'status': 'available', 'report_date': day.isoformat(),
            'bank_car': number('FIRST_ADEQUACY_RATIO'), 'bank_provision': number('BLDKBBL'),
            'bank_npl_ratio': (npl / loans) if npl is not None and loans not in (None, 0) else None,
            'sec_risk_coverage': number('RISK_COVERAGE'), 'sec_liquidity_coverage': number('LIQUIDITY_COVERAGE_RATIO'),
            'insurance_solvency': number('SOLVENCY_AR')}


@dataclass
class SavedFinancialTask:
    """同一选择结果供不同研究投影使用；原文不直接返回网页。"""
    source: dict[str, Any]
    payload: Any = None
    task: dict[str, Any] | None = None
    name: str = ''


def load_financial_task(db: Path, symbol: str, as_of: date) -> SavedFinancialTask:
    """按截点、创建顺序取最新包含公司的任务；坏来源不向旧任务回退。"""
    symbol = _symbol(symbol)
    source: dict[str, Any] = {'symbol': symbol, 'evaluation_as_of': as_of.isoformat(),
                              'status': 'unavailable', 'reason': '尚无可用的已保存财务任务',
                              'channel': 'structured_financial', 'financial': None}
    if not db.is_file():
        return SavedFinancialTask(source)
    with closing(sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('BEGIN')
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='stocks_update_runs'").fetchone() is None:
            return SavedFinancialTask(source)
        rows = connection.execute("SELECT rowid AS creation_order,* FROM stocks_update_runs WHERE kind='financial_review' AND target_date<=? ORDER BY target_date DESC,created_at DESC,rowid DESC",
                                  (as_of.isoformat(),)).fetchall()
    for row in rows:
        try:
            snapshot = json.loads(row['input_snapshot'])
            from radar.stocks.update_runs import StockUpdateStore

            # 范围损坏时无法证明任务与当前公司无关，不能据此跳过到旧通过结果。
            pools = json.loads(row['pool_ids'])
            validated = StockUpdateStore._financial_snapshot(snapshot, pools, row['target_date'])
            if validated != snapshot:
                raise StockResearchError('财务冻结范围格式不一致')
            companies = validated['companies']
            if not any(c['symbol'] == symbol for c in companies):
                continue
            # 来源身份不含路径或第三方错误，但包含完整冻结输入和最新公司结果的摘要。
            source.update(run_id=row['run_id'], snapshot_sha256=hashlib.sha256(row['input_snapshot'].encode()).hexdigest(),
                          task_identity_sha256=_id({key: row[key] for key in ('run_id', 'target_date', 'rule_version', 'pool_ids', 'created_at')}))
            results = json.loads(row['results'])
            item = next((entry for entry in reversed(results) if entry.get('scope') == 'financial:' + symbol), None)
            source['item_sha256'] = _id(item)
            source.update(as_of=row['target_date'], rule_version=RULE_VERSION if row['rule_version'] == RULE_VERSION else 'unrecognized',
                          created_at=row['created_at'], scope_as_of=validated['scope_as_of'], scope_sha256=validated['scope_sha256'])
            if row['rule_version'] != RULE_VERSION:
                source.update(status='version_mismatch', reason='已保存财务任务规则版本与当前版本不符，仅作参考')
                return SavedFinancialTask(source)
            if item is None:
                source.update(status='not_attempted', reason='最新财务任务尚未处理该公司，不能回退旧结果')
                return SavedFinancialTask(source)
            if item.get('status') not in {'completed', 'partial'}:
                raise StockResearchError('该公司财务任务未成功保存产物')
            payload = read_financial_artifact(item, db.parent/'stock-updates/artifacts')
            if not isinstance(payload, dict) or payload.get('symbol') != symbol or payload.get('as_of') != row['target_date']:
                raise StockResearchError('财务产物身份或截点不匹配')
            name = next(c['name'] for c in companies if c['symbol'] == symbol)
            current = row['target_date'] == as_of.isoformat()
            source.update(status='current' if current else 'historical',
                          reason='已校验结构化原始产物并按当前规则重算' if current else '历史财务结果仅供参考，不能提升本次截点的盈利核验')
            return SavedFinancialTask(source, payload, dict(row), name)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, StopIteration) as exc:
            logger.warning('已保存财务来源不可用 %s: %s', symbol, exc)
            source.update(run_id=row['run_id'], snapshot_sha256=hashlib.sha256(row['input_snapshot'].encode()).hexdigest(),
                          task_identity_sha256=_id({key: row[key] for key in ('run_id', 'target_date', 'rule_version', 'pool_ids', 'created_at')}),
                          status='invalid', reason='最新财务任务范围或产物校验失败，资料待补，不能回退旧结果', financial=None)
            return SavedFinancialTask(source)
    return SavedFinancialTask(source)


def load_financial_source(db: Path, symbol: str, as_of: date) -> dict[str, Any]:
    """盈利投影与成长共享任务选择；任一投影失败不授权回退旧来源。"""
    selected = load_financial_task(db, symbol, as_of)
    source = selected.source
    if selected.task is None:
        return source
    try:
        from radar.stocks.research_candidates import load_audit_clues

        identity_keys = ('status', 'run_id', 'snapshot_sha256', 'task_identity_sha256', 'item_sha256')
        before = {key: source.get(key) for key in identity_keys}
        financial = verify_financial_payload(selected.payload, selected.task, symbol)
        if financial.get('rule_version', RULE_VERSION) != RULE_VERSION:
            raise StockResearchError('财务重算规则版本不匹配')
        source['financial'] = safe_financial_result(financial, selected.name)
        source['financial']['type_metrics'] = type_specific_metrics(
            (selected.payload or {}).get('collection') if isinstance(selected.payload, dict) else None, as_of)
        audit = load_audit_clues(db, symbol, as_of)
        after = load_financial_task(db, symbol, as_of).source
        if {key: after.get(key) for key in identity_keys} != before:
            raise StockResearchError('读取审计依据时财务任务发生变化')
        source['audit_clues'] = audit
        if source['status'] == 'current' and financial.get('financial_numeric_status') == 'pending_verification':
            source['reason'] = '已校验保存的产物；结构化资料仍待补，按各项已知依据保留结论'
    except (OSError, ValueError, TypeError, KeyError, AttributeError, StopIteration) as exc:
        logger.warning('财务投影不可用 %s: %s', symbol, exc)
        source.update(status='invalid', reason='最新财务任务范围或产物校验失败，资料待补，不能回退旧结果', financial=None)
    return source
