"""跨市场财务期间计算，缺失不补造。"""
from datetime import timedelta

from data.schemas import FinancialData
from utils.numbers import is_finite_number


def _hk_verified(rows: list[FinancialData], field: str) -> bool:
    return bool(rows) and all(
        row.amount_currency_verified and row.report_currency for row in rows
    ) and len({row.display_currency or row.report_currency for row in rows}) == 1 and all(
        row.field_positions.get(field, {}).get("unit", row.amount_unit) in {"", "currency"}
        and (not row.display_currency or row.amount_unit == "currency") for row in rows
    )


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
    if market == "hk" and latest.period_start:
        def is_annual(row: FinancialData) -> bool:
            return bool(row.period_start and 350 <= (row.fiscal_quarter - row.period_start).days <= 380)

        if is_annual(latest):
            value = getattr(latest, field)
            return value if _hk_verified([latest], field) and usable([latest]) and is_finite_number(value) else None
        annual_row = next((row for row in reversed(ordered) if is_annual(row)
                           and row.fiscal_quarter + timedelta(days=1) == latest.period_start), None)
        previous_row = next((row for row in reversed(ordered)
                             if row.period_start and latest.period_start
                             and row.period_basis == latest.period_basis == "cumulative"
                             and (row.fiscal_quarter.month, row.fiscal_quarter.day) == (end.month, end.day)
                             and row.fiscal_quarter.year == end.year - 1
                             and row.period_start.year == latest.period_start.year - 1
                             and (row.period_start.month, row.period_start.day) ==
                                 (latest.period_start.month, latest.period_start.day)), None)
        if annual_row is None or previous_row is None:
            return None
        selected = [latest, annual_row, previous_row]
        values = [getattr(row, field) for row in selected]
        return (values[0] + values[1] - values[2] if _hk_verified(selected, field) and usable(selected)
                and all(is_finite_number(value) for value in values) else None)
    if end.month == 12:
        if market == "hk" and not _hk_verified([latest], field):
            return None
        return getattr(latest, field) if usable([latest]) else None
    annual = next((row for row in reversed(ordered)
                   if row.fiscal_quarter.year == end.year - 1 and row.fiscal_quarter.month == 12), None)
    previous = next((row for row in reversed(ordered)
                     if row.fiscal_quarter.year == end.year - 1 and row.fiscal_quarter.month == end.month), None)
    if annual is None or previous is None:
        return None
    selected = [latest, annual, previous]
    if market == "hk" and not _hk_verified(selected, field):
        return None
    values = [getattr(row, field) for row in selected]
    return values[0] + values[1] - values[2] if usable(selected) and all(is_finite_number(value) for value in values) else None
