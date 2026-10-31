"""指数资金面分析 — 北向资金、主力资金、融资余额"""
from typing import Any, Literal

from analysis.base import AnalysisModule
from data.schemas import AnalysisResult, IndexAnalysisContext


class CapitalFlowAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> Literal["index_capital_flow"]:
        return "index_capital_flow"

    def analyze(self, context: IndexAnalysisContext,
                config: dict[str, Any] | None = None) -> AnalysisResult:
        cf = context.capital_flow
        if cf is None:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="资金流向数据不可用", metrics={})


        if context.target.market == "hk":
            if cf.southbound_sh_yi is None or cf.southbound_sz_yi is None:
                return AnalysisResult(dimension=self.dimension, status="unavailable", summary="港股通市场背景未取得", metrics={})
            return AnalysisResult(dimension=self.dimension, status="ok", summary=f"港股通市场背景：沪通道成交净买额 {cf.southbound_sh_yi:.2f}亿元，深通道 {cf.southbound_sz_yi:.2f}亿元，日期 {cf.date.isoformat()}", metrics={"data_date": cf.date.isoformat(), "southbound_sh_yi": cf.southbound_sh_yi, "southbound_sz_yi": cf.southbound_sz_yi, "notes": cf.notes})

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
