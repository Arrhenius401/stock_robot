"""策略指标按独立官方权重覆盖计算，部分样本不冒充完整指数。"""
import math
from statistics import mean, pstdev
from typing import Any, Literal

from analysis.base import AnalysisModule
from data.schemas import AnalysisResult, AnnualFinancialSnapshot, IndexAnalysisContext
from index.strategy_data import finite_number


def _fcf(record: AnnualFinancialSnapshot) -> float | None:
    cash = finite_number(record.operating_cash_flow)
    capex = finite_number(record.capital_expenditure)
    return cash - capex if cash is not None and capex is not None and capex >= 0 else None


class IndexStrategyAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> Literal["index_strategy"]:
        return "index_strategy"

    def analyze(self, context: IndexAnalysisContext, config: dict[str, Any] | None = None) -> AnalysisResult:
        snapshot = context.strategy_data
        if snapshot is None or not snapshot.members:
            errors = snapshot.errors if snapshot else ["专项数据未采集"]
            return AnalysisResult(dimension=self.dimension, status="unavailable", summary="官方成分权重不可用，专项指标不能估算", metrics={"errors": errors})
        members = [m for m in snapshot.members if math.isfinite(m.weight) and m.weight >= 0]
        year_coverage: dict[int, float] = {}
        for member in members:
            known_years = {r.report_date.year for r in member.annual_financials
                           if r.available_date <= snapshot.as_of and (r.report_date.month, r.report_date.day) == (12, 31)
                           and (finite_number(r.operating_cash_flow) is not None or finite_number(r.net_profit) is not None)}
            for year in known_years:
                year_coverage[year] = year_coverage.get(year, 0) + member.weight
        sufficient_years = [y for y, weight in year_coverage.items() if weight >= 80]
        latest_year = max(sufficient_years or list(year_coverage) or [snapshot.as_of.year - 1])
        years = list(range(latest_year - 2, latest_year + 1))
        industries: dict[str, float] = {}
        values: dict[str, list[tuple[float, float]]] = {}
        dates: set[str] = set()
        for member in members:
            if member.industry:
                industries[member.industry] = industries.get(member.industry, 0) + member.weight
            records: dict[int, AnnualFinancialSnapshot] = {}
            for record in sorted(member.annual_financials, key=lambda r: r.available_date):
                if record.available_date <= snapshot.as_of and record.report_date.month == 12 and record.report_date.day == 31 and record.report_date.year in years:
                    records[record.report_date.year] = record
            latest = records.get(years[-1])
            def add(key: str, value: float | None, weight: float = member.weight) -> None:
                if value is not None and math.isfinite(value):
                    values.setdefault(key, []).append((weight, value))
            if latest:
                fcf = _fcf(latest)
                cap = finite_number(latest.market_cap)
                if latest.market_cap_date:
                    dates.add(latest.market_cap_date.isoformat())
                if fcf is not None and cap is not None and cap > 0:
                    add("free_cash_flow_yield", fcf / cap * 100)
                debt, cash = finite_number(latest.total_debt), finite_number(latest.cash)
                if fcf is not None and cap is not None and debt is not None and cash is not None and cap + debt - cash > 0:
                    add("free_cash_flow_ev_yield", fcf / (cap + debt - cash) * 100)
                ocf, profit = finite_number(latest.operating_cash_flow), finite_number(latest.net_profit)
                if ocf is not None and profit is not None and profit > 0:
                    add("operating_cash_flow_profit_ratio", ocf / profit)
                dividend, close = finite_number(latest.dividend_per_share), finite_number(latest.close_price)
                if dividend is not None and dividend >= 0 and close is not None and close > 0:
                    add("dividend_yield", dividend / close * 100)
            complete = [records.get(y) for y in years]
            if all(r is not None for r in complete):
                fcfs = [_fcf(r) for r in complete if r is not None]
                if len(fcfs) == 3 and all(v is not None for v in fcfs):
                    add("free_cash_flow_continuity", 100.0 if all(v is not None and v > 0 for v in fcfs) else 0.0)
                dividends = [finite_number(r.dividend_per_share) for r in complete if r is not None]
                if len(dividends) == 3 and all(v is not None and v >= 0 for v in dividends):
                    ds = [v for v in dividends if v is not None]
                    add("dividend_continuity", 100.0 if all(v > 0 for v in ds) else 0.0)
                    if mean(ds) > 0:
                        add("dividend_stability", pstdev(ds) / mean(ds) * 100)
        specifications = [
            ("free_cash_flow_yield", "自由现金流收益率（市值口径）", "%"),
            ("free_cash_flow_ev_yield", "自由现金流收益率（参考企业价值口径）", "%"),
            ("free_cash_flow_continuity", "连续三年自由现金流为正的样本权重占比", "%"),
            ("operating_cash_flow_profit_ratio", "经营现金流/归母净利润", "倍"),
            ("dividend_yield", "年度实施分红/采样股价", "%"),
            ("dividend_continuity", "连续三年分红的样本权重占比", "%"),
            ("dividend_stability", "三年每股分红变异系数（越低越稳定）", "%"),
        ]
        relevant = specifications[:4] if snapshot.strategy_kind == "free_cash_flow" else specifications[4:]
        indicators = []
        for key, label, unit in relevant:
            samples = values.get(key, [])
            coverage = sum(w for w, _ in samples)
            value = sum(w * v for w, v in samples) / coverage if coverage > 0 else None
            reason = "" if value is not None else "同一财报年度的完整字段或连续三年数据缺失"
            if key == "free_cash_flow_ev_yield" and value is None:
                reason = "同日市值及已公开总负债/货币资金字段不完整；未以零值替代"
            indicators.append({"key": key, "label": label, "value": round(value, 6) if value is not None else None,
                               "unit": unit, "coverage_pct": round(coverage, 4),
                               "scope": "指数估算" if coverage >= 80 else "已覆盖样本估算", "reason": reason})
        total_weight = sum(m.weight for m in members)
        metrics = {
            "strategy_kind": snapshot.strategy_kind, "source": snapshot.source, "as_of": snapshot.as_of.isoformat(),
            "weight_as_of": snapshot.weight_as_of.isoformat() if snapshot.weight_as_of else snapshot.as_of.isoformat(),
            "member_count": len(members), "member_weight_coverage_pct": round(total_weight, 4),
            "top10_weight_pct": round(sum(sorted((m.weight for m in members), reverse=True)[:10]), 4),
            "industry_coverage_pct": round(sum(industries.values()), 4),
            "industry_weights": [{"industry": k, "weight_pct": round(v, 4)} for k, v in sorted(industries.items(), key=lambda kv: kv[1], reverse=True)],
            "financial_years": years, "market_cap_dates": sorted(dates), "indicators": indicators, "errors": snapshot.errors,
            "methodology": "自由现金流=年度经营现金流-购建固定/无形等长期资产现金支出；参考企业价值=同日总市值+最新公开总负债-货币资金。股息率采用同年度中期及末期已实施每股派息/采样股价，非TTM。所有财报公告/修订及实施日期不晚于采样日；权重日期、财报年度、市值日期独立展示。指标按覆盖样本权重加权平均；低于80%称已覆盖样本估算，缺失不填零。不构成官方因子复现或历史回测；分红金额不调整历史送转股。",
        }
        sufficient = all(i["coverage_pct"] >= 80 for i in indicators) and 99 <= total_weight <= 100.5
        summary = f"官方权重覆盖 {total_weight:.2f}%，前十大集中度 {metrics['top10_weight_pct']:.2f}%；各指标按独立覆盖率展示"
        return AnalysisResult(dimension=self.dimension, status="ok" if sufficient else "partial", summary=summary, metrics=metrics)
