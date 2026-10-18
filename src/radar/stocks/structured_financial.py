"""适配统一财务表；保留结构化来源身份，不生成PDF模板或审核记录。"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, localcontext
from typing import Any

from radar.stocks.admission import _amount, _day
from radar.stocks.research_store import _symbol

VERSION = 'eastmoney-structured-financial-v1'
_BASE = 'https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/'
_URLS = {'profit': _BASE + 'lrbAjaxNew', 'cashflow': _BASE + 'xjllbAjaxNew',
         'main': 'https://datacenter.eastmoney.com/securities/api/data/get'}
_FIELDS = {'parent_profit_yuan': ('profit', 'PARENT_NETPROFIT', 'reported_parent'),
           'deducted_parent_profit_yuan': ('profit', 'DEDUCT_PARENT_NETPROFIT', 'reported_parent'),
           'revenue_yuan': ('profit', 'OPERATE_INCOME', 'consolidated'),
           'operating_cash_flow_yuan': ('cashflow', 'NETCASH_OPERATE', 'consolidated')}
_MAIN = {'parent_profit_yuan': 'PARENTNETPROFIT', 'deducted_parent_profit_yuan': 'KCFJCXSYJLR'}


def _source_day(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _money(value: Decimal) -> str:
    text = format(value, 'f')
    return ('0' if value == 0 else text.rstrip('0').rstrip('.')) if '.' in text else text


def _metadata(value: Any, table: str) -> bool:
    if not isinstance(value, dict) or value.get('source_url') != _URLS[table]:
        return False
    sha = value.get('content_sha256')
    if not isinstance(sha, str) or re.fullmatch('[0-9a-f]{64}', sha) is None:
        return False
    if type(value.get('bytes')) is not int or value['bytes'] <= 0:
        return False
    try:
        fetched = datetime.fromisoformat(value['fetched_at'])
        return fetched.tzinfo is not None and fetched.utcoffset() is not None
    except (ValueError, TypeError, KeyError):
        return False


def _identity(row: dict[str, Any], symbol: str) -> bool:
    expected = symbol + ('.SH' if symbol.startswith('6') else '.SZ')
    identities = [row[key] for key in ('SECURITY_CODE', 'SECUCODE') if key in row]
    return bool(identities) and all(value == (expected if '.' in str(value) else symbol) for value in identities)


def normalize_structured_financials(collection: dict[str, Any]) -> dict[str, Any]:
    """仅整理当前采集的可追溯数值；完整性不等于公司或股票池资格通过。"""
    symbol = _symbol(collection.get('symbol'))
    cutoff = _day(collection.get('as_of')) if isinstance(collection.get('as_of'), str) else None
    if cutoff is None or cutoff.year < 4:
        raise ValueError('结构化财务截点必须为可覆盖三年窗口的ISO日期文本')
    records, metadata = collection.get('records', {}), collection.get('sources', {})
    if not isinstance(records, dict) or not isinstance(metadata, dict):
        raise TypeError('结构化财务缺少报表及来源对象')
    result: dict[str, Any] = {
        'version': VERSION, 'symbol': symbol, 'as_of': cutoff.isoformat(),
        'captured_at': collection.get('captured_at'), 'point_in_time_verified': False,
        'annual': [], 'ttm': {}, 'cross_source_differences': [], 'diagnostics': [],
        'data_complete': False, 'financial_verified': False, 'formal_screening_enabled': False,
        'members_published': 0, 'source_family': 'eastmoney_structured',
        'cash_flow_ratio': {'applicability': 'requires_issuer_classification', 'ratio': None},
    }
    indexed: dict[tuple[str, str], list[tuple[dict[str, Any], bool]]] = {}
    observed: set[date] = set()
    for value in collection.get('targets', []):
        day = _day(value)
        if day is not None and day <= cutoff:
            observed.add(day)
    for table in _URLS:
        rows = records.get(table, [])
        if not isinstance(rows, list):
            result['diagnostics'].append(f'{table}报表不是记录列表')
            continue
        source_ok = _metadata(metadata.get(table), table)
        if not source_ok:
            result['diagnostics'].append(f'{table}来源端点或响应摘要、抓取时间无效')
        for row in rows:
            if not isinstance(row, dict):
                result['diagnostics'].append(f'{table}存在非对象记录')
                continue
            end, notice, updated = (_source_day(row.get(key)) for key in ('REPORT_DATE', 'NOTICE_DATE', 'UPDATE_DATE'))
            if end is None or end > cutoff:
                continue
            # 保留最新目标，即使其值缺失，也不回退较旧期间来伪装完整。
            if _identity(row, symbol) and notice is not None and end < notice <= cutoff:
                observed.add(end)
            valid = (source_ok and _identity(row, symbol) and row.get('CURRENCY') == 'CNY'
                     and notice is not None and end < notice <= cutoff and updated is not None
                     and (end.month, end.day) in {(3, 31), (6, 30), (9, 30), (12, 31)})
            indexed.setdefault((table, end.isoformat()), []).append((row, valid))

    def cell(metric: str, end: date, *, table_override: str | None = None) -> dict[str, Any]:
        table, field, scope = _FIELDS[metric]
        if table_override is not None:
            table, field = table_override, _MAIN[metric]
        sources, values = [], set()
        invalid = False
        for row, valid in indexed.get((table, end.isoformat()), []):
            number = _amount(row.get(field))
            notice = _source_day(row.get('NOTICE_DATE'))
            if not valid or number is None or notice is None:
                invalid = True
                continue
            updated = _source_day(row['UPDATE_DATE'])
            reference = {
                **deepcopy(metadata[table]), 'source_table': table, 'source_field': field,
                'period_end': end.isoformat(), 'disclosed_on': notice.isoformat(),
                'source_updated_at': row['UPDATE_DATE'], 'source_unit': '元', 'currency': 'CNY',
                'unit_basis': 'AkShare同源合并利润/现金流及主要指标金额字段，元',
                'scope': scope, 'period_basis': 'year_to_date', 'raw_value': deepcopy(row[field]),
                'amount_yuan': _money(number), 'updated_after_reference': updated > cutoff if updated else None,
            }
            sources.append(reference)
            values.add(number)
        sources.sort(key=lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False))
        status = 'conflict' if len(values) > 1 else ('invalid' if invalid else ('available' if values else 'missing'))
        return {'status': status, 'amount_yuan': _money(next(iter(values))) if status == 'available' else None,
                'sources': sources}

    years = range(cutoff.year - 3, cutoff.year)
    for year in years:
        end = date(year, 12, 31)
        result['annual'].append({'year': year, 'metrics': {metric: cell(metric, end) for metric in _FIELDS}})
    latest = max(observed) if observed else None
    ttm: dict[str, Any] = {'period_end': latest.isoformat() if latest else None, 'metrics': {}}
    for metric in _FIELDS:
        components = []
        if latest is not None and latest.month in (3, 6, 9):
            for role, end in [('prior_fy', date(latest.year - 1, 12, 31)), ('current_ytd', latest),
                              ('prior_ytd', latest.replace(year=latest.year - 1))]:
                components.append({'role': role, 'period_end': end.isoformat(), **cell(metric, end)})
        states = [part['status'] for part in components]
        available = len(states) == 3 and all(state == 'available' for state in states)
        with localcontext() as context:
            context.prec = 100
            total = (Decimal(components[0]['amount_yuan']) + Decimal(components[1]['amount_yuan'])
                     - Decimal(components[2]['amount_yuan'])) if available else None
        ttm['metrics'][metric] = {'status': 'available' if available else ('conflict' if 'conflict' in states else 'missing'),
                                  'amount_yuan': _money(total) if total is not None else None, 'components': components}
    result['ttm'] = ttm
    for end in sorted(observed):
        for metric in _MAIN:
            primary, auxiliary = cell(metric, end), cell(metric, end, table_override='main')
            if primary['status'] != 'available' or auxiliary['status'] != 'available':
                continue
            with localcontext() as context:
                context.prec = 100
                amount, check = Decimal(primary['amount_yuan']), Decimal(auxiliary['amount_yuan'])
                difference = check - amount
            if difference:
                result['cross_source_differences'].append({
                    'period_end': end.isoformat(), 'metric': metric, 'primary_source': 'profit',
                    'primary_amount_yuan': _money(amount), 'main_amount_yuan': _money(check),
                    'difference_yuan': _money(difference), 'changes_positive_sign': (amount > 0) != (check > 0),
                    'primary_sources': primary['sources'], 'main_sources': auxiliary['sources'],
                    'decision_impact': '须在具体筛选条件中比较，不默认差异无影响',
                })
    annual_cells = [value for row in result['annual'] for value in row['metrics'].values()]
    result['data_complete'] = (collection.get('status') == 'complete'
                               and all(value['status'] == 'available' for value in [*annual_cells, *ttm['metrics'].values()]))
    if all(row['metrics'][field]['status'] == 'available' for row in result['annual']
           for field in ('parent_profit_yuan', 'operating_cash_flow_yuan')):
        with localcontext() as context:
            context.prec = 100
            profit = sum((Decimal(row['metrics']['parent_profit_yuan']['amount_yuan']) for row in result['annual']), Decimal(0))
            cash = sum((Decimal(row['metrics']['operating_cash_flow_yuan']['amount_yuan']) for row in result['annual']), Decimal(0))
            result['cash_flow_ratio'].update(parent_profit_sum_yuan=_money(profit), operating_cash_flow_sum_yuan=_money(cash),
                                            ratio=_money(cash / profit) if profit > 0 else None)
    result['diagnostics'] = sorted(set(result['diagnostics']))
    return result
