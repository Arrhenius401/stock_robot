"""回测仿真测试 — 成交时序、费用拆分与净值。"""

from datetime import date

import pandas as pd
import pytest

from backtest.data import BacktestDataError, HistoricalPriceProvider
from backtest.models import BacktestRequest
from backtest.runner import BacktestRunner
from backtest.strategy import StrategyConfigError, StrategyRepository
from data.schemas import PriceData
from utils.config import Config

START = date(2024, 1, 2)
END = date(2024, 6, 30)
INIT_CASH = 100000.0

# 测试专用成本档：滑点为 0 保证成交价可精确断言；佣金率极低使最低佣金生效
TEST_COSTS = {
    "commission_rate": 0.00001,
    "minimum_commission": 5.0,
    "stamp_duty_rate": 0.001,
    "transfer_fee_rate": 0.0001,
    "slippage_rate": 0.0,
}


def _strategy_yaml() -> str:
    return (
        "id: test_strategy\n"
        "name: 测试策略\n"
        "version: 1\n"
        "signal_source: technical_score\n"
        "thresholds:\n"
        "  attack: 7\n"
        "  watch: 4\n"
        "target_positions:\n"
        "  attack: 0.7\n"
        "  watch: 0.0\n"
        "  defend: 0.0\n"
        "execution: next_open\n"
        "warmup_days: 90\n"
        "cost_profile: test_profile\n"
    )


def _price_series() -> list[PriceData]:
    """固定行情：前 90 日阴跌放量，第 90 日（2024-01-02）暴涨，之后回落。

    第 90 日信号为 attack（0.7），第 91 日开盘 11.0 成交；
    第 108 日回落至 defend，第 109 日开盘卖出。
    """
    dates = pd.bdate_range("2023-08-29", periods=150)
    prices: list[PriceData] = []
    for i, d in enumerate(dates):
        if i < 90:
            close = 12.0 - 0.1 * i - 0.0001 * i * i
            volume = int(10000 * 1.025**i)
        elif i == 90:
            close = 11.0
            volume = 300000
        else:
            close = 11.0 - 0.08 * (i - 90)
            volume = int(10000 * 1.025**i)
        if i > 90:
            op = 11.0 if i == 91 else round(close - 0.1, 2)
        else:
            op = round(close, 2)
        prices.append(
            PriceData(
                symbol="000001",
                trade_date=d.date(),
                open=op,
                high=round(close + 0.5, 2),
                low=round(max(close - 0.5, 0.5), 2),
                close=round(close, 2),
                volume=volume,
            )
        )
    return prices


class _FixedProvider(HistoricalPriceProvider):
    """固定行情与基准的假数据提供者（不访问网络）。

    名义继承真实提供者以满足结构匹配，fetch_stock/fetch_benchmark 全部覆写。
    """

    def __init__(self, prices: list[PriceData]):
        self._prices = prices
        self._benchmark = pd.Series(
            100.0,
            index=pd.bdate_range(prices[0].trade_date, prices[-1].trade_date),
        )

    def fetch_stock(self, symbol: str, start: date, end: date) -> list[PriceData]:
        return [p for p in self._prices if start <= p.trade_date <= end]

    def fetch_benchmark(self, benchmark, start: date, end: date) -> pd.Series:
        series = self._benchmark.loc[pd.Timestamp(start):pd.Timestamp(end)]
        series.index.name = "trade_date"
        return series


def _request() -> BacktestRequest:
    return BacktestRequest(
        symbol="000001",
        start_date=START,
        end_date=END,
        strategy_id="test_strategy",
        benchmark_id="money_fund",
        initial_cash=INIT_CASH,
    )


@pytest.fixture
def runner(tmp_path) -> BacktestRunner:
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    (strategies_dir / "test_strategy.yaml").write_text(_strategy_yaml(), encoding="utf-8")

    cfg = Config(config_dir=tmp_path / "config")
    cfg.set("backtest.cost_profiles.test_profile", TEST_COSTS)
    return BacktestRunner(
        provider=_FixedProvider(_price_series()),
        strategies=StrategyRepository(strategies_dir),
        config=cfg,
    )


def test_order_from_day_n_signal_uses_day_n_plus_1_open(runner):
    result = runner.run(_request())

    first = result.trades.iloc[0]
    assert first["signal_date"] == date(2024, 1, 2)
    assert first["trade_date"] == date(2024, 1, 3)
    assert first["price"] == pytest.approx(11.0)
    assert first["direction"] == "buy"
    assert first["reason"] == "attack"


def test_sell_cost_includes_commission_stamp_and_transfer_fee(runner):
    result = runner.run(_request())

    last = result.trades.iloc[-1]
    assert last["direction"] == "sell"
    notional = last["quantity"] * last["price"]
    expected = (
        max(notional * 0.00001, 5.0)  # 佣金（最低佣金 5 元）
        + notional * 0.0001  # 过户费
        + notional * 0.001  # 印花税（仅卖出）
    )
    assert last["fees"] == pytest.approx(expected)


def test_buy_cost_excludes_stamp_duty(runner):
    result = runner.run(_request())

    first = result.trades.iloc[0]
    assert first["direction"] == "buy"
    notional = first["quantity"] * first["price"]
    expected = max(notional * 0.00001, 5.0) + notional * 0.0001
    assert first["fees"] == pytest.approx(expected)


def test_equity_curve_matches_order_book_and_benchmark(runner):
    result = runner.run(_request())
    eq = result.equity_curve

    # 策略净值 1.0 起算；基准恒为 100 点 → 基准净值恒为 1.0
    assert eq["策略净值"].iloc[0] == pytest.approx(1.0)
    assert eq["基准净值"].abs().max() == pytest.approx(1.0)

    # 由订单簿推导最终现金（期末仓位为 0），与 VectorBT 净值曲线严格一致
    cash = INIT_CASH
    for _, trade in result.trades.iterrows():
        notional = trade["quantity"] * trade["price"]
        if trade["direction"] == "buy":
            cash -= notional + trade["fees"]
        else:
            cash += notional - trade["fees"]
    assert eq["策略净值"].iloc[-1] * INIT_CASH == pytest.approx(cash)


def test_metrics_cover_formal_window(runner):
    result = runner.run(_request())

    assert result.metrics["交易次数"] == pytest.approx(2.0)
    assert result.metrics["累计收益"] == pytest.approx(
        result.equity_curve["策略净值"].iloc[-1] - 1.0
    )
    # 基准恒平 → 超额收益等于策略累计收益
    assert result.metrics["超额收益"] == pytest.approx(result.metrics["累计收益"])
    for key in ("年化收益", "最大回撤", "年化波动率", "夏普比率"):
        assert key in result.metrics
    # 预热期不计入正式区间：净值曲线含预热日，但正式区间起点为 2024-01-02
    assert result.data_start == _price_series()[0].trade_date
    assert len(result.equity_curve) == 150
    assert result.equity_curve.loc[pd.Timestamp("2023-12-29"), "当日信号"] == "defend"


def test_benchmark_alignment_gap_raises(tmp_path):
    # 基准缺失前几个交易日 → 对齐后仍有 NaN → 抛 BacktestDataError
    prices = _price_series()

    class _GapProvider(_FixedProvider):
        def fetch_benchmark(self, benchmark, start, end):
            series = self._benchmark.iloc[3:]
            series = series.loc[pd.Timestamp(start):pd.Timestamp(end)]
            series.index.name = "trade_date"
            return series

    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    (strategies_dir / "test_strategy.yaml").write_text(_strategy_yaml(), encoding="utf-8")
    cfg = Config(config_dir=tmp_path / "config")
    cfg.set("backtest.cost_profiles.test_profile", TEST_COSTS)
    gap_runner = BacktestRunner(
        provider=_GapProvider(prices),
        strategies=StrategyRepository(strategies_dir),
        config=cfg,
    )
    with pytest.raises(BacktestDataError, match="money_fund"):
        gap_runner.run(_request())


def test_missing_cost_profile_raises(tmp_path):
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    (strategies_dir / "test_strategy.yaml").write_text(_strategy_yaml(), encoding="utf-8")
    cfg = Config(config_dir=tmp_path / "config")
    missing_runner = BacktestRunner(
        provider=_FixedProvider(_price_series()),
        strategies=StrategyRepository(strategies_dir),
        config=cfg,
    )
    with pytest.raises(StrategyConfigError, match="成本配置"):
        missing_runner.run(_request())


def test_missing_cost_key_raises(tmp_path):
    # 成本档存在但缺费率键 → StrategyConfigError，消息含成本档名与缺失键名
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    (strategies_dir / "test_strategy.yaml").write_text(_strategy_yaml(), encoding="utf-8")
    cfg = Config(config_dir=tmp_path / "config")
    bad_costs = dict(TEST_COSTS)
    del bad_costs["stamp_duty_rate"]
    cfg.set("backtest.cost_profiles.test_profile", bad_costs)
    broken_runner = BacktestRunner(
        provider=_FixedProvider(_price_series()),
        strategies=StrategyRepository(strategies_dir),
        config=cfg,
    )
    with pytest.raises(StrategyConfigError, match="成本"):
        broken_runner.run(_request())


def test_negative_cost_rate_raises(tmp_path):
    # 费率值为负 → 非法成本配置，同样明确失败
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    (strategies_dir / "test_strategy.yaml").write_text(_strategy_yaml(), encoding="utf-8")
    cfg = Config(config_dir=tmp_path / "config")
    bad_costs = dict(TEST_COSTS)
    bad_costs["slippage_rate"] = -0.01
    cfg.set("backtest.cost_profiles.test_profile", bad_costs)
    broken_runner = BacktestRunner(
        provider=_FixedProvider(_price_series()),
        strategies=StrategyRepository(strategies_dir),
        config=cfg,
    )
    with pytest.raises(StrategyConfigError, match="成本"):
        broken_runner.run(_request())


def test_warmup_insufficient_data_raises(tmp_path):
    # 次新股：行情仅 60 天 < warmup_days=90 → 预热不足，明确失败
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    (strategies_dir / "test_strategy.yaml").write_text(_strategy_yaml(), encoding="utf-8")
    cfg = Config(config_dir=tmp_path / "config")
    cfg.set("backtest.cost_profiles.test_profile", TEST_COSTS)
    short_runner = BacktestRunner(
        provider=_FixedProvider(_price_series()[:60]),
        strategies=StrategyRepository(strategies_dir),
        config=cfg,
    )
    with pytest.raises(BacktestDataError, match="预热"):
        short_runner.run(_request())


def test_warmup_sufficient_data_passes(tmp_path):
    # 行情覆盖预热期（120 天 ≥ 90）→ 正常通过；正式区间收窄到数据范围内
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    (strategies_dir / "test_strategy.yaml").write_text(_strategy_yaml(), encoding="utf-8")
    cfg = Config(config_dir=tmp_path / "config")
    cfg.set("backtest.cost_profiles.test_profile", TEST_COSTS)
    long_runner = BacktestRunner(
        provider=_FixedProvider(_price_series()[:120]),
        strategies=StrategyRepository(strategies_dir),
        config=cfg,
    )
    request = BacktestRequest(
        symbol="000001",
        start_date=START,
        end_date=date(2024, 1, 31),
        strategy_id="test_strategy",
        benchmark_id="money_fund",
        initial_cash=INIT_CASH,
    )
    result = long_runner.run(request)
    assert result.data_start == _price_series()[0].trade_date
