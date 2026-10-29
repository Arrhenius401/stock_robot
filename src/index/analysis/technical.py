"""指数技术面分析 — 趋势、均线、支撑/压力位"""

import calendar
import math
from itertools import pairwise
from typing import Any, Literal

from analysis.base import AnalysisModule
from data.schemas import AnalysisResult, IndexAnalysisContext, SufficiencyLevel
from index.enricher import tag_technical


class IndexTechnicalAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> Literal["index_technical"]:
        return "index_technical"

    def analyze(
        self, context: IndexAnalysisContext, config: dict[str, Any] | None = None
    ) -> AnalysisResult:
        prices = context.price_data or []
        if not prices:
            return AnalysisResult(
                dimension=self.dimension,
                status="unavailable",
                summary="行情数据不可用",
                metrics={},
            )

        sorted_prices = sorted(
            {
                p.trade_date: p
                for p in prices
                if p.symbol == context.target.symbol
                and math.isfinite(p.close)
                and p.close > 0
            }.values(),
            key=lambda x: x.trade_date,
        )
        if not sorted_prices:
            return AnalysisResult(
                dimension=self.dimension,
                status="unavailable",
                summary="无有效同代码行情",
                metrics={},
            )
        closes = [p.close for p in sorted_prices]
        latest_close = closes[-1] if closes else 0

        # 计算均线
        ma_5 = sum(closes[-5:]) / min(5, len(closes)) if len(closes) >= 5 else None
        ma_20 = sum(closes[-20:]) / min(20, len(closes)) if len(closes) >= 20 else None
        ma_60 = sum(closes[-60:]) / min(60, len(closes)) if len(closes) >= 60 else None

        end = sorted_prices[-1].trade_date
        start = end.replace(
            year=end.year - 1,
            day=min(end.day, calendar.monthrange(end.year - 1, end.month)[1]),
        )
        year_prices = [p for p in sorted_prices if p.trade_date >= start]
        ohlc = [
            p
            for p in sorted_prices
            if all(math.isfinite(v) and v > 0 for v in (p.high, p.low, p.open))
            and p.low <= min(p.open, p.close) <= max(p.open, p.close) <= p.high
        ]
        year_ohlc = [p for p in ohlc if p.trade_date >= start]
        valid_range = len(year_ohlc) == len(year_prices) and bool(year_ohlc)
        year_high = max(p.high for p in year_ohlc) if valid_range else None
        year_low = min(p.low for p in year_ohlc) if valid_range else None
        tr, plus, minus = [], [], []
        for prev, cur in pairwise(ohlc):
            tr.append(
                max(
                    cur.high - cur.low,
                    abs(cur.high - prev.close),
                    abs(cur.low - prev.close),
                )
            )
            up, down = cur.high - prev.high, prev.low - cur.low
            plus.append(up if up > down and up > 0 else 0)
            minus.append(down if down > up and down > 0 else 0)

        def smooth(values: list[float], n: int) -> list[float]:
            if len(values) < n:
                return []
            out = [sum(values[:n]) / n]
            for value in values[n:]:
                out.append((out[-1] * (n - 1) + value) / n)
            return out

        # OHLC异常会破坏连续指标，因此拒绝计算而非拼接不相邻样本。
        usable = len(ohlc) == len(sorted_prices)
        atrs = smooth(tr, 20) if usable else []
        dx = (
            [
                100 * abs(p - m) / (p + m) if p + m else 0
                for p, m in zip(smooth(plus, 14), smooth(minus, 14))
            ]
            if usable
            else []
        )
        adxs = smooth(dx, 14)
        adx, atr = adxs[-1] if adxs else None, atrs[-1] if atrs else None

        def relation(value: float | None, label: str) -> str:
            return (
                "样本不足"
                if value is None
                else f"收盘价{'高于' if latest_close > value else '低于' if latest_close < value else '等于'}{label}"
            )

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
            "adx_14": adx,
            "atr_20": atr,
            "atr_pct": atr / latest_close * 100 if atr is not None else None,
            "price_position_pct": (latest_close - year_low)
            / (year_high - year_low)
            * 100
            if year_high is not None and year_low is not None and year_high > year_low
            else None,
            "sample_start": sorted_prices[0].trade_date,
            "sample_end": end,
            "sample_count": len(closes),
            "direction_short": relation(ma_5, "MA5") + "；" + relation(ma_20, "MA20"),
            "direction_medium": relation(ma_60, "MA60"),
            "trend_strength": "样本不足"
            if adx is None
            else "趋势较强"
            if adx >= 25
            else "过渡状态"
            if adx >= 20
            else "趋势强度不足",
            "methodology": "ADX14与ATR20采用Wilder平滑；ADX不表示方向；价格位置为近12个月高低区间位置，不是历史分位",
            "notes": (["存在异常OHLC，未计算ADX、ATR及受影响的年内高低/价格位置"] if not usable else [])
            + (["样本不足完整12个月"] if sorted_prices[0].trade_date > start else []),
        }

        if (
            context.sufficiency
            and context.sufficiency.price.level == SufficiencyLevel.INSUFFICIENT
        ):
            return AnalysisResult(
                dimension=self.dimension,
                status="unavailable",
                summary="行情数据不足",
                metrics=metrics,
            )

        status = "partial" if len(closes) < 60 or not usable else "ok"
        summary = (
            f"行情数据截至 {sorted_prices[-1].trade_date.isoformat()}；"
            f"最新价 {latest_close:.2f}；{metrics['direction_short']}；{metrics['direction_medium']}"
        )

        return AnalysisResult(
            dimension=self.dimension, status=status, summary=summary, metrics=metrics
        )
