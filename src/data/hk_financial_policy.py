"""港股财务完整性与来源标注；沿用现有分析消费字段。"""

from dataclasses import asdict
from datetime import date

from data.financial_selection import (
    NUMERIC_FIELDS,
    FinancialPackage,
    FinancialSelection,
)
from data.schemas import FinancialData
from utils.numbers import is_finite_number

# 港股分析实际展示/比较的最新快照字段；ROE、扣非、毛利率、普通股权益为可选。
HK_REQUIRED_FIELDS = frozenset({
    "revenue", "net_profit", "total_assets", "total_equity", "basic_eps",
})

HK_DEPENDENCIES = (
    frozenset({"revenue", "net_profit"}),
    frozenset({"total_assets", "total_equity"}),
    frozenset({"net_profit", "basic_eps"}),
)


def package_complete(package: FinancialPackage) -> bool:
    """沿用现有分析消费字段；不把港股评为 A 股的完整财报包。"""
    rows = package.rows
    if sum(
        any(is_finite_number(getattr(row, name)) for name in NUMERIC_FIELDS)
        for row in rows
    ) < 2:
        return False
    latest = max(rows, key=lambda row: row.fiscal_quarter)
    return (
        all(is_finite_number(getattr(latest, name)) for name in HK_REQUIRED_FIELDS)
        and (
            is_finite_number(latest.operating_cash_flow)
            or is_finite_number(latest.operating_cash_flow_per_share)
        )
    )


def annotate_selection(selection: FinancialSelection) -> list[FinancialData]:
    """为缓存、报告与模型事实保存一致的逐期字段来源。"""
    package = selection.package
    if package is None:
        return []
    providers = sorted({
        source for fields in selection.supplements.values() for source in fields.values()
    })
    source_label = package.source + (
        "；等效补位：" + "、".join(providers) if providers else ""
    )
    note = (
        "完整来源包" if selection.complete
        else f"{package.source}未能提供完整财务包，保留可用事实；未知口径不补位"
    )
    if providers and selection.complete:
        note = "字段已按等效口径补齐，非单一来源完整包"
    if package.source != "东方财富港股财报":
        note = "采用备用来源；" + note
    if providers:
        note = "按逐期等效口径补位；" + note
    for row in package.rows:
        row.financial_source = package.source
        row.field_sources = {
            name: selection.supplements.get(row.fiscal_quarter, {}).get(name, package.source)
            for name in NUMERIC_FIELDS if is_finite_number(getattr(row, name))
        }
        row.field_positions = {
            name: {
                key: value.isoformat() if isinstance(value, date) else str(value or "")
                for key, value in asdict(position).items()
            }
            for name, position in package.positions.get(row.fiscal_quarter, {}).items()
        }
        row.financial_selection_status = "complete" if selection.complete else "partial"
        row.financial_selection_note = note
        row._data_source = source_label
    return package.rows
