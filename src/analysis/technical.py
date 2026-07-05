"""技术面分析模块 — 均线系统、MACD、量价分析"""
from statistics import mean
from src.analysis.base import AnalysisModule
from src.data.schemas import AnalysisContext, AnalysisResult


class TechnicalAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> str:
        return "technical"

    def analyze(self, context: AnalysisContext) -> AnalysisResult:
        prices = context.price_data or []
        if not prices:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="行情数据不可用", metrics={})

        sorted_prices = sorted(prices, key=lambda x: x.trade_date)
        closes = [p.close for p in sorted_prices]

        metrics = {}
        if len(closes) >= 5:
            metrics["ma5"] = round(mean(closes[-5:]), 2)
        if len(closes) >= 10:
            metrics["ma10"] = round(mean(closes[-10:]), 2)
        if len(closes) >= 20:
            metrics["ma20"] = round(mean(closes[-20:]), 2)
        if len(closes) >= 60:
            metrics["ma60"] = round(mean(closes[-60:]), 2)

        latest_close = closes[-1] if closes else 0
        metrics["latest_close"] = latest_close

        if "ma20" in metrics and latest_close > 0:
            metrics["price_vs_ma20"] = round((latest_close - metrics["ma20"]) / metrics["ma20"] * 100, 2)

        if len(closes) >= 5:
            volumes = [p.volume for p in sorted_prices[-5:]]
            metrics["avg_volume_5d"] = int(mean(volumes))
            if len(closes) >= 25:
                prev_volumes = [p.volume for p in sorted_prices[-25:-5]]
                if prev_volumes and mean(prev_volumes) > 0:
                    metrics["volume_ratio"] = round(mean(volumes) / mean(prev_volumes), 2)

        if len(closes) >= 26:
            ema12 = self._ema(closes, 12)
            ema26 = self._ema(closes, 26)
            dif = ema12 - ema26
            dea = self._ema_from_values([dif], 9, dif) if dif else 0
            macd = 2 * (dif - dea)
            metrics["macd_dif"] = round(dif, 4)
            metrics["macd_dea"] = round(dea, 4)
            metrics["macd_bar"] = round(macd, 4)

        status = "partial" if len(closes) < 20 else "ok"
        summary = self._build_summary(metrics, status)
        return AnalysisResult(dimension=self.dimension, status=status, summary=summary, metrics=metrics)

    def _ema(self, data: list[float], period: int) -> float:
        if len(data) < period:
            return data[-1] if data else 0
        multiplier = 2 / (period + 1)
        ema = mean(data[:period])
        for price in data[period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    def _ema_from_values(self, data: list[float], period: int, initial: float) -> float:
        multiplier = 2 / (period + 1)
        ema = initial
        for value in data:
            ema = (value - ema) * multiplier + ema
        return ema

    def _build_summary(self, metrics: dict, status: str) -> str:
        parts = []
        price = metrics.get("latest_close", 0)
        if price:
            parts.append(f"最新价 {price:.2f}")
        vs_ma20 = metrics.get("price_vs_ma20")
        if vs_ma20 is not None:
            position = "上方" if vs_ma20 > 0 else "下方"
            parts.append(f"位于20日均线{position} {abs(vs_ma20):.1f}%")
        return "；".join(parts) if parts else "技术指标数据不足"
