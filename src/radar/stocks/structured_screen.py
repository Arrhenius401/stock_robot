"""只对结构化报表执行财务数值初筛，不计算正式入池资格。"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, localcontext
from typing import Any

from radar.stocks.admission import RULE_VERSION
from radar.stocks.dividend_payout import _sum_amounts
from radar.stocks.structured_financial import normalize_structured_financials

_METRICS = (('parent_profit', 'parent_profit_yuan'), ('deducted_profit', 'deducted_parent_profit_yuan'))


def _text(value: Decimal) -> str:
    text = format(value, 'f')
    compact = ('0' if value == 0 else text.rstrip('0').rstrip('.')) if '.' in text else text
    return str(value) if len(compact) > 100 else compact


def _options(cell: dict[str, Any], differences: list[dict[str, Any]]) -> list[Decimal]:
    if cell.get('status') != 'available' or cell.get('amount_yuan') is None:
        return []
    # 原始金额已经过normalizer的_amount校验；展开小数不能再次套原始文本长度限制。
    return [Decimal(cell['amount_yuan']), *[Decimal(row['main_amount_yuan']) for row in differences]]


def _positive_check(code: str, metric: str, parts: list[dict[str, Any]], differences: list[dict[str, Any]],
                    *, ttm: bool = False) -> dict[str, Any]:
    relevant = [row for row in differences if row['metric'] == metric
                and row['period_end'] in {part['period_end'] for part in parts}]
    values = [_options(part, [row for row in relevant if row['period_end'] == part['period_end']]) for part in parts]
    expected = 3 if ttm else 1
    row: dict[str, Any] = {'code': code, 'metric': metric, 'outcome': 'pending_verification',
        'reason': '期间、金额缺失/无效或修订冲突，不能当作零', 'amount_yuan': None,
        'sources': [source for part in parts for source in part['sources']],
        'components': deepcopy(parts), 'cross_source_differences': deepcopy(relevant)}
    if len(values) != expected or not all(values):
        return row
    signs = [1, 1, -1] if ttm else [1]
    primary = _sum_amounts([options[0] if sign == 1 else options[0].copy_negate()
                            for options, sign in zip(values, signs, strict=True)])
    low = _sum_amounts([min(options) if sign == 1 else max(options).copy_negate()
                        for options, sign in zip(values, signs, strict=True)])
    high = _sum_amounts([max(options) if sign == 1 else min(options).copy_negate()
                         for options, sign in zip(values, signs, strict=True)])
    ambiguous = (low > 0) != (high > 0)
    row.update(amount_yuan=_text(primary), alternative_min_yuan=_text(low), alternative_max_yuan=_text(high),
               outcome='pending_verification' if ambiguous else ('passed' if primary > 0 else 'failed'),
               reason='主辅源替代组合会改变严格正利润判断' if ambiguous else '完整年度或最新季度TTM利润须严格为正')
    return row


def _cash_reference(data: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {'ratio': None, 'reference_condition_status': 'pending_verification',
        'applicability': 'requires_issuer_classification', 'dividend_applicability': 'pending_verification',
        'growth_role': 'quality_indicator_only',
        'reason': '0.8只计算数值参考；发行人身份及口径适用性未核验，不据此淘汰金融或未知公司',
        'sources': [], 'cross_source_differences': []}
    cash_values: list[Decimal] = []
    profit_values: list[Decimal] = []
    profit_options: list[list[Decimal]] = []
    for annual in data['annual']:
        cash = annual['metrics']['operating_cash_flow_yuan']
        profit = annual['metrics']['parent_profit_yuan']
        differences = [row for row in data['cross_source_differences']
                       if row['metric'] == 'parent_profit_yuan' and row['period_end'] == f"{annual['year']}-12-31"]
        result['sources'].extend([*cash['sources'], *profit['sources']])
        result['cross_source_differences'].extend(deepcopy(differences))
        amount = Decimal(cash['amount_yuan']) if cash['status'] == 'available' else None
        choices = _options(profit, differences)
        if cash['status'] != 'available' or amount is None or not choices or choices[0] <= 0:
            continue
        cash_values.append(amount)
        profit_values.append(choices[0])
        profit_options.append(choices)
    if len(cash_values) != 3:
        return result
    cash_total, profit_total = _sum_amounts(cash_values), _sum_amounts(profit_values)
    with localcontext() as context:
        context.prec = 100
        result['ratio'] = _text(cash_total / profit_total)
    result.update(operating_cash_flow_sum_yuan=_text(cash_total), parent_profit_sum_yuan=_text(profit_total))
    minimum = _sum_amounts([min(values) for values in profit_options])
    maximum = _sum_amounts([max(values) for values in profit_options])
    cash_scaled = _sum_amounts([cash_total] * 5)
    # 每年分母须严格为正；用精确交叉相乘判定，显示比率不参与门槛判断。
    if any(min(values) <= 0 for values in profit_options):
        return result
    low_pass = cash_scaled >= _sum_amounts([maximum] * 4)
    high_pass = cash_scaled >= _sum_amounts([minimum] * 4)
    if low_pass != high_pass:
        return result
    result['reference_condition_status'] = 'passed' if low_pass else 'failed'
    return result


def screen_structured_financials(collection: dict[str, Any]) -> dict[str, Any]:
    """从原始结构化采集重新规范化，忽略调用方提供的任何筛选结论。"""
    data = normalize_structured_financials(collection)
    differences = data['cross_source_differences']
    checks = []
    for annual in data['annual']:
        for prefix, metric in _METRICS:
            part = {'period_end': f"{annual['year']}-12-31", **annual['metrics'][metric]}
            checks.append(_positive_check(f"annual_{prefix}_{annual['year']}", metric, [part], differences))
    for prefix, metric in _METRICS:
        checks.append(_positive_check(f'ttm_{prefix}', metric, data['ttm']['metrics'][metric]['components'], differences, ttm=True))
    outcomes = [row['outcome'] for row in checks]
    status = 'failed' if 'failed' in outcomes else ('passed' if all(value == 'passed' for value in outcomes) else 'pending_verification')
    return {'symbol': data['symbol'], 'as_of': data['as_of'], 'rule_version': RULE_VERSION,
        'financial_numeric_status': status, 'financial_numeric_checks': checks,
        'cash_flow_reference': _cash_reference(data), 'financial_data': data,
        'source_company_type': deepcopy(collection.get('company_type')), 'issuer_applicability': 'unverified',
        'financial_verified': False, 'formal_screening_enabled': False, 'members_published': 0,
        'point_in_time_verified': False,
        'uncovered_conditions': ['行业龙头', '治理与审计', '市场与市值及流动性', '分红', '估值', '成长与趋势', '金融专项']}
