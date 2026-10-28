"""AkShare 指数数据采集测试"""
from datetime import date

import pandas as pd
import pytest

from data.akshare import AkShareAdapter
from data.schemas import IndexPriceData


@pytest.mark.parametrize("reverse", [False, True])
def test_index_valuation_uses_latest_date_from_csindex(mocker, reverse):
    mocker.patch("akshare.index_value_hist_funddb", side_effect=AttributeError, create=True)
    rows = [
        {"日期": "2024-01-01", "市盈率1": 10.0, "股息率1": 2.0},
        {"日期": "2024-01-02", "市盈率1": 20.0, "股息率1": 3.0},
    ]
    if reverse:
        rows.reverse()
    rows.insert(1, {"日期": "invalid", "市盈率1": 99.0, "股息率1": 1.0})
    mocker.patch("akshare.stock_zh_index_value_csindex", return_value=pd.DataFrame(rows))

    result = AkShareAdapter().fetch("000300", data_type="index_valuation")

    assert result[0].date == date(2024, 1, 2)
    assert result[0].pe_ttm == 20.0


def test_index_valuation_returns_empty_when_dates_invalid(mocker):
    mocker.patch("akshare.index_value_hist_funddb", side_effect=AttributeError, create=True)
    mocker.patch("akshare.stock_zh_index_value_csindex", return_value=pd.DataFrame([
        {"日期": "invalid", "市盈率1": 10.0, "股息率1": 2.0},
    ]))

    assert AkShareAdapter().fetch("000300", data_type="index_valuation") == []


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


def test_fetch_index_price_sector_accepts_chinese_columns(mocker):
    """申万行业指数接口返回中文列名时，应正常解析而不是整批跳过"""

    def _mock_sw(symbol):
        return pd.DataFrame([
            {"日期": "2026-07-01", "开盘": "3000.0", "最高": "3050.0",
             "最低": "2990.0", "收盘": "3000.0", "成交量": 100000,
             "成交额": 5.0e8, "涨跌幅": "1.20"},
            {"日期": "2026-07-02", "开盘": "3010.0", "最高": "3060.0",
             "最低": "3000.0", "收盘": "3030.0", "成交量": 110000,
             "成交额": 5.5e8, "涨跌幅": "1.00"},
        ])

    sw = mocker.patch("akshare.index_hist_sw", side_effect=_mock_sw)
    adapter = AkShareAdapter()
    results = adapter.fetch("801010", data_type="index_price", index_style="sector")

    sw.assert_called_once_with(symbol="801010")
    assert len(results) == 2
    assert results[0].symbol == "801010"
    assert results[0].close == pytest.approx(3000.0)
    assert results[0].turnover == pytest.approx(5.0)
    assert results[1].change_pct == pytest.approx(1.0)


def test_fetch_overseas_index_falls_back_to_sina_hk(mocker):
    """东财全球指数接口失败时回退新浪港股日线源（HSI 走 stock_hk_index_daily_sina）"""

    def _mock_sina(symbol):
        return pd.DataFrame([
            {"date": "2026-07-01", "open": 25000.0, "high": 25200.0,
             "low": 24900.0, "close": 25100.0, "volume": 1000},
            {"date": "2026-07-02", "open": 25100.0, "high": 25300.0,
             "low": 25000.0, "close": 25200.0, "volume": 1200},
        ])

    mocker.patch("akshare.index_global_hist_em", side_effect=ConnectionError("东财失效"))
    sina = mocker.patch("akshare.stock_hk_index_daily_sina", side_effect=_mock_sina)
    adapter = AkShareAdapter()
    results = adapter.fetch("HSI", data_type="index_price", index_style="overseas")
    sina.assert_called_once_with(symbol="HSI")
    assert len(results) == 2
    assert results[0].close == pytest.approx(25100.0)
    assert results[1].change_pct == pytest.approx(0.4)


def test_fetch_overseas_index_falls_back_to_sina_us(mocker):
    """东财主源返回空时回退新浪美股日线源（SPX 映射 .INX）"""

    def _mock_us(symbol):
        return pd.DataFrame([
            {"date": "2026-07-01", "open": 5000.0, "high": 5050.0,
             "low": 4980.0, "close": 5020.0, "volume": 1000},
        ])

    mocker.patch("akshare.index_global_hist_em", return_value=pd.DataFrame())
    us = mocker.patch("akshare.index_us_stock_sina", side_effect=_mock_us)
    adapter = AkShareAdapter()
    results = adapter.fetch("SPX", data_type="index_price", index_style="overseas")
    us.assert_called_once_with(symbol=".INX")
    assert len(results) == 1
    assert results[0].close == pytest.approx(5020.0)


def test_fetch_overseas_index_unknown_symbol_returns_empty(mocker):
    """无新浪映射的海外符号且东财失败时返回空列表而非崩溃"""
    mocker.patch("akshare.index_global_hist_em", side_effect=ConnectionError("东财失效"))
    adapter = AkShareAdapter()
    results = adapter.fetch("UNKNOWN_INDEX", data_type="index_price", index_style="overseas")
    assert results == []


def test_index_history_result_reports_source_and_state(monkeypatch):
    frame = pd.DataFrame({"date": ["2024-01-02"], "close": [3500.0]})
    monkeypatch.setattr("data.akshare._ak_csindex", lambda **_: frame)
    from data.akshare import fetch_index_history_result

    result = fetch_index_history_result("000300", "20240101", "20240131")
    assert result.value is frame
    assert result.record.source == "中证指数官网"
    assert result.record.result_state == "complete"
    assert result.record.collected_at is not None


def test_index_history_result_empty_and_safe_failure(monkeypatch):
    from data.akshare import fetch_index_history_result

    monkeypatch.setattr("data.akshare._ak_csindex", lambda **_: None)
    empty = fetch_index_history_result("000300", "20240101", "20240131")
    assert empty.value is None
    assert empty.record.result_state == "empty"

    def fail(**kwargs):
        raise TimeoutError("敏感的上游细节")

    monkeypatch.setattr("data.akshare._ak_csindex", fail)
    failed = fetch_index_history_result("000300", "20240101", "20240131")
    assert failed.value is None
    assert failed.record.result_state == "failed"
    assert failed.record.error_kind == "timeout"
    assert "敏感的上游细节" not in failed.record.error_message
