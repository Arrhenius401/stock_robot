"""指数风险收益：价格口径与ETF复权口径分开，基准仅比较共同日期。"""
import math
from itertools import pairwise
from statistics import stdev
from typing import Any, Literal

from analysis.base import AnalysisModule
from data.schemas import AnalysisResult, IndexAnalysisContext, IndexPriceData


def _prices(prices: list[IndexPriceData]) -> list[IndexPriceData]:
    unique = {p.trade_date: p for p in prices if math.isfinite(p.close) and p.close > 0}
    return sorted(unique.values(), key=lambda p: p.trade_date)


def _return(prices: list[IndexPriceData]) -> float | None:
    return (prices[-1].close / prices[0].close - 1) * 100 if len(prices) >= 2 else None


class IndexPerformanceAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> Literal["index_performance"]:
        return "index_performance"

    def analyze(self, context: IndexAnalysisContext, config: dict[str, Any] | None = None) -> AnalysisResult:
        prices = _prices(context.price_data)
        if len(prices) < 2:
            return AnalysisResult(dimension=self.dimension, status="unavailable", summary="至少需要两个有效交易日的真实行情", metrics={})
        returns = [cur.close / prev.close - 1 for prev, cur in pairwise(prices)]
        peak = prices[0].close
        drawdown = 0.0
        for p in prices:
            peak = max(peak, p.close)
            drawdown = min(drawdown, p.close / peak - 1)
        metrics: dict[str, Any] = {
            "start_date": prices[0].trade_date.isoformat(), "end_date": prices[-1].trade_date.isoformat(),
            "sample_count": len(prices), "return_pct": _return(prices),
            "annualized_volatility_pct": stdev(returns) * math.sqrt(252) * 100 if len(returns) >= 2 else None,
            "max_drawdown_pct": drawdown * 100, "return_basis": "价格指数，不含分红；年化波动按252个交易日",
            "benchmark_symbol": None, "benchmark_return_pct": None, "excess_return_pct": None,
            "benchmark_sample_count": 0, "benchmark_start_date": None, "benchmark_end_date": None,
            "benchmark_aligned_index_return_pct": None,
            "etf_symbol": None, "etf_return_pct": None, "etf_return_basis": "前复权价格，含分红调整；非基金净值全收益",
        }
        benchmark = {p.trade_date: p for p in _prices(context.benchmark_prices)}
        aligned = [p for p in prices if p.trade_date in benchmark]
        if len(aligned) >= 2:
            aligned_benchmark = [benchmark[p.trade_date] for p in aligned]
            benchmark_return = _return(aligned_benchmark)
            index_return = _return(aligned)
            metrics.update({"benchmark_symbol": aligned_benchmark[-1].symbol, "benchmark_return_pct": benchmark_return,
                            "benchmark_sample_count": len(aligned), "benchmark_start_date": aligned[0].trade_date.isoformat(),
                            "benchmark_end_date": aligned[-1].trade_date.isoformat(), "benchmark_aligned_index_return_pct": index_return,
                            "excess_return_pct": index_return - benchmark_return if index_return is not None and benchmark_return is not None else None})
        etf = _prices(context.etf_prices)
        if len(etf) >= 2:
            metrics.update({"etf_symbol": etf[-1].symbol, "etf_return_pct": _return(etf), "etf_start_date": etf[0].trade_date.isoformat(),
                            "etf_end_date": etf[-1].trade_date.isoformat(), "etf_sample_count": len(etf)})
        elif context.requested_instrument:
            metrics["etf_symbol"] = context.requested_instrument.get("symbol")
            metrics["etf_unavailable_reason"] = "ETF独立复权行情未取得，指数收益不能替代ETF表现"
        summary = f"{metrics['start_date']} 至 {metrics['end_date']}，价格指数区间收益 {metrics['return_pct']:.2f}%，最大回撤 {drawdown * 100:.2f}%"
        if not aligned:
            summary += "；基准行情不可用，未计算超额收益"
        return AnalysisResult(dimension=self.dimension, status="ok" if len(aligned) >= 2 else "partial", summary=summary, metrics=metrics)
