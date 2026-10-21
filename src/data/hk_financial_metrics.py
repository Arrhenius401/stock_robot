"""港股原始利润表的确定性派生指标，不发起请求或改写原始金额。"""
import logging
from datetime import date
from typing import Any

from data.hk_eastmoney import finite_number
from data.schemas import FinancialData

logger = logging.getLogger(__name__)

GROSS_MARGIN_BASIS = (
    "计算值：同期间披露毛利÷营运收入；营运收入可能包含其他营业收入，"
    "与本报告保留的营业额口径不同；未换汇"
)
_GROSS_PROFIT = "004007999"
_OPERATING_REVENUE = "004001999"


def gross_margins(records: list[dict[str, Any]], symbol: str) -> dict[date, float]:
    """只合并同期间、无冲突的原表科目；不回退营业额或成本反推值。"""
    amounts: dict[date, dict[str, set[float]]] = {}
    starts: dict[date, set[date]] = {}
    currencies: dict[date, set[str]] = {}
    units: dict[date, set[str]] = {}
    invalid: set[date] = set()
    for row in records:
        item = str(row.get("STD_ITEM_CODE", ""))
        if item not in {_GROSS_PROFIT, _OPERATING_REVENUE}:
            continue
        if row.get("SECUCODE") != symbol or row.get("SECURITY_CODE") != symbol.removesuffix(".HK"):
            continue
        try:
            end = date.fromisoformat(str(row.get("REPORT_DATE"))[:10])
        except (ValueError, TypeError):
            logger.debug("港股毛利率科目报告日期无效，不采用该行")
            continue
        try:
            start = date.fromisoformat(str(row.get("START_DATE"))[:10])
            if start > end:
                raise ValueError("实际期间起点晚于终点")
        except (ValueError, TypeError):
            logger.debug("港股毛利率实际期间无效，保留该期其他指标")
            invalid.add(end)
            continue
        amount = finite_number(row.get("AMOUNT"))
        if amount is None:
            invalid.add(end)
            continue
        amounts.setdefault(end, {}).setdefault(item, set()).add(amount)
        starts.setdefault(end, set()).add(start)
        for key in ("CURRENCY", "CURRENCY_CODE"):
            raw = row.get(key)
            if raw is not None and raw != "":
                currency = FinancialData.normalize_report_currency(raw)
                if not currency:
                    invalid.add(end)
                else:
                    currencies.setdefault(end, set()).add(currency)
        # 端点目前返回基本单位金额；跨字段单位无法证明等价时也保守降级，不猜测换算。
        for key in ("UNIT", "UNIT_CODE", "AMOUNT_UNIT"):
            raw = row.get(key)
            if raw is not None and str(raw).strip():
                units.setdefault(end, set()).add(str(raw).strip().casefold())

    result = {}
    for end, subjects in amounts.items():
        if (end in invalid or len(starts[end]) != 1 or len(currencies.get(end, set())) > 1
                or len(units.get(end, set())) > 1):
            continue
        gross, revenue = subjects.get(_GROSS_PROFIT, set()), subjects.get(_OPERATING_REVENUE, set())
        if len(gross) != 1 or len(revenue) != 1:
            continue
        denominator = next(iter(revenue))
        if denominator > 0:
            ratio = finite_number(next(iter(gross)) / denominator)
            if ratio is not None:
                result[end] = ratio
    return result
