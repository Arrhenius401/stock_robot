from abc import ABC, abstractmethod


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
