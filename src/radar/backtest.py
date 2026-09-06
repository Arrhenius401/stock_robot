"""ETF 配置雷达的低频、无未来函数回测。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
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


def run_monthly_rotation(
    histories: Mapping[str, pd.DataFrame],
    categories: Mapping[str, str],
    start: date,
    end: date,
    initial_cash: float = 100_000.0,
    cost_rate: float = 0.0005,
    weights: Mapping[str, float] | None = None,
) -> RadarBacktestResult:
    """月末按类别选冠军、下一交易日开盘等权调仓；不能成交时保留现金。"""
    if initial_cash <= 0 or cost_rate < 0:
        raise ValueError("初始资金必须为正，成本率不能为负")
    dates = _trading_dates(histories, start, end)
    if not dates:
        return RadarBacktestResult(pd.DataFrame(columns=["equity"]), pd.DataFrame(), {}, ())
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
    for current in dates:
        instruction = planned.get(current)
        if instruction is not None:
            signal_date, choices = instruction
            cash, holdings, executed = _rebalance(histories, holdings, cash, choices, signal_date, current, cost_rate)
            trades.extend(executed)
            rebalance_dates.append(signal_date)
        equity = cash + sum(shares * (_last_close(histories[symbol], current) or 0.0) for symbol, shares in holdings.items())
        rows.append({"date": current, "equity": equity})
    curve = pd.DataFrame(rows).set_index("date")
    return RadarBacktestResult(curve, pd.DataFrame(trades), _metrics(curve, initial_cash), tuple(rebalance_dates))


def _choices_at(histories: Mapping[str, pd.DataFrame], categories: Mapping[str, str], signal_date: date, weights: Mapping[str, float] | None) -> tuple[str, ...]:
    windows = {symbol: cast(pd.DataFrame, frame[pd.to_datetime(frame["date"]).dt.date <= signal_date]) for symbol, frame in histories.items()}
    scores = score_etfs(windows, categories, weights) if weights is not None else score_etfs(windows, categories)
    eligible = scores.dropna(subset=["rank"])
    if eligible.empty:
        return ()
    winners = eligible[eligible["rank"] == 1]
    return tuple(sorted(str(symbol) for symbol in winners["symbol"]))


def _rebalance(histories: Mapping[str, pd.DataFrame], holdings: Mapping[str, float], cash: float, choices: tuple[str, ...], signal_date: date, execution_date: date, cost_rate: float) -> tuple[float, dict[str, float], list[dict[str, Any]]]:
    """先按开盘价卖出旧仓，再对可成交的新类别冠军等权买入。"""
    trades: list[dict[str, Any]] = []
    for symbol, shares in holdings.items():
        price = _price(histories[symbol], execution_date, "open")
        if price is None:
            continue
        gross, cost = shares * price, shares * price * cost_rate
        cash += gross - cost
        trades.append(_trade(signal_date, execution_date, symbol, "sell", price, shares, cost))
    tradable = [symbol for symbol in choices if _price(histories[symbol], execution_date, "open") is not None]
    next_holdings: dict[str, float] = {}
    if not tradable:
        return cash, next_holdings, trades
    budget = cash / len(tradable)
    for symbol in tradable:
        price = _price(histories[symbol], execution_date, "open")
        if price is None:
            continue
        shares = budget / (price * (1 + cost_rate))
        gross, cost = shares * price, shares * price * cost_rate
        cash -= gross + cost
        next_holdings[symbol] = shares
        trades.append(_trade(signal_date, execution_date, symbol, "buy", price, shares, cost))
    return cash, next_holdings, trades


def _trade(signal_date: date, trade_date: date, symbol: str, direction: str, price: float, shares: float, cost: float) -> dict[str, Any]:
    return {"signal_date": signal_date, "trade_date": trade_date, "symbol": symbol, "direction": direction, "price": price, "shares": shares, "cost": cost}


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


def _metrics(curve: pd.DataFrame, initial_cash: float) -> dict[str, float]:
    if curve.empty:
        return {}
    equity = cast(pd.Series, curve["equity"])
    returns = equity.pct_change().dropna()
    total_return = float(equity.iloc[-1] / initial_cash - 1)
    annualized_return = float((1 + total_return) ** (252 / max(len(equity), 1)) - 1)
    volatility = float(returns.std() * (252**0.5)) if not returns.empty else 0.0
    drawdown = equity / equity.cummax() - 1
    return {"累计收益": total_return, "年化收益": annualized_return, "年化波动": volatility, "夏普比率": float(annualized_return / volatility) if volatility else 0.0, "最大回撤": float(drawdown.min())}
