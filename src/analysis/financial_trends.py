"""同口径多期财报的固定模板摘要；不参与评分、风险标签或模型调用。"""
import itertools
from datetime import date
from typing import Any

from data.schemas import FinancialData
from report.formatter import format_metric_value
from utils.numbers import is_finite_number

_FIELDS = {
    "revenue": "营收", "net_profit": "净利润", "deducted_net_profit": "扣非净利润",
    "gross_margin": "毛利率", "roe": "ROE", "operating_cash_flow": "经营现金流",
}
_RATIOS = {"gross_margin", "roe"}


def _previous_year(day: date) -> date | None:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return None


def _same_period(before: FinancialData, after: FinancialData, field: str) -> bool:
    if (before.period_basis == "unknown" or before.period_basis != after.period_basis
            or not before.reporting_basis or before.reporting_basis != after.reporting_basis
            or before.period_start is None or after.period_start is None
            or before.period_start > before.fiscal_quarter or after.period_start > after.fiscal_quarter
            or _previous_year(after.fiscal_quarter) != before.fiscal_quarter
            or _previous_year(after.period_start) != before.period_start):
        return False
    if field not in _RATIOS and (not before.report_currency
                                or before.report_currency != after.report_currency):
        return False
    if field == "operating_cash_flow" and (
            before.cash_flow_basis != "reported" or after.cash_flow_basis != "reported"
            or before.cash_flow_period_start not in (None, before.period_start)
            or after.cash_flow_period_start not in (None, after.period_start)):
        return False
    left, right = getattr(before, field), getattr(after, field)
    if not is_finite_number(left) or not is_finite_number(right):
        return False
    return field not in _RATIOS or all(is_finite_number(v) for v in (left * 100, right * 100, (right - left) * 100))


def _amount(field: str, value: float) -> str:
    # 扣非净利润复用净利润的金额单位契约。
    return str(format_metric_value("net_profit" if field == "deducted_net_profit" else field, value))


def _change(before: float, after: float) -> str:
    if after == before:
        return "同比持平"
    rate = (after - before) / before * 100
    if not is_finite_number(rate):
        return "同比变化率超出有效数值范围"
    return f"同比{'增长' if after > before else '下降'}{abs(rate):.2f}%"


def build_financial_trends(records: list[FinancialData], symbol: str) -> dict[str, Any]:
    """使用实际期间和有限数；冲突重复期不任意选值，缺币种时不作金额比较。"""
    by_date: dict[date, FinancialData] = {}
    conflicts: set[date] = set()
    for row in records:
        if row.symbol != symbol:
            continue
        day = row.fiscal_quarter
        if day in by_date and row != by_date[day]:
            conflicts.add(day)
        by_date[day] = row
    if not by_date:
        return {"highlights": [], "note": "暂无可比财报；不判断经营趋势。", "latest_yoy": {}}
    newest = max(by_date)
    rows = [row for day, row in sorted(by_date.items()) if day not in conflicts]
    latest = by_date[newest] if newest not in conflicts else None
    candidates: list[tuple[int, int, frozenset[str], str]] = []
    latest_yoy: dict[str, float] = {}
    note: list[str] = []
    if any(row.period_basis == "unknown" or not row.reporting_basis or row.period_start is None for row in rows):
        note.append("部分报告期间口径未记录，不用于趋势比较")
    if any(not row.report_currency for row in rows):
        note.append("币种未记录的期间不作金额趋势比较；不换汇")
    if any(row.cash_flow_basis == "estimated" for row in rows):
        note.append("经营现金流含推算值，未纳入连续趋势")
    if conflicts:
        note.append("重复报告期数值或口径冲突，已排除")
    if latest is None:
        return {"highlights": [], "note": "；".join(note), "latest_yoy": {}}
    previous_day = _previous_year(newest)
    previous = by_date.get(previous_day) if previous_day is not None else None
    if previous is not None and previous.fiscal_quarter in conflicts:
        previous = None
    previous_label = previous.fiscal_quarter.isoformat() if previous is not None else ""

    def pair(field: str) -> tuple[float, float] | None:
        if previous is None or not _same_period(previous, latest, field):
            return None
        return getattr(previous, field), getattr(latest, field)

    # 两期指标背离直接描述事实，不编造成本、需求或现金回收原因。
    rev, profit = pair("revenue"), pair("net_profit")
    if rev and profit and rev[0] > 0 and profit[0] > 0 and profit[1] >= 0 and rev[1] > rev[0] and profit[1] < profit[0]:
        candidates.append((1, -1, frozenset({"revenue", "net_profit"}), (f"{newest} 较 {previous_label}：营收增长而净利润下降"
                           f"（营收{_change(*rev)}，净利润{_change(*profit)}），收入与利润变化背离。")))
    cf = pair("operating_cash_flow")
    if profit and cf and profit[1] > profit[0] > 0 and cf[1] < cf[0]:
        candidates.append((1, 0, frozenset({"net_profit", "operating_cash_flow"}), (f"{newest} 较 {previous_label}：净利润增长，"
                           f"经营现金流由 {_amount('operating_cash_flow', cf[0])} 降至 "
                           f"{_amount('operating_cash_flow', cf[1])}，两者变化背离。")))

    for order, (field, label) in enumerate(_FIELDS.items()):
        values = pair(field)
        if values is None:
            continue
        before, after = values
        if field in {"revenue", "net_profit"} and before > 0 and after >= 0:
            growth = (after - before) / before
            if is_finite_number(growth) and is_finite_number(growth * 100):
                latest_yoy["revenue_growth_yoy" if field == "revenue" else "profit_growth_yoy"] = round(growth, 4)
        dates = f"{newest} 较 {previous_label}"
        if field in {"net_profit", "deducted_net_profit"} and (before <= 0 or after < 0):
            state = ("扭亏为盈" if before < 0 < after else "由盈转亏" if before > 0 > after
                     else "亏损收窄" if before < after <= 0 else "亏损扩大" if after < before < 0
                     else "零基数变化" if before == 0 else "亏损持平" if before == after
                     else "由亏损转为盈亏平衡")
            candidates.append((0, order, frozenset({field}), (f"{dates}：{label}{state}（{_amount(field, before)} → "
                               f"{_amount(field, after)}），不计算普通同比增长率。")))
            continue

        # 相同报告期间跨年串联；缺年不能称为连续趋势。
        chain = [latest]
        while len(chain) < 3:
            previous_day = _previous_year(chain[-1].fiscal_quarter)
            prev = by_date.get(previous_day) if previous_day is not None else None
            if prev is None or prev.fiscal_quarter in conflicts or not _same_period(prev, chain[-1], field):
                break
            chain.append(prev)
        chain.reverse()
        sequence = [getattr(row, field) for row in chain]
        differences = [b - a for a, b in itertools.pairwise(sequence)]
        continuous = len(chain) == 3 and (all(d > 0 for d in differences) or all(d < 0 for d in differences))
        if continuous:
            direction = "连续提高" if field in _RATIOS and after > before else "连续下降" if after < before else "连续增长"
            evidence = " → ".join(f"{row.fiscal_quarter} {_amount(field, getattr(row, field))}" for row in chain)
            candidates.append((2, order, frozenset({field}), f"同报告期间{label}{direction}（{evidence}）。"))
        elif field in _RATIOS:
            delta = (after - before) * 100
            description = "持平" if delta == 0 else f"{'提高' if delta > 0 else '下降'} {abs(delta):.2f} 个百分点"
            candidates.append((4, order, frozenset({field}), (f"{dates}：{label}由 {_amount(field, before)} 至 "
                               f"{_amount(field, after)}，同比{description}。")))
        elif before > 0:
            candidates.append((4, order, frozenset({field}), (f"{dates}：{label}{_change(before, after)}"
                               f"（{_amount(field, before)} → {_amount(field, after)}）。")))
        else:
            candidates.append((4, order, frozenset({field}), (f"{dates}：{label}由 {_amount(field, before)} 至 "
                               f"{_amount(field, after)}；非正基数，不计算普通同比增长率。")))

        # 最近各披露期间的同比增速；不把累计值相减为未经披露的单季度。
        if field not in {"revenue", "net_profit", "deducted_net_profit"}:
            continue
        rates: list[tuple[date, float]] = []
        for row in rows:
            if (row.reporting_basis != latest.reporting_basis or row.report_currency != latest.report_currency
                    or row.period_basis != latest.period_basis):
                continue
            previous_day = _previous_year(row.fiscal_quarter)
            prev = by_date.get(previous_day) if previous_day is not None else None
            if (prev is not None and prev.fiscal_quarter not in conflicts and _same_period(prev, row, field)
                    and getattr(prev, field) > 0 and getattr(row, field) >= 0):
                rate = (getattr(row, field) / getattr(prev, field) - 1) * 100
                if is_finite_number(rate):
                    rates.append((row.fiscal_quarter, rate))
        recent = rates[-3:]
        gaps = [(b.year - a.year) * 12 + b.month - a.month for (a, _), (b, _) in itertools.pairwise(recent)]
        deltas = [b - a for (_, a), (_, b) in itertools.pairwise(recent)]
        if (len(recent) == 3 and recent[-1][0] == newest and gaps[0] == gaps[1] and gaps[0] in (3, 6)
                and (all(d > 0 for d in deltas) or all(d < 0 for d in deltas))):
            direction = "提高" if deltas[0] > 0 else "下降"
            evidence = " → ".join(f"{day} {rate:.2f}%" for day, rate in recent)
            kind = "累计" if latest.period_basis == "cumulative" else "单期"
            candidates.append((2, order - 10, frozenset({field}), f"{kind}{label}同比增速连续{direction}（{evidence}）。"))

    highlights: list[str] = []
    covered: set[str] = set()
    for _, _, fields, description in sorted(candidates, key=lambda item: item[:2]):
        if covered.intersection(fields):
            continue
        highlights.append(description)
        covered.update(fields)
        if len(highlights) == 3:
            break
    if not highlights:
        note.append("同口径同期样本不足，暂不判断经营趋势")
    return {"highlights": highlights, "note": "；".join(dict.fromkeys(note)), "latest_yoy": latest_yoy}
