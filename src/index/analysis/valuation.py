"""指数估值面分析 — PE/PB 历史分位 + 估值区域判断"""
from typing import Any, Literal

from analysis.base import AnalysisModule
from data.schemas import AnalysisResult, IndexAnalysisContext, IndexValuationData
from index.enricher import index_tag_label, tag_valuation


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

        if context.target.index_style == "strategy":
            return self._analyze_strategy(val)

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
        summary = f"PE-TTM {val.pe_ttm}，历史分位 {pe_pct}%，估值：{index_tag_label(vtag)}"
        return AnalysisResult(dimension=self.dimension, status=status,
                              summary=summary, metrics=metrics)

    def _analyze_strategy(self, val: IndexValuationData) -> AnalysisResult:
        """按公开官方真实覆盖展示，单张快照不能推导历史分位。"""
        fields = (
            "pe_ttm", "pe_snapshot", "pb", "pe_percentile", "pb_percentile",
            "pe_snapshot_basis", "pe_snapshot_source_url",
            "dividend_yield", "pe_as_of", "pe_snapshot_as_of", "pb_as_of", "pe_basis", "pb_basis",
            "pe_source_url", "pb_source_url", "pe_sample_count", "pb_sample_count",
        )
        metrics = {key: getattr(val, key) for key in fields if getattr(val, key) is not None and getattr(val, key) != ""}
        metrics["valuation_notes"] = val.valuation_notes
        has_values = any(getattr(val, key) is not None for key in ("pe_ttm", "pe_snapshot", "pb"))
        if not has_values:
            reason = "；".join(val.valuation_notes) or "公开官方估值未取得有效数值"
            return AnalysisResult(dimension=self.dimension, status="unavailable", summary=reason, metrics=metrics)
        parts = []
        if val.pe_ttm is not None:
            parts.append(f"PE-TTM {val.pe_ttm:g}")
        elif val.pe_snapshot is not None:
            parts.append(f"官方单张PE {val.pe_snapshot:g}")
        if val.pb is not None:
            parts.append(f"PB {val.pb:g}")
        if val.pe_percentile is not None and val.valuation_valid:
            metrics.update({
                "tag": tag_valuation(val.pe_percentile),
                "percentile_lookback_years": val.percentile_lookback_years,
                "sample_start": val.percentile_sample_start,
                "sample_end": val.percentile_sample_end,
            })
            parts.append(f"近{val.percentile_lookback_years}年窗口内实际可用样本 {val.pe_sample_count} 个交易日，PE历史分位 {val.pe_percentile:g}%")
            parts.append(f"PE估值：{index_tag_label(metrics['tag'])}")
        else:
            metrics.pop("pe_percentile", None)
            parts.append("没有有效PE历史分位，不判断高低估")
        # PE 与 PB 历史独立判断；公开单张 PB 只表示该月末快照。
        complete = val.valuation_valid and val.pe_percentile is not None and val.pb_percentile is not None
        return AnalysisResult(dimension=self.dimension, status="ok" if complete else "partial", summary="，".join(parts), metrics=metrics)
