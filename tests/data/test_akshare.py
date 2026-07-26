from datetime import date
from http.client import RemoteDisconnected
import pandas as pd
import pytest
from data.akshare import AkShareAdapter
from data.schemas import PriceData, FinancialData


class TestAkShareAdapter:
    def test_supports_a_shares_and_price(self):
        adapter = AkShareAdapter()
        assert adapter.supports("a-shares", "price") is True

    def test_does_not_support_us_market(self):
        adapter = AkShareAdapter()
        assert adapter.supports("us", "price") is False

    def test_fetch_price_data(self, mock_akshare):
        adapter = AkShareAdapter()
        results = adapter.fetch("000001", data_type="price", days=365)
        assert len(results) == 2
        assert isinstance(results[0], PriceData)
        assert results[0].close == 10.5
        assert results[0].symbol == "000001"

    def test_fetch_financial_data(self, mock_akshare):
        adapter = AkShareAdapter()
        results = adapter.fetch("000001", data_type="financial")
        assert len(results) == 4
        assert isinstance(results[0], FinancialData)
        assert results[0].revenue == 45000000000
        assert results[0].fiscal_quarter == date(2025, 12, 31)

    def test_unsupported_data_type_returns_empty(self):
        adapter = AkShareAdapter()
        results = adapter.fetch("000001", data_type="unknown_type")
        assert results == []

    def test_fetch_with_error_returns_empty(self, mocker):
        mocker.patch("akshare.stock_zh_a_hist", side_effect=Exception("网络错误"))
        adapter = AkShareAdapter()
        results = adapter.fetch("000001", data_type="price")
        assert results == []


def test_fetch_financial_parses_chinese_units(mocker):
    def _mock(symbol):
        return pd.DataFrame({
            "报告期": ["2025-12-31", "2025-09-30"],
            "营业总收入": ["3.54亿", "3.76亿"],
            "净利润": ["7217.13万", "2.08亿"],
            "扣非净利润": ["7000万", "1.95亿"],
            "净资产收益率": ["12.5", "11.8"],
            "销售净利率": ["15.2", "14.8"],
            "每股经营现金流": ["1.2", "-0.35"],
        })
    mocker.patch("akshare.stock_financial_abstract_ths", side_effect=_mock)
    adapter = AkShareAdapter()
    results = adapter.fetch("600350", data_type="financial")
    assert len(results) == 2  # 行不再被跳过
    assert results[0].revenue == pytest.approx(3.54e8)
    assert results[0].net_profit == pytest.approx(7.21713e7)
    assert results[0].deducted_net_profit == pytest.approx(7.0e7)
    assert results[0].total_assets is None  # 此 API 不提供资产总计
    assert results[0].roe == pytest.approx(0.125)  # 12.5% → 0.125
    assert results[0].gross_margin == pytest.approx(0.152)  # 15.2% → 0.152（销售净利率）
    assert results[1].operating_cash_flow == pytest.approx(-0.35)  # 每股经营现金流


def test_fetch_financial_unparseable_becomes_none(mocker):
    def _mock(symbol):
        return pd.DataFrame({
            "报告期": ["2025-12-31"],
            "营业总收入": ["--"],
            "净利润": ["8.5亿"],
            "扣非净利润": ["8.0亿"],
            "净资产收益率": ["15.8"],
            "销售净利率": ["18.2"],
            "每股经营现金流": ["1.5"],
        })
    mocker.patch("akshare.stock_financial_abstract_ths", side_effect=_mock)
    adapter = AkShareAdapter()
    results = adapter.fetch("600350", data_type="financial")
    assert len(results) == 1  # 缺一个字段不再整行丢弃
    assert results[0].revenue is None  # "--" 无法解析
    assert results[0].net_profit == pytest.approx(8.5e8)
    assert results[0].total_assets is None  # 此 API 不提供资产总计
    assert results[0].roe == pytest.approx(0.158)  # 15.8% → 0.158


def test_fetch_price_retries_on_network_error(mocker):
    mocker.patch("utils.retry.time.sleep")
    calls = {"n": 0}

    def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RemoteDisconnected("boom")
        return pd.DataFrame([
            {"日期": "2026-07-01", "开盘": "10.0", "最高": "11.0",
             "最低": "9.5", "收盘": "10.5", "成交量": 1000000},
        ])

    mocker.patch("akshare.stock_zh_a_hist", side_effect=flaky)
    adapter = AkShareAdapter()
    results = adapter.fetch("000001", data_type="price")
    assert calls["n"] == 2  # 第一次失败被重试
    assert len(results) == 1


def test_fetch_valuation_falls_back_to_old_endpoint(mocker):
    """新端点失败时回退旧端点"""
    mocker.patch(
        "akshare.stock_individual_spot_xq",
        side_effect=RemoteDisconnected("boom"),
    )
    mocker.patch(
        "akshare.stock_zh_a_spot_em",
        return_value=pd.DataFrame([
            {"代码": "000001", "市盈率-动态": 7.5, "市净率": 0.85},
            {"代码": "600036", "市盈率-动态": 6.2, "市净率": 0.72},
        ]),
    )
    mocker.patch("utils.retry.time.sleep")

    adapter = AkShareAdapter()
    results = adapter.fetch("000001", data_type="valuation")
    assert len(results) == 1
    assert results[0].pe_ttm == 7.5
    assert results[0].pb == 0.85


def test_fetch_valuation_uses_new_endpoint_first(mocker):
    """新端点成功时使用新端点数据"""
    mocker.patch(
        "akshare.stock_individual_spot_xq",
        return_value=pd.DataFrame({
            "item": ["市盈率(动)", "市净率"],
            "value": [7.5, 0.85],
        }),
    )
    mocker.patch("utils.retry.time.sleep")

    adapter = AkShareAdapter()
    results = adapter.fetch("000001", data_type="valuation")
    assert len(results) == 1
    assert results[0].pe_ttm == 7.5
    assert results[0].pb == 0.85


def test_fetch_industry_falls_back_to_old_endpoint(mocker):
    """新端点失败时回退旧端点"""
    mocker.patch(
        "akshare.stock_individual_info_em",
        side_effect=RemoteDisconnected("boom"),
    )
    mocker.patch(
        "akshare.stock_board_industry_name_em",
        return_value=pd.DataFrame({"板块名称": ["银行", "保险", "证券"]}),
    )
    mocker.patch("utils.retry.time.sleep")

    adapter = AkShareAdapter()
    results = adapter.fetch("000001", data_type="industry")
    assert len(results) == 1
    assert results[0].industry in ("银行", "保险", "证券")


def test_fetch_industry_uses_new_endpoint_first(mocker):
    """新端点返回含行业字段时正确提取"""
    mocker.patch(
        "akshare.stock_individual_info_em",
        return_value=pd.DataFrame({
            "item": ["行业", "上市时间"],
            "value": ["银行", "1991-04-03"],
        }),
    )
    mocker.patch("utils.retry.time.sleep")

    adapter = AkShareAdapter()
    results = adapter.fetch("000001", data_type="industry")
    assert len(results) == 1
    assert results[0].industry == "银行"
