"""财务评分证据校验：未知期间、冲突和不等价口径均按缺项处理。"""
from datetime import datetime

from data.schemas import AnalysisContext, FinancialData
from utils.numbers import is_finite_number, is_positive_finite


def trusted_rows(ctx: AnalysisContext) -> list[FinancialData]:
    """排除错标的、未来及冲突重复期，按最新截止日排序。"""
    by_date: dict = {}
    conflicts = set()
    for row in ctx.financial_data or []:
        if row.symbol != ctx.symbol or row.fiscal_quarter > datetime.now().astimezone().date():
            continue
        if row.fiscal_quarter in by_date and by_date[row.fiscal_quarter] != row:
            conflicts.add(row.fiscal_quarter)
        by_date[row.fiscal_quarter] = row
    return [row for day, row in sorted(by_date.items(), reverse=True) if day not in conflicts]


def _source(row: FinancialData, key: str) -> str:
    return row.field_sources.get(key) or row.financial_source or row._data_source


def debt_ratio(row: FinancialData) -> float | None:
    assets, equity = row.total_assets, row.total_equity
    if not is_positive_finite(assets) or not is_finite_number(equity):
        return None
    assert assets is not None and equity is not None
    if not _source(row, "total_assets") or not _source(row, "total_equity"):
        return None
    left, right = (row.field_positions.get(key, {}) for key in ("total_assets", "total_equity"))
    if (left or right) and (not left or not right or any(
            left.get(k) != right.get(k) for k in ("currency", "unit", "scope", "basis", "revision"))):
        return None
    value = (1 - equity / assets) * 100
    return value if is_finite_number(value) else None


def cashflow_compatible(row: FinancialData) -> bool:
    """明确的同源期间与币种是最低证据；逐字段元信息存在时也须一致。"""
    if (row.period_start is None or row.period_start > row.fiscal_quarter
            or row.period_basis == "unknown" or not row.reporting_basis or not row.report_currency
            or row.cash_flow_period_start != row.period_start or row.cash_flow_basis == "unknown"
            or not is_finite_number(row.net_profit) or not is_finite_number(row.operating_cash_flow)
            or not _source(row, "net_profit") or not _source(row, "operating_cash_flow")):
        return False
    profit, cash = (row.field_positions.get(key, {}) for key in ("net_profit", "operating_cash_flow"))
    if profit or cash:
        if not profit or not cash:
            return False
        for key in ("currency", "unit", "start", "basis", "scope", "accounting", "revision"):
            if not profit.get(key) or profit[key] != cash.get(key):
                return False
        if (profit["currency"] != row.report_currency or profit["start"] != row.period_start.isoformat()
                or profit["basis"] != row.period_basis):
            return False
    return _source(row, "net_profit") == _source(row, "operating_cash_flow") or bool(profit and cash)


def gross_margin_periods(rows: list[FinancialData]) -> list[float]:
    """最近可用毛利率为锚，只保留同财政年内位置、同类型同口径的最多四期。"""
    valid = [r for r in rows if is_finite_number(r.gross_margin) and r.period_start
             and r.period_start <= r.fiscal_quarter and r.period_basis != "unknown"
             and r.reporting_basis and r.gross_margin_basis and _source(r, "gross_margin")]
    if not valid:
        return []
    latest = valid[0]

    def signature(row: FinancialData):
        start = row.period_start
        return (row.period_basis, row.reporting_basis, row.gross_margin_basis,
                _source(row, "gross_margin"), row.fiscal_quarter.month, row.fiscal_quarter.day,
                start.month if start else None, start.day if start else None,
                row.fiscal_quarter.year - start.year if start else None)

    return [r.gross_margin for r in valid if r.gross_margin is not None and signature(r) == signature(latest)][:4]
