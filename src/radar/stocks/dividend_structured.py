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


def evaluate_dividend_absence(symbol: str, as_of: date, cninfo: Any, eastmoney: Any) -> dict[str, Any]:
    """双源完整且该年度无任何正分红记录时，独立判 no_dividend；不把单源失败当缺失。"""
    _symbol(symbol)
    if type(as_of) is not date or as_of.year < 4:
        raise ValueError('分红研究截点无效')
    if not isinstance(cninfo, list) or not isinstance(eastmoney, list):
        raise TypeError('分红来源须为记录列表')
    years = list(range(as_of.year - 3, as_of.year))
    cninfo_result = evaluate_cninfo_records(symbol, as_of, cninfo)
    cninfo_passed = {item['year'] for item in cninfo_result['annual'] if item['outcome'] == 'passed'}
    implemented: set[int] = set()
    planned: set[int] = set()
    cancelled: set[int] = set()
    for row in eastmoney:
        if not isinstance(row, dict) or row.get('SECURITY_CODE') != symbol:
            continue
        report = _source_day(row.get('REPORT_DATE'))
        ratio = _amount(row.get('PRETAX_BONUS_RMB'))
        progress = str(row.get('ASSIGN_PROGRESS') or '')
        if report is None or report.year not in years:
            continue
        if progress in {'实施分配', '实施方案'} and ratio is not None and ratio > 0:
            implemented.add(report.year)
        elif progress in {'预披露', '董事会决议通过', '股东大会决议通过'} and ratio is not None and ratio > 0:
            planned.add(report.year)
        elif progress in {'取消分配', '股东大会否决'}:
            cancelled.add(report.year)
    annual = []
    for year in years:
        if year in cninfo_passed or year in implemented:
            outcome = 'passed'
        elif year in planned or year in cancelled:
            outcome = 'pending_verification'
        else:
            outcome = 'no_dividend'
        annual.append({'year': year, 'outcome': outcome, 'events': []})
    if any(item['outcome'] == 'no_dividend' for item in annual):
        outcome = 'failed'
        reason = '双源完整且该年度无正分红记录，独立判定为不分红'
    elif any(item['outcome'] == 'pending_verification' for item in annual):
        outcome = 'pending_verification'
        reason = '存在未实施/取消的预案，待核验'
    else:
        outcome = 'passed'
        reason = '三年均有实施分配或已核实分红记录'
    return {'symbol': symbol, 'as_of': as_of.isoformat(), 'outcome': outcome, 'annual': annual,
            'reason': reason, 'rule_version': 'dividend-absence-2026-11-10-v1',
            'financial_verified': False, 'formal_screening_enabled': False, 'members_published': 0}


def evaluate_eastmoney_page_absence(symbol: str, as_of: date, rows: Any) -> dict[str, Any]:
    """用东财 F10 分红页的年度预案识别“不分配不转增”；没有 REPORT_DATE 时按公告月份归年。"""
    _symbol(symbol)
    if type(as_of) is not date or as_of.year < 4:
        raise ValueError('分红研究截点无效')
    if not isinstance(rows, list):
        raise TypeError('分红来源须为记录列表')
    years = list(range(as_of.year - 3, as_of.year))
    outcomes = {year: 'pending_verification' for year in years}
    for row in rows:
        if not isinstance(row, dict) or row.get('SECURITY_CODE') != symbol:
            continue
        notice = _source_day(row.get('NOTICE_DATE'))
        if notice is None or notice > as_of:
            continue
        # 年报预案通常在上半年公告；8-12 月是中期/季度预案，不用于年度判定。
        if notice.month > 6:
            continue
        year = notice.year - 1
        if year not in outcomes:
            continue
        profile = str(row.get('IMPL_PLAN_PROFILE') or '')
        if any(token in profile for token in ('不分配', '不转增', '不派发', '不派现')):
            outcomes[year] = 'no_dividend'
        elif '派' in profile:
            outcomes[year] = 'pending_verification'
    annual = [{'year': year, 'outcome': outcomes[year], 'events': []} for year in years]
    if any(item['outcome'] == 'no_dividend' for item in annual):
        outcome = 'failed'
        reason = '东财F10页面明确列示不分配不转增'
    else:
        outcome = 'pending_verification'
        reason = '东财F10页面预案不足或未实施'
    return {'symbol': symbol, 'as_of': as_of.isoformat(), 'outcome': outcome, 'annual': annual,
            'reason': reason, 'rule_version': 'dividend-page-absence-2026-11-10-v1',
            'financial_verified': False, 'formal_screening_enabled': False, 'members_published': 0}


def evaluate_cninfo_records(symbol: str, as_of: date, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """以请求绑定的巨潮记录独立证明各年度至少一条正向实施记录。"""
    _symbol(symbol)
    years = range(as_of.year - 3, as_of.year)
    items: dict[int, list[dict[str, Any]]] = {year: [] for year in years}
    for row in rows:
        period = row.get('F001V')
        matched = re.fullmatch(r'(\d{4})(?:年报|半年报|一季报|三季报)', period) if isinstance(period, str) else None
        if not matched:
            continue
        year = int(matched.group(1))
        if year not in items:
            continue
        notice, paid, ratio = _source_day(row.get('F006D')), _source_day(row.get('F023D')), _amount(row.get('F012N'))
        if (notice is None or paid is None or notice.year < year or notice > as_of or paid > as_of
                or paid < notice or ratio is None or ratio <= 0):
            continue
        items[year].append({'report_period': period, 'announced_on': notice.isoformat(),
                            'paid_on': paid.isoformat(), 'cash_per_ten_shares': str(ratio)})
    annual = [{'year': year, 'outcome': 'passed' if items[year] else 'pending_verification',
               'events': items[year]} for year in years]
    outcome = 'passed' if all(item['outcome'] == 'passed' for item in annual) else 'pending_verification'
    return {'symbol': symbol, 'as_of': as_of.isoformat(), 'outcome': outcome, 'annual': annual,
            'reason': '三年均有已实施A股现金分红' if outcome == 'passed' else '分红记录缺失、冲突或日期待核验',
            'financial_verified': False, 'formal_screening_enabled': False, 'members_published': 0}


def evaluate_eastmoney_records(symbol: str, as_of: date, page: list[dict[str, Any]],
                               periods: list[dict[str, Any]]) -> dict[str, Any]:
    """同一供应方内按公司、公告日、金额及除权日连接年度与实际派息。"""
    rows: list[dict[str, Any]] = []
    for event in page:
        if event.get('ASSIGN_PROGRESS') != '实施方案':
            continue
        profile = event.get('IMPL_PLAN_PROFILE')
        match = re.fullmatch(r'10派(\d+(?:\.\d+)?)元(?:\(含税\))?', profile) if isinstance(profile, str) else None
        notice, paid, exday = (_source_day(event.get(key)) for key in ('NOTICE_DATE', 'PAY_CASH_DATE', 'EX_DIVIDEND_DATE'))
        if match is None or notice is None or paid is None or exday is None:
            continue
        ratio = _amount(match.group(1))
        if sum(_source_day(row.get('NOTICE_DATE')) == notice and row.get('ASSIGN_PROGRESS') == '实施方案'
               for row in page) != 1:
            continue
        candidates = [row for row in periods if row.get('SECURITY_CODE') == symbol
                      and row.get('ASSIGN_PROGRESS') == '实施分配'
                      and _source_day(row.get('NOTICE_DATE')) == notice
                      and _source_day(row.get('EX_DIVIDEND_DATE')) == exday
                      and _amount(row.get('PRETAX_BONUS_RMB')) == ratio]
        if len(candidates) != 1 or ratio is None or ratio <= 0 or not notice <= exday <= paid:
            continue
        report = _source_day(candidates[0].get('REPORT_DATE'))
        if report is None or report > notice or report.month not in (3, 6, 9, 12):
            continue
        suffix = {3: '一季报', 6: '半年报', 9: '三季报', 12: '年报'}[report.month]
        rows.append({'F001V': f'{report.year}{suffix}', 'F006D': notice.isoformat(),
                     'F023D': paid.isoformat(), 'F012N': str(ratio)})
    return evaluate_cninfo_records(symbol, as_of, rows)
