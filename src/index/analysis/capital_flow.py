"""指数资金面分析：沪深融资余额与港股通市场背景。"""
from datetime import datetime
from typing import Any, Literal

from analysis.base import AnalysisModule
from data.schemas import AnalysisResult, IndexAnalysisContext
from utils.numbers import is_finite_number


class CapitalFlowAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> Literal["index_capital_flow"]:
        return "index_capital_flow"

    def analyze(self, context: IndexAnalysisContext,
                config: dict[str, Any] | None = None) -> AnalysisResult:
        cf = context.capital_flow
        if cf is None:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="资金数据不可用", metrics={})


        if context.target.market == "hk":
            if cf.southbound_sh_yi is None or cf.southbound_sz_yi is None:
                return AnalysisResult(dimension=self.dimension, status="unavailable", summary="港股通市场背景未取得", metrics={})
            return AnalysisResult(dimension=self.dimension, status="ok", summary=f"港股通市场背景：沪通道成交净买额 {cf.southbound_sh_yi:.2f}亿元，深通道 {cf.southbound_sz_yi:.2f}亿元，日期 {cf.date.isoformat()}", metrics={"data_date": cf.date.isoformat(), "southbound_sh_yi": cf.southbound_sh_yi, "southbound_sz_yi": cf.southbound_sz_yi, "notes": cf.notes})

        if cf.margin_date and cf.margin_balance is not None and is_finite_number(cf.margin_balance):
            stale = (datetime.now().astimezone().date() - cf.date).days > 7
            change = cf.margin_change if is_finite_number(cf.margin_change) else None
            summary = f"A 股融资余额（沪深合计）为 {cf.margin_balance:,.2f} 亿元（截至 {cf.margin_date}）"
            summary += f"，较前一交易日变化 {change:,.2f} 亿元" if change is not None else "，前一共同交易日数据未取得"
            summary += "；余额指标，不代表指数净流入或行业资金流。"
            if context.target.index_style == "sector":
                summary += "全 A 市场背景，不是行业资金流。"
            if stale:
                summary += "数据已超过 7 个自然日，仅作历史参考。"
            return AnalysisResult(dimension=self.dimension, status="partial" if stale or change is None else "ok",
                                  summary=summary, metrics={"margin_balance": cf.margin_balance,
                                      "margin_date": cf.margin_date, "margin_change": change, "data_date": cf.margin_date})

        metrics = {
            "notes": cf.notes,
            "north_bound": cf.north_bound,
            "main_net_inflow": cf.main_net_inflow,
            "margin_balance": cf.margin_balance,

        }

        summary = "；".join(cf.notes) if cf.notes else "资金指标按来源披露，不作综合投票"
        if cf.north_bound is not None:
            direction = "流入" if cf.north_bound > 0 else "流出"
            summary += f"，北向资金净{direction} {abs(cf.north_bound):.1f}亿"

        return AnalysisResult(dimension=self.dimension, status="partial" if cf.notes else "ok",
                              summary=summary, metrics=metrics)
