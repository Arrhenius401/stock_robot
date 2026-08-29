"""技术信号时序测试 — 杜绝未来函数。"""

import pandas as pd
import pytest

from analysis.config_loader import ConfigLoader
from backtest.models import BacktestStrategy
from backtest.signal import build_target_weights
from data.schemas import PriceData


def _strategy() -> BacktestStrategy:
    """固定测试策略：阈值 7/4，仓位 attack 0.7 / watch 0 / defend 0。

    watch 目标仓位为 0：预热期（无 MA60）得分 4 视为观望，不下单，
    保证首笔成交由 2024-01-02 的 attack 信号驱动（见 test_runner）。
    """
    return BacktestStrategy(
        id="test_strategy",
        name="测试策略",
        version=1,
        signal_source="technical_score",
        thresholds={"attack": 7.0, "watch": 4.0},
        target_positions={"attack": 0.7, "watch": 0.0, "defend": 0.0},
        execution="next_open",
        warmup_days=90,
        cost_profile="test_profile",
    )


def _price_series(n: int, last_close: float | None = None) -> list[PriceData]:
    """固定 OHLCV 序列：前 90 日阴跌放量（defend），第 90 日暴涨（attack），之后回落。

    last_close 用于构造"未来一日收盘价不同"的对照组；序列第 90 日为 2024-01-02。
    """
    dates = pd.bdate_range("2023-08-29", periods=n)
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
        if last_close is not None and i == n - 1:
            close = last_close
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


@pytest.fixture(scope="module")
def technical_config() -> dict:
    """完整合并打分配置：GeneralScorer 内部取 self.config["technical"]，必须传全量 dict。"""
    return ConfigLoader().load("")


def test_signal_uses_only_prices_available_on_same_day(technical_config):
    # 基线：截至第 n 日的数据；对照组：多一天且最后一日收盘价不同。
    baseline = build_target_weights(_price_series(120), _strategy(), technical_config)
    changed = build_target_weights(_price_series(121, last_close=8.0), _strategy(), technical_config)

    # 未来数据（第 n 日的收盘价）不得影响更早任何一天的信号与仓位
    pd.testing.assert_frame_equal(baseline.iloc[:-1], changed.iloc[:-2])


def test_target_weights_frame_shape(technical_config):
    weights = build_target_weights(_price_series(150), _strategy(), technical_config)

    assert list(weights.columns) == ["signal", "technical_score", "target_weight"]
    assert isinstance(weights.index, pd.DatetimeIndex)
    assert weights.index.name == "signal_date"
    assert len(weights) == 150
    assert set(weights["signal"]) <= {"attack", "watch", "defend"}
    assert (weights["target_weight"] >= 0).all() and (weights["target_weight"] <= 1).all()


def test_warmup_days_produce_defend_then_attack(technical_config):
    # 前 90 日预热期无 target>0（defend/watch=0），第 90 日（2024-01-02）转 attack
    weights = build_target_weights(_price_series(150), _strategy(), technical_config)
    assert (weights["target_weight"].iloc[:90] == 0.0).all()
    assert weights["signal"].iloc[90] == "attack"
    assert weights["target_weight"].iloc[90] == pytest.approx(0.7)
