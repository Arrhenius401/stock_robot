"""ETF 配置雷达的低频、无未来函数回测。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, cast

import pandas as pd

from radar.scoring import score_etfs


@dataclass(frozen=True, slots=True)
class RadarBacktestResult:
    """ETF 月度轮动回测的净值、成交、指标及调仓审计信息。"""

    equity_curve: pd.DataFrame
    trades: pd.DataFrame
    metrics: dict[str, float]
    rebalance_dates: tuple[date, ...] = ()
    benchmark_metrics: dict[str, dict[str, float]] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()


def run_monthly_rotation(
    histories: Mapping[str, pd.DataFrame],
    categories: Mapping[str, str],
    start: date,
    end: date,
    initial_cash: float = 100_000.0,
    cost_rate: float | None = None,
    commission_rate: float | None = None,
    slippage_rate: float = 0.0,
    weights: Mapping[str, float] | None = None,
    benchmarks: Mapping[str, pd.Series] | None = None,
) -> RadarBacktestResult:
    """月末按类别选冠军、下一交易日开盘等权调仓；异常成交保留仓位或现金。"""
    effective_commission = cost_rate if commission_rate is None else commission_rate
    if effective_commission is None:
        effective_commission = 0.0005
    if initial_cash <= 0 or effective_commission < 0 or slippage_rate < 0:
        raise ValueError("初始资金必须为正，佣金率和滑点率不能为负")
    dates = _trading_dates(histories, start, end)
    if not dates:
        return RadarBacktestResult(pd.DataFrame(columns=["equity"]), pd.DataFrame(), {}, (), {})
    planned: dict[date, tuple[date, tuple[str, ...]]] = {}
    for signal_date in _month_ends(dates):
        execution = _next_date(dates, signal_date)
        if execution is not None:
            planned[execution] = (signal_date, _choices_at(histories, categories, signal_date, weights))

    cash = initial_cash
    holdings: dict[str, float] = {}
    trades: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    rebalance_dates: list[date] = []
    warnings: list[str] = []
    for current in dates:
        instruction = planned.get(current)
        if instruction is not None:
            signal_date, choices = instruction
            cash, holdings, executed, rebalance_warnings = _rebalance(
                histories, holdings, cash, choices, signal_date, current,
                effective_commission, slippage_rate,
            )
            trades.extend(executed)
            warnings.extend(rebalance_warnings)
            rebalance_dates.append(signal_date)
        equity = cash + sum(shares * (_last_close(histories[symbol], current) or 0.0) for symbol, shares in holdings.items())
        rows.append({"date": current, "equity": equity})
    curve = pd.DataFrame(rows).set_index("date")
    curve["strategy_equity"] = curve["equity"] / initial_cash
    benchmark_metrics: dict[str, dict[str, float]] = {}
    for benchmark_id, closes in (benchmarks or {}).items():
        normalized = _normalized_benchmark(closes, dates)
        curve[f"{benchmark_id}_equity"] = normalized
        curve[f"{benchmark_id}_relative_equity"] = curve["strategy_equity"] / normalized
        strategy_equity = cast(pd.Series, curve["strategy_equity"])
        benchmark_metrics[benchmark_id] = _benchmark_metrics(strategy_equity, normalized)
    return RadarBacktestResult(
        curve,
        pd.DataFrame(trades),
        _metrics(curve, initial_cash, trades),
        rebalance_dates=tuple(rebalance_dates),
        benchmark_metrics=benchmark_metrics,
        warnings=tuple(warnings),
    )


def _choices_at(histories: Mapping[str, pd.DataFrame], categories: Mapping[str, str], signal_date: date, weights: Mapping[str, float] | None) -> tuple[str, ...]:
    windows = {symbol: cast(pd.DataFrame, frame[pd.to_datetime(frame["date"]).dt.date <= signal_date]) for symbol, frame in histories.items()}
    scores = score_etfs(windows, categories, weights) if weights is not None else score_etfs(windows, categories)
    eligible = scores.dropna(subset=["rank"])
    if eligible.empty:
        return ()
    winners = eligible[eligible["rank"] == 1]
    return tuple(sorted(str(symbol) for symbol in winners["symbol"]))


def _rebalance(
    histories: Mapping[str, pd.DataFrame], holdings: Mapping[str, float], cash: float,
    choices: tuple[str, ...], signal_date: date, execution_date: date,
    commission_rate: float, slippage_rate: float,
) -> tuple[float, dict[str, float], list[dict[str, Any]], list[str]]:
    """先卖出可成交旧仓，再对可成交冠军等权买入；不能卖出的旧仓继续持有。"""
    trades: list[dict[str, Any]] = []
    warnings: list[str] = []
    next_holdings: dict[str, float] = {}
    for symbol, shares in holdings.items():
        price = _price(histories[symbol], execution_date, "open")
        if price is None:
            next_holdings[symbol] = shares
            warnings.append(f"{execution_date.isoformat()} {symbol} 缺少开盘价，保留原持仓")
            continue
        execution_price = price * (1 - slippage_rate)
        gross, cost = shares * execution_price, shares * execution_price * commission_rate
        cash += gross - cost
        trades.append(_trade(
            signal_date, execution_date, symbol, "sell", price, execution_price, shares,
            cost, 0.0, slippage_rate,
        ))
    tradable = [symbol for symbol in choices if _price(histories[symbol], execution_date, "open") is not None]
    if not tradable:
        if choices:
            warnings.append(f"{execution_date.isoformat()} 没有可交易候选，剩余资金保留为现金")
        return cash, next_holdings, trades, warnings
    budget = cash / len(tradable)
    for symbol in tradable:
        price = _price(histories[symbol], execution_date, "open")
        if price is None:
            continue
        execution_price = price * (1 + slippage_rate)
        shares = budget / (execution_price * (1 + commission_rate))
        gross, cost = shares * execution_price, shares * execution_price * commission_rate
        cash -= gross + cost
        next_holdings[symbol] = shares
        trades.append(_trade(
            signal_date, execution_date, symbol, "buy", price, execution_price, shares,
            cost, 1 / len(tradable), slippage_rate,
        ))
    return cash, next_holdings, trades, warnings


def _trade(
    signal_date: date, trade_date: date, symbol: str, direction: str, price: float,
    execution_price: float, shares: float, cost: float, target_weight: float,
    slippage_rate: float,
) -> dict[str, Any]:
    gross = shares * execution_price
    return {
        "signal_date": signal_date, "trade_date": trade_date, "symbol": symbol,
        "direction": direction, "price": price, "execution_price": execution_price,
        "shares": shares, "gross": gross, "cost": cost,
        "target_weight": target_weight, "slippage_rate": slippage_rate,
    }


def _trading_dates(histories: Mapping[str, pd.DataFrame], start: date, end: date) -> list[date]:
    values: set[date] = set()
    for frame in histories.values():
        for value in frame["date"]:
            normalized = pd.Timestamp(value).date()
            if isinstance(normalized, date):
                values.add(normalized)
    return sorted(value for value in values if start <= value <= end)


def _month_ends(dates: list[date]) -> list[date]:
    return [value for index, value in enumerate(dates) if index == len(dates) - 1 or dates[index + 1].month != value.month]


def _next_date(dates: list[date], current: date) -> date | None:
    index = dates.index(current) + 1
    return dates[index] if index < len(dates) else None


def _last_close(frame: pd.DataFrame, target: date) -> float | None:
    values = frame[pd.to_datetime(frame["date"]).dt.date <= target]
    if values.empty or pd.isna(values.iloc[-1]["close"]):
        return None
    return float(values.iloc[-1]["close"])


def _price(frame: pd.DataFrame, target: date, column: str) -> float | None:
    matches = frame[pd.to_datetime(frame["date"]).dt.date == target]
    if matches.empty or pd.isna(matches.iloc[0][column]):
        return None
    return float(matches.iloc[0][column])


def _metrics(curve: pd.DataFrame, initial_cash: float, trades: list[dict[str, Any]]) -> dict[str, float]:
    if curve.empty:
        return {}
    equity = cast(pd.Series, curve["equity"])
    returns = equity.pct_change().dropna()
    total_return = float(equity.iloc[-1] / initial_cash - 1)
    annualized_return = float((1 + total_return) ** (252 / max(len(equity), 1)) - 1)
    volatility = float(returns.std() * (252**0.5)) if not returns.empty else 0.0
    drawdown = equity / equity.cummax() - 1
    turnover = sum(float(trade["gross"]) for trade in trades) / initial_cash
    return {
        "累计收益": total_return, "年化收益": annualized_return, "年化波动": volatility,
        "夏普比率": float(annualized_return / volatility) if volatility else 0.0,
        "最大回撤": float(drawdown.min()), "交易次数": float(len(trades)), "换手率": turnover,
    }


def _normalized_benchmark(closes: pd.Series, dates: list[date]) -> pd.Series:
    """按策略交易日对齐基准并从正式区间首日归一化。"""
    values = closes.copy()
    values.index = pd.Index([pd.Timestamp(item).date() for item in values.index], name="date")
    aligned = values.reindex(dates).ffill()
    if aligned.isna().any() or float(aligned.iloc[0]) <= 0:
        raise ValueError("基准无法对齐至策略交易日")
    return aligned / float(aligned.iloc[0])


def _benchmark_metrics(strategy: pd.Series, benchmark: pd.Series) -> dict[str, float]:
    """计算单条基准的绝对、超额与相对净值风险指标。"""
    strategy_return = float(strategy.iloc[-1] - 1)
    benchmark_return = float(benchmark.iloc[-1] - 1)
    relative = strategy / benchmark
    drawdown = relative / relative.cummax() - 1
    days = max(len(strategy), 1)
    return {
        "累计收益": benchmark_return,
        "年化收益": float((1 + benchmark_return) ** (252 / days) - 1),
        "超额累计收益": strategy_return - benchmark_return,
        "超额年化收益": float((1 + strategy_return) ** (252 / days) - (1 + benchmark_return) ** (252 / days)),
        "相对净值最大回撤": float(drawdown.min()),
    }
