"""指数估值面分析 — PE/PB 历史分位 + 估值区域判断"""
from typing import Any, Literal

from analysis.base import AnalysisModule
from data.schemas import AnalysisResult, IndexAnalysisContext
from index.enricher import tag_valuation


class IndexValuationAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> Literal["index_valuation"]:
        return "index_valuation"

    def analyze(self, context: IndexAnalysisContext,
                config: dict[str, Any] | None = None) -> AnalysisResult:
        val = context.valuation_data
        if val is None:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="估值数据不可用", metrics={})

        pe_pct = val.pe_percentile
        pb_pct = val.pb_percentile
        vtag = tag_valuation(pe_pct)

        metrics = {
            "pe_ttm": val.pe_ttm,
            "pb": val.pb,
            "pe_percentile": pe_pct,
            "pb_percentile": pb_pct,
            "dividend_yield": val.dividend_yield,
            "valuation_valid": val.valuation_valid,
            "tag": vtag,
            "percentile_lookback_years": val.percentile_lookback_years,
            "sample_start": val.percentile_sample_start,
            "sample_end": val.percentile_sample_end,
        }

        if not val.valuation_valid:
            return AnalysisResult(dimension=self.dimension, status="partial",
                                  summary=f"PE-TTM {val.pe_ttm}，估值样本不足，分位仅供参考",
                                  metrics=metrics)

        status = "ok"
        summary = f"PE-TTM {val.pe_ttm}，历史分位 {pe_pct}%，估值: {vtag}"
        return AnalysisResult(dimension=self.dimension, status=status,
                              summary=summary, metrics=metrics)
