"""财务指标统一入口：仅消费事实，不发起网络请求。"""
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from data.collection_state import ValueResult, make_record
from data.package_selection import Position
from data.schemas import AnalysisContext, FinancialData
from utils.numbers import is_finite_number

METRIC_FIELDS = {
    "revenue_growth_yoy": "revenue",
    "profit_growth_yoy": "net_profit",
    "deducted_profit_growth_yoy": "deducted_net_profit",
    "cash_flow_growth_yoy": "operating_cash_flow",
}


@dataclass(frozen=True)
class FinancialMetricFact:
    value: float
    metric: str
    method: Literal["reported", "calculated"]
    source: str
    period_start: date
    period_end: date
    position: Position
    dependencies: tuple[str, ...]
    precision: str = "source_native"


def native_amounts(rows: list[FinancialData], fields: tuple[str, ...]) -> bool:
    """不从换算后的数值位数反推原始精度。"""
    return bool(rows) and all(
        row.field_precision.get(field) == "source_native"
        and is_finite_number(getattr(row, field))
        for row in rows for field in fields
    )


def _position(row: FinancialData, field: str) -> Position | None:
    raw = row.field_positions.get(field, {})
    try:
        return Position(
            definition=raw["definition"], unit=raw["unit"], currency=raw["currency"],
            start=date.fromisoformat(raw["start"]), basis=raw["basis"],
            scope=raw["scope"], accounting=raw.get("accounting", ""),
            revision=raw.get("revision", ""), calculation=raw.get("calculation", ""),
        )
    except (KeyError, ValueError, TypeError):
        return None


def _source(row: FinancialData, field: str) -> str:
    return row.field_sources.get(field) or row.financial_source or row._data_source


def _position_matches(row: FinancialData, pos: Position | None) -> bool:
    return bool(pos is not None and row.period_start is not None
                and pos.start == row.period_start <= row.fiscal_quarter
                and pos.basis == row.period_basis and pos.basis in {"cumulative", "single"}
                and pos.currency == (row.display_currency or row.report_currency) and pos.currency
                and pos.scope == "consolidated")


def comparable_fields(before: FinancialData, after: FinancialData, field: str) -> bool:
    """跨期比较复用同一证据规则，不猜测修订一致。"""
    left, right = _position(before, field), _position(after, field)
    if field == "operating_cash_flow" and any(
            row.cash_flow_basis != "reported" for row in (before, after)):
        return False
    try:
        previous_end = after.fiscal_quarter.replace(year=after.fiscal_quarter.year - 1)
        previous_start = after.period_start.replace(year=after.period_start.year - 1) if after.period_start else None
    except ValueError:
        return False
    return bool(
        native_amounts([before, after], (field,))
        and before.fiscal_quarter == previous_end
        and _position_matches(before, left) and _position_matches(after, right)
        and left is not None and right is not None and left.proven() and right.proven()
        and left.start == previous_start and _source(after, field)
        and _source(after, field) == _source(before, field)
        and before.reporting_basis == after.reporting_basis
        and all(getattr(left, key) == getattr(right, key) for key in
                ("definition", "unit", "currency", "basis", "scope", "accounting", "revision"))
        and right.unit == ("ratio" if field in {"roe", "gross_margin"} else "currency")
        and right.definition == {"net_profit": "parent_net_profit",
                                  "deducted_net_profit": "deducted_parent_net_profit"}.get(field, field))


def resolve_financial_metric(
    context: AnalysisContext, metric: str, period_end: date | None = None,
) -> ValueResult[FinancialMetricFact]:
    """披露值优先；精确复算额外要求明确的可比修订版本。比例单位为小数。"""
    if metric not in METRIC_FIELDS:
        raise ValueError(f"不支持的财务指标: {metric}")
    field = METRIC_FIELDS[metric]
    rows = [row for row in context.financial_data or [] if row.symbol == context.symbol]
    ending = period_end or max((row.fiscal_quarter for row in rows), default=None)
    selected = [row for row in rows if row.fiscal_quarter == ending]

    def missing(reason: str, *, na: bool = False) -> ValueResult[FinancialMetricFact]:
        return ValueResult(None, make_record(
            "财务指标入口", "not_applicable" if na else "partial",
            error_kind="not_applicable" if na else "insufficient_evidence", error_message=reason),
            [reason])

    if len(selected) != 1 or ending is None or ending > datetime.now().astimezone().date():
        return missing("报告期缺失、重复或晚于当前日期")
    latest = selected[0]
    try:
        previous_day = ending.replace(year=ending.year - 1)
    except ValueError:
        return missing("上年同期日期无法对应")
    previous = [row for row in rows if row.fiscal_quarter == previous_day]
    before = previous[0] if len(previous) == 1 else None
    right = _position(latest, field)
    comparable = before is not None and comparable_fields(before, latest, field)
    # 只有可证实的同口径基数才能否决披露值；无关历史不能覆盖本期事实。
    if comparable and before is not None and (
            getattr(before, field) <= 0 or getattr(latest, field) < 0):
        return missing("零或负基数/负利润变化，不计算普通同比", na=True)

    disclosed = latest.reported_metrics.get(metric)
    pos = _position(latest, metric)
    source = _source(latest, metric)
    if (is_finite_number(disclosed) and latest.field_precision.get(metric) == "source_native"
            and _position_matches(latest, pos) and pos is not None
            and pos.definition == metric and pos.unit == "ratio" and source
            and latest.period_start is not None):
        assert disclosed is not None
        return ValueResult(FinancialMetricFact(
            value=disclosed, metric=metric, method="reported", source=source,
            period_start=latest.period_start, period_end=ending, position=pos, dependencies=(metric,)),
            make_record(source, "complete"))

    if before is None or not native_amounts([before, latest], (field,)):
        return missing("缺少上年同期原生精度金额；舍入、估算或未知精度不参与精确复算")
    source = _source(latest, field)
    if not comparable or right is None:
        return missing("指标定义、同源期间、币种、范围、会计或明确修订口径不足/不一致")
    base, amount = getattr(before, field), getattr(latest, field)
    value = (amount - base) / base
    if not is_finite_number(value):
        return missing("复算结果超出有限数值范围")
    assert latest.period_start is not None
    return ValueResult(FinancialMetricFact(
        value=value, metric=metric, method="calculated", source=source,
        period_start=latest.period_start, period_end=ending, position=right,
        dependencies=(field, f"{previous_day}:{field}")), make_record(source, "complete"))
