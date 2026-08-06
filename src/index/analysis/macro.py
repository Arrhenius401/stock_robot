"""指数宏观面分析 — PMI、利率、汇率关联"""
from typing import Any
from analysis.base import AnalysisModule
from data.schemas import AnalysisResult, IndexAnalysisContext


class MacroAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> str:
        return "index_macro"

    def analyze(self, context: IndexAnalysisContext,
                config: dict[str, Any] | None = None) -> AnalysisResult:
        macro = context.macro
        if macro is None:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="宏观数据不可用", metrics={})

        # sector 的 macro 字段全为 None → 输出 na
        vals = [macro.pmi, macro.shibor_3m, macro.cpi_yoy, macro.usd_cny]
        if all(v is None for v in vals):
            return AnalysisResult(dimension=self.dimension, status="ok",
                                  summary="行业指数不适用宏观分析",
                                  metrics={"tag": "na"})

        # 简单宏观信号
        signals = []
        if macro.pmi is not None:
            signals.append(f"PMI {macro.pmi:.1f}" + ("（扩张）" if macro.pmi >= 50 else "（收缩）"))
        if macro.shibor_3m is not None:
            signals.append(f"Shibor3M {macro.shibor_3m:.2f}%")
        if macro.usd_cny is not None:
            signals.append(f"USD/CNY {macro.usd_cny:.4f}")

        # 宏观标签：以 PMI 为主要信号
        if macro.pmi is not None:
            if macro.pmi >= 50:
                macro_tag = "positive"
            elif macro.pmi >= 48:
                macro_tag = "neutral"
            else:
                macro_tag = "negative"
        else:
            macro_tag = "neutral"

        metrics = {
            "pmi": macro.pmi, "shibor_3m": macro.shibor_3m,
            "cpi_yoy": macro.cpi_yoy, "usd_cny": macro.usd_cny,
            "shibor_percentile": macro.shibor_percentile,
            "pmi_percentile": macro.pmi_percentile,
            "tag": macro_tag,
        }

        return AnalysisResult(dimension=self.dimension, status="ok",
                              summary="；".join(signals) if signals else "宏观数据不足",
                              metrics=metrics)
