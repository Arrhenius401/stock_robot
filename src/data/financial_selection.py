"""财务包选择门面：复用通用整包规则，保留既有 A 股接口。"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

from data.package_selection import (
    Position,
    Selection,
    SourcePackage,
    select_package,
)
from data.schemas import FinancialData

NUMERIC_FIELDS = frozenset({
    "revenue", "net_profit", "deducted_net_profit", "total_assets", "total_equity",
    "common_equity", "operating_cash_flow", "operating_cash_flow_per_share",
    "roe", "gross_margin", "basic_eps",
})


@dataclass
class FinancialPackage(SourcePackage[FinancialData]):
    pass


@dataclass
class FinancialSelection(Selection[FinancialData]):
    pass


def _valid(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _row_compatible(row: FinancialData, position: Position, name: str) -> bool:
    return not (
        (row.report_currency and row.report_currency != position.currency)
        or (position.basis != "instant" and row.period_start is not None
            and row.period_start != position.start)
        or (position.basis != "instant" and row.period_basis != "unknown"
            and row.period_basis != position.basis)
        or (name == "operating_cash_flow" and position.calculation not in {"reported", "estimated"})
        or (name == "operating_cash_flow" and row.cash_flow_period_start is not None
            and row.cash_flow_period_start != position.start)
        or (name == "operating_cash_flow" and _valid(row.operating_cash_flow)
            and row.cash_flow_basis != position.calculation)
    )


def _apply_metadata(row: FinancialData, name: str, other: FinancialData) -> None:
    if name == "operating_cash_flow":
        row.cash_flow_basis = other.cash_flow_basis
        row.cash_flow_period_start = other.cash_flow_period_start
    elif name == "gross_margin":
        row.gross_margin_basis = other.gross_margin_basis


def _as_financial(package: SourcePackage[FinancialData]) -> FinancialPackage:
    if isinstance(package, FinancialPackage):
        return package
    return FinancialPackage(package.source, package.rows, package.positions)


def _wrap_completeness(
    completeness: Callable[[FinancialPackage], bool],
) -> Callable[[SourcePackage[FinancialData]], bool]:
    def wrapped(package: SourcePackage[FinancialData]) -> bool:
        return completeness(_as_financial(package))
    return wrapped


def select_financial(
    providers: Sequence[tuple[str, Callable[[], FinancialPackage]]], *,
    symbol: str,
    required: frozenset[str],
    required_periods: frozenset[date] = frozenset(),
    completeness: Callable[[FinancialPackage], bool] | None = None,
    dependencies: Sequence[frozenset[str]] = (),
    on_error: Callable[[str, Exception], None] | None = None,
) -> FinancialSelection:
    result = select_package(
        providers,
        entity=symbol,
        fields=NUMERIC_FIELDS,
        entity_of=lambda row: row.symbol,
        period_of=lambda row: row.fiscal_quarter,
        required=required,
        required_periods=required_periods,
        completeness=_wrap_completeness(completeness) if completeness is not None else None,
        dependencies=dependencies,
        row_compatible=_row_compatible,
        apply_metadata=_apply_metadata,
        on_error=on_error,
    )
    return FinancialSelection(
        package=_as_financial(result.package) if result.package is not None else None,
        complete=result.complete,
        supplements=result.supplements,
    )
