"""ETF 月度轮动回测的无未来函数测试。"""

from datetime import date, timedelta
from typing import Any, cast

import pandas as pd

from radar.backtest import run_monthly_rotation


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
