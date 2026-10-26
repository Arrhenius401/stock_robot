from datetime import date

import pytest

from data.base import CollectionResult, DataSource
from data.schemas import DataCollectionRecord, PriceData



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
            DataSource()  # pyright: ignore[reportAbstractUsage]

    def test_concrete_implementation_works(self):
        source = FakeSource()
        assert source.supports("a-shares", "price") is True
        assert source.supports("us", "price") is False
        result = source.fetch("000001")
        assert len(result) == 1
        assert result[0].close == 10.5

class DiagnosticSource(DataSource):
    def supports(self, market, data_type):
        return market == "a-shares" and data_type == "price"

    def fetch(self, symbol, **kwargs):
        return [PriceData(
            symbol=symbol, trade_date=date(2026, 10, 20),
            open=1, high=2, low=0.5, close=1.5, volume=10,
        )]

    def get_collection_error(self):
        return DataCollectionRecord(
            source="测试源", error_kind="timeout", error_message="请求超时"
        )


def test_fetch_result_returns_rows_and_diagnostic():
    result = DiagnosticSource().fetch_result(
        "000001", data_type="price", market="a-shares"
    )
    assert isinstance(result, CollectionResult)
    assert result.rows[0].symbol == "000001"
    assert result.record is not None
    assert result.record.error_kind == "timeout"
