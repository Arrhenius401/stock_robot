"""港股逐期币种/单位交叉核验；不把目录币种当作金额证据。"""
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import akshare as ak

from data.hk_eastmoney import finite_number
from data.schemas import FinancialData

logger = logging.getLogger(__name__)
SOURCE = "东方财富港股分析指标"
_STANDARD = {"revenue": ("004001001", "004001002"), "profit": ("004025002",)}
_BANK = {"revenue": ("001003999",), "profit": ("001025002",)}


@dataclass(frozen=True)
class PeriodCurrencyEvidence:
    period: date
    start: date | None
    currency: str
    verified: bool
    reason: str


def _date(value: Any) -> date | None:
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except (TypeError, ValueError):
        return None


def parse_indicator_rows(frame: Any) -> dict[date, dict[str, Any]]:
    if frame is None or getattr(frame, "empty", True):
        raise ValueError("港股分析指标为空")
    rows: dict[date, dict[str, Any]] = {}
    conflicts: set[date] = set()
    for record in frame.to_dict("records"):
        period = _date(record.get("REPORT_DATE"))
        if period is None:
            continue
        if period in rows and rows[period] != record:
            conflicts.add(period)
        rows[period] = record
    for period in conflicts:
        rows.pop(period, None)
    if not rows:
        raise ValueError("港股分析指标没有有效报告期")
    return rows


def load_indicator_periods(code: str) -> dict[date, dict[str, Any]]:
    return parse_indicator_rows(ak.stock_financial_hk_analysis_indicator_em(
        symbol=code, indicator="报告期"))


def _close(left: Any, right: Any) -> bool:
    a, b = finite_number(left), finite_number(right)
    return a is not None and b is not None and math.isclose(a, b, rel_tol=1e-6, abs_tol=1.0)


def verify_period(period: date, start: date | None,
                  income_rows: list[dict[str, Any]], indicator: dict[str, Any] | None,
                  *, bank: bool) -> PeriodCurrencyEvidence:
    if indicator is None:
        return PeriodCurrencyEvidence(period, start, "", False, "未取得同期分析指标")
    if _date(indicator.get("REPORT_DATE")) != period:
        return PeriodCurrencyEvidence(period, start, "", False, "分析指标与报表报告期不一致")
    currency = FinancialData.normalize_report_currency(indicator.get("CURRENCY"))
    indicator_start = _date(indicator.get("START_DATE"))
    if not currency:
        return PeriodCurrencyEvidence(period, start, "", False, "分析指标未提供有效币种")
    if start is None or indicator_start != start:
        return PeriodCurrencyEvidence(period, start, currency, False, "分析指标与报表期间起点不一致")
    flag = str(indicator.get("IS_CNY_CODE", ""))
    if flag and ((currency == "CNY") != (flag == "1")):
        return PeriodCurrencyEvidence(period, start, currency, False, "IS_CNY_CODE 与币种矛盾")
    codes = _BANK if bank else _STANDARD
    selected = [row for row in income_rows if _date(row.get("REPORT_DATE")) == period
                and _date(row.get("START_DATE")) == start]
    identities = {str(row.get("SECUCODE", "")) for row in selected}
    if len(identities) != 1 or not next(iter(identities), "").endswith(".HK"):
        return PeriodCurrencyEvidence(period, start, currency, False, "同期报表身份缺失或冲突")
    amounts: dict[str, float | None] = {}
    for row in selected:
        explicit = FinancialData.normalize_report_currency(row.get("CURRENCY_CODE") or row.get("CURRENCY"))
        if explicit and explicit != currency:
            return PeriodCurrencyEvidence(period, start, currency, False, "报表与指标币种不一致")
        code, value = str(row.get("STD_ITEM_CODE")), finite_number(row.get("AMOUNT"))
        if code in amounts and amounts[code] != value:
            return PeriodCurrencyEvidence(period, start, currency, False, "同期报表科目金额冲突")
        amounts[code] = value
    verified_amounts: dict[str, float] = {}
    for code in (*codes["revenue"], *codes["profit"]):
        value = amounts.get(code)
        if value is None:
            return PeriodCurrencyEvidence(period, start, currency, False, "同期报表核验科目金额缺失，不补零")
        verified_amounts[code] = value
    revenue = sum(verified_amounts[code] for code in codes["revenue"])
    profit = sum(verified_amounts[code] for code in codes["profit"])
    if not _close(revenue, indicator.get("OPERATE_INCOME")):
        return PeriodCurrencyEvidence(period, start, currency, False, "报表收入聚合与指标接口不一致")
    if not _close(profit, indicator.get("HOLDER_PROFIT")):
        return PeriodCurrencyEvidence(period, start, currency, False, "报表利润聚合与指标接口不一致")
    return PeriodCurrencyEvidence(period, start, currency, True, "")


def verify_cash_flow(start: date | None, cash_flow: Any,
                     indicator: dict[str, Any] | None, currency: str) -> tuple[bool, str]:
    """用指标接口的 OCF_SALES 与同币种收入反推 OCF，核验现金流金额币种/单位。"""
    if cash_flow is None:
        return False, "经营现金流金额缺失"
    if indicator is None or not currency:
        return False, "缺少同期分析指标或币种"
    if _date(indicator.get("START_DATE")) != start:
        return False, "分析指标与现金流量表期间起点不一致"
    revenue = finite_number(indicator.get("OPERATE_INCOME"))
    ratio = finite_number(indicator.get("OCF_SALES"))
    if revenue is None or ratio is None or revenue <= 0:
        return False, "分析指标未提供可用于核验的 OCF_SALES"
    derived = revenue * ratio / 100
    if not math.isclose(float(cash_flow), derived, rel_tol=1e-4, abs_tol=1.0):
        return False, "经营现金流与指标 OCF_SALES 反推值不一致"
    return True, ""
