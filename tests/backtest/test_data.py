"""历史行情与基准数据边界测试。"""

from datetime import date

import pandas as pd
import pytest

from backtest.data import (
    BacktestDataError,
    HistoricalPriceProvider,
    validate_price_history,
)
from backtest.models import BenchmarkSpec
from data.schemas import PriceData

# 全局配置 backtest.benchmarks.<key> 仅有 {name, symbol} 无 id；
# 消费处（Task 4/5 的 CLI 与 runner）以配置键作为 id 显式补全，测试沿用同一约定。
MONEY_FUND = BenchmarkSpec(id="money_fund", name="中证货币型基金指数", symbol="H11025")

START = date(2022, 1, 1)
END = date(2022, 12, 31)


def _price(trade_date: date) -> PriceData:
    return PriceData(
        symbol="000001",
        trade_date=trade_date,
        open=10.0,
        high=11.0,
        low=9.0,
        close=10.5,
        volume=1000,
    )


def _benchmark_rows(start: date, end: date) -> pd.DataFrame:
    """生成覆盖 [start, end] 全部工作日的基准行情（无内部缺口，日期/收盘两列）。"""
    days = pd.bdate_range(start, end)
    return pd.DataFrame(
        {"日期": [d.strftime("%Y-%m-%d") for d in days], "收盘": ["100.0"] * len(days)}
    )


def test_stock_history_passes_requested_dates_to_adapter(mocker):
    adapter = mocker.Mock()
    provider = HistoricalPriceProvider(adapter)
    provider.fetch_stock("000001", date(2022, 1, 1), date(2022, 12, 31))
    adapter.fetch.assert_called_once_with(
        "000001", data_type="price", start_date="20220101", end_date="20221231"
    )


def test_benchmark_close_only_series_is_normalized(mocker):
    # H11025/H11001 的中证接口只有日期和收盘价，也必须形成 date→close 序列。
    raw = _benchmark_rows(START, END)
    mocker.patch("backtest.data._ak_csindex", return_value=raw)
    provider = HistoricalPriceProvider(mocker.Mock())
    series = provider.fetch_benchmark(MONEY_FUND, START, END)
    assert series.index.name == "trade_date"
    assert series.index[0] == date(2022, 1, 3)
    assert series.index[-1] == date(2022, 12, 30)
    assert series.iloc[0] == pytest.approx(100.0)


def test_missing_trading_days_raise_clear_error():
    broken = [_price(date(2022, 6, 1)), _price(date(2022, 7, 1))]
    with pytest.raises(BacktestDataError, match="连续净值"):
        validate_price_history(broken, START, END)


def test_benchmark_english_columns_normalized(mocker):
    # 中证接口英文列名（date/close）同样必须归一化
    raw = _benchmark_rows(START, END).rename(columns={"日期": "date", "收盘": "close"})
    mocker.patch("backtest.data._ak_csindex", return_value=raw)
    series = HistoricalPriceProvider(mocker.Mock()).fetch_benchmark(MONEY_FUND, START, END)
    assert series.index.name == "trade_date"
    assert series.index[0] == date(2022, 1, 3)
    assert series.iloc[0] == pytest.approx(100.0)


def test_benchmark_gap_raises_error_with_benchmark_id(mocker):
    # 股票有交易日而基准在该日缺失 → 错误含基准 ID 与缺失日期范围。
    # 首末日均在边界容差内，命中内部缺口检查（间隔 148 天 > 15 天）。
    raw = pd.DataFrame(
        {"日期": ["2022-01-04", "2022-06-01", "2022-12-30"], "收盘": ["100.0", "101.0", "102.0"]}
    )
    mocker.patch("backtest.data._ak_csindex", return_value=raw)
    provider = HistoricalPriceProvider(mocker.Mock())
    with pytest.raises(BacktestDataError, match="money_fund"):
        provider.fetch_benchmark(MONEY_FUND, START, END)


def test_benchmark_first_day_beyond_tolerance_raises(mocker):
    # 基准序列首日比 start 晚 10 天（超出 7 天容差）→ 区间起点数据缺失
    raw = _benchmark_rows(date(2022, 1, 11), END)
    mocker.patch("backtest.data._ak_csindex", return_value=raw)
    provider = HistoricalPriceProvider(mocker.Mock())
    with pytest.raises(BacktestDataError, match="money_fund"):
        provider.fetch_benchmark(MONEY_FUND, START, END)


def test_benchmark_last_day_beyond_tolerance_raises(mocker):
    # 基准序列末日比 end 早 10 天（超出 7 天容差）→ 区间终点数据缺失
    raw = _benchmark_rows(START, date(2022, 12, 21))
    mocker.patch("backtest.data._ak_csindex", return_value=raw)
    provider = HistoricalPriceProvider(mocker.Mock())
    with pytest.raises(BacktestDataError, match="money_fund"):
        provider.fetch_benchmark(MONEY_FUND, START, END)


def test_benchmark_first_day_within_weekend_tolerance_ok(mocker):
    # 首日比 start 晚 3 天（周末容差内）→ 正常返回
    raw = _benchmark_rows(START, END).iloc[1:]  # 去掉首个工作日，序列首日变为 01-04
    mocker.patch("backtest.data._ak_csindex", return_value=raw)
    provider = HistoricalPriceProvider(mocker.Mock())
    series = provider.fetch_benchmark(MONEY_FUND, START, END)
    assert series.index[0] == date(2022, 1, 4)
    assert series.index[-1] == date(2022, 12, 30)


def test_benchmark_bad_rows_logged_before_dropped(mocker, caplog):
    # errors="coerce" 丢弃坏行前必须记录 warning（含丢弃行数），剩余行照常返回
    raw = _benchmark_rows(START, END)
    raw.loc[raw.index[5], "日期"] = "not-a-date"
    raw.loc[raw.index[10], "收盘"] = "oops"
    mocker.patch("backtest.data._ak_csindex", return_value=raw)
    provider = HistoricalPriceProvider(mocker.Mock())
    series = provider.fetch_benchmark(MONEY_FUND, START, END)
    assert len(series) == len(_benchmark_rows(START, END)) - 2
    assert "2 行" in caplog.text


def test_benchmark_empty_data_raises(mocker):
    raw = pd.DataFrame({"日期": [], "收盘": []})
    mocker.patch("backtest.data._ak_csindex", return_value=raw)
    provider = HistoricalPriceProvider(mocker.Mock())
    with pytest.raises(BacktestDataError, match="money_fund"):
        provider.fetch_benchmark(MONEY_FUND, START, END)


def test_unordered_prices_raise_clear_error():
    unordered = [_price(date(2022, 7, 1)), _price(date(2022, 6, 1))]
    with pytest.raises(BacktestDataError, match="连续净值"):
        validate_price_history(unordered, START, END)


def test_duplicate_prices_raise_clear_error():
    duplicated = [_price(date(2022, 6, 1)), _price(date(2022, 6, 1))]
    with pytest.raises(BacktestDataError, match="连续净值"):
        validate_price_history(duplicated, START, END)


def test_empty_prices_raise_clear_error():
    with pytest.raises(BacktestDataError, match="连续净值"):
        validate_price_history([], START, END)
