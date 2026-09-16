"""ETF 月度轮动回测的无未来函数测试。"""

from datetime import date, timedelta
from typing import Any, cast

import pandas as pd
import pytest

from radar.backtest import _rebalance, run_monthly_rotation


def _history(symbol: str, offset: float) -> pd.DataFrame:
    start = date(2024, 1, 1)
    rows = []
    for day in range(92):
        current = start + timedelta(days=day)
        close = 100 + offset + day
        rows.append({"date": current, "open": close, "close": close, "amount": 1_000_000})
    return pd.DataFrame(rows)


def test_monthly_rotation_uses_next_day_open_and_writes_trades():
    result = run_monthly_rotation(
        {"a": _history("a", 0), "b": _history("b", 1), "c": _history("c", 2)},
        {"a": "cn", "b": "cn", "c": "cn"},
        date(2024, 1, 1),
        date(2024, 4, 1),
    )

    assert not result.equity_curve.empty
    assert not result.trades.empty
    trades: Any = result.trades
    assert all(row.trade_date > row.signal_date for row in cast(Any, trades.itertuples()))


def test_monthly_rotation_equally_selects_each_category_winner():
    histories = {f"cn{index}": _history(f"cn{index}", index) for index in range(3)}
    histories.update({f"hk{index}": _history(f"hk{index}", index + 10) for index in range(3)})
    result = run_monthly_rotation(
        histories,
        {symbol: "cn" if symbol.startswith("cn") else "hk" for symbol in histories},
        date(2024, 1, 1),
        date(2024, 4, 1),
    )

    buys = result.trades[result.trades["direction"] == "buy"]
    assert {"cn", "hk"} == {symbol[:2] for symbol in buys["symbol"]}
    assert {"年化收益", "最大回撤", "夏普比率"} <= set(result.metrics)


def test_monthly_rotation_writes_three_benchmark_curves_and_metrics():
    dates = [date(2024, 1, 1) + timedelta(days=index) for index in range(92)]
    benchmarks = {
        "money_fund": pd.Series([100 + index * 0.01 for index in range(92)], index=dates),
        "csi_300": pd.Series([100 + index * 0.2 for index in range(92)], index=dates),
        "csi_all_bond": pd.Series([100 + index * 0.05 for index in range(92)], index=dates),
    }
    result = run_monthly_rotation(
        {"a": _history("a", 0), "b": _history("b", 1), "c": _history("c", 2)},
        {"a": "cn", "b": "cn", "c": "cn"},
        date(2024, 1, 1),
        date(2024, 4, 1),
        benchmarks=benchmarks,
    )

    assert {"strategy_equity", "money_fund_equity", "csi_300_equity", "csi_all_bond_equity"} <= set(result.equity_curve.columns)
    assert set(result.benchmark_metrics) == set(benchmarks)
    assert "超额累计收益" in result.benchmark_metrics["csi_300"]


def test_rebalance_retains_position_when_sell_open_is_missing():
    execution_date = date(2024, 2, 1)
    histories = {
        "old": pd.DataFrame([{"date": execution_date, "open": None, "close": 100.0}]),
        "new": pd.DataFrame([{"date": execution_date, "open": 100.0, "close": 100.0}]),
    }

    cash, holdings, trades, warnings = _rebalance(
        histories, {"old": 10.0}, 1_000.0, ("new",), date(2024, 1, 31),
        execution_date, 0.0003, 0.0002,
    )

    assert holdings["old"] == 10.0
    assert holdings["new"] > 0
    assert cash >= 0
    assert {trade["direction"] for trade in trades} == {"buy"}
    assert warnings == ["2024-02-01 old 缺少开盘价，保留原持仓"]


def test_rebalance_applies_commission_and_slippage_to_both_sides():
    execution_date = date(2024, 2, 1)
    histories = {
        "old": pd.DataFrame([{"date": execution_date, "open": 100.0, "close": 100.0}]),
        "new": pd.DataFrame([{"date": execution_date, "open": 100.0, "close": 100.0}]),
    }

    cash, _, sells, _ = _rebalance(
        histories, {"old": 10.0}, 0.0, (), date(2024, 1, 31), execution_date, 0.01, 0.02,
    )
    _, holdings, buys, _ = _rebalance(
        histories, {}, 1_000.0, ("new",), date(2024, 1, 31), execution_date, 0.01, 0.02,
    )

    assert cash == pytest.approx(10 * 100 * (1 - 0.02) * (1 - 0.01))
    assert sells[0]["execution_price"] == pytest.approx(98.0)
    assert sells[0]["cost"] == pytest.approx(9.8)
    assert holdings["new"] == pytest.approx(1_000 / (102 * 1.01))
    assert buys[0]["execution_price"] == pytest.approx(102.0)
    assert buys[0]["cost"] == pytest.approx(1_000 / 1.01 * 0.01)


def test_monthly_rotation_trims_to_common_benchmark_start():
    histories = {"a": _history("a", 0), "b": _history("b", 1), "c": _history("c", 2)}
    late_benchmark = pd.Series(
        [100.0, 101.0], index=[date(2024, 1, 2), date(2024, 1, 3)],
    )

    result = run_monthly_rotation(
        histories, {"a": "cn", "b": "cn", "c": "cn"},
        date(2024, 1, 1), date(2024, 1, 4), benchmarks={"csi_300": late_benchmark},
    )

    assert result.equity_curve.index.min() == date(2024, 1, 2)
    assert result.warnings == ("基准可用期限制，策略回测起点已从 2024-01-01 裁剪至 2024-01-02（csi_300 自 2024-01-02 起可用）",)
