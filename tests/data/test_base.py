from datetime import date
import pytest
from src.data.base import DataSource
from src.data.schemas import PriceData


class FakeSource(DataSource):
    """测试用数据源实现"""
    def supports(self, market: str, data_type: str) -> bool:
        return market == "a-shares" and data_type == "price"

    def fetch(self, symbol: str, **kwargs) -> list:
        return [
            PriceData(
                symbol=symbol,
                trade_date=date(2026, 7, 1),
                open=10.0,
                high=11.0,
                low=9.5,
                close=10.5,
                volume=1000000,
            )
        ]


class TestDataSource:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            DataSource()

    def test_concrete_implementation_works(self):
        source = FakeSource()
        assert source.supports("a-shares", "price") is True
        assert source.supports("us", "price") is False
        result = source.fetch("000001")
        assert len(result) == 1
        assert result[0].close == 10.5
