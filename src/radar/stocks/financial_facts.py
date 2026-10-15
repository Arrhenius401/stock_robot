"""将带原文追踪的年报建议整理为待审核规范组，不确认财务事实。"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from radar.stocks.admission import _BJ, _amount, _choice, _day, _groups, _source_valid
from radar.stocks.research_store import StockResearchError, _json, _sha, _symbol, _url

FINANCIAL_FACTS_VERSION = 'financial-facts-2026-10-15-v1'
_METRICS = {
    'parent_net_profit': ('parent_profit_yuan', 'reported_parent'),
    'deducted_parent_net_profit': ('deducted_parent_profit_yuan', 'reported_parent'),
    'operating_cash_flow': ('operating_cash_flow_yuan', 'consolidated'),
    'revenue': ('revenue_yuan', 'consolidated'),
}


def _review_id(row: dict[str, Any]) -> int | None:
    review = row.get('latest_review')
    if review is None:
        return None
    identifier = review.get('review_id') if isinstance(review, dict) else None
    if type(identifier) is not int or identifier < 1:
        raise StockResearchError('上游审核身份无效')
    return identifier


def _money(value: Decimal) -> str:
    if value == 0:
        return '0'
    text = format(value, 'f')
    text = text.rstrip('0').rstrip('.') if '.' in text else text
    # 规范值仍须处于下游数值解析域，科学计数保留全部有效位而不做normalize舍入。
    return str(value) if len(text) > 100 else text


def _valid_proposal(row: dict[str, Any], symbol: str, cutoff: date, years: list[int]) -> tuple[dict[str, Any], Decimal] | None:
    if row.get('symbol') != symbol or row.get('confirmable') is not True:
        return None
    if row.get('review_status') not in ('pending_verification', 'confirmed'):
        return None
    review = row.get('latest_review')
    if review is None:
        if row.get('review_status') != 'pending_verification':
            return None
    elif not _source_valid(row, symbol, datetime.combine(cutoff, time.max, tzinfo=_BJ)):
        return None
    try:
        _sha(row.get('evidence_id'))
        _sha(row.get('content_sha256'))
        _url(row.get('source_url'))
        _review_id(row)
    except StockResearchError:
        return None
    payload = row.get('payload')
    if not isinstance(payload, dict) or payload.get('symbol', symbol) != symbol:
        return None
    fields, origin = payload.get('fields'), payload.get('origin')
    if not isinstance(fields, dict) or not isinstance(origin, dict):
        return None
    if fields.get('symbol') != symbol or origin.get('symbol', symbol) != symbol:
        return None
    if fields.get('status') != 'matched_template' or origin.get('status') != 'matched_template':
        return None
    if (fields.get('source_url') != row.get('source_url') or origin.get('source_url') != row.get('source_url')
            or fields.get('disclosed_on') != row.get('disclosed_on') or origin.get('disclosed_on') != row.get('disclosed_on')
            or origin.get('file_sha256') != row.get('content_sha256')
            or fields.get('file_sha256', row.get('content_sha256')) != row.get('content_sha256')):
        return None
    metric, year = fields.get('metric'), fields.get('fiscal_year')
    if not isinstance(metric, str) or metric not in _METRICS or type(year) is not int or year not in years:
        return None
    if fields.get('currency') != 'CNY' or fields.get('scope') != _METRICS[metric][1]:
        return None
    disclosed = _day(row.get('disclosed_on'))
    amount = _amount(fields.get('amount_yuan'))
    if disclosed is None or not date(year, 12, 31) < disclosed <= cutoff or amount is None:
        return None
    reference = {
        'evidence_id': row['evidence_id'], 'review_id': _review_id(row), 'source_url': row['source_url'],
        'content_sha256': row['content_sha256'], 'disclosed_on': disclosed.isoformat(),
        'page_number': fields.get('page_number'), 'evidence_excerpt': fields.get('evidence_excerpt'),
        'source_unit': fields.get('source_unit'), 'amount_yuan': fields.get('amount_yuan'),
        'canonical_amount_yuan': _money(amount),
        'original_amount_yuan': fields.get('amount_yuan'), 'metric': metric, 'fiscal_year': year,
        'review_status': row.get('review_status'), 'latest_review': deepcopy(review),
        'raw_comparison_values': deepcopy(fields.get('raw_comparison_values')),
    }
    # 额外原始字段也留在原文引用中，未知值不会补成金额。
    reference['fields'] = deepcopy(fields)
    reference['origin'] = deepcopy(origin)
    try:
        reference = json.loads(_json(reference))
    except StockResearchError:
        return None
    return reference, amount


def prepare_financial_facts(symbol: str, evidence: list[dict[str, Any]], as_of: date) -> dict[str, Any]:
    """汇总年度建议并保留完整上游集合，输出仍须逐事实组显式审核。"""
    cutoff = _day(as_of)
    result: dict[str, Any] = {
        'version': FINANCIAL_FACTS_VERSION, 'symbol': symbol if isinstance(symbol, str) else None,
        'as_of': cutoff.isoformat() if cutoff else None, 'facts': [], 'annual_summary': [], 'inventory': [],
        'unresolved': [], 'requires_review': True, 'financial_verified': False,
        'formal_screening_enabled': False, 'members_published': 0,
    }
    try:
        _symbol(symbol)
    except StockResearchError:
        result['unresolved'].append('公司代码无效')
        return result
    if cutoff is None or cutoff.year < 4:
        result['unresolved'].append('研究截点无效或不能覆盖三个完整年度')
        return result
    years = list(range(cutoff.year - 3, cutoff.year))
    candidates: dict[tuple[int, str], list[tuple[dict[str, Any], Decimal]]] = {}
    inventory: list[dict[str, Any]] = []
    seen: set[str] = set()
    inventory_complete = True
    for row in evidence if isinstance(evidence, list) else []:
        if not isinstance(row, dict) or row.get('symbol') != symbol or row.get('kind') != 'financial_proposal':
            continue
        try:
            identifier = _sha(row.get('evidence_id'))
            review_id = _review_id(row)
            if identifier in seen:
                raise StockResearchError('同公司上游建议身份重复')
            seen.add(identifier)
            inventory.append({'evidence_id': identifier, 'review_id': review_id})
        except StockResearchError:
            inventory_complete = False
            result['unresolved'].append('存在无法绑定身份的上游财务建议或审核，不生成可保存规范组')
            continue
        parsed = _valid_proposal(row, symbol, cutoff, years)
        if parsed is None:
            result['unresolved'].append(f'上游建议{identifier}未采用：身份、来源、期间、数值、币种、口径或审核状态待核验')
            continue
        reference, amount = parsed
        candidates.setdefault((reference['fiscal_year'], reference['metric']), []).append((reference, amount))
    inventory.sort(key=lambda row: row['evidence_id'])
    result['inventory'] = inventory
    summary: dict[int, dict[str, Any]] = {}
    for year in years:
        annual: dict[str, Any] = {'year': year}
        for metric, (field, _scope) in _METRICS.items():
            entries = candidates.get((year, metric), [])
            entries.sort(key=lambda entry: entry[0]['evidence_id'])
            values = {value for _source, value in entries}
            status = 'available' if len(values) == 1 else ('conflict' if values else 'missing')
            amount = _money(next(iter(values))) if len(values) == 1 else None
            annual[field] = {'status': status, 'amount_yuan': amount,
                             'evidence_ids': [source['evidence_id'] for source, _value in entries],
                             'sources': [source for source, _value in entries]}
            if status != 'available':
                result['unresolved'].append(f'{year}年{metric}：' + ('原文数值冲突，未选择版本' if status == 'conflict' else '缺少合法年度建议'))
        summary[year] = annual
    result['annual_summary'] = list(summary.values())
    security_groups = _groups(symbol, evidence, datetime.combine(cutoff, time.max, tzinfo=_BJ))
    security, security_ids, _reason = security_groups['security']
    issuer = security.get('issuer_type') if security else None
    security_dependency = None
    if _choice(issuer, {'ordinary', 'bank', 'securities', 'insurance'}):
        for identifier in security_ids:
            row = next((row for row in evidence if isinstance(row, dict) and row.get('evidence_id') == identifier), None)
            if row is None:
                continue
            try:
                review_id = _review_id(row)
                if review_id is not None:
                    security_dependency = {'evidence_id': identifier, 'review_id': review_id}
                    break
            except StockResearchError:
                continue
    if security_dependency is None:
        issuer = None
    if not inventory_complete:
        result['unresolved'] = list(dict.fromkeys(result['unresolved']))
        return result
    definitions = (
        ('admission_profitability', ('parent_profit_yuan', 'deducted_parent_profit_yuan')),
        ('style_cash_flow', ('operating_cash_flow_yuan',)),
    )
    for kind, fields in definitions:
        source_map = {f'{field}:{year}': summary[year][field]['sources'] for year in years for field in fields}
        all_sources = [source for sources in source_map.values() for source in sources]
        if not all_sources:
            continue
        annual_rows = []
        for year in years:
            year_sources = [source for field in fields for source in summary[year][field]['sources']]
            disclosed = max((source['disclosed_on'] for source in year_sources), default=None)
            annual_rows.append({'year': year, **{field: summary[year][field]['amount_yuan'] for field in fields}, 'disclosed_on': disclosed})
        payload: dict[str, Any] = {
            'currency': 'CNY', 'scope': 'company_parent' if kind == 'admission_profitability' else 'company_consolidated',
            'annual': annual_rows, 'field_sources': source_map,
            '_financial_dependencies': {'version': 1, 'as_of': cutoff.isoformat(), 'inventory': deepcopy(inventory),
                                        'security': None if kind == 'admission_profitability' else deepcopy(security_dependency)},
        }
        if kind == 'admission_profitability':
            payload['ttm'] = None
        else:
            payload.update(issuer_type=issuer, parent_profit_scope_reconciled=False)
        primary = max(all_sources, key=lambda source: (source['disclosed_on'], source['evidence_id']))
        result['facts'].append({'kind': kind, 'payload': payload, 'source_url': primary['source_url'],
                                'disclosed_on': primary['disclosed_on'], 'content_sha256': primary['content_sha256']})
    result['unresolved'].extend(['规范映射须审核归母/合并范围，不能由机器建议代替确认', '缺少独立最新TTM证据，未以年报回填'])
    result['unresolved'] = list(dict.fromkeys(result['unresolved']))
    return result
