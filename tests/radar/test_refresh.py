"""配置雷达刷新失败边界测试。"""

from datetime import date

import pandas as pd
import pytest

from radar.data import RadarDataError
from radar.models import RadarUniverse
from radar.refresh import RadarRefresher
from radar.store import RadarStore
from radar.universe import UniverseRepository


class _Repository(UniverseRepository):
    """只提供刷新器所需的固定池版本。"""

    def __init__(self, universe: RadarUniverse):
        self.universe = universe

    def active_on(self, universe_id: str, target_date: date) -> RadarUniverse:
        assert universe_id == self.universe.id
        return self.universe


class _UnavailableProvider:
    """模拟所有免费数据源均不可达。"""

    def supports(self, asset_type: str) -> bool:
        return asset_type == "etf"

    def fetch_spot(self) -> pd.DataFrame:
        raise RadarDataError("现货不可用")

    def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        raise RadarDataError("AkShare 请求失败；腾讯请求失败；官方兜底未启用")


def test_refresh_reports_deduplicated_provider_chain_when_every_instrument_fails(tmp_path):
    universe = RadarUniverse.model_validate({
        "id": "test_etf",
        "name": "测试 ETF 池",
        "version": 1,
        "asset_type": "etf",
        "score_profile": "core_rotation_v1",
        "description": "刷新失败测试",
        "instruments": [
            {
                "symbol": "510500", "name": "中证500ETF", "asset_type": "etf",
                "category": "cn_equity", "market": "cn", "exposure_region": "cn",
                "effective_from": "2020-01-01", "min_avg_amount": 0,
            },
            {
                "symbol": "510300", "name": "沪深300ETF", "asset_type": "etf",
                "category": "cn_equity", "market": "cn", "exposure_region": "cn",
                "effective_from": "2020-01-01", "min_avg_amount": 0,
            },
        ],
    })
    refresher = RadarRefresher(_Repository(universe), _UnavailableProvider(), RadarStore(tmp_path / "radar.db"))

    with pytest.raises(RuntimeError, match="AkShare 请求失败；腾讯请求失败；官方兜底未启用") as error:
        refresher.refresh("test_etf", as_of=date(2025, 12, 31))

    assert str(error.value).count("腾讯请求失败") == 1

class _DailyProvider:
    adjustment = "unadjusted"
    def __init__(self):
        self.calls = []
        self.fail = set()
    def supports(self, asset_type):
        return True
    def fetch_spot(self):
        raise NotImplementedError
    def fetch_daily(self, symbol, start, end):
        self.calls.append(symbol)
        if symbol in self.fail:
            raise RadarDataError("测试源失败")
        dates = [stamp.date() for stamp in pd.bdate_range(end=end, periods=80)]
        return pd.DataFrame({"date": dates, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "amount": 100.0})


def _universe():
    return RadarUniverse.model_validate({
        "id": "test_etf", "name": "测试池", "version": 1, "asset_type": "etf", "score_profile": "core_rotation_v1", "description": "测试",
        "instruments": [{"symbol": symbol, "name": symbol, "asset_type": "etf", "category": "cn_equity", "market": "cn", "exposure_region": "cn", "effective_from": "2020-01-01", "min_avg_amount": 0} for symbol in ("510300", "510500", "510050")],
    })


def test_retry_only_fetches_failed_symbol_and_recomputes_complete_pool(tmp_path):
    provider = _DailyProvider()
    provider.fail = {"510050"}
    store = RadarStore(tmp_path / "radar.db")
    refresher = RadarRefresher(_Repository(_universe()), provider, store)
    first = refresher.refresh("test_etf", as_of=date(2026, 9, 30))
    assert store.status_summary("test_etf") == {"fresh": 2, "failed": 1, "stale": 0}
    provider.fail = set()
    provider.calls.clear()
    events = []
    second = refresher.refresh("test_etf", as_of=date(2026, 9, 30), retry_symbols={"510050"}, on_item=lambda *args: events.append(args))
    assert provider.calls == ["510050"]
    snapshot = store.get_snapshot(second)
    assert snapshot is not None
    assert len(snapshot["items"]) == 3
    assert all(item["score"] is not None for item in snapshot["items"])
    assert snapshot["replaces_run_id"] == first
    old_snapshot = store.get_snapshot(first)
    assert old_snapshot is not None
    assert next(item for item in old_snapshot["items"] if item["symbol"] == "510050")["status"] == "failed"
    assert len(events) == 3


def test_cancellation_at_item_boundary_leaves_previous_snapshot_readable(tmp_path):
    from radar.refresh import RadarRefreshCancelled

    provider = _DailyProvider()
    store = RadarStore(tmp_path / "radar.db")
    refresher = RadarRefresher(_Repository(_universe()), provider, store)
    first = refresher.refresh("test_etf", as_of=date(2026, 9, 30))
    provider.calls.clear()
    with pytest.raises(RadarRefreshCancelled):
        refresher.refresh("test_etf", as_of=date(2026, 9, 30), should_cancel=lambda: len(provider.calls) >= 1)
    assert len(provider.calls) == 1
    latest = store.latest_completed("test_etf")
    assert latest is not None
    assert latest["run_id"] == first


def test_stale_target_never_publishes_fresh_snapshot(tmp_path):
    provider = _DailyProvider()
    original = provider.fetch_daily
    provider.fetch_daily = lambda symbol, start, end: original(symbol, start, end).iloc[:-1]
    store = RadarStore(tmp_path / "radar.db")
    with pytest.raises(RuntimeError, match="目标交易日"):
        RadarRefresher(_Repository(_universe()), provider, store).refresh("test_etf", as_of=date(2026, 9, 30))
    assert store.latest_completed("test_etf") is None


def test_new_pool_attempt_resets_circuit_and_retries_real_provider(tmp_path):
    from radar.data import AkShareETFDataProvider, FallbackETFDataProvider

    calls = []
    def fetcher(**kwargs):
        calls.append(kwargs["symbol"])
        if len(calls) == 1:
            raise ConnectionError("临时网络中断")
        dates = [stamp.date() for stamp in pd.bdate_range(end=date(2026, 9, 30), periods=80)]
        return pd.DataFrame({"日期": dates, "开盘": 10.0, "最高": 11.0, "最低": 9.0, "收盘": 10.0, "成交量": 10.0, "成交额": 100.0})
    provider = FallbackETFDataProvider([AkShareETFDataProvider(daily_fetcher=fetcher)])
    refresher = RadarRefresher(_Repository(_universe()), provider, RadarStore(tmp_path / "radar.db"))
    with pytest.raises(RuntimeError, match="所有标的"):
        refresher.refresh("test_etf", as_of=date(2026, 9, 30))
    refresher.refresh("test_etf", as_of=date(2026, 9, 30))
    assert len(calls) == 4


def test_publishing_callback_cancellation_does_not_publish(tmp_path):
    from radar.refresh import RadarRefreshCancelled

    store = RadarStore(tmp_path / "radar.db")
    refresher = RadarRefresher(_Repository(_universe()), _DailyProvider(), store)
    first = refresher.refresh("test_etf", as_of=date(2026, 9, 30))
    cancelled = False
    def phase(value):
        nonlocal cancelled
        if value == "publishing":
            cancelled = True
    with pytest.raises(RadarRefreshCancelled):
        refresher.refresh("test_etf", as_of=date(2026, 9, 30), on_phase=phase, should_cancel=lambda: cancelled)
    latest = store.latest_completed("test_etf")
    assert latest is not None
    assert latest["run_id"] == first
