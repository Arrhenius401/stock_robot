"""指数估值面分析 — PE/PB 历史分位 + 估值区域判断"""
from typing import Any, Literal

from analysis.base import AnalysisModule
from data.schemas import AnalysisResult, IndexAnalysisContext, IndexValuationData
from index.enricher import index_tag_label, tag_valuation
from report.formatter import format_metric_value
from utils.numbers import is_finite_number, is_positive_finite, is_valid_percentile


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

        if context.target.index_style == "strategy" or val.pe_snapshot is not None or val.valuation_notes:
            return self._analyze_strategy(val, extra=context.extra_analysis_enabled)

        pe_pct = val.pe_percentile if val.valuation_valid and is_positive_finite(val.pe_ttm) and is_valid_percentile(val.pe_percentile) else None
        pb_pct = val.pb_percentile if val.pb_sample_count >= 252 and is_positive_finite(val.pb) and is_valid_percentile(val.pb_percentile) else None
        vtag = tag_valuation(pe_pct) if context.extra_analysis_enabled else "invalid"

        metrics = {
            "pe_ttm": val.pe_ttm if is_finite_number(val.pe_ttm) else None,
            "pb": val.pb if is_finite_number(val.pb) else None,
            "pe_percentile": pe_pct,
            "pb_percentile": pb_pct,
            "dividend_yield": val.dividend_yield if is_finite_number(val.dividend_yield) else None,
            "valuation_valid": pe_pct is not None,
            "tag": vtag,
            "percentile_lookback_years": val.percentile_lookback_years,
            "sample_start": val.percentile_sample_start,
            "sample_end": val.percentile_sample_end,
        }

        if pe_pct is None:
            return AnalysisResult(dimension=self.dimension, status="partial",
                                  summary="估值快照仅作披露，历史样本不足或分位无效，不判断高低估",
                                  metrics=metrics)

        status = "ok"
        summary = (f"PE-TTM {format_metric_value('pe_ttm', val.pe_ttm)}，"
                   f"历史分位 {format_metric_value('pe_percentile', pe_pct)}，估值：{index_tag_label(vtag)}")
        return AnalysisResult(dimension=self.dimension, status=status,
                              summary=summary, metrics=metrics)

    def _analyze_strategy(self, val: IndexValuationData, *, extra: bool = True) -> AnalysisResult:
        """按公开官方真实覆盖展示，单张快照不能推导历史分位。"""
        fields = (
            "pe_ttm", "pe_snapshot", "pb", "pe_percentile", "pb_percentile",
            "pe_snapshot_basis", "pe_snapshot_source_url",
            "dividend_yield", "pe_as_of", "pe_snapshot_as_of", "pb_as_of", "pe_basis", "pb_basis",
            "pe_source_url", "pb_source_url", "pe_sample_count", "pb_sample_count",
        )
        metrics = {key: getattr(val, key) for key in fields if getattr(val, key) is not None and getattr(val, key) != ""}
        for key in ("pe_ttm", "pe_snapshot", "pb", "dividend_yield"):
            if key in metrics and not is_finite_number(metrics[key]):
                metrics.pop(key)
        if val.pb_sample_count < 252 or not is_positive_finite(val.pb) or not is_valid_percentile(val.pb_percentile):
            metrics.pop("pb_percentile", None)
        if not val.valuation_valid or not is_positive_finite(val.pe_ttm) or not is_valid_percentile(val.pe_percentile):
            metrics.pop("pe_percentile", None)
        metrics["valuation_notes"] = val.valuation_notes
        has_values = any(is_finite_number(getattr(val, key)) for key in ("pe_ttm", "pe_snapshot", "pb"))
        if not has_values:
            reason = "；".join(val.valuation_notes) or "公开官方估值未取得有效数值"
            return AnalysisResult(dimension=self.dimension, status="unavailable", summary=reason, metrics=metrics)
        parts = []
        if is_finite_number(val.pe_ttm):
            parts.append(f"PE-TTM {format_metric_value('pe_ttm', val.pe_ttm)}")
        elif is_finite_number(val.pe_snapshot):
            # 快照容器也可承载日频未声明TTM的PE，展示名须服从真实来源口径。
            pe_label = "官方日频PE" if "日频" in val.pe_snapshot_basis else "官方月末PE" if "月" in val.pe_snapshot_basis else "官方PE"
            parts.append(f"{pe_label} {format_metric_value('pe_snapshot', val.pe_snapshot)}")
        if is_finite_number(val.pb):
            parts.append(f"PB {format_metric_value('pb', val.pb)}")
        if is_valid_percentile(val.pe_percentile) and val.valuation_valid and is_positive_finite(val.pe_ttm):
            metrics.update({
                "tag": tag_valuation(val.pe_percentile) if extra else "invalid",
                "percentile_lookback_years": val.percentile_lookback_years,
                "sample_start": val.percentile_sample_start,
                "sample_end": val.percentile_sample_end,
            })
            parts.append(f"近{val.percentile_lookback_years}年窗口内实际可用样本 {val.pe_sample_count} 个交易日，"
                         f"PE历史分位 {format_metric_value('pe_percentile', val.pe_percentile)}")
            parts.append(f"PE估值：{index_tag_label(metrics['tag'])}")
        else:
            metrics.pop("pe_percentile", None)
            parts.append("没有有效PE历史分位，不判断高低估")
        # PE 与 PB 历史独立判断；公开单张 PB 只表示该月末快照。
        complete = "pe_percentile" in metrics and "pb_percentile" in metrics
        return AnalysisResult(dimension=self.dimension, status="ok" if complete else "partial", summary="，".join(parts), metrics=metrics)
