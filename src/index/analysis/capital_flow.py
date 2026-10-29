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
