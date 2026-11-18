"""指数宏观面分析 — PMI、利率、汇率关联"""
from datetime import date, datetime
from typing import Any, Literal

from analysis.base import AnalysisModule
from data.schemas import AnalysisResult, IndexAnalysisContext
from utils.numbers import is_finite_number


class MacroAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> Literal["index_macro"]:
        return "index_macro"

    def analyze(self, context: IndexAnalysisContext,
                config: dict[str, Any] | None = None) -> AnalysisResult:
        if context.target.market == "hk":
            macro = context.macro
            if macro is None:
                return AnalysisResult(dimension=self.dimension, status="unavailable", summary="香港金融背景未取得或已过期", metrics={})
            values = {"hibor_1m": macro.hibor_1m, "hk_base_rate": macro.hk_base_rate,
                      "hk_closing_balance_million": macro.hk_closing_balance_million,
                      "indicator_dates": macro.indicator_dates, "notes": macro.notes}
            fields = [("HIBOR 1个月", macro.hibor_1m, "%"), ("基本利率", macro.hk_base_rate, "%"),
                      ("总结余", macro.hk_closing_balance_million, "百万港元")]
            signals = [f"{label} {value:g}{unit}" for label, value, unit in fields if value is not None]
            return AnalysisResult(dimension=self.dimension, status="partial" if len(signals) < 3 else "ok",
                                  summary="；".join(signals) or "香港金融背景未取得", metrics=values)
        if context.target.market == "us":
            return self._analyze_us(context)
        if context.target.index_style == "overseas":
            return AnalysisResult(dimension=self.dimension, status="unavailable", summary="尚未适配当地宏观来源，国内PMI/Shibor不代表当地环境", metrics={})
        macro = context.macro
        if macro is None:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="宏观数据不可用", metrics={})

        # sector 的 macro 字段全为 None → 输出 na
        vals = [macro.pmi, macro.shibor_3m, macro.cpi_yoy, macro.usd_cny]
        if all(v is None for v in vals):
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="未取得适用的宏观指标",
                                  metrics={"tag": "na"})

        # 简单宏观信号
        signals = []
        if macro.pmi is not None:
            signals.append(f"PMI {macro.pmi:.1f}" + ("（扩张）" if macro.pmi >= 50 else "（收缩）"))
        if macro.shibor_3m is not None:
            signals.append(f"Shibor3M {macro.shibor_3m:.2f}%")
        if macro.usd_cny is not None:
            signals.append(f"USD/CNY {macro.usd_cny:.4f}")

        metrics = {
            "indicator_dates": macro.indicator_dates, "notes": macro.notes,
            "pmi": macro.pmi, "shibor_3m": macro.shibor_3m,
            "cpi_yoy": macro.cpi_yoy, "usd_cny": macro.usd_cny,
            "shibor_percentile": macro.shibor_percentile,
            "pmi_percentile": macro.pmi_percentile,
            "pmi_state": "未取得" if macro.pmi is None else "扩张" if macro.pmi >= 50 else "收缩",
        }

        return AnalysisResult(dimension=self.dimension, status="ok",
                              summary="；".join(signals) if signals else "宏观数据不足",
                              metrics=metrics)


    def _analyze_us(self, context: IndexAnalysisContext) -> AnalysisResult:
        macro = context.macro
        if macro is None:
            record = context.collection_records.get("index_macro")
            reason = record.error_message if record else ""
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary=reason or "未取得有效的美国 CPI 或 10 年期国债收益率。")

        today = datetime.now().astimezone().date()
        notes, signals = list(macro.notes), []
        metrics: dict[str, Any] = {"indicator_dates": macro.indicator_dates, "notes": notes}
        for key, label, value, max_days in (
            ("us_cpi_yoy", "美国CPI年率", macro.us_cpi_yoy, 90),
            ("us_10y_yield", "美国10年期国债收益率", macro.us_10y_yield, 14),
        ):
            raw_day = macro.indicator_dates.get(key, "")
            metrics[key] = None
            try:
                observed = date.fromisoformat(raw_day)
            except ValueError:
                if value is not None:
                    notes.append(f"{label}日期未取得")
                continue
            if not 0 <= (today - observed).days <= max_days:
                notes.append(f"{label}（{raw_day}）日期无效或已过期")
                continue
            if is_finite_number(value):
                metrics[key] = value
                released = macro.indicator_dates.get(f"{key}_release")
                dates = f"{observed:%Y-%m}，发布日期 {released}" if released else raw_day
                signals.append(f"{label} {value:g}%（{dates}）")
        status = "ok" if len(signals) == 2 and not notes else "partial" if signals or notes else "unavailable"
        return AnalysisResult(dimension=self.dimension, status=status, metrics=metrics,
                              summary="；".join([*signals, *notes]) or "未取得当前有效的美国宏观数据。")
