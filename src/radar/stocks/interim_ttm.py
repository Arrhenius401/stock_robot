"""用可追踪的年度和季度累计建议计算TTM，结果仍需审核。"""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from radar.stocks.admission import _BJ, _amount, _day, _groups, _source_valid
from radar.stocks.dividend_payout import _sum_amounts
from radar.stocks.research_store import StockResearchError, _json, _sha, _symbol, _url

TTM_VERSION = 'interim-ttm-2026-10-15-v1'
_KINDS = ('financial_proposal', 'quarterly_financial_proposal', 'quarterly_financial_document', 'admission_reporting_coverage')
_METRICS = {'parent_net_profit': ('parent_profit_yuan', 'reported_parent'),
            'deducted_parent_net_profit': ('deducted_parent_profit_yuan', 'reported_parent'),
            'revenue': ('revenue_yuan', 'consolidated'),
            'operating_cash_flow': ('operating_cash_flow_yuan', 'consolidated')}


def _money(value: Decimal) -> str:
    text = format(value, 'f')
    text = text.rstrip('0').rstrip('.') if '.' in text else text
    return ('0' if value == 0 else text) if len(text) <= 100 else str(value)


def _review_id(row: dict[str, Any]) -> int | None:
    review = row.get('latest_review')
    if review is None:
        return None
    identifier = review.get('review_id') if isinstance(review, dict) else None
    if type(identifier) is not int or identifier < 1:
        raise StockResearchError('上游审核身份无效')
    return identifier


def _base_valid(row: dict[str, Any], symbol: str, cutoff: date, *, require_hash: bool = True) -> bool:
    if row.get('symbol') != symbol:
        return False
    try:
        _sha(row.get('evidence_id'))
        if require_hash:
            _sha(row.get('content_sha256'))
        _url(row.get('source_url'))
    except StockResearchError:
        return False
    disclosed = _day(row.get('disclosed_on'))
    payload = row.get('payload')
    return (disclosed is not None and disclosed <= cutoff and isinstance(payload, dict)
            and payload.get('symbol', symbol) == symbol)


def _period(fields: dict[str, Any]) -> date | None:
    year, months = fields.get('fiscal_year'), fields.get('period_months')
    if type(year) is not int or not 1 <= year <= 9999 or type(months) is not int or months not in (3, 6, 9):
        return None
    end = date(year, months, 31 if months == 3 else 30)
    return end if _day(fields.get('period_end')) == end else None


def _quarter_identity(fields: dict[str, Any], origin: dict[str, Any]) -> date | None:
    current, reported = _period(fields), _period(origin)
    basis = fields.get('comparison_basis')
    if current is None or reported is None or basis not in ('current', 'previous_unadjusted', 'previous_adjusted_after'):
        return None
    expected_year = reported.year if basis == 'current' else reported.year - 1
    return reported if current.year == expected_year and current.month == reported.month else None


def _known_period(row: dict[str, Any], symbol: str, cutoff: date) -> date | None:
    if not _base_valid(row, symbol, cutoff, require_hash=row['kind'] != 'quarterly_financial_document'):
        return None
    payload = row['payload']
    fields = payload.get('fields', payload)
    if not isinstance(fields, dict) or fields.get('symbol', symbol) != symbol:
        return None
    if row['kind'] == 'admission_reporting_coverage':
        end = _day(payload.get('period_end'))
        if end is None or end.month not in (3, 6, 9) or end.day != (31 if end.month == 3 else 30):
            return None
    elif row['kind'] == 'quarterly_financial_proposal':
        origin = payload.get('origin')
        end = _quarter_identity(fields, origin) if isinstance(origin, dict) else None
    elif row['kind'] == 'financial_proposal':
        year = fields.get('fiscal_year')
        end = date(year, 12, 31) if type(year) is int and 1 <= year <= 9999 else None
    else:
        end = _period(fields)
    disclosed = _day(row.get('disclosed_on'))
    return end if end is not None and disclosed is not None and end < disclosed <= cutoff else None


def _parse(row: dict[str, Any], symbol: str, cutoff: date) -> tuple[dict[str, Any], Decimal] | None:
    if not _base_valid(row, symbol, cutoff) or row.get('confirmable') is not True:
        return None
    review = row.get('latest_review')
    if review is None:
        if row.get('review_status') != 'pending_verification':
            return None
    elif not _source_valid(row, symbol, datetime.combine(cutoff, time.max, tzinfo=_BJ)):
        return None
    payload = row['payload']
    fields, origin = payload.get('fields'), payload.get('origin')
    if not isinstance(fields, dict) or not isinstance(origin, dict):
        return None
    if fields.get('symbol') != symbol or origin.get('symbol', symbol) != symbol:
        return None
    if fields.get('status') != 'matched_template' or origin.get('status') != 'matched_template':
        return None
    if any(part.get('source_url') != row['source_url'] or part.get('disclosed_on') != row['disclosed_on'] for part in (fields, origin)):
        return None
    if (origin.get('file_sha256') != row['content_sha256']
            or fields.get('file_sha256', row['content_sha256']) != row['content_sha256']):
        return None
    metric, year = fields.get('metric'), fields.get('fiscal_year')
    if not isinstance(metric, str) or metric not in _METRICS or type(year) is not int or not 1 <= year <= 9999:
        return None
    if fields.get('currency') != 'CNY' or fields.get('scope') != _METRICS[metric][1]:
        return None
    end = date(year, 12, 31)
    if row['kind'] == 'quarterly_financial_proposal':
        quarter = _quarter_identity(fields, origin)
        if quarter is None or fields.get('period_basis') != 'year_to_date':
            return None
        end = quarter
    disclosed, amount = _day(row['disclosed_on']), _amount(fields.get('amount_yuan'))
    if disclosed is None or not end < disclosed <= cutoff or amount is None:
        return None
    try:
        reference = {'evidence_id': row['evidence_id'], 'review_id': _review_id(row), 'source_url': row['source_url'],
                     'content_sha256': row['content_sha256'], 'disclosed_on': row['disclosed_on'],
                     'page_number': fields.get('page_number'), 'evidence_excerpt': fields.get('evidence_excerpt'),
                     'source_unit': fields.get('source_unit'), 'amount_yuan': fields.get('amount_yuan'),
                     'original_amount_yuan': fields.get('amount_yuan'), 'canonical_amount_yuan': _money(amount),
                     'metric': metric, 'fiscal_year': year, 'review_status': row.get('review_status'),
                     'latest_review': deepcopy(review), 'fields': deepcopy(fields), 'origin': deepcopy(origin)}
        return json.loads(_json(reference)), amount
    except StockResearchError:
        return None


def prepare_ttm(symbol: str, evidence: list[dict[str, Any]], as_of: date) -> dict[str, Any]:
    """保留最新已知期间，未知/冲突不回退，只有显式覆盖审核才能标记最新。"""
    cutoff = _day(as_of)
    result: dict[str, Any] = {'version': TTM_VERSION, 'symbol': symbol if isinstance(symbol, str) else None,
        'as_of': cutoff.isoformat() if cutoff else None, 'period_end': None, 'period_months': None,
        'disclosed_on': None, 'metrics': {key: {'status': 'missing', 'amount_yuan': None, 'sources': [], 'components': []}
                                          for key, _scope in _METRICS.values()},
        'latest_announced': False, 'coverage_evidence_ids': [], 'dependency_inventory': [], 'inventory_complete': True,
        'unresolved': [], 'requires_review': True, 'formal_screening_enabled': False}
    try:
        _symbol(symbol)
    except StockResearchError:
        result['unresolved'].append('公司代码无效')
        return result
    if cutoff is None or cutoff.year < 2:
        result['unresolved'].append('研究截点无效')
        return result
    rows = [row for row in evidence if isinstance(row, dict) and row.get('symbol') == symbol
            and isinstance(row.get('kind'), str) and row['kind'] in _KINDS] if isinstance(evidence, list) else []
    seen: set[str] = set()
    periods: list[date] = []
    values: dict[tuple[int, int, str], list[tuple[dict[str, Any], Decimal]]] = {}
    for row in rows:
        try:
            identifier = _sha(row.get('evidence_id'))
            if identifier in seen:
                raise StockResearchError('上游身份重复')
            seen.add(identifier)
            result['dependency_inventory'].append({'evidence_id': identifier, 'review_id': _review_id(row)})
        except StockResearchError:
            result['inventory_complete'] = False
            result['unresolved'].append('上游身份或审核版本无法唯一绑定')
        period = _known_period(row, symbol, cutoff)
        if period is not None:
            periods.append(period)
        if row['kind'] not in ('financial_proposal', 'quarterly_financial_proposal'):
            continue
        parsed = _parse(row, symbol, cutoff)
        if parsed is None:
            result['unresolved'].append(f'上游建议{row.get("evidence_id")}未采用：身份、期间、来源、口径、数值或审核状态待核验')
            continue
        reference, amount = parsed
        fields = reference['fields']
        months = 12 if row['kind'] == 'financial_proposal' else fields['period_months']
        values.setdefault((fields['fiscal_year'], months, fields['metric']), []).append((reference, amount))
    result['dependency_inventory'].sort(key=lambda entry: entry['evidence_id'])
    if not periods:
        result['unresolved'].append('缺少合法已披露的季度报告期间，未用年度替代TTM')
        return result
    period = max(periods)
    result.update(period_end=period.isoformat(), period_months=period.month)
    if period.month == 12:
        result['unresolved'].append('最新已知期间是完整年度，不能把较旧季度称最新，仍需独立最新TTM或新季度资料')
        return result
    all_sources: list[dict[str, Any]] = []
    for metric, (key, _scope) in _METRICS.items():
        components = []
        numbers = []
        sources = []
        for role, year, months in [('prior_fy', period.year-1, 12), ('current_ytd', period.year, period.month),
                                   ('prior_ytd', period.year-1, period.month)]:
            entries = sorted(values.get((year, months, metric), []), key=lambda entry: entry[0]['evidence_id'])
            distinct = {value for _ref, value in entries}
            status = 'available' if len(distinct) == 1 else ('conflict' if distinct else 'missing')
            number = next(iter(distinct)) if len(distinct) == 1 else None
            refs = [ref for ref, _value in entries]
            components.append({'role': role, 'fiscal_year': year, 'period_months': months, 'status': status,
                               'amount_yuan': _money(number) if number is not None else None, 'sources': refs})
            numbers.append(number)
            sources.extend(refs)
        statuses = {item['status'] for item in components}
        status = 'conflict' if 'conflict' in statuses else ('available' if statuses == {'available'} else 'missing')
        amount = None
        if status == 'available' and all(number is not None for number in numbers):
            first, current, previous = numbers
            if first is not None and current is not None and previous is not None:
                amount = _money(_sum_amounts([first, current, previous.copy_negate()]))
        result['metrics'][key] = {'status': status, 'amount_yuan': amount, 'sources': sources, 'components': components,
                                  'formula': 'prior_fy + current_ytd - prior_ytd'}
        all_sources.extend(sources)
        if status != 'available':
            result['unresolved'].append(f'{key}：' + ('分量冲突，未选择有利版本' if status == 'conflict' else '累计或年度分量缺失'))
    result['disclosed_on'] = max((source['disclosed_on'] for source in all_sources), default=None)
    group, ids, reason = _groups(symbol, rows, datetime.combine(cutoff, time.max, tzinfo=_BJ), kinds=('reporting_coverage',))['reporting_coverage']
    result['coverage_evidence_ids'] = ids
    valid_coverage = (group is not None and group.get('period_end') == period.isoformat()
                      and group.get('coverage_through') == cutoff.isoformat() and group.get('report_types_complete') is True
                      and group.get('scope_comparable') is True and isinstance(group.get('basis'), str) and bool(group['basis'].strip()))
    complete_profit = all(result['metrics'][key]['status'] == 'available' for key in ('parent_profit_yuan', 'deducted_parent_profit_yuan'))
    result['latest_announced'] = bool(valid_coverage and complete_profit and result['inventory_complete'])
    if not result['latest_announced']:
        result['unresolved'].append(reason or '最新报告覆盖、可比口径或归母/扣非分量尚未完整核验')
    result['unresolved'] = list(dict.fromkeys(result['unresolved']))
    return result
