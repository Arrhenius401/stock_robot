"""从完整雷达回测产物派生展示区间，避免为每个时间范围重复运行策略。"""

from __future__ import annotations

from datetime import date
from typing import Any, cast

import pandas as pd


class BacktestCacheError(ValueError):
    """缓存产物无法覆盖所请求的展示窗口。"""


def derive_backtest_window(payload: dict[str, Any], start_date: date | None) -> dict[str, Any]:
    """从已完成产物切片并重算窗口指标，保留来源与命中信息。"""
    source_rows = cast(list[dict[str, Any]], payload.get("equity_curve", {}).get("rows", []))
    if not source_rows:
        raise BacktestCacheError("回测缓存缺少净值曲线")
    frame: Any = pd.DataFrame(source_rows)
    if "date" not in frame or "strategy_equity" not in frame:
        raise BacktestCacheError("回测缓存缺少策略净值字段")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    frame = frame.dropna(subset=["date"]).sort_values("date")
    if start_date is not None:
        frame = frame[frame["date"] >= start_date]
    if len(frame) < 2:
        raise BacktestCacheError("已完成回测无法覆盖所选区间")

    actual_start = cast(date, frame.iloc[0]["date"])
    actual_end = cast(date, frame.iloc[-1]["date"])
    rebased: Any = frame.copy()
    equity_columns = [column for column in frame.columns if column.endswith("_equity")]
    for column in equity_columns:
        values: Any = pd.to_numeric(rebased[column], errors="coerce")
        first = values.iloc[0]
        if pd.isna(first) or float(first) <= 0:
            raise BacktestCacheError(f"回测缓存中的 {column} 无法归一化")
        rebased[column] = values / float(first)

    metrics: dict[str, Any] = _strategy_metrics(cast(pd.Series, rebased["strategy_equity"]))
    source_summary = cast(dict[str, Any], payload.get("summary", {}))
    trades = _window_trades(payload, actual_start, actual_end)
    metrics["交易次数"] = float(len(trades))
    metrics["换手率"] = _window_turnover(trades, rebased.iloc[0].get("equity"))
    metrics["strategy_id"] = source_summary.get("strategy_id")
    metrics["start_date"] = actual_start.isoformat()
    metrics["end_date"] = actual_end.isoformat()
    metrics["benchmarks"] = _benchmark_metrics(rebased)

    result = dict(payload)
    result["summary"] = metrics
    result["equity_curve"] = {
        "columns": ["date", *[column for column in rebased.columns if column != "date"]],
        "rows": [
            {key: _json_value(value) for key, value in row.items()}
            for row in rebased.to_dict(orient="records")
        ],
    }
    result["trades"] = {**cast(dict[str, Any], payload.get("trades", {})), "rows": trades}
    result["cache"] = {
        "status": "derived" if start_date is not None else "hit",
        "requested_start_date": start_date.isoformat() if start_date else None,
        "actual_start_date": actual_start.isoformat(),
        "actual_end_date": actual_end.isoformat(),
    }
    return result


def _strategy_metrics(equity: pd.Series) -> dict[str, float]:
    numeric: Any = pd.to_numeric(equity, errors="coerce")
    values: Any = numeric.dropna()
    if len(values) < 2 or float(values.iloc[0]) <= 0:
        raise BacktestCacheError("回测缓存的策略净值不足")
    returns = values.pct_change().dropna()
    total_return = float(values.iloc[-1] - 1)
    annualized_return = float((1 + total_return) ** (252 / len(values)) - 1)
    volatility = float(returns.std() * (252**0.5)) if not returns.empty else 0.0
    drawdown = values / values.cummax() - 1
    return {
        "累计收益": total_return,
        "年化收益": annualized_return,
        "年化波动": volatility,
        "夏普比率": float(annualized_return / volatility) if volatility else 0.0,
        "最大回撤": float(drawdown.min()),
    }


def _benchmark_metrics(frame: pd.DataFrame) -> dict[str, dict[str, float]]:
    source: Any = frame
    strategy = cast(pd.Series, pd.to_numeric(source["strategy_equity"], errors="coerce"))
    result: dict[str, dict[str, float]] = {}
    for column in source.columns:
        if not column.endswith("_equity") or column in {"strategy_equity", "equity"}:
            continue
        if "_relative_" in column:
            continue
        benchmark_id = column.removesuffix("_equity")
        benchmark = cast(pd.Series, pd.to_numeric(source[column], errors="coerce"))
        if benchmark.isna().any() or float(benchmark.iloc[0]) <= 0:
            continue
        benchmark_return = float(benchmark.iloc[-1] - 1)
        strategy_return = float(strategy.iloc[-1] - 1)
        relative = strategy / benchmark
        relative_drawdown = relative / relative.cummax() - 1
        periods = len(strategy)
        result[benchmark_id] = {
            "累计收益": benchmark_return,
            "年化收益": float((1 + benchmark_return) ** (252 / periods) - 1),
            "超额累计收益": strategy_return - benchmark_return,
            "超额年化收益": float((1 + strategy_return) ** (252 / periods) - (1 + benchmark_return) ** (252 / periods)),
            "相对净值最大回撤": float(relative_drawdown.min()),
        }
    return result


def _window_trades(payload: dict[str, Any], start_date: date, end_date: date) -> list[dict[str, Any]]:
    rows = cast(list[dict[str, Any]], payload.get("trades", {}).get("rows", []))
    result: list[dict[str, Any]] = []
    for row in rows:
        raw_date = row.get("trade_date") or row.get("signal_date")
        if raw_date is None:
            continue
        try:
            trade_date = cast(date, pd.Timestamp(raw_date).date())
        except (TypeError, ValueError):
            continue
        if start_date <= trade_date <= end_date:
            result.append(row)
    return result


def _window_turnover(rows: list[dict[str, Any]], opening_equity: Any) -> float:
    gross = sum(float(row.get("gross") or 0) for row in rows)
    try:
        return gross / float(opening_equity) if float(opening_equity) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _json_value(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    return float(value) if isinstance(value, (int, float)) else value
