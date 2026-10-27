from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from data.schemas import DataCollectionRecord
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CollectionResult:
    """统一取数结果：数据和诊断在同一次调用中返回。"""
    rows: list[Any]
    record: DataCollectionRecord | None = None
    errors: list[str] = field(default_factory=list)


class DataSource(ABC):
    """数据源抽象接口 — 所有数据适配器需实现此接口"""

    @abstractmethod
    def supports(self, market: str, data_type: str) -> bool:
        """是否支持指定市场和数据类型"""
        ...

    @abstractmethod
    def fetch(self, symbol: str, **kwargs) -> list:
        """获取数据，返回 Pydantic 模型列表"""
        ...

    def fetch_result(self, symbol: str, **kwargs) -> CollectionResult:
        """未迁移适配器的兼容桥；五组能力的数据源必须显式覆盖。"""
        rows = self.fetch(symbol, **kwargs)
        reader = getattr(self, "get_collection_error", None)
        diagnostic = reader() if callable(reader) else None
        return CollectionResult(
            rows=rows or [],
            record=diagnostic if isinstance(diagnostic, DataCollectionRecord) else None,
        )
