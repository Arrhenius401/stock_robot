"""按结构化实施记录复核A股年度分红；缺失不能视为零。"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from radar.stocks.admission import _amount, _day
from radar.stocks.research_store import _symbol


def _source_day(value: Any) -> date | None:
    return _day(value[:10]) if isinstance(value, str) else None


def evaluate_dividend_records(symbol: str, as_of: date, cninfo: Any, eastmoney: Any) -> dict[str, Any]:
    """返回研究数值结论；来源身份、完整性与原始响应须由调用方先验证。"""
    _symbol(symbol)
    if type(as_of) is not date or as_of.year < 4:
        raise ValueError('分红研究截点无效')
    if not isinstance(cninfo, list) or not isinstance(eastmoney, list):
        raise TypeError('分红来源须为记录列表')
    years = list(range(as_of.year - 3, as_of.year))
    items: dict[int, list[dict[str, Any]]] = {year: [] for year in years}
    unknown = False
    seen: dict[tuple[str, str], tuple[str, str]] = {}
    corroborated: set[tuple[int, str, str]] = set()
    for row in cninfo:
        if not isinstance(row, dict):
            unknown = True
            continue
        period = row.get('F001V')
        matched = re.fullmatch(r'(\d{4})(?:年报|半年报|一季报|三季报)', period) if isinstance(period, str) else None
        notice, paid, ratio = _source_day(row.get('F006D')), _source_day(row.get('F023D')), _amount(row.get('F012N'))
        if not matched:
            unknown = True
            continue
        period = matched.group(0)
        year = int(matched.group(1))
        if year not in items:
            continue
        if notice is None or paid is None or notice > as_of or paid > as_of or paid < notice or ratio is None or ratio <= 0:
            unknown = True
            continue
        key = (notice.isoformat(), paid.isoformat())
        identity = (period, str(ratio))
        if key in seen and seen[key] != identity:
            unknown = True
        seen[key] = identity
        items[year].append({'report_period': period, 'announced_on': notice.isoformat(),
                            'paid_on': paid.isoformat(), 'cash_per_ten_shares': str(ratio)})
    for row in eastmoney:
        if not isinstance(row, dict) or row.get('SECURITY_CODE') != symbol:
            unknown = True
            continue
        if row.get('ASSIGN_PROGRESS') != '实施分配':
            continue
        report, notice = _source_day(row.get('REPORT_DATE')), _source_day(row.get('NOTICE_DATE'))
        exday, ratio = _source_day(row.get('EX_DIVIDEND_DATE')), _amount(row.get('PRETAX_BONUS_RMB'))
        if report is None or report.year not in items or notice is None or notice > as_of:
            continue
        if exday is None or exday > as_of or ratio is None or ratio <= 0:
            continue
        for year, events in items.items():
            for event in events:
                if event['announced_on'] == notice.isoformat():
                    if (year != report.year or _amount(event['cash_per_ten_shares']) != ratio
                            or {'年报': 12, '半年报': 6, '一季报': 3, '三季报': 9}.get(event['report_period'][4:]) != report.month
                            or exday > date.fromisoformat(event['paid_on'])):
                        unknown = True
                    else:
                        corroborated.add((year, event['announced_on'], event['paid_on']))
    if any((year, event['announced_on'], event['paid_on']) not in corroborated
           for year, events in items.items() for event in events):
        unknown = True
    annual = [{'year': year, 'outcome': 'passed' if not unknown and items[year] else 'pending_verification',
               'events': items[year]} for year in years]
    outcome = 'passed' if not unknown and all(row['outcome'] == 'passed' for row in annual) else 'pending_verification'
    return {'symbol': symbol, 'as_of': as_of.isoformat(), 'outcome': outcome, 'annual': annual,
            'reason': '三年均有已实施A股现金分红' if outcome == 'passed' else '分红记录缺失、冲突或日期待核验',
            'financial_verified': False, 'formal_screening_enabled': False, 'members_published': 0}
