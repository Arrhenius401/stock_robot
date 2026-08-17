"""AkShare 指数数据采集测试"""
import pandas as pd
import pytest

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


def test_fetch_index_price_computes_change_pct_without_column(mocker):
    """指数源无涨跌幅列（安装版 akshare 现状）：按前收盘计算，首行为 None"""

    def _mock_em(symbol):
        return pd.DataFrame([
            {"date": "2026-07-01", "open": "3000.0", "high": "3050.0", "low": "2990.0",
             "close": "3000.0", "volume": 100000, "amount": 5.0e8},
            {"date": "2026-07-02", "open": "3010.0", "high": "3060.0", "low": "3000.0",
             "close": "3030.0", "volume": 110000, "amount": 5.5e8},
        ])

    mocker.patch("akshare.stock_zh_index_daily_em", side_effect=_mock_em)
    adapter = AkShareAdapter()
    results = adapter.fetch("000300", data_type="index_price", index_style="broad")
    assert len(results) == 2
    assert results[0].change_pct is None  # 首行无前收盘
    assert results[1].change_pct == pytest.approx(1.0)  # (3030-3000)/3000*100


def test_fetch_index_price_uses_pct_column(mocker):
    """指数源提供涨跌幅列时取列值（列优先于前收盘计算）"""

    def _mock_em(symbol):
        return pd.DataFrame([
            {"date": "2026-07-01", "open": "3000.0", "high": "3050.0", "low": "2990.0",
             "close": "3000.0", "volume": 100000, "amount": 5.0e8, "涨跌幅": "1.20"},
            {"date": "2026-07-02", "open": "3010.0", "high": "3060.0", "low": "3000.0",
             "close": "3030.0", "volume": 110000, "amount": 5.5e8, "涨跌幅": "1.00"},
        ])

    mocker.patch("akshare.stock_zh_index_daily_em", side_effect=_mock_em)
    adapter = AkShareAdapter()
    results = adapter.fetch("000300", data_type="index_price", index_style="broad")
    assert len(results) == 2
    assert results[0].change_pct == pytest.approx(1.2)
    assert results[1].change_pct == pytest.approx(1.0)
