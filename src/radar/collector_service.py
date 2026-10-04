"""CLI 与网页控制接口共享的采集依赖构造。"""

from pathlib import Path

from radar.calendar import TradingCalendar
from radar.collector_store import CollectorStore
from radar.collector_worker import CollectorWorker
from radar.data import (
    AkShareETFDataProvider,
    FallbackETFDataProvider,
    LocalETFDataProvider,
    OfficialExchangeETFDataProvider,
    RequestPacer,
    SinaETFDataProvider,
    TencentETFDataProvider,
)
from radar.overseas import OverseasService
from radar.refresh import RadarRefresher
from radar.store import RadarStore
from radar.universe import UniverseRepository
from radar.update_service import UpdateService
from utils.config import Config


def build_collector(*, refresh_calendar: bool = True) -> CollectorWorker:
    """状态目录由固定 cwd 决定，不从 Web 生命周期派生采集进程。"""
    config = Config()
    root = Path(__file__).parents[2]
    repository = UniverseRepository(root / 'config' / 'radar_universes')
    provider = FallbackETFDataProvider((
        LocalETFDataProvider(config.config_dir / 'radar_local_data' / 'etf'),
        AkShareETFDataProvider(pacer=RequestPacer(config.get('radar.minimum_interval_seconds', 1.0))),
        TencentETFDataProvider(), SinaETFDataProvider(), OfficialExchangeETFDataProvider(),
    ))
    market_store = RadarStore(config.config_dir / 'radar.db')
    refresher = RadarRefresher(repository, provider, market_store)
    store = CollectorStore(config.config_dir / 'radar_collector.db')
    calendar = TradingCalendar(config.config_dir / 'radar_collector.db', refresh_enabled=refresh_calendar)
    worker = CollectorWorker(store, repository, calendar, refresher.refresh, state_dir=config.config_dir)
    if refresh_calendar:
        worker.update_service = UpdateService(worker, market_store, OverseasService(market_store.db_path), manual_only=False)
    return worker
