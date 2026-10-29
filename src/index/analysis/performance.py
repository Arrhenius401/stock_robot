"""指数风险收益：价格口径与ETF复权口径分开，基准仅比较共同日期。"""
import calendar
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
        prices = _prices([p for p in context.price_data if p.symbol == context.target.symbol])
        if len(prices) < 2:
            return AnalysisResult(dimension=self.dimension, status="unavailable", summary="至少需要两个有效交易日的真实行情", metrics={})
        end = prices[-1].trade_date
        windows = []
        for months in (1, 3, 12):
            year, month = divmod(end.year * 12 + end.month - 1 - months, 12)
            month += 1
            start = end.replace(year=year, month=month, day=min(end.day, calendar.monthrange(year, month)[1]))
            sample = [p for p in prices if p.trade_date >= start]
            daily = [b.close / a.close - 1 for a, b in pairwise(sample)]
            peak, dd = sample[0].close, 0.0
            for p in sample:
                peak = max(peak, p.close)
                dd = min(dd, p.close / peak - 1)
            notes = ["窗口起点取边界当日或之后首个有效交易日"]
            if prices[0].trade_date > start:
                notes.append("历史不足完整窗口")
            gaps = any((b.trade_date - a.trade_date).days > 4 for a, b in pairwise(sample))
            if gaps:
                notes.append("存在交易日期间隔，可能包含节假日或缺失记录；波动仅基于已取得的相邻样本")
            windows.append({"period": f"{months}m", "label": f"近{months}个月", "start_date": sample[0].trade_date.isoformat(), "end_date": end.isoformat(), "sample_count": len(sample), "return_pct": _return(sample), "max_drawdown_pct": dd * 100 if len(sample) >= 2 else None, "annualized_volatility_pct": stdev(daily) * math.sqrt(252) * 100 if len(daily) >= 60 else None, "notes": notes})
        prices = [p for p in prices if p.trade_date.isoformat() >= windows[-1]["start_date"]]
        returns = [cur.close / prev.close - 1 for prev, cur in pairwise(prices)]
        peak = prices[0].close
        drawdown = 0.0
        for p in prices:
            peak = max(peak, p.close)
            drawdown = min(drawdown, p.close / peak - 1)
        metrics: dict[str, Any] = {
            "windows": windows, "start_date": prices[0].trade_date.isoformat(), "end_date": prices[-1].trade_date.isoformat(),
            "sample_count": len(prices), "return_pct": _return(prices),
            "annualized_volatility_pct": stdev(returns) * math.sqrt(252) * 100 if len(returns) >= 60 else None,
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
        summary = f"{metrics['start_date']} 至 {metrics['end_date']}，价格指数区间收益 " + (f"{metrics['return_pct']:.2f}%" if metrics["return_pct"] is not None else "样本不足") + f"，最大回撤 {drawdown * 100:.2f}%"
        needs_benchmark = context.target.index_style in ("sector", "strategy") and context.target.symbol != "000300"
        if needs_benchmark and len(aligned) < 2:
            summary += "；基准行情不可用，未计算超额收益"
        return AnalysisResult(dimension=self.dimension, status="partial" if needs_benchmark and len(aligned) < 2 else "ok", summary=summary, metrics=metrics)
