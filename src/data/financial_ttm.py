"""跨市场财务期间计算，缺失不补造。"""
from data.schemas import FinancialData
from utils.numbers import is_finite_number


def financial_ttm(rows: list[FinancialData], field: str, market: str, *,
                  require_precision: bool = False) -> float | None:
    ordered = sorted(rows, key=lambda row: row.fiscal_quarter)
    if not ordered:
        return None
    latest = ordered[-1]
    from data.financial_metrics import native_amounts

    def usable(selected: list[FinancialData]) -> bool:
        return not require_precision or native_amounts(selected, (field,))
    if market == "us":
        recent = ordered[-4:]
        values = [getattr(row, field) for row in recent]
        return sum(values) if len(recent) == 4 and usable(recent) and all(is_finite_number(value) for value in values) else None
    end = latest.fiscal_quarter
    if end.month == 12:
        return getattr(latest, field) if usable([latest]) else None
    annual = next((row for row in reversed(ordered) if row.fiscal_quarter.year == end.year - 1 and row.fiscal_quarter.month == 12), None)
    previous = next((row for row in reversed(ordered) if row.fiscal_quarter.year == end.year - 1 and row.fiscal_quarter.month == end.month), None)
    if annual is None or previous is None:
        return None
    values = [getattr(row, field) for row in (latest, annual, previous)]
    return values[0] + values[1] - values[2] if usable([latest, annual, previous]) and all(is_finite_number(value) for value in values) else None
