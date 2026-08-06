"""指数技术面分析 — 趋势、均线、支撑/压力位"""
from typing import Any
from analysis.base import AnalysisModule
from data.schemas import AnalysisResult, IndexAnalysisContext, SufficiencyLevel
from index.enricher import tag_technical


class IndexTechnicalAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> str:
        return "index_technical"

    def analyze(self, context: IndexAnalysisContext,
                config: dict[str, Any] | None = None) -> AnalysisResult:
        prices = context.price_data or []
        if not prices:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="行情数据不可用", metrics={})

        sorted_prices = sorted(prices, key=lambda x: x.trade_date)
        closes = [p.close for p in sorted_prices]
        latest_close = closes[-1] if closes else 0

        # 计算均线
        ma_5 = sum(closes[-5:]) / min(5, len(closes)) if len(closes) >= 5 else None
        ma_20 = sum(closes[-20:]) / min(20, len(closes)) if len(closes) >= 20 else None
        ma_60 = sum(closes[-60:]) / min(60, len(closes)) if len(closes) >= 60 else None

        # 计算支撑/压力位（简化：近期高点和低点）
        year_high = max(p.high for p in sorted_prices[-250:]) if len(sorted_prices) >= 250 else max(p.high for p in sorted_prices)
        year_low = min(p.low for p in sorted_prices[-250:]) if len(sorted_prices) >= 250 else min(p.low for p in sorted_prices)

        # 趋势标签
        trend_tag = tag_technical(ma_5, ma_20, latest_close)

        metrics = {
            "latest_close": latest_close,
            "ma_5": ma_5,
            "ma_20": ma_20,
            "ma_60": ma_60,
            "year_high": year_high,
            "year_low": year_low,
            "tag": trend_tag,
        }

        if context.sufficiency and context.sufficiency.price.level == SufficiencyLevel.INSUFFICIENT:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="行情数据不足", metrics=metrics)

        status = "partial" if len(closes) < 20 else "ok"
        summary = f"最新价 {latest_close:.2f}，趋势: {trend_tag}"

        return AnalysisResult(dimension=self.dimension, status=status,
                              summary=summary, metrics=metrics)
