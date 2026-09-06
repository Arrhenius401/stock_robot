"""ETF 数据提供者的离线测试。"""

from datetime import date

import pandas as pd
import pytest

from radar.data import (
    AkShareETFDataProvider,
    FallbackETFDataProvider,
    ProviderCircuitOpenError,
    RadarDataError,
    RequestPacer,
    SinaETFDataProvider,
)


def test_pacer_waits_for_minimum_interval():
    values = iter([0.0, 0.0, 0.25, 1.0])
    waits: list[float] = []
    pacer = RequestPacer(1.0, clock=lambda: next(values), sleeper=waits.append)

    pacer.wait_turn()
    pacer.wait_turn()

    assert waits == [0.75]


def test_provider_normalizes_spot_and_daily_fields():
    spot = pd.DataFrame({"代码": ["510300"], "名称": ["沪深300ETF"], "最新价": [3.8], "成交额": [100]})
    daily = pd.DataFrame(
        {
            "日期": ["2024-01-02"], "开盘": [3.7], "最高": [3.9], "最低": [3.6],
            "收盘": [3.8], "成交量": [10], "成交额": [100],
        }
    )
    provider = AkShareETFDataProvider(spot_fetcher=lambda: spot, daily_fetcher=lambda **_: daily)

    result_spot = provider.fetch_spot()
    result_daily = provider.fetch_daily("510300", date(2024, 1, 1), date(2024, 1, 31))

    assert result_spot.loc[0, "symbol"] == "510300"
    assert set(result_daily.columns) == {"date", "open", "high", "low", "close", "volume", "amount"}
    assert result_daily.loc[0, "date"] == date(2024, 1, 2)


def test_provider_opens_circuit_after_upstream_failure():
    provider = AkShareETFDataProvider(spot_fetcher=lambda: (_ for _ in ()).throw(ConnectionError("429")))

    with pytest.raises(RadarDataError, match="熔断"):
        provider.fetch_spot()
    with pytest.raises(ProviderCircuitOpenError):
        provider.fetch_spot()


def test_provider_rejects_missing_required_columns_and_invalid_range():
    provider = AkShareETFDataProvider(spot_fetcher=lambda: pd.DataFrame({"代码": ["510300"]}))
    with pytest.raises(RadarDataError, match="缺少"):
        provider.fetch_spot()

    with pytest.raises(ValueError, match="start"):
        AkShareETFDataProvider().fetch_daily("510300", date(2024, 2, 1), date(2024, 1, 1))


def test_sina_provider_normalizes_history_and_estimates_amount():
    raw = pd.DataFrame({
        "date": ["2024-01-02"], "open": [3.7], "high": [3.9], "low": [3.6],
        "close": [3.8], "volume": [100],
    })
    provider = SinaETFDataProvider(fetcher=lambda symbol: raw)

    result = provider.fetch_daily("159915", date(2024, 1, 1), date(2024, 1, 31))

    assert result.loc[0, "amount"] == 380.0
    assert result.loc[0, "date"] == date(2024, 1, 2)


def test_fallback_uses_sina_after_primary_failure():
    primary = AkShareETFDataProvider(daily_fetcher=lambda **_: (_ for _ in ()).throw(ConnectionError()))
    raw = pd.DataFrame({
        "date": ["2024-01-02"], "open": [3.7], "high": [3.9], "low": [3.6],
        "close": [3.8], "volume": [100],
    })
    provider = FallbackETFDataProvider((primary, SinaETFDataProvider(fetcher=lambda symbol: raw)))

    result = provider.fetch_daily("510300", date(2024, 1, 1), date(2024, 1, 31))

    assert result.loc[0, "close"] == 3.8
