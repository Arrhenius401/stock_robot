"""本地 ETF 日线托底测试。"""

from datetime import date

import pandas as pd
import pytest

from radar.data import FallbackETFDataProvider, LocalETFDataProvider, RadarDataError


def _history(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "日期": ["2024-01-02", "2024-01-03"],
        "开盘": [3.7, 3.8],
        "最高": [3.9, 4.0],
        "最低": [3.6, 3.7],
        "收盘": closes,
        "成交量": [100, 110],
        "成交额": [380, 440],
    })


def test_local_provider_imports_merges_and_reads_history(tmp_path):
    source = tmp_path / "source.csv"
    _history([3.8, 4.0]).to_csv(source, index=False, encoding="utf-8-sig")
    provider = LocalETFDataProvider(tmp_path / "local")

    rows, start, end = provider.import_csv("510300", source)
    result = provider.fetch_daily("510300", date(2024, 1, 1), date(2024, 1, 31))

    assert (rows, start, end) == (2, date(2024, 1, 2), date(2024, 1, 3))
    assert result.loc[1, "close"] == 4.0
    replacement = tmp_path / "replacement.csv"
    _history([3.8, 4.2]).to_csv(replacement, index=False, encoding="utf-8-sig")
    provider.import_csv("510300", replacement)
    updated = provider.fetch_daily("510300", date(2024, 1, 1), date(2024, 1, 31))
    assert len(updated) == 2
    assert updated.loc[1, "close"] == 4.2


def test_local_provider_rejects_incomplete_or_wrong_symbol_files(tmp_path):
    incomplete = tmp_path / "incomplete.csv"
    pd.DataFrame({"日期": ["2024-01-02"], "收盘": [3.8]}).to_csv(incomplete, index=False)
    provider = LocalETFDataProvider(tmp_path / "local")

    with pytest.raises(RadarDataError, match="缺少字段"):
        provider.import_csv("510300", incomplete)
    with pytest.raises(RadarDataError, match="未导入"):
        provider.fetch_daily("510300", date(2024, 1, 1), date(2024, 1, 31))


def test_fallback_prefers_local_history_and_reports_local_source(tmp_path):
    source = tmp_path / "source.csv"
    _history([3.8, 4.0]).to_csv(source, index=False)
    local = LocalETFDataProvider(tmp_path / "local")
    local.import_csv("510300", source)
    provider = FallbackETFDataProvider((local,))

    result, source_name = provider.fetch_daily_with_source("510300", date(2024, 1, 1), date(2024, 1, 31))

    assert len(result) == 2
    assert source_name == "本地导入 CSV"
