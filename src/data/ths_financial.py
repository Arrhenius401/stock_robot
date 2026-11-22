"""同花顺原生精度长表；仅声明已验证的字段，不推断修订版本。"""
import logging
from datetime import date
from typing import Any, Literal

import akshare as ak

from data.schemas import FinancialData
from utils.numbers import is_finite_number, parse_cn_number

logger = logging.getLogger(__name__)
SUMMARY = {
    "revenue": "operating_income_total", "net_profit": "parent_holder_net_profit",
    "deducted_net_profit": "index_deduct_holder_net_profit", "basic_eps": "basic_eps",
    "operating_cash_flow_per_share": "index_per_operating_cash_flow_net",
    "roe": "index_weighted_avg_roe", "gross_margin": "sale_gross_margin",
    "net_margin": "sale_net_interest_ratio",
}
GROWTH = {
    "revenue_growth_yoy": "calculate_operating_income_total_yoy_growth_ratio",
    "profit_growth_yoy": "calculate_parent_holder_net_profit_yoy_growth_ratio",
    "deducted_profit_growth_yoy": "deduct_net_profit_yoy_growth_ratio",
}
BALANCE = {"total_assets": "assets_total", "total_equity": "holder_equity_total"}


def source_precision(raw: object) -> Literal["source_native", "rounded", "unknown"]:
    if isinstance(raw, bool) or parse_cn_number(raw) is None:
        return "unknown"
    return "rounded" if isinstance(raw, str) and raw.strip().endswith(("万", "亿")) else "source_native"


def _load(symbol: str, api: str) -> dict[date, dict[str, dict[str, Any]]]:
    try:
        frame: Any = getattr(ak, api)(symbol=symbol)
        result: dict[date, dict[str, dict[str, Any]]] = {}
        for raw in frame.to_dict("records"):
            day = date.fromisoformat(str(raw["report_date"])[:10])
            name = str(raw["metric_name"])
            existing = result.setdefault(day, {}).get(name)
            if existing is not None and existing != raw:
                raise ValueError("同花顺长表重复科目冲突")
            result[day][name] = raw
        return result
    except Exception as exc:  # noqa: BLE001 — 第三方长表接口隔离，保留明确缺失
        logger.warning("同花顺原生精度接口失败 %s/%s: %s", symbol, api, type(exc).__name__)
        return {}


def _value(table: dict[str, dict[str, Any]], key: str) -> float | None:
    raw = table.get(key, {}).get("value")
    value = parse_cn_number(raw)
    return value if source_precision(raw) == "source_native" and is_finite_number(value) else None


def attach_positions(row: FinancialData) -> None:
    """源直接披露可用；revision 留空，不能充当跨源补位或跨期修订证据。"""
    ratios = {"roe", "gross_margin", "net_margin", *row.reported_metrics}
    per_share = {"basic_eps", "operating_cash_flow_per_share"}
    instant = {"total_assets", "total_equity", "common_equity"}
    for field in (*row.field_precision, *row.reported_metrics):
        row.field_positions[field] = {
            "definition": {"net_profit": "parent_net_profit",
                           "deducted_net_profit": "deducted_parent_net_profit"}.get(field, field),
            "unit": "ratio" if field in ratios else "currency_per_share" if field in per_share else "currency",
            "currency": row.report_currency, "start": str(row.fiscal_quarter if field in instant else row.period_start or ""),
            "basis": "instant" if field in instant else row.period_basis, "scope": "consolidated",
            "accounting": "", "revision": "", "calculation": "reported",
        }


def fetch_ths_native(symbol: str) -> list[FinancialData]:
    summary = _load(symbol, "stock_financial_abstract_new_ths")
    if not summary:
        return []
    balance = _load(symbol, "stock_financial_debt_new_ths")
    cash = _load(symbol, "stock_financial_cash_new_ths")
    if not balance or not cash:
        logger.warning("同花顺新版三表缺包 %s，按来源包整体回退旧接口，不拼接未知修订版本", symbol)
        return []
    output = []
    for day in sorted(summary)[-12:]:
        if (day.month, day.day) not in {(3, 31), (6, 30), (9, 30), (12, 31)}:
            continue
        values = {field: _value(summary[day], key) for field, key in SUMMARY.items()}
        for field in ("roe", "gross_margin", "net_margin"):
            value = values[field]
            if value is not None:
                values[field] = value / 100
        values.update({field: _value(balance.get(day, {}), key) for field, key in BALANCE.items()})
        row = FinancialData(
            symbol=symbol, fiscal_quarter=day, period_start=date(day.year, 1, 1),
            period_basis="cumulative", reporting_basis="同花顺财务摘要-按报告期", report_currency="CNY",
            financial_source="同花顺财报", precision_version=1,
            gross_margin_basis="同花顺新版财务摘要-销售毛利率（源披露）" if values["gross_margin"] is not None else "",
        )
        for field, value in values.items():
            setattr(row, field, value)
        row.net_margin_basis = "同花顺源披露销售净利率" if row.net_margin is not None else "未取得源披露净利率"
        if row.total_equity is not None:
            other = _value(balance.get(day, {}), "other_equity_tools")
            preferred = _value(balance.get(day, {}), "preferred_stock")
            if other is not None and preferred is not None:
                row.common_equity = row.total_equity - other - preferred
        row.operating_cash_flow = _value(cash.get(day, {}), "act_cash_flow_net")
        row.capital_expenditure = _value(cash.get(day, {}), "pay_fixed_assets_etc_cash")
        if row.capital_expenditure is not None:
            row.capital_expenditure = abs(row.capital_expenditure)
        if row.operating_cash_flow is not None:
            row.cash_flow_basis = "reported"
            row.cash_flow_period_start = row.period_start
            if row.capital_expenditure is not None:
                row.free_cash_flow = row.operating_cash_flow - row.capital_expenditure
        for metric, key in GROWTH.items():
            value = _value(summary[day], key)
            if value is not None:
                row.reported_metrics[metric] = value / 100
        # 此长表 yoy 列是小数比例，不是摘要同比字段的百分数单位。
        cash_yoy = cash.get(day, {}).get("act_cash_flow_net", {}).get("yoy")
        parsed_cash_yoy = parse_cn_number(cash_yoy)
        if (source_precision(cash_yoy) == "source_native" and parsed_cash_yoy is not None
                and is_finite_number(parsed_cash_yoy)):
            row.reported_metrics["cash_flow_growth_yoy"] = parsed_cash_yoy
        for field in (*SUMMARY, *BALANCE, "common_equity", "operating_cash_flow",
                      "capital_expenditure", "free_cash_flow"):
            if is_finite_number(getattr(row, field)):
                row.field_precision[field] = "source_native"
        row.field_precision.update(dict.fromkeys(row.reported_metrics, "source_native"))
        row.field_sources = dict.fromkeys(row.field_precision, "同花顺财报")
        attach_positions(row)
        if row.revenue is not None or row.net_profit is not None:
            output.append(row)
    return output
