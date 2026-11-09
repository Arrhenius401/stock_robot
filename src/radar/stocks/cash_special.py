"""普通企业现金专项的来源、用途和风险计算。"""
from __future__ import annotations

from datetime import date
from typing import Any


def _number(row: dict[str, Any], key: str, default: float | None = None) -> float | None:
    try:
        value = row.get(key)
        return float(value) if value not in (None, '') else default
    except (TypeError, ValueError):
        return default


def cash_special_metrics(collection: Any, as_of: date) -> dict[str, Any]:
    """三年经营现金流覆盖分红、利息保障和现金余额；资本开支超标只作提示。"""
    records = collection.get('records') if isinstance(collection, dict) else None
    cash_rows = records.get('cashflow') if isinstance(records, dict) else None
    main_rows = records.get('main') if isinstance(records, dict) else None
    years = list(range(as_of.year - 3, as_of.year))
    by_year: dict[int, dict[str, Any]] = {}
    for row in cash_rows if isinstance(cash_rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get('REPORT_DATE'), str):
            continue
        try:
            day = date.fromisoformat(row['REPORT_DATE'][:10])
        except ValueError:
            continue
        if day.month == 12 and day.day == 31 and day.year in years:
            by_year[day.year] = row
    if not all(year in by_year for year in years):
        return {'status': 'pending_verification', 'reason': '缺少三个完整年度现金流原文'}
    ocf: list[float | None] = []
    capex: list[float | None] = []
    dividends: list[float | None] = []
    cash: list[float | None] = []
    for year in years:
        row = by_year[year]
        ocf.append(_number(row, 'NETCASH_OPERATE'))
        capex.append(_number(row, 'CONSTRUCT_LONG_ASSET'))
        dividends.append(_number(row, 'ASSIGN_DIVIDEND_PORFIT', 0.0))
        end = _number(row, 'END_CASH')
        cash.append(end if end is not None else _number(row, 'END_CASH_EQUIVALENTS'))
    if any(value is None for value in ocf + capex + cash):
        return {'status': 'pending_verification', 'reason': '三个年度经营现金流、资本开支或现金余额缺失'}
    interest = None
    for row in main_rows if isinstance(main_rows, list) else []:
        if (isinstance(row, dict) and isinstance(row.get('REPORT_DATE'), str)
                and row['REPORT_DATE'].startswith(f'{years[-1]}-12-31')):
            interest = _number(row, 'INTEREST_COVERAGE_RATIO')
    if interest is None:
        return {'status': 'pending_verification', 'reason': '缺少利息保障倍数'}
    ocf_sum, capex_sum, dividend_sum = sum(ocf), sum(capex), sum(dividends)  # type: ignore[arg-type]
    return {'status': 'available', 'years': years, 'ocf_sum': ocf_sum, 'capex_sum': capex_sum,
            'dividend_sum': dividend_sum, 'cash_end': cash, 'interest_coverage': interest,
            'capex_exceeds_ocf': capex_sum > ocf_sum}


def cash_special_verdict(source: dict[str, Any] | None, issuer: str | None, as_of: date) -> dict[str, Any]:
    """已确认口径：普通企业分红优先；证券用流动性；银行/保险缺专用字段则待核验。"""
    if not isinstance(source, dict) or source.get('status') != 'current' or source.get('as_of') != as_of.isoformat():
        return {'outcome': 'pending_verification', 'reason': '财务来源缺失、历史或非本次截点'}
    raw_financial: Any = source.get('financial')
    financial: dict[str, Any] = raw_financial if isinstance(raw_financial, dict) else {}
    if issuer == 'ordinary':
        raw_metrics: Any = financial.get('cash_special')
        metrics: dict[str, Any] = raw_metrics if isinstance(raw_metrics, dict) else {}
        if metrics.get('status') != 'available':
            return {'outcome': 'pending_verification', 'reason': str(metrics.get('reason') or '现金专项数据缺失')}
        ocf = metrics.get('ocf_sum')
        dividend = metrics.get('dividend_sum')
        interest = metrics.get('interest_coverage')
        cash = metrics.get('cash_end')
        if (not isinstance(ocf, (int, float)) or not isinstance(dividend, (int, float))
                or not isinstance(interest, (int, float)) or not isinstance(cash, list)
                or any(not isinstance(value, (int, float)) for value in cash)):
            return {'outcome': 'pending_verification', 'reason': '现金专项指标类型无效'}
        if ocf <= 0 or ocf < dividend or interest < 1 or not all(value >= 0 for value in cash):
            return {'outcome': 'failed', 'reason': '经营现金流为负、不足以覆盖分红、利息保障<1或现金余额为负',
                    'values': metrics}
        return {'outcome': 'passed', 'reason': '三年经营现金流覆盖分红、利息保障≥1且现金余额非负；资本开支超标仅提示',
                'values': metrics}
    if issuer == 'securities':
        raw_metrics = financial.get('type_metrics')
        metrics = raw_metrics if isinstance(raw_metrics, dict) else {}
        risk, liquidity = metrics.get('sec_risk_coverage'), metrics.get('sec_liquidity_coverage')
        if not isinstance(risk, (int, float)) or not isinstance(liquidity, (int, float)):
            return {'outcome': 'pending_verification', 'reason': '证券风险覆盖率或流动性覆盖率缺失'}
        if risk < 100 or liquidity < 100:
            return {'outcome': 'failed', 'reason': '证券风险覆盖率或流动性覆盖率低于100%'}
        return {'outcome': 'passed', 'reason': '证券风险覆盖率及流动性覆盖率≥100%'}
    if issuer in ('bank', 'insurance'):
        return {'outcome': 'pending_verification', 'reason': '银行/保险缺少专用流动性和分配约束字段'}
    return {'outcome': 'pending_verification', 'reason': '发行人类型未核实，现金专项不能适用'}
