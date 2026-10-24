"""两池已确认条件的纯研究计算；未确认阈值始终保持未配置。"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time
from decimal import Decimal, localcontext
from typing import Any

from radar.stocks.admission import (
    _BJ,
    _amount,
    _choice,
    _day,
    _groups,
    evaluate_common_admission,
)
from radar.stocks.dividend_payout import _sum_amounts

STYLE_RULE_VERSION = 'styles-approved-2026-10-15-v1'
_STYLE_KINDS = ('cash_flow', 'cash_review', 'dividend_history', 'liquidity')


def _check(code: str, outcome: str, reason: str, identifiers: list[str], role: str,
           values: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {'code': code, 'outcome': outcome, 'reason': reason,
                              'evidence_ids': identifiers, 'role': role}
    if values is not None:
        result['values'] = values
    return result


def _aggregate(statuses: list[str]) -> str:
    if 'failed' in statuses:
        return 'failed'
    return 'passed' if all(status in {'passed', 'attention', 'not_applicable'} for status in statuses) else 'pending_verification'


def _pool(status: str, checks: list[dict[str, Any]], rules: list[str], opportunity: str = 'unavailable') -> dict[str, Any]:
    return {'checks': checks, 'approved_conditions_status': status,
            'style_status': 'failed' if status == 'failed' else ('pending_configuration' if status == 'passed' else 'pending_verification'),
            'admission_status': 'excluded' if status == 'failed' else 'pending_verification',
            'opportunity_status': opportunity, 'unconfigured_rules': rules,
            'formal_screening_enabled': False, 'members_published': 0}


def _annual(rows: Any, years: list[int]) -> dict[int, dict[str, Any]] | None:
    if not isinstance(rows, list) or len(rows) != 3:
        return None
    indexed: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or type(row.get('year')) is not int or row['year'] not in years or row['year'] in indexed:
            return None
        indexed[row['year']] = row
    return indexed if set(indexed) == set(years) else None


def _cash_ratio(groups: dict[str, Any], common_groups: dict[str, Any], issuer: Any,
                cutoff: date, years: list[int]) -> tuple[str, str, list[str], dict[str, Any] | None]:
    cash, cash_ids, cash_reason = groups['cash_flow']
    profit, profit_ids, profit_reason = common_groups['profitability']
    identifiers = sorted(set(cash_ids + profit_ids))
    if _choice(issuer, {'bank', 'securities', 'insurance'}):
        return 'not_applicable', '金融机构采用类型匹配的现金专项核验，不套用0.8比率', cash_ids, None
    if (issuer != 'ordinary' or cash is None or profit is None or cash.get('issuer_type') != 'ordinary'
            or cash.get('currency') != 'CNY' or cash.get('scope') != 'company_consolidated'
            or cash.get('parent_profit_scope_reconciled') is not True or profit.get('currency') != 'CNY'
            or profit.get('scope') != 'company_parent'):
        return 'pending_verification', cash_reason or profit_reason or '现金流、归母利润或合并口径调节尚未核实', identifiers, None
    cash_rows = _annual(cash.get('annual'), years)
    # 分母只能读取共同事实，其他年份可留档，目标三年须逐年唯一。
    profit_rows: dict[int, dict[str, Any]] = {}
    annual_profit = profit.get('annual')
    if isinstance(annual_profit, list):
        for year in years:
            matches = [row for row in annual_profit if isinstance(row, dict) and type(row.get('year')) is int and row['year'] == year]
            if len(matches) == 1:
                profit_rows[year] = matches[0]
    if cash_rows is None or set(profit_rows) != set(years):
        return 'pending_verification', '最近三个完整年度现金流或共同归母利润缺失、重复或期间不符', identifiers, None
    cash_values: list[Decimal] = []
    profit_values: list[Decimal] = []
    for year in years:
        cash_row, profit_row = cash_rows[year], profit_rows[year]
        cash_day, profit_day = _day(cash_row.get('disclosed_on')), _day(profit_row.get('disclosed_on'))
        cash_value, profit_value = _amount(cash_row.get('operating_cash_flow_yuan')), _amount(profit_row.get('parent_profit_yuan'))
        if (cash_day is None or profit_day is None or not date(year, 12, 31) < cash_day <= cutoff
                or not date(year, 12, 31) < profit_day <= cutoff or cash_value is None or profit_value is None or profit_value <= 0):
            return 'pending_verification', '现金流金额、已披露期间或共同归母利润分母无效', identifiers, None
        cash_values.append(cash_value)
        profit_values.append(profit_value)
    cash_total, profit_total = _sum_amounts(cash_values), _sum_amounts(profit_values)
    # 交叉相乘也使用扩展精度求和，避免默认Decimal上下文舍入边界。
    passed = _sum_amounts([cash_total] * 5) >= _sum_amounts([profit_total] * 4)
    with localcontext() as context:
        context.prec = 80
        ratio = cash_total / profit_total
    return ('passed' if passed else 'failed', '三年经营现金流合计/同期间归母利润合计须不低于0.8', identifiers,
            {'operating_cash_flow_sum_yuan': str(cash_total), 'parent_profit_sum_yuan': str(profit_total), 'ratio': str(ratio), 'years': years})


def _cash_review(groups: dict[str, Any], issuer: Any) -> tuple[str, str, list[str]]:
    payload, identifiers, reason = groups['cash_review']
    if (payload is None or not _choice(issuer, {'ordinary', 'bank', 'securities', 'insurance'})
            or payload.get('issuer_type') != issuer or not isinstance(payload.get('basis'), str) or not payload['basis'].strip()):
        return 'pending_verification', reason or '缺少发行人类型匹配的现金专项核验依据', identifiers
    verdict = payload.get('verdict')
    if verdict == 'clear':
        return 'passed', '类型匹配的现金专项已核验', identifiers
    if verdict == 'failed':
        return 'failed', '现金专项核验未通过', identifiers
    return 'pending_verification', '现金专项仍待核验', identifiers


def _dividend_history(groups: dict[str, Any], cutoff: date, years: list[int]) -> dict[str, Any]:
    payload, identifiers, reason = groups['dividend_history']
    role = 'dividend_branch_condition'
    if (payload is None or payload.get('currency') != 'CNY' or payload.get('scope') != 'company_all_shares'
            or payload.get('share_classes_complete') is not True or _day(payload.get('coverage_through')) != cutoff):
        return _check('dividend_history', 'pending_verification', reason or '缺少截至评估日的全股类已实施分红完整性核验', identifiers, role)
    indexed = _annual(payload.get('annual'), years)
    if indexed is None:
        return _check('dividend_history', 'pending_verification', '分红财政年度缺失、重复或不是最近三个完整年度', identifiers, role)
    amounts: dict[str, str] = {}
    unknown = False
    zero = False
    for year in years:
        row = indexed[year]
        amount, disclosed = _amount(row.get('implemented_cash_yuan')), _day(row.get('disclosed_on'))
        if (row.get('coverage_complete') is not True or amount is None or amount < 0 or disclosed is None
                or not date(year, 1, 1) <= disclosed <= cutoff):
            unknown = True
            continue
        if amount == 0:
            zero = True
            amounts[str(year)] = str(amount)
            continue
        paid = _day(row.get('latest_paid_on'))
        if paid is None or not date(year, 1, 1) <= paid <= cutoff:
            unknown = True
            continue
        amounts[str(year)] = str(amount)
    status = 'failed' if zero else ('pending_verification' if unknown else 'passed')
    return _check('dividend_history', status, '逐年已实施分红须为正；不要求金额逐年增长，也不代表支付率核验', identifiers,
                  role, {'annual_implemented_cash_yuan': amounts, 'years': years})


def _liquidity(groups: dict[str, Any], cutoff: date, market_window: Any) -> tuple[str, str, list[str], dict[str, Any] | None]:
    payload, identifiers, reason = groups['liquidity']
    parsed = [_day(day) for day in market_window] if isinstance(market_window, list) else []
    window = [day for day in parsed if day is not None]
    valid = (len(parsed) == 60 and len(window) == 60 and all(day <= cutoff for day in window)
             and all(window[index] < window[index + 1] for index in range(59)))
    if (not valid or payload is None or payload.get('currency') != 'CNY' or payload.get('scope') != 'a_share_raw_turnover'):
        return 'unavailable', reason or '缺少60个已核验市场交易日的人民币原始A股成交额', identifiers, None
    daily = payload.get('daily')
    indexed: dict[date, Decimal] = {}
    if not isinstance(daily, list) or len(daily) != 60:
        return 'unavailable', '成交额窗口不是完整60日', identifiers, None
    for row in daily:
        day = _day(row.get('date')) if isinstance(row, dict) else None
        amount = _amount(row.get('amount_yuan')) if isinstance(row, dict) else None
        if day is None or day in indexed or amount is None or amount < 0:
            return 'unavailable', '成交额日期重复、金额未知或负值', identifiers, None
        indexed[day] = amount
    if set(indexed) != set(window):
        return 'unavailable', '成交额未逐日匹配市场窗口', identifiers, None
    total = _sum_amounts(list(indexed.values()))
    passed = total >= _sum_amounts([Decimal(100000000)] * 60)
    with localcontext() as context:
        context.prec = 80
        average = total / Decimal(60)
    return ('passed' if passed else 'below_reference', '60日原始成交额均值1亿元仅作为提醒/机会参考，不是共同资格线',
            identifiers, {'average_amount_yuan': str(average), 'observations': 60})


def evaluate_stock_styles(symbol: str, evidence: list[dict[str, Any]], as_of: date, *,
                          market_window: list[date] | None = None, common_result: dict[str, Any] | None = None) -> dict[str, Any]:
    """返回共同条件及独立风格研究状态，所有正式发布能力保持关闭。"""
    result = deepcopy(common_result) if common_result is not None else evaluate_common_admission(symbol, evidence, as_of, market_window=market_window)
    result.update(style_rule_version=STYLE_RULE_VERSION, members_published=0)
    cutoff = _day(as_of)
    if cutoff is None or cutoff.year < 4:
        result['pools'] = {name: _pool('pending_verification', [], ['style_rules']) for name in ('dividend_value', 'growth_trend')}
        result['pools']['dividend_value']['branches'] = {
            name: _pool('pending_verification', [], ['style_rules']) for name in ('dividend', 'value')}
        return result
    end = datetime.combine(cutoff, time.max, tzinfo=_BJ)
    groups = _groups(symbol, evidence, end, kinds=_STYLE_KINDS, prefix='style_')
    common_groups = _groups(symbol, evidence, end)
    security = common_groups['security'][0]
    issuer = security.get('issuer_type') if security else None
    years = list(range(cutoff.year - 3, cutoff.year))
    ratio_status, ratio_reason, ratio_ids, ratio_values = _cash_ratio(groups, common_groups, issuer, cutoff, years)
    review_status, review_reason, review_ids = _cash_review(groups, issuer)
    # 发行人分支也是判断依据，逐项保留其规范事实来源。
    security_ids = common_groups['security'][1]
    ratio_ids = sorted(set(ratio_ids + security_ids))
    review_ids = sorted(set(review_ids + security_ids))
    liquidity_status, liquidity_reason, liquidity_ids, liquidity_values = _liquidity(groups, cutoff, market_window)
    red_checks = [_check('cash_ratio', ratio_status, ratio_reason, ratio_ids, 'cash_hard_condition', ratio_values),
                  _check('cash_review', review_status, review_reason, review_ids, 'cash_hard_condition'),
                  _dividend_history(groups, cutoff, years),
                  _check('liquidity', 'warning' if liquidity_status == 'below_reference' else liquidity_status,
                         liquidity_reason, liquidity_ids, 'liquidity_warning', liquidity_values)]
    red_base = _aggregate([result['common_status'], ratio_status, review_status])
    dividend_status = _aggregate([red_base, red_checks[2]['outcome']])
    red = _pool(red_base, red_checks, ['dividend_payout_ratio', 'dividend_yield', 'value_valuation'])
    red['branches'] = {
        'dividend': _pool(dividend_status, [*red_checks[:3]], ['dividend_payout_ratio', 'dividend_yield']),
        'value': _pool(red_base, red_checks[:2], ['value_valuation'])}
    growth_ratio_status = 'attention' if ratio_status == 'failed' else ratio_status
    growth_review_status = 'attention' if review_status == 'failed' else review_status
    growth_checks = [_check('cash_ratio', growth_ratio_status, '成长池现金比率仅作质量提示；' + ratio_reason,
                            ratio_ids, 'cash_quality_indicator', ratio_values),
                     _check('cash_review', growth_review_status, review_reason, review_ids, 'cash_quality_indicator'),
                     _check('liquidity', 'observe' if liquidity_status == 'below_reference' else liquidity_status,
                            liquidity_reason, liquidity_ids, 'opportunity_reference', liquidity_values)]
    # 已知低比率只提醒，缺资料仍待核验；未清晰完成专项也不冒充已核验。
    quality_review = 'passed' if review_status == 'passed' else 'pending_verification'
    growth_status = _aggregate([result['common_status'], growth_ratio_status, quality_review])
    growth = _pool(growth_status, growth_checks, ['growth_revenue', 'growth_deducted_profit', 'valuation', 'trend'],
                   'observe' if liquidity_status == 'below_reference' and result['common_status'] != 'failed' else 'unavailable')
    result['pools'] = {'dividend_value': red, 'growth_trend': growth}
    result['evidence_ids'] = sorted(set(result['evidence_ids']) | {
        identifier for pool in result['pools'].values() for row in pool['checks'] for identifier in row['evidence_ids']})
    return result
