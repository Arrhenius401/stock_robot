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
        "score_profile": "core_etf_v1",
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

    with pytest.raises(RuntimeError, match="AkShare 请求失败；腾讯请求失败；官方兜底未启用"):
        refresher.refresh("test_etf", as_of=date(2025, 12, 31))
