"""采集、缓存和报告共同使用的确定性财务事实判据。"""
import math
from collections.abc import Iterable, Mapping
from datetime import date
from numbers import Real
from typing import Any

from data.schemas import FinancialData

FINANCIAL_FACT_FIELDS = frozenset({
    "revenue", "net_profit", "total_assets", "total_equity", "roe", "gross_margin",
    "operating_cash_flow", "operating_cash_flow_per_share",
})
FINANCIAL_ANALYSIS_FIELDS = FINANCIAL_FACT_FIELDS | {"revenue_growth_yoy", "profit_growth_yoy"}


def is_finite_fact(value: Any) -> bool:
    """零和负数也是事实；布尔值、字符串与非有限数不是指标。"""
    if not isinstance(value, Real) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def has_finite_facts(values: Mapping[str, Any], fields: Iterable[str]) -> bool:
    return any(is_finite_fact(values.get(field)) for field in fields)


def financial_has_facts(records: list[FinancialData], symbol: str | None = None) -> bool:
    """只缓存标的/报告期合法且至少一个关键实值存在的财报。"""
    if not records:
        return False
    expected = symbol or records[0].symbol
    if not expected or any(item.symbol != expected or not isinstance(item.fiscal_quarter, date)
                           for item in records):
        return False
    return any(has_finite_facts(item.model_dump(), FINANCIAL_FACT_FIELDS) for item in records)
