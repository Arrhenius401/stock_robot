"""行情缓存的实际覆盖、口径隔离和恢复补齐测试。"""
from datetime import date

import pandas as pd
import pytest

from radar.data import RadarDataError
from radar.history import HistoryKey, HistoryStore, HistorySynchronizer


def frame(*days, scale=1):
    return pd.DataFrame({"date": [date(2026, 9, day) for day in days], "open": [scale * 10.0]*len(days), "high": [scale * 11.0]*len(days), "low": [scale * 9.0]*len(days), "close": [scale * 10.0]*len(days), "amount": [100.0]*len(days)})


class Provider:
    adjustment = "unadjusted"
    def __init__(self, result):
        self.result = result
        self.calls = []
    def supports(self, asset_type):
        return asset_type == "etf"
    def fetch_spot(self):
        raise NotImplementedError
    def fetch_daily(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        return self.result.copy()


def test_sort_deduplicate_and_actual_coverage_without_full_history_claim(tmp_path):
    store = HistoryStore(tmp_path / "radar.db")
    provider = Provider(frame(30, 28, 29, 29))
    sync = HistorySynchronizer(store, provider)
    result, source = sync.prepare("510300", "cn", date(2026, 1, 1), date(2026, 9, 30))
    assert result.date.tolist() == [date(2026, 9, d) for d in (28, 29, 30)]
    coverage = store.coverage(HistoryKey(source, "cn", "unadjusted", "510300"))
    assert coverage is not None
    assert coverage["first_date"] == "2026-09-28"
    assert coverage["row_count"] == 3
    assert coverage["requested_start"] == "2026-01-01"
    assert coverage["complete_since_inception"] is False


def test_stale_tail_and_internal_gaps_fail_but_preserve_download_for_retry(tmp_path):
    store = HistoryStore(tmp_path / "radar.db")
    sync = HistorySynchronizer(store, Provider(frame(28, 30)))
    with pytest.raises(RadarDataError, match="缺失交易日"):
        sync.prepare("510300", "cn", date(2026, 9, 28), date(2026, 9, 30), trading_days=[date(2026, 9, d) for d in (28, 29, 30)])
    with pytest.raises(RadarDataError, match="目标交易日"):
        HistorySynchronizer(store, Provider(frame(28))).prepare("510500", "cn", date(2026, 9, 28), date(2026, 9, 30))


def test_series_do_not_mix_and_unadjusted_revisions_update_overlap(tmp_path):
    store = HistoryStore(tmp_path / "radar.db")
    key = HistoryKey("source", "cn", "unadjusted", "510300")
    store.merge(key, frame(28, 29), date(2026, 9, 1), date(2026, 9, 29))
    store.merge(key, frame(29, 30, scale=2), date(2026, 9, 29), date(2026, 9, 30))
    store.merge(HistoryKey("source", "cn", "qfq", "510300"), frame(30, scale=3), date(2026, 9, 30), date(2026, 9, 30))
    result = store.read(key, date(2026, 9, 1), date(2026, 9, 30))
    assert result.close.tolist() == [10, 20, 20]
    assert store.read(HistoryKey("source", "hk", "unadjusted", "510300"), date(2026, 9, 1), date(2026, 9, 30)).empty


def test_adjustment_revision_invalidates_old_prefix_and_refetches_its_coverage(tmp_path):
    store = HistoryStore(tmp_path / "radar.db")
    provider = Provider(frame(1, 28, 29))
    provider.adjustment = "qfq"
    sync = HistorySynchronizer(store, provider)
    sync.prepare("510300", "cn", date(2026, 9, 1), date(2026, 9, 29))
    provider.result = frame(1, 28, 29, 30, scale=2)
    result, _ = sync.prepare("510300", "cn", date(2026, 9, 1), date(2026, 9, 30))
    assert len(provider.calls) == 3
    assert provider.calls[-1][1] == date(2026, 9, 1)
    assert result.close.tolist() == [20, 20, 20, 20]


def test_successful_retry_reuses_cached_series_without_network(tmp_path):
    provider = Provider(frame(28, 29, 30))
    sync = HistorySynchronizer(HistoryStore(tmp_path / "radar.db"), provider)
    sync.prepare("510300", "cn", date(2026, 9, 28), date(2026, 9, 30))
    sync.prepare("510300", "cn", date(2026, 9, 28), date(2026, 9, 30), reuse=True)
    assert len(provider.calls) == 1


def test_invalid_price_rejected(tmp_path):
    bad = frame(30)
    bad.loc[0, "close"] = float("nan")
    with pytest.raises(RadarDataError, match="无效价格"):
        HistorySynchronizer(HistoryStore(tmp_path / "radar.db"), Provider(bad)).prepare("510300", "cn", date(2026, 9, 28), date(2026, 9, 30))


def test_fallback_skips_stale_primary_for_fresh_alternative(tmp_path):
    from radar.data import FallbackETFDataProvider

    primary = Provider(frame(28, 29))
    class Alternative(Provider):
        source_label = "备源"

    alternative = Alternative(frame(28, 29, 30))
    sync = HistorySynchronizer(HistoryStore(tmp_path / "radar.db"), FallbackETFDataProvider([primary, alternative]))
    result, source = sync.prepare("510300", "cn", date(2026, 9, 28), date(2026, 9, 30))
    assert source == "备源"
    assert result.iloc[-1]["date"] == date(2026, 9, 30)


def test_unknown_adjustment_revisions_also_invalidate_prefix(tmp_path):
    provider = Provider(frame(1, 28, 29))
    provider.adjustment = "unknown"
    sync = HistorySynchronizer(HistoryStore(tmp_path / "radar.db"), provider)
    sync.prepare("510300", "cn", date(2026, 9, 1), date(2026, 9, 29))
    provider.result = frame(1, 28, 29, 30, scale=2)
    result, _ = sync.prepare("510300", "cn", date(2026, 9, 1), date(2026, 9, 30))
    assert provider.calls[-1][1] == date(2026, 9, 1)
    assert result.iloc[0]["close"] == 20


def test_fallback_internal_gap_selects_complete_alternative(tmp_path):
    from radar.data import FallbackETFDataProvider

    primary = Provider(frame(28, 30))
    class Alternative(Provider):
        source_label = "备源"
    alternative = Alternative(frame(28, 29, 30))
    sync = HistorySynchronizer(HistoryStore(tmp_path / "radar.db"), FallbackETFDataProvider([primary, alternative]))
    result, source = sync.prepare("510300", "cn", date(2026, 9, 28), date(2026, 9, 30), trading_days=[date(2026, 9, day) for day in (28, 29, 30)])
    assert source == "备源"
    assert len(result) == 3


def test_old_prefix_gap_is_filled_even_outside_scoring_window(tmp_path):
    store = HistoryStore(tmp_path / "radar.db")
    provider = Provider(frame(1, 2, 3, 29, 30))
    key = HistoryKey("Provider", "cn", "unadjusted", "510300")
    store.merge(key, frame(1, 3, 29), date(2026, 9, 1), date(2026, 9, 29), missing_dates=[date(2026, 9, 2)])
    store.select(key, "Provider")
    result, _ = HistorySynchronizer(store, provider).prepare("510300", "cn", date(2026, 9, 20), date(2026, 9, 30), trading_days=[date(2026, 9, d) for d in (1, 2, 3, 29, 30)])
    assert provider.calls[0][1] <= date(2026, 9, 2)
    assert result.date.tolist() == [date(2026, 9, 29), date(2026, 9, 30)]
    assert date(2026, 9, 2) in store.read(key, date(2026, 9, 1), date(2026, 9, 30)).date.tolist()
    coverage = store.coverage(key)
    assert coverage is not None
    assert coverage["missing_dates"] == []


def test_read_or_sync_expands_earlier_window_but_reuses_shorter_window(tmp_path):
    provider = Provider(frame(28, 29, 30))
    sync = HistorySynchronizer(HistoryStore(tmp_path / "radar.db"), provider)
    sync.read_or_sync("510300", "cn", date(2026, 9, 28), date(2026, 9, 30))
    sync.read_or_sync("510300", "cn", date(2026, 9, 29), date(2026, 9, 30))
    assert len(provider.calls) == 1
    provider.result = frame(1, 28, 29, 30)
    result, _ = sync.read_or_sync("510300", "cn", date(2026, 9, 1), date(2026, 9, 30))
    assert len(provider.calls) == 2
    assert result.iloc[0]["date"] == date(2026, 9, 1)


def test_success_target_binding_survives_other_date_source_switch(tmp_path):
    from radar.data import FallbackETFDataProvider

    primary = Provider(frame(28, 29, 30))
    class Alternative(Provider):
        source_label = "其他源"
    alternative = Alternative(frame(15))
    sync = HistorySynchronizer(HistoryStore(tmp_path / "radar.db"), FallbackETFDataProvider([primary, alternative]))
    sync.prepare("510300", "cn", date(2026, 9, 1), date(2026, 9, 30))
    primary.result = pd.DataFrame(columns=["date", "open", "high", "low", "close", "amount"])
    sync.prepare("510300", "cn", date(2026, 9, 1), date(2026, 9, 15))
    primary.calls.clear()
    alternative.calls.clear()
    result, source = sync.prepare("510300", "cn", date(2026, 9, 1), date(2026, 9, 30), reuse=True)
    assert source == "Provider"
    assert result.iloc[-1]["date"] == date(2026, 9, 30)
    assert primary.calls == [] and alternative.calls == []


@pytest.mark.parametrize("refetch_failure", [None, "network", "stale"])
def test_earlier_adjustment_revision_keeps_latest_tail_and_failed_rebuild_keeps_cache(tmp_path, refetch_failure):
    store = HistoryStore(tmp_path / "radar.db")
    provider = Provider(frame(1, 15, 30))
    provider.adjustment = "qfq"
    sync = HistorySynchronizer(store, provider)
    key = HistoryKey("Provider", "cn", "qfq", "510300")
    sync.prepare("510300", "cn", date(2026, 9, 1), date(2026, 9, 30))
    original_fetch = provider.fetch_daily

    def revised_fetch(symbol, start, end):
        if end == date(2026, 9, 30) and refetch_failure == "network":
            raise RadarDataError("模拟重建网络失败")
        revised = frame(1, 15, 30, scale=2)
        earlier = frame(1, 15, scale=2)
        earlier.loc[0, "date"] = date(2026, 8, 1)
        provider.result = pd.concat([earlier, revised], ignore_index=True)
        if end == date(2026, 9, 30) and refetch_failure == "stale":
            provider.result = provider.result.drop(index=provider.result.index[provider.result.date >= end])
        return original_fetch(symbol, start, end)

    provider.fetch_daily = revised_fetch
    if refetch_failure:
        with pytest.raises(RadarDataError):
            sync.prepare("510300", "cn", date(2026, 8, 1), date(2026, 9, 15))
        assert store.read(key, date(2026, 9, 1), date(2026, 9, 30)).close.tolist() == [10, 10, 10]
    else:
        result, _ = sync.prepare("510300", "cn", date(2026, 8, 1), date(2026, 9, 15))
        assert provider.calls[-1][1:] == (date(2026, 8, 1), date(2026, 9, 30))
        assert result.iloc[0]["date"] == date(2026, 8, 1)
        assert result.iloc[-1]["date"] == date(2026, 9, 15)
        assert store.read(key, date(2026, 9, 30), date(2026, 9, 30)).close.tolist() == [20]
    before = len(provider.calls)
    latest, _ = sync.prepare("510300", "cn", date(2026, 9, 1), date(2026, 9, 30), reuse=True)
    assert latest.iloc[-1]["date"] == date(2026, 9, 30)
    assert len(provider.calls) == before
