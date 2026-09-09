"""单个 ETF 历史表现的确定性计算。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

import pandas as pd


@dataclass(frozen=True, slots=True)
class InstrumentPerformance:
    """单标的及固定基准的归一化曲线和指标。"""

    equity_curve: pd.DataFrame
    metrics: dict[str, float]
    benchmark_metrics: dict[str, dict[str, float]]


def calculate_instrument_performance(
    history: pd.DataFrame,
    benchmarks: Mapping[str, pd.Series],
) -> InstrumentPerformance:
    """按 ETF 实际交易日对齐三条基准，计算自身而非池级策略表现。"""
    required = {"date", "close"}
    if missing := required - set(history.columns):
        raise ValueError(f"标的历史缺少字段: {', '.join(sorted(missing))}")
    frame = history[["date", "close"]].copy()
    dates: pd.Series = pd.Series(pd.to_datetime(frame["date"], errors="coerce"))
    frame["date"] = dates.dt.date
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna()
    frame = frame.groupby("date", as_index=False, sort=True).last()
    if len(frame) < 2 or float(frame.iloc[0]["close"]) <= 0:
        raise ValueError("标的历史不足以计算表现")
    frame = frame.set_index("date")
    closes = cast(pd.Series, frame["close"])
    curve = pd.DataFrame(index=closes.index)
    curve.index.name = "date"
    curve["instrument_equity"] = closes / float(closes.iloc[0])
    benchmark_metrics: dict[str, dict[str, float]] = {}
    for benchmark_id, values in benchmarks.items():
        normalized = _align_benchmark(values, closes.index)
        curve[f"{benchmark_id}_equity"] = normalized
        curve[f"{benchmark_id}_relative_equity"] = curve["instrument_equity"] / normalized
        benchmark_metrics[benchmark_id] = _benchmark_metrics(cast(pd.Series, curve["instrument_equity"]), normalized)
    return InstrumentPerformance(
        equity_curve=curve,
        metrics=_metrics(cast(pd.Series, curve["instrument_equity"])),
        benchmark_metrics=benchmark_metrics,
    )


def _align_benchmark(values: pd.Series, dates: pd.Index) -> pd.Series:
    aligned = values.copy()
    aligned.index = pd.Index([pd.Timestamp(value).date() for value in aligned.index], name="date")
    result = aligned.reindex(dates).ffill()
    if result.isna().any() or float(result.iloc[0]) <= 0:
        raise ValueError("基准无法对齐至标的交易日")
    return result / float(result.iloc[0])


def _metrics(equity: pd.Series) -> dict[str, float]:
    returns = equity.pct_change().dropna()
    total_return = float(equity.iloc[-1] - 1)
    annualized_return = float((1 + total_return) ** (252 / len(equity)) - 1)
    volatility = float(returns.std() * (252**0.5)) if not returns.empty else 0.0
    drawdown = equity / equity.cummax() - 1
    return {
        "累计收益": total_return,
        "年化收益": annualized_return,
        "年化波动": volatility,
        "夏普比率": float(annualized_return / volatility) if volatility else 0.0,
        "最大回撤": float(drawdown.min()),
    }


def _benchmark_metrics(instrument: pd.Series, benchmark: pd.Series) -> dict[str, float]:
    instrument_return = float(instrument.iloc[-1] - 1)
    benchmark_return = float(benchmark.iloc[-1] - 1)
    relative = instrument / benchmark
    relative_drawdown = relative / relative.cummax() - 1
    days = len(instrument)
    return {
        "累计收益": benchmark_return,
        "年化收益": float((1 + benchmark_return) ** (252 / days) - 1),
        "超额累计收益": instrument_return - benchmark_return,
        "超额年化收益": float((1 + instrument_return) ** (252 / days) - (1 + benchmark_return) ** (252 / days)),
        "相对净值最大回撤": float(relative_drawdown.min()),
    }
