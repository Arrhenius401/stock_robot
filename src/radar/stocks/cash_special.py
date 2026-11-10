"""普通企业现金专项的来源、用途和风险计算。"""
from __future__ import annotations

from datetime import date
from typing import Any


def _number(row: dict[str, Any], key: str) -> float | None:
    try:
        value = row.get(key)
        return float(value) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _day(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _identity(row: dict[str, Any], symbol: str | None) -> bool:
    """同一公司才可进入现金专项；未传公司时只做结构校验。"""
    if symbol is None:
        return True
    code = row.get('SECURITY_CODE')
    secucode = row.get('SECUCODE')
    if code is not None and code != symbol:
        return False
    if secucode is not None and str(secucode).split('.')[0] != symbol:
        return False
    return code is not None or secucode is not None


def _published(row: dict[str, Any], as_of: date) -> tuple[date, date] | None:
    """报告期早于披露日，且披露与最后更新均不晚于研究截点。"""
    report = _day(row.get('REPORT_DATE'))
    notice = _day(row.get('NOTICE_DATE'))
    updated = _day(row.get('UPDATE_DATE'))
    if report is None or notice is None or updated is None or not report < notice <= as_of or updated > as_of:
        return None
    return notice, updated


def cash_special_metrics(collection: Any, as_of: date, symbol: str | None = None) -> dict[str, Any]:
    """三年经营现金流覆盖分红及利息、利息保障和现金余额；资本开支超标只作提示。"""
    records = collection.get('records') if isinstance(collection, dict) else None
    cash_rows = records.get('cashflow') if isinstance(records, dict) else None
    main_rows = records.get('main') if isinstance(records, dict) else None
    years = list(range(as_of.year - 3, as_of.year))
    by_year: dict[int, dict[str, Any]] = {}
    disclosed: dict[int, str] = {}
    for row in cash_rows if isinstance(cash_rows, list) else []:
        if not isinstance(row, dict) or row.get('CURRENCY') != 'CNY' or not _identity(row, symbol):
            continue
        report = _day(row.get('REPORT_DATE'))
        if report is None or report.month != 12 or report.day != 31 or report.year not in years:
            continue
        published = _published(row, as_of)
        if published is None:
            continue
        if report.year in by_year:
            return {'status': 'pending_verification', 'reason': f'{report.year}年现金流原文重复，无法确认唯一口径'}
        by_year[report.year] = row
        disclosed[report.year] = published[0].isoformat()
    if not all(year in by_year for year in years):
        return {'status': 'pending_verification', 'reason': '缺少三个完整年度、同公司、人民币且截点前已披露的现金流原文'}
    ocf: list[float | None] = []
    capex: list[float | None] = []
    dividends: list[float | None] = []
    cash: list[float | None] = []
    for year in years:
        row = by_year[year]
        ocf.append(_number(row, 'NETCASH_OPERATE'))
        capex.append(_number(row, 'CONSTRUCT_LONG_ASSET'))
        dividends.append(_number(row, 'ASSIGN_DIVIDEND_PORFIT'))
        end = _number(row, 'END_CASH')
        if end is None:
            end = _number(row, 'END_CASH_EQUIVALENTS')
        if end is None:
            end = _number(row, 'END_CCE')
        cash.append(end)
    if any(value is None for value in ocf + capex + dividends + cash):
        return {'status': 'pending_verification', 'reason': '三个年度经营现金流、资本开支、分红及利息支付或现金余额缺失'}
    interest = None
    main_candidates: list[dict[str, Any]] = []
    for row in main_rows if isinstance(main_rows, list) else []:
        if not isinstance(row, dict) or row.get('CURRENCY') != 'CNY' or not _identity(row, symbol):
            continue
        report = _day(row.get('REPORT_DATE'))
        if report is None or report != date(years[-1], 12, 31) or _published(row, as_of) is None:
            continue
        main_candidates.append(row)
    if len(main_candidates) == 1:
        interest = _number(main_candidates[0], 'INTEREST_COVERAGE_RATIO')
    elif len(main_candidates) > 1:
        return {'status': 'pending_verification', 'reason': '最新完整年度主要指标原文重复，无法确认唯一口径'}
    interest_basis = 'disclosed_interest_coverage_ratio'
    if interest is None:
        profit_rows = records.get('profit') if isinstance(records, dict) else None
        profit_candidates: list[dict[str, Any]] = []
        for row in profit_rows if isinstance(profit_rows, list) else []:
            if not isinstance(row, dict) or row.get('CURRENCY') != 'CNY' or not _identity(row, symbol):
                continue
            report = _day(row.get('REPORT_DATE'))
            if report is None or report != date(years[-1], 12, 31) or _published(row, as_of) is None:
                continue
            profit_candidates.append(row)
        if len(profit_candidates) == 1:
            row = profit_candidates[0]
            finance = _number(row, 'FINANCE_EXPENSE')
            total_profit = _number(row, 'TOTAL_PROFIT')
            fe_interest = _number(row, 'FE_INTEREST_EXPENSE')
            if finance is not None and finance <= 0:
                interest = 1.0
                interest_basis = 'net_finance_expense_nonpositive'
            elif fe_interest is not None and fe_interest > 0 and total_profit is not None:
                interest = (total_profit + fe_interest) / fe_interest
                interest_basis = 'computed_from_profit_and_interest'
    if interest is None:
        return {'status': 'pending_verification', 'reason': '缺少截点前已披露的利息保障倍数'}
    ocf_sum, capex_sum, dividend_sum = sum(ocf), sum(capex), sum(dividends)  # type: ignore[arg-type]
    return {'status': 'available', 'years': years, 'ocf_sum': ocf_sum, 'capex_sum': capex_sum,
            'dividend_sum': dividend_sum, 'cash_end': cash, 'interest_coverage': interest,
            'interest_basis': interest_basis,
            'capex_exceeds_ocf': capex_sum > ocf_sum, 'currency': 'CNY', 'unit': 'CNY_yuan',
            'dividend_field': 'ASSIGN_DIVIDEND_PORFIT',
            'dividend_field_meaning': '分配股利、利润或偿付利息支付的现金',
            'coverage': 'dividends_and_interest', 'disclosed_on': {str(year): disclosed[year] for year in years}}


def cash_special_verdict(source: dict[str, Any] | None, issuer: str | None, as_of: date,
                         symbol: str | None = None) -> dict[str, Any]:
    """已确认口径：普通企业分红及利息优先；证券用流动性；银行/保险/其他金融不适用普通现金专项。"""
    if issuer in ('bank', 'insurance', 'other_financial'):
        return {'outcome': 'not_applicable', 'reason': '银行/保险/其他金融不适用普通企业现金专项'}
    if (not isinstance(source, dict) or source.get('status') != 'current' or source.get('as_of') != as_of.isoformat()
            or source.get('evaluation_as_of') != as_of.isoformat()
            or (symbol is not None and source.get('symbol') != symbol)):
        return {'outcome': 'pending_verification', 'reason': '财务来源缺失、历史、非本次截点或公司身份不符'}
    raw_financial: Any = source.get('financial')
    financial: dict[str, Any] = raw_financial if isinstance(raw_financial, dict) else {}
    source_type = financial.get('source_company_type')
    expected_type = {'ordinary': '4', 'securities': '1'}.get(issuer or '')
    if source_type is not None and expected_type is not None and str(source_type) != expected_type:
        return {'outcome': 'pending_verification', 'reason': '财务数据公司类型与发行人类型不一致，现金专项不能套用'}
    if issuer == 'ordinary':
        raw_metrics: Any = financial.get('cash_special')
        metrics: dict[str, Any] = raw_metrics if isinstance(raw_metrics, dict) else {}
        years = list(range(as_of.year - 3, as_of.year))
        if (metrics.get('status') != 'available' or metrics.get('years') != years
                or metrics.get('currency') != 'CNY' or metrics.get('unit') != 'CNY_yuan'
                or metrics.get('coverage') != 'dividends_and_interest'):
            return {'outcome': 'pending_verification', 'reason': str(metrics.get('reason') or '现金专项年度、币种或字段口径不完整')}
        ocf = metrics.get('ocf_sum')
        dividend = metrics.get('dividend_sum')
        interest = metrics.get('interest_coverage')
        cash = metrics.get('cash_end')
        if (not isinstance(ocf, (int, float)) or not isinstance(dividend, (int, float))
                or not isinstance(interest, (int, float)) or not isinstance(cash, list) or len(cash) != 3
                or any(not isinstance(value, (int, float)) for value in cash)):
            return {'outcome': 'pending_verification', 'reason': '现金专项指标类型或年度数量无效'}
        if ocf <= 0 or ocf < dividend or interest < 1 or not all(value >= 0 for value in cash):
            return {'outcome': 'failed', 'reason': '经营现金流为负、不足以覆盖分红及利息支付、利息保障<1或现金余额为负',
                    'values': metrics}
        return {'outcome': 'passed', 'reason': '三年经营现金流覆盖分红及利息支付、利息保障≥1且现金余额非负；资本开支超标仅提示',
                'values': metrics}
    if issuer == 'securities':
        raw_metrics = financial.get('type_metrics')
        metrics = raw_metrics if isinstance(raw_metrics, dict) else {}
        report = _day(metrics.get('report_date'))
        risk, liquidity = metrics.get('sec_risk_coverage'), metrics.get('sec_liquidity_coverage')
        if (metrics.get('status') != 'available' or report is None or report > as_of
                or not isinstance(risk, (int, float)) or not isinstance(liquidity, (int, float))):
            return {'outcome': 'pending_verification', 'reason': '证券风险覆盖率或流动性覆盖率缺失、日期无效或非截点前报告'}
        if risk < 100 or liquidity < 100:
            return {'outcome': 'failed', 'reason': '证券风险覆盖率或流动性覆盖率低于100%', 'values': metrics}
        return {'outcome': 'passed', 'reason': '证券风险覆盖率及流动性覆盖率≥100%', 'values': metrics}
    return {'outcome': 'pending_verification', 'reason': '发行人类型未核实，现金专项不能适用'}
