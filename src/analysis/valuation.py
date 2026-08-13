"""估值分析模块 — PE/PB/PS 当前值与历史分位"""
from typing import Any, Literal

from analysis.base import AnalysisModule
from analysis.financial import FinancialAnalyzer
from data.schemas import AnalysisContext, AnalysisResult, SufficiencyLevel


class ValuationAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> Literal["valuation"]:
        return "valuation"

    def analyze(self, context: AnalysisContext,
                config: dict[str, Any] | None = None) -> AnalysisResult:
        val_data = context.valuation_data
        if val_data is None:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="估值数据不可用", metrics={})

        latest = val_data
        metrics = {
            "pe_ttm": latest.pe_ttm,
            "pb": latest.pb,
            "ps_ttm": latest.ps_ttm,
        }

        if context.enriched_valuation and context.enriched_valuation.pe_percentile is not None:
            metrics["pe_percentile"] = context.enriched_valuation.pe_percentile

        status = "partial"
        summary = self._build_summary(metrics)

        if context.sufficiency and context.sufficiency.valuation.level == SufficiencyLevel.INSUFFICIENT:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="估值数据不足", metrics=metrics,
                                  score=None, score_detail="估值维度数据不足，跳过打分")

        if config:
            scorer = FinancialAnalyzer._get_scorer(config)
            score, score_detail, risk_flags = scorer.score_valuation(context)
            industry_note = scorer._industry_note() if hasattr(scorer, '_industry_note') else ""
            partial_status = "partial" if (context.sufficiency and context.sufficiency.valuation.level == SufficiencyLevel.PARTIAL) else status
            return AnalysisResult(dimension=self.dimension, status=partial_status, summary=summary,
                                  metrics=metrics, score=score, score_detail=score_detail,
                                  risk_flags=risk_flags, industry_note=industry_note)

        return AnalysisResult(dimension=self.dimension, status=status, summary=summary,
                              metrics=metrics)

    @staticmethod
    def _build_summary(metrics: dict) -> str:
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
