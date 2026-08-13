"""AkShare 指数数据采集测试"""
from data.akshare import AkShareAdapter
from data.schemas import IndexPriceData


class TestAkShareIndexAdapter:
    def test_supports_index_price(self):
        adapter = AkShareAdapter()
        assert adapter.supports("a-shares", "index_price") is True

    def test_supports_index_valuation(self):
        adapter = AkShareAdapter()
        assert adapter.supports("a-shares", "index_valuation") is True

    def test_fetch_index_price_broad(self):
        adapter = AkShareAdapter()
        result = adapter.fetch("000300", data_type="index_price",
                               index_style="broad")
        assert isinstance(result, list)
        if len(result) > 0:
            item = result[0]
            assert isinstance(item, IndexPriceData)
            assert item.symbol == "000300"
            assert item.close > 0

    def test_fetch_index_price_returns_empty_on_error(self):
        adapter = AkShareAdapter()
        result = adapter.fetch("INVALID_INDEX", data_type="index_price")
        assert result == []
