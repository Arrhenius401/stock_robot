"""指数资金面分析 — 北向资金、主力资金、融资余额"""
from typing import Any
from analysis.base import AnalysisModule
from data.schemas import AnalysisResult, IndexAnalysisContext
from index.enricher import tag_capital


class CapitalFlowAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> str:
        return "index_capital_flow"

    def analyze(self, context: IndexAnalysisContext,
                config: dict[str, Any] | None = None) -> AnalysisResult:
        cf = context.capital_flow
        if cf is None:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="资金流向数据不可用", metrics={})

        flow_tag = tag_capital(cf.north_bound, cf.main_net_inflow)

        metrics = {
            "north_bound": cf.north_bound,
            "main_net_inflow": cf.main_net_inflow,
            "margin_balance": cf.margin_balance,
            "tag": flow_tag,
        }

        summary = f"资金面: {flow_tag}"
        if cf.north_bound is not None:
            direction = "流入" if cf.north_bound > 0 else "流出"
            summary += f"，北向资金净{direction} {abs(cf.north_bound):.1f}亿"

        return AnalysisResult(dimension=self.dimension, status="ok",
                              summary=summary, metrics=metrics)
