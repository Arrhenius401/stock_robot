"""同口径多期财报的固定模板摘要；不参与评分、风险标签或模型调用。"""
import itertools
from datetime import date
from typing import Any

from data.financial_metrics import (
    METRIC_FIELDS,
    comparable_fields,
    resolve_financial_metric,
)
from data.schemas import AnalysisContext, FinancialData
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
    if not comparable_fields(before, after, field):
        return False
    if (before.period_basis == "unknown" or before.period_basis != after.period_basis
            or not before.reporting_basis or before.reporting_basis != after.reporting_basis
            or before.period_start is None or after.period_start is None
            or before.period_start > before.fiscal_quarter or after.period_start > after.fiscal_quarter
            or _previous_year(after.fiscal_quarter) != before.fiscal_quarter
            or _previous_year(after.period_start) != before.period_start):
        return False
    if field not in _RATIOS and (not (before.display_currency or before.report_currency)
                                or (before.display_currency or before.report_currency)
                                != (after.display_currency or after.report_currency)):
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
    metric_context = AnalysisContext(symbol=symbol, name=symbol, financial_data=rows)
    resolved = {(row.fiscal_quarter, field): resolve_financial_metric(metric_context, metric, row.fiscal_quarter)
                for metric, field in METRIC_FIELDS.items() for row in rows}
    basis = []
    for metric, field in METRIC_FIELDS.items():
        result = resolved.get((newest, field))
        if result is not None and result.value is not None:
            latest_yoy[metric] = result.value.value
            basis.append(f"{_FIELDS[field]}同比：{result.value.source}·"
                         + ("源披露" if result.value.method == "reported" else "精确金额复算"))
    note: list[str] = []
    if any(row.period_basis == "unknown" or not row.reporting_basis or row.period_start is None for row in rows):
        note.append("部分报告期间口径未记录，不用于趋势比较")
    if any(not row.report_currency for row in rows):
        note.append("币种未记录的期间不作金额趋势比较；不换汇")
    if any(row.cash_flow_basis == "estimated" for row in rows):
        note.append("经营现金流含推算值，未纳入连续趋势")
    if any(row.field_precision.get(field, "unknown") != "source_native"
           for row in rows for field in ("revenue", "net_profit") if getattr(row, field) is not None):
        note.append("部分金额为舍入、估算或未知精度，不用于精确复算")
    if conflicts:
        note.append("重复报告期数值或口径冲突，已排除")
    if any((result := resolved.get((newest, field))) is not None and result.value is None
           for field in METRIC_FIELDS.values()):
        note.append("部分同比缺少有效披露值或精确可比金额/修订证据，未生成")
    if latest is None:
        return {"highlights": [], "note": "；".join(note), "latest_yoy": {}}
    previous_day = _previous_year(newest)
    previous = by_date.get(previous_day) if previous_day is not None else None
    if previous is not None and previous.fiscal_quarter in conflicts:
        previous = None
    previous_label = previous.fiscal_quarter.isoformat() if previous is not None else ""

    def change(field: str, values: tuple[float, float]) -> str:
        result = resolved.get((newest, field))
        if result is not None and result.value is not None:
            value = result.value.value * 100
            return f"同比{'增长' if value >= 0 else '下降'}{abs(value):.2f}%"
        return "金额增加" if values[1] > values[0] else "金额下降" if values[1] < values[0] else "金额持平"

    def pair(field: str) -> tuple[float, float] | None:
        if previous is None or not _same_period(previous, latest, field):
            return None
        return getattr(previous, field), getattr(latest, field)

    # 披露同比不依赖历史金额完整；所有增速序列只消费同一个入口。
    for order, field in enumerate(METRIC_FIELDS.values()):
        current = resolved.get((newest, field))
        if current is None or current.value is None:
            continue
        fact = current.value
        rate = fact.value * 100
        candidates.append((4, order, frozenset({field}),
            (f"{fact.period_start} 至 {fact.period_end}：{_FIELDS[field]}同比"
             f"{'增长' if rate >= 0 else '下降'}{abs(rate):.2f}%。")))
        rates = []
        for row in rows:
            result = resolved.get((row.fiscal_quarter, field))
            if result is None or result.value is None:
                continue
            other = result.value
            if (other.source != fact.source or any(
                    getattr(other.position, key) != getattr(fact.position, key)
                    for key in ("definition", "unit", "currency", "basis", "scope", "accounting"))):
                continue
            rates.append((row.fiscal_quarter, other.value * 100))
        recent = rates[-3:]
        gaps = [(b.year - a.year) * 12 + b.month - a.month for (a, _), (b, _) in itertools.pairwise(recent)]
        deltas = [b - a for (_, a), (_, b) in itertools.pairwise(recent)]
        if (len(recent) == 3 and recent[-1][0] == newest and gaps[0] == gaps[1] and gaps[0] in (3, 6)
                and (all(d > 0 for d in deltas) or all(d < 0 for d in deltas))):
            direction = "提高" if deltas[0] > 0 else "下降"
            evidence = " → ".join(f"{day} {value:.2f}%" for day, value in recent)
            kind = "累计" if latest.period_basis == "cumulative" else "单期"
            candidates.append((2, order - 10, frozenset({field}),
                f"{kind}{_FIELDS[field]}同比增速连续{direction}（{evidence}）。"))

    # 两期指标背离直接描述事实，不编造成本、需求或现金回收原因。
    rev_rate, profit_rate = latest_yoy.get("revenue_growth_yoy"), latest_yoy.get("profit_growth_yoy")
    if rev_rate is not None and profit_rate is not None and rev_rate > 0 > profit_rate:
        candidates.append((1, -1, frozenset({"revenue", "net_profit"}), (f"{newest} 较 {previous_label}：营收增长而净利润下降"
                           f"（营收同比增长{rev_rate*100:.2f}%，净利润同比下降{abs(profit_rate)*100:.2f}%），收入与利润变化背离。")))
    cash_rate = latest_yoy.get("cash_flow_growth_yoy")
    if profit_rate is not None and cash_rate is not None and profit_rate > 0 > cash_rate:
        candidates.append((1, 0, frozenset({"net_profit", "operating_cash_flow"}),
            f"{newest}：净利润同比增长{profit_rate*100:.2f}%，经营现金流同比下降{abs(cash_rate)*100:.2f}%，两者变化背离。"))

    for order, (field, label) in enumerate(_FIELDS.items()):
        values = pair(field)
        if values is None:
            continue
        before, after = values
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
            candidates.append((4, order, frozenset({field}), (f"{dates}：{label}{change(field, values)}"
                               f"（{_amount(field, before)} → {_amount(field, after)}）。")))
        else:
            candidates.append((4, order, frozenset({field}), (f"{dates}：{label}由 {_amount(field, before)} 至 "
                               f"{_amount(field, after)}；非正基数，不计算普通同比增长率。")))

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
    return {"highlights": highlights, "note": "；".join(dict.fromkeys(note)), "latest_yoy": latest_yoy,
            "yoy_basis": "；".join(basis)}
