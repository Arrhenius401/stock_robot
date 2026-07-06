"""估值分析模块 — PE/PB/PS 当前值与历史分位"""
from analysis.base import AnalysisModule
from data.schemas import AnalysisContext, AnalysisResult, ValuationData


class ValuationAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> str:
        return "valuation"

    def analyze(self, context: AnalysisContext) -> AnalysisResult:
        val_data = context.valuation_data
        if val_data is None:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="估值数据不可用", metrics={})

        if isinstance(val_data, ValuationData):
            val_list = [val_data]
        else:
            val_list = val_data

        if not val_list:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="估值数据不可用", metrics={})

        latest = val_list[0]
        metrics = {
            "pe_ttm": latest.pe_ttm,
            "pb": latest.pb,
            "ps_ttm": latest.ps_ttm,
        }

        if len(val_list) >= 20:
            pe_history = [v.pe_ttm for v in val_list if v.pe_ttm is not None]
            if pe_history and latest.pe_ttm is not None:
                below = sum(1 for p in pe_history if p < latest.pe_ttm)
                metrics["pe_percentile"] = round(below / len(pe_history) * 100, 1)

        status = "partial"
        summary = self._build_summary(metrics)
        return AnalysisResult(dimension=self.dimension, status=status, summary=summary, metrics=metrics)

    def _build_summary(self, metrics: dict) -> str:
        parts = []
        pe = metrics.get("pe_ttm")
        if pe is not None:
            parts.append(f"PE(TTM) {pe:.2f}")
        pb = metrics.get("pb")
        if pb is not None:
            parts.append(f"PB {pb:.2f}")
        pct = metrics.get("pe_percentile")
        if pct is not None:
            parts.append(f"PE处于历史{pct}%分位")
        return "；".join(parts) if parts else "估值数据不足"
