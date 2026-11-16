"""只读股票池配置仓储。"""
from pathlib import Path

import yaml
from pydantic import ValidationError

from radar.stocks.models import StockMembers, StockPool


class StockPoolConfigError(ValueError):
    """定义错误，不能降级成其他池。"""


class StockPoolRepository:
    """本地配置读取不触发采集或写入。"""
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def load_all(self) -> list[StockPool]:
        """禁用定义也必须合法。"""
        if not self.directory.is_dir():
            raise StockPoolConfigError(f"股票池目录不存在: {self.directory}")
        pools: list[StockPool] = []
        seen: set[str] = set()
        for path in sorted(self.directory.glob("*.yaml")):
            try:
                pool = StockPool.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            except (OSError, yaml.YAMLError, ValidationError) as exc:
                raise StockPoolConfigError(f"股票池配置无效: {path}") from exc
            if pool.id in seen:
                raise StockPoolConfigError(f"股票池 ID 重复: {pool.id}")
            seen.add(pool.id)
            pools.append(pool)
        return sorted(pools, key=lambda item: (item.display_order, item.id))

    def get(self, pool_id: str) -> StockPool | None:
        """禁用定义不能经直接路由访问。"""
        return next((pool for pool in self.load_all() if pool.id == pool_id and pool.enabled), None)

    def members(self, pool_id: str) -> StockMembers | None:
        """返回真实空集合，不创建数据库或任务。"""
        return StockMembers(pool_id=pool_id) if self.get(pool_id) else None
