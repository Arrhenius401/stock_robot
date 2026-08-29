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


def test_stock_history_passes_requested_dates_to_adapter(mocker):
    adapter = mocker.Mock()
    provider = HistoricalPriceProvider(adapter)
    provider.fetch_stock("000001", date(2022, 1, 1), date(2022, 12, 31))
    adapter.fetch.assert_called_once_with(
        "000001", data_type="price", start_date="20220101", end_date="20221231"
    )


def test_benchmark_close_only_series_is_normalized(mocker):
    # H11025/H11001 的中证接口只有日期和收盘价，也必须形成 date→close 序列。
    raw = pd.DataFrame({"日期": ["2022-01-04", "2022-01-05"], "收盘": ["100.0", "100.2"]})
    mocker.patch("backtest.data._ak_csindex", return_value=raw)
    provider = HistoricalPriceProvider(mocker.Mock())
    series = provider.fetch_benchmark(MONEY_FUND, START, END)
    assert series.index.name == "trade_date"
    assert list(series.index) == [date(2022, 1, 4), date(2022, 1, 5)]
    assert series.iloc[0] == pytest.approx(100.0)


def test_missing_trading_days_raise_clear_error():
    broken = [_price(date(2022, 6, 1)), _price(date(2022, 7, 1))]
    with pytest.raises(BacktestDataError, match="连续净值"):
        validate_price_history(broken, START, END)


def test_benchmark_english_columns_normalized(mocker):
    # 中证接口英文列名（date/close）同样必须归一化
    raw = pd.DataFrame({"date": ["2022-01-04"], "close": [100.0]})
    mocker.patch("backtest.data._ak_csindex", return_value=raw)
    series = HistoricalPriceProvider(mocker.Mock()).fetch_benchmark(MONEY_FUND, START, END)
    assert series.index.name == "trade_date"
    assert series.iloc[0] == pytest.approx(100.0)


def test_benchmark_gap_raises_error_with_benchmark_id(mocker):
    # 股票有交易日而基准在该日缺失 → 错误含基准 ID 与缺失日期范围
    raw = pd.DataFrame({"日期": ["2022-01-04", "2022-06-01"], "收盘": ["100.0", "101.0"]})
    mocker.patch("backtest.data._ak_csindex", return_value=raw)
    provider = HistoricalPriceProvider(mocker.Mock())
    with pytest.raises(BacktestDataError, match="money_fund"):
        provider.fetch_benchmark(MONEY_FUND, START, END)


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
