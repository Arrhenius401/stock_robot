"""技术面分析模块 — 均线系统、MACD、量价分析"""
from typing import Any

from analysis.base import AnalysisModule
from data.schemas import AnalysisContext, AnalysisResult, SufficiencyLevel


class TechnicalAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> str:
        return "technical"

    def analyze(self, context: AnalysisContext,
                config: dict[str, Any] | None = None) -> AnalysisResult:
        prices = context.price_data or []
        if not prices:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="行情数据不可用", metrics={})

        sorted_prices = sorted(prices, key=lambda x: x.trade_date)
        closes = [p.close for p in sorted_prices]
        latest_close = closes[-1] if closes else 0

        metrics = {"latest_close": latest_close}

        if context.sufficiency and context.sufficiency.price.level == SufficiencyLevel.INSUFFICIENT:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="行情数据不足", metrics=metrics,
                                  score=None, score_detail="技术面数据不足，跳过打分")

        status = "partial" if len(closes) < 20 else "ok"
        summary = self._build_summary(metrics, status)

        if config:
            from analysis.financial import FinancialAnalyzer
            scorer = FinancialAnalyzer._get_scorer(config)
            score, score_detail, risk_flags = scorer.score_technical(context)
            industry_note = scorer._industry_note() if hasattr(scorer, '_industry_note') else ""
            return AnalysisResult(dimension=self.dimension, status=status, summary=summary,
                                  metrics=metrics, score=score, score_detail=score_detail,
                                  risk_flags=risk_flags, industry_note=industry_note)

        return AnalysisResult(dimension=self.dimension, status=status, summary=summary,
                              metrics=metrics)

    def _build_summary(self, metrics: dict, status: str) -> str:
        parts = []
        price = metrics.get("latest_close", 0)
        if price:
            parts.append(f"最新价 {price:.2f}")
        return "；".join(parts) if parts else "技术指标数据不足"
