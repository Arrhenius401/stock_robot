"""结构化成长研究指标；参考线不授予股票池资格。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, localcontext
from typing import Any

from radar.stocks.admission import _amount, _day
from radar.stocks.dividend_payout import _sum_amounts


def _end(year: int, quarter: int) -> date:
    return date(year, quarter * 3, 31 if quarter in (1, 4) else 30)


def _previous(day: date) -> date:
    return _end(day.year, day.month // 3 - 1) if day.month > 3 else _end(day.year - 1, 4)


def _display(value: Decimal) -> str:
    with localcontext() as context:
        context.prec = 1100
        return format(value.quantize(Decimal('0.000001')), 'f').rstrip('0').rstrip('.') or '0'


def calculate_growth_research(symbol: str, rows: list[dict[str, Any]], as_of: date, latest_period: date) -> dict[str, Any]:
    """来源文件由调用方验证；此处严格计算期间、身份及可比数据。"""
    if (type(as_of) is not date or type(latest_period) is not date or latest_period > as_of
            or latest_period != _end(latest_period.year, latest_period.month // 3)
            or latest_period.year < 4):
        raise ValueError('研究截点或季度期末无效')
    indexed: dict[date, list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError('财务记录须为对象')
        if row.get('SECURITY_CODE') != symbol:
            raise ValueError('财务记录公司身份不匹配')
        expected_identity = f'{symbol}.{"SH" if symbol.startswith("6") else "SZ"}'
        if 'SECUCODE' in row and row['SECUCODE'] != expected_identity:
            raise ValueError('财务记录交易所身份不匹配')
        end = _day(str(row.get('REPORT_DATE', ''))[:10])
        if end is not None:
            indexed.setdefault(end, []).append(row)

    def cell(day: date, field: str) -> Decimal | None:
        values = set()
        for row in indexed.get(day, []):
            notice = _day(str(row.get('NOTICE_DATE', ''))[:10])
            updated = _day(str(row.get('UPDATE_DATE', ''))[:10])
            if notice is not None and notice > as_of:
                continue
            value = _amount(row.get(field))
            if (notice is None or not day < notice <= as_of or updated is None or updated > as_of
                    or row.get('CURRENCY') != 'CNY' or value is None):
                return None
            values.add(value)
        return next(iter(values)) if len(values) == 1 else None

    def aggregate(parts: list[tuple[date, int]], field: str) -> Decimal | None:
        values = [cell(day, field) for day, _ in parts]
        if any(value is None for value in values):
            return None
        with localcontext() as context:
            context.prec = 1100
            return _sum_amounts([value * sign for value, (_, sign) in zip(values, parts, strict=True) if value is not None])

    def quarter(day: date, field: str) -> Decimal | None:
        parts = [(day, 1)]
        if day.month != 3:
            parts.append((_previous(day), -1))
        return aggregate(parts, field)

    def ttm(day: date, field: str) -> Decimal | None:
        parts = [(day, 1)] if day.month == 12 else [
            (_end(day.year - 1, 4), 1), (day, 1), (_end(day.year - 1, day.month // 3), -1)]
        return aggregate(parts, field)

    def comparison(current: Decimal | None, prior: Decimal | None, floor: int) -> dict[str, Any]:
        valid = current is not None and prior is not None and prior > 0
        with localcontext() as context:
            context.prec = 1100
            yoy = (current / prior - 1) * 100 if valid and current is not None and prior is not None else None
            met = current * 100 >= prior * (100 + floor) if valid and current is not None and prior is not None else None
        return {'current': str(current) if current is not None else None,
                'prior': str(prior) if prior is not None else None,
                'yoy_percent': _display(yoy) if yoy is not None else None, 'meets_floor': met,
                'reason': '可比正基数' if valid else '缺失、冲突、不可比或同比基数非正'}

    quarters = []
    for day in (_previous(latest_period), latest_period):
        prior_day = _end(day.year - 1, day.month // 3)
        revenue, profit = quarter(day, 'TOTALOPERATEREVE'), quarter(day, 'KCFJCXSYJLR')
        old_revenue, old_profit = quarter(prior_day, 'TOTALOPERATEREVE'), quarter(prior_day, 'KCFJCXSYJLR')
        comparable = revenue is not None and old_revenue is not None and profit is not None and old_profit is not None and revenue > 0 and old_revenue > 0
        with localcontext() as context:
            context.prec = 1100
            margin = ((profit / revenue - old_profit / old_revenue) * 100
                      if comparable and profit is not None and revenue is not None and old_profit is not None and old_revenue is not None else None)
            improved = profit * old_revenue > old_profit * revenue if comparable and profit is not None and old_profit is not None and revenue is not None and old_revenue is not None else None
        quarters.append({'period': day.isoformat(), 'prior_period': prior_day.isoformat(),
                         'revenue': comparison(revenue, old_revenue, 0), 'deducted_profit': comparison(profit, old_profit, 0),
                         'margin_change_pp': _display(margin) if margin is not None else None, 'margin_improved': improved})
    annual_prior = _end(latest_period.year - 1, latest_period.month // 3)
    metrics = {label: comparison(ttm(latest_period, field), ttm(annual_prior, field), floor)
               for label, field, floor in [('revenue', 'TOTALOPERATEREVE', 10), ('deducted_profit', 'KCFJCXSYJLR', 15)]}
    positive = [None if row[key]['yoy_percent'] is None else
                Decimal(row[key]['current']) > Decimal(row[key]['prior']) for row in quarters for key in ('revenue', 'deducted_profit')]

    def path(flags: list[bool | None]) -> str:
        return 'pending_verification' if None in flags else 'met_reference' if all(flags) else 'below_reference'

    return {'symbol': symbol, 'as_of': as_of.isoformat(), 'latest_period': latest_period.isoformat(),
            'quarters': quarters, 'ttm': metrics,
            'paths': {'sustained_growth': path([*positive, *(row['meets_floor'] for row in metrics.values())]),
                      'operating_improvement': path([*positive, *(row['margin_improved'] for row in quarters)])},
            'research_only': True, 'formal_screening_enabled': False, 'members_published': 0}
