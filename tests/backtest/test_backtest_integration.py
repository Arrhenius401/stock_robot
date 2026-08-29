"""回测端到端集成测试 — 固定数据完整链路：数据 → 信号 → 仿真 → 产物。

不访问网络：行情与基准由假数据提供者注入，策略与成本走真实默认配置。
"""

import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from backtest.artifacts import write_backtest_artifacts
from backtest.data import HistoricalPriceProvider
from backtest.models import BacktestRequest
from backtest.runner import BacktestRunner
from backtest.strategy import StrategyRepository
from data.schemas import PriceData
from utils.config import Config


def _price_series() -> list[PriceData]:
    """固定行情：55 天上涨 → 35 天下跌 → 45 天横盘缩量。

    前 90 天构成预热（coverage 恰好 90）；横盘段技术评分落在
    [watch, attack) 区间（探针实测），产生 watch → attack 的调仓序列。
    """
    n_rise, n_fall, n_flat = 55, 35, 45
    total = n_rise + n_fall + n_flat
    dates = pd.bdate_range("2023-08-01", periods=total)
    closes: list[float] = []
    price = 10.0
    for i in range(total):
        if i < n_rise:
            price = price * 1.008
        elif i < n_rise + n_fall:
            price = price * 0.985
        else:
            price = price * 1.0005  # 横盘微涨，MA5 逐渐上穿但 MA60 仍压制
        closes.append(round(price, 2))
    prices: list[PriceData] = []
    for i, d in enumerate(dates):
        c = closes[i]
        prev = closes[i - 1] if i > 0 else c
        if i < n_rise + n_fall - 5:
            volume = int(10000 * (1 + i * 0.01))  # 上涨/下跌段放量
        else:
            volume = int(10000 * (1.0 - (i % 10) * 0.02))  # 横盘段缩量
        prices.append(
            PriceData(
                symbol="000001",
                trade_date=d.date(),
                open=round(prev, 2),
                high=round(max(c, prev) * 1.01, 2),
                low=round(min(c, prev) * 0.99, 2),
                close=c,
                volume=volume,
            )
        )
    return prices


class _FixedProvider(HistoricalPriceProvider):
    """固定行情与基准的假数据提供者（名义继承以满足结构匹配）。"""

    def __init__(self, prices: list[PriceData]):
        self._prices = prices
        self._benchmark = pd.Series(
            100.0 * 1.001 ** pd.RangeIndex(len(prices)),  # 固定缓慢上涨
            index=pd.bdate_range(prices[0].trade_date, prices[-1].trade_date),
        )
        self._benchmark.index.name = "trade_date"

    def fetch_stock(self, symbol: str, start: date, end: date) -> list[PriceData]:
        return [p for p in self._prices if start <= p.trade_date <= end]

    def fetch_benchmark(self, benchmark, start: date, end: date) -> pd.Series:
        series = self._benchmark.loc[pd.Timestamp(start):pd.Timestamp(end)]
        series.index.name = "trade_date"
        return series


@pytest.fixture
def runner(tmp_path):
    """真实默认策略 + 默认配置（临时目录）的完整运行器。"""
    prices = _price_series()
    provider = _FixedProvider(prices)
    strategies = StrategyRepository(Path("config/strategies"))
    config = Config(config_dir=tmp_path)
    return (
        BacktestRunner(provider=provider, strategies=strategies, config=config),
        prices,
    )


def test_single_stock_backtest_writes_auditable_run(tmp_path, runner):
    """端到端：固定数据跑完整回测并落盘五类可审计产物。"""
    backtest_runner, prices = runner
    request = BacktestRequest(
        symbol="000001",
        start_date=prices[90].trade_date,  # 预热 90 天完成后进入正式区间
        end_date=prices[-1].trade_date,
        strategy_id="report_technical",
        benchmark_id="money_fund",
        initial_cash=100000.0,
    )

    result = backtest_runner.run(request)
    output = write_backtest_artifacts(result, tmp_path / "reports")

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["strategy"]["id"] == "report_technical"
    assert manifest["request"]["start_date"] == str(request.start_date)
    assert output.relative_to(tmp_path / "reports").parts[:4] == (
        "backtests", "report_technical", "000001",
        datetime.now().astimezone().strftime("%Y-%m"),
    )

    report_md = (output / "report.md").read_text(encoding="utf-8")
    assert "策略年化" in report_md
    assert "历史表现不代表未来收益" in report_md

    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    # 验收：summary 扁平摘要与运行器指标一致（指标键名直通）
    assert summary["交易次数"] == result.metrics["交易次数"]
    assert summary["strategy_id"] == "report_technical"
    assert summary["run_id"].split("_")[-1] == manifest["fingerprint"]

    for name in ("equity_curve.csv", "trades.csv"):
        df = pd.read_csv(output / name)
        assert len(df) > 0


def test_default_strategy_watch_signal_triggers_position(tmp_path, runner):
    """默认策略（watch→0.4）在 watch 信号下真实建仓，覆盖 watch 档路径。"""
    backtest_runner, prices = runner
    request = BacktestRequest(
        symbol="000001",
        start_date=prices[90].trade_date,
        end_date=prices[-1].trade_date,
        strategy_id="report_technical",
        benchmark_id="money_fund",
        initial_cash=100000.0,
    )

    result = backtest_runner.run(request)

    assert not result.trades.empty
    first = result.trades.iloc[0]
    assert first["reason"] == "watch"
    assert first["direction"] == "buy"
    # 首个可下单日为 start（由 start 前一交易日信号决定）
    assert first["signal_date"] == prices[89].trade_date
    assert first["trade_date"] == request.start_date
    assert result.metrics["交易次数"] >= 1
