"""技术面分析模块 — 均线系统、MACD、量价分析"""
from statistics import mean
from analysis.base import AnalysisModule
from data.schemas import AnalysisContext, AnalysisResult


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

        # ===== 打分逻辑 =====
        from data.schemas import SufficiencyLevel

        if context.sufficiency and context.sufficiency.price.level == SufficiencyLevel.INSUFFICIENT:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="行情数据不足", metrics=metrics,
                                  score=None, score_detail="技术面数据不足，跳过打分")

        score = 0.0
        score_parts = []
        risk_flags = []

        # 1. 均线结构 (4分)
        ma5 = metrics.get("ma5")
        ma20 = metrics.get("ma20")
        ma60 = metrics.get("ma60")
        if ma5 and ma20 and ma60:
            if ma5 > ma20 > ma60:
                score += 4; score_parts.append("MA5>MA20>MA60 多头排列，得 4/4 分")
            elif ma5 > ma20 and ma20 < ma60:
                score += 2; score_parts.append("均线交叉震荡，得 2/4 分")
            else:
                score += 1; score_parts.append("均线空头排列，得 1/4 分")
                risk_flags.append("bearish_ma")
        elif ma5 and ma20:
            score += 2; score_parts.append("缺少 MA60，仅短期均线可判，得 2/4 分")
        else:
            score_parts.append("均线数据不足，得 0/4 分")

        # 2. 量价配合 (3分)
        price_vs_ma20 = metrics.get("price_vs_ma20")
        vol_ratio = metrics.get("volume_ratio")
        if vol_ratio is not None and price_vs_ma20 is not None:
            if price_vs_ma20 > 0 and vol_ratio > 1.2:
                score += 3; score_parts.append(f"放量上涨（量比 {vol_ratio:.2f}），得 3/3 分")
            elif abs(price_vs_ma20) < 2 and 0.8 <= vol_ratio <= 1.2:
                score += 2; score_parts.append(f"横盘缩量（量比 {vol_ratio:.2f}），得 2/3 分")
            elif price_vs_ma20 < 0 and vol_ratio > 1.2:
                score += 1; score_parts.append(f"放量下跌（量比 {vol_ratio:.2f}），得 1/3 分")
                risk_flags.append("volume_bearish")
            else:
                score += 2; score_parts.append(f"量价配合一般（量比 {vol_ratio:.2f}），得 2/3 分")
        else:
            score_parts.append("量能数据不足，得 0/3 分")

        # 3. MACD 形态 (3分)
        dif = metrics.get("macd_dif")
        dea = metrics.get("macd_dea")
        macd_bar = metrics.get("macd_bar")
        if dif is not None and dea is not None and macd_bar is not None:
            if dif > dea and macd_bar > 0:
                score += 3; score_parts.append("DIF>DEA 且 MACD 柱为正，得 3/3 分")
            elif (dif > dea) != (macd_bar > 0):
                score += 2; score_parts.append("MACD 临界状态，得 2/3 分")
            else:
                score += 1; score_parts.append("MACD 空头形态，得 1/3 分")
        else:
            score_parts.append("MACD 数据不足，得 0/3 分")

        score = round(score, 1)
        score_detail = "；".join(score_parts)
        return AnalysisResult(dimension=self.dimension, status=status, summary=summary,
                              metrics=metrics, score=score, score_detail=score_detail,
                              risk_flags=risk_flags)

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
