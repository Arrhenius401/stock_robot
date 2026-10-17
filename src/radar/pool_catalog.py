"""组合 ETF 与股票的只读目录。"""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from radar.stocks.pools import StockPoolConfigError, StockPoolRepository
from radar.universe import UniverseConfigError, UniverseRepository


class PoolCatalogError(ValueError):
    """目录配置冲突。"""


class PoolCapabilities(BaseModel):
    """能力取决于真实实现。"""
    update: bool
    manage: bool


class PoolDescriptor(BaseModel):
    """统一目录，详情仍按类型分支。"""
    id: str
    name: str
    asset_type: Literal["etf", "stock"]
    description: str
    display_order: int
    capabilities: PoolCapabilities
    rule_status: Literal["unconfigured", "configured"]


class PoolCatalog:
    """保留 ETF 生效版本语义，不采集数据。"""
    def __init__(self, etfs: UniverseRepository, stocks: StockPoolRepository):
        self.etfs = etfs
        self.stocks = stocks

    def list(self, target_date: date | None = None) -> list[PoolDescriptor]:
        """跨类型 ID 冲突直接报错。"""
        try:
            all_etfs = self.etfs.load_all()
            all_stocks = self.stocks.load_all()
            if {pool.id for pool in all_etfs} & {pool.id for pool in all_stocks}:
                raise PoolCatalogError("ETF 与股票池 ID 重复")
            enabled_etfs = self.etfs.enabled_on(target_date or datetime.now().astimezone().date())
        except (UniverseConfigError, StockPoolConfigError) as exc:
            raise PoolCatalogError(str(exc)) from exc
        items = [
            PoolDescriptor(id=pool.id, name=pool.name, asset_type="etf",
                           description=pool.description, display_order=index * 10,
                           capabilities=PoolCapabilities(update=True, manage=False),
                           rule_status="configured")
            for index, pool in enumerate(enabled_etfs)
        ]
        items.extend(
            PoolDescriptor(id=pool.id, name=pool.name, asset_type="stock",
                           description=pool.description, display_order=pool.display_order,
                           capabilities=PoolCapabilities(update=True, manage=True),
                           rule_status=pool.rule_status)
            for pool in all_stocks if pool.enabled
        )
        return sorted(items, key=lambda item: (item.display_order, item.id))
