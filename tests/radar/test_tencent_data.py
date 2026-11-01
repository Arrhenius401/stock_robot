"""腾讯 ETF 历史日线适配器测试。"""

from datetime import date

import pandas as pd
import pytest
import requests

from radar.data import RadarDataError, TencentETFDataProvider


def test_tencent_provider_normalizes_qfq_history_and_estimates_amount():
    frame = pd.DataFrame([
        {"date": "2025-01-02", "open": "3.1", "high": "3.3", "low": "3.0", "close": "3.2", "volume": "1000"},
    ])
    provider = TencentETFDataProvider(fetcher=lambda symbol, start, end: frame)

    result = provider.fetch_daily("510300", date(2025, 1, 1), date(2025, 1, 3))

    assert result.iloc[0].amount == 3200.0
    assert result.iloc[0].date == date(2025, 1, 2)


def test_tencent_provider_rejects_missing_ohlc_field():
    provider = TencentETFDataProvider(fetcher=lambda symbol, start, end: pd.DataFrame({"date": []}))

    with pytest.raises(RadarDataError, match="缺少字段"):
        provider.fetch_daily("510300", date(2025, 1, 1), date(2025, 1, 3))


def test_tencent_daily_result_reports_failure_and_circuit():
    provider = TencentETFDataProvider(
        fetcher=lambda symbol, start, end: (_ for _ in ()).throw(requests.ConnectionError("offline"))
    )

    result = provider.fetch_daily_result("510300", date(2025, 1, 1), date(2025, 1, 3))

    assert result.value is None
    assert result.record.source == "TencentETFDataProvider"
    assert result.record.result_state == "failed"
    with pytest.raises(RadarDataError, match="已熔断"):
        provider.fetch_daily("510500", date(2025, 1, 1), date(2025, 1, 3))
