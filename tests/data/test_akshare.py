from datetime import date
from http.client import RemoteDisconnected

import pandas as pd
import pytest

from data.akshare import AkShareAdapter
from data.schemas import FinancialData, PriceData


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
    # mock 资产负债表端点（空表），避免测试依赖网络与真实资产数据
    mocker.patch("akshare.stock_financial_debt_ths", return_value=pd.DataFrame({
        "报告期": [], "*所有者权益（或股东权益）合计": [], "*资产合计": [],
    }))
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
    # mock 资产负债表端点（空表），避免测试依赖网络与真实资产数据
    mocker.patch("akshare.stock_financial_debt_ths", return_value=pd.DataFrame({
        "报告期": [], "*所有者权益（或股东权益）合计": [], "*资产合计": [],
    }))
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


def test_fetch_price_uses_change_pct_column(mocker):
    """东方财富源：优先使用数据行的涨跌幅列"""

    def _mock_hist(symbol, period, start_date, end_date, adjust):
        return pd.DataFrame([
            {"日期": "2026-07-01", "开盘": "10.0", "最高": "11.0", "最低": "9.5",
             "收盘": "10.0", "成交量": 1000, "涨跌幅": "1.20"},
            {"日期": "2026-07-02", "开盘": "10.1", "最高": "10.8", "最低": "9.9",
             "收盘": "10.5", "成交量": 1200, "涨跌幅": "5.00"},
        ])

    mocker.patch("akshare.stock_zh_a_hist", side_effect=_mock_hist)
    # 根 conftest 将腾讯源 patch 成 ConnectionError，会触发重试退避，屏蔽真实 sleep
    mocker.patch("utils.retry.time.sleep")
    adapter = AkShareAdapter()
    results = adapter.fetch("000001", data_type="price", days=365)
    assert len(results) == 2
    assert results[0].change_pct == pytest.approx(1.2)
    assert results[1].change_pct == pytest.approx(5.0)


def test_fetch_price_nan_change_falls_back_to_compute(mocker):
    """涨跌幅列为 NaN 时回退按前收盘计算，不丢弃整行"""

    def _mock_hist(symbol, period, start_date, end_date, adjust):
        return pd.DataFrame([
            {"日期": "2026-07-01", "开盘": "10.0", "最高": "11.0", "最低": "9.5",
             "收盘": "10.0", "成交量": 1000, "涨跌幅": "1.20"},
            {"日期": "2026-07-02", "开盘": "10.1", "最高": "10.8", "最低": "9.9",
             "收盘": "10.5", "成交量": 1200, "涨跌幅": float("nan")},
        ])

    mocker.patch("akshare.stock_zh_a_hist", side_effect=_mock_hist)
    mocker.patch("utils.retry.time.sleep")
    adapter = AkShareAdapter()
    results = adapter.fetch("000001", data_type="price", days=365)
    assert len(results) == 2  # NaN 不丢行
    assert results[0].change_pct == pytest.approx(1.2)
    assert results[1].change_pct == pytest.approx(5.0)  # (10.5-10.0)/10.0*100


def test_fetch_price_computes_change_pct_without_column(mocker):
    """腾讯源：无涨跌幅列时按前收盘价计算；首行为 None"""

    # 60 行数据使 _fetch_price 满足 >=60 阈值直接返回，避免回退到未被 mock 的东方财富源
    dates = pd.date_range("2026-04-01", periods=60, freq="B").strftime("%Y-%m-%d").tolist()
    rows = [
        {"date": d, "open": "10.0", "high": "10.6", "low": "9.8",
         "close": "10.0" if i < 59 else "10.5", "volume": 1000}
        for i, d in enumerate(dates)
    ]

    def _mock_daily(symbol, start_date, end_date, adjust):
        return pd.DataFrame(rows)

    # 根 conftest 全局把腾讯源 patch 成 ConnectionError，此处覆盖为成功返回
    mocker.patch("akshare.stock_zh_a_daily", side_effect=_mock_daily)
    adapter = AkShareAdapter()
    results = adapter.fetch("000001", data_type="price", days=365)
    assert len(results) == 60
    assert results[0].change_pct is None  # 首行无前收盘
    assert results[1].change_pct == pytest.approx(0.0)  # 中间行收盘持平
    assert results[-1].change_pct == pytest.approx(5.0)  # (10.5-10.0)/10.0*100


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


def test_fetch_industry_uses_eastmoney_info(mocker):
    """行业判定优先东财轻量接口"""
    import pandas as pd
    info_df = pd.DataFrame({"item": ["行业", "总股本"], "value": ["银行", "194.05亿"]})
    mocker.patch("data.akshare._ak_individual_info_em", return_value=info_df)
    mocker.patch("data.akshare._fetch_sw_peers", return_value=[])
    mocker.patch("data.akshare._ak_board_industry_cons_em", return_value=pd.DataFrame())
    from data.akshare import AkShareAdapter
    results = AkShareAdapter()._fetch_industry("000001")
    assert results[0].industry == "银行"


def test_fetch_industry_falls_back_to_local_mapping(mocker):
    """东财失败 → 本地映射表"""
    mocker.patch("data.akshare._ak_individual_info_em",
                 side_effect=ConnectionError("mock"))
    fake = type("Fake", (), {"lookup": lambda self, s: type("R", (), {"sw_level1": "银行"})()})()
    mocker.patch("data.industry_classifier.IndustryClassifier", return_value=fake)
    mocker.patch("data.akshare._fetch_sw_peers", return_value=[])
    mocker.patch("data.akshare._ak_board_industry_cons_em", return_value=pd.DataFrame())
    from data.akshare import AkShareAdapter
    results = AkShareAdapter()._fetch_industry("000001")
    assert results[0].industry == "银行"


def test_fetch_financial_fills_basic_eps(mocker):
    """采集层应填充 basic_eps，供总股本财报反推使用"""
    import pandas as pd
    fin_df = pd.DataFrame({
        "报告期": ["2026-06-30", "2026-03-31"],
        "营业总收入": [70000000000, 38000000000],
        "净利润": [25000000000, 13000000000],
        "基本每股收益": [1.32, 0.68],
    })
    mocker.patch("akshare.stock_financial_abstract_ths", return_value=fin_df)
    mocker.patch("akshare.stock_financial_debt_ths", return_value=pd.DataFrame({
        "报告期": [], "*所有者权益（或股东权益）合计": [], "*资产合计": [],
    }))
    from data.akshare import AkShareAdapter
    results = AkShareAdapter()._fetch_financial("000001")
    assert results[0].basic_eps == 1.32


def test_parse_debt_new_long_table(mocker):
    """THS 新长表应解析出 equity/assets，并作为旧表失败后的回退"""
    import pandas as pd

    from data.akshare import AkShareAdapter, _parse_debt_new

    # 长表格式：每行一个指标
    long_df = pd.DataFrame({
        "report_date": ["2026-06-30", "2026-06-30", "2026-06-30", "2026-03-31", "2026-03-31"],
        "metric_name": ["assets_total", "holder_equity_total", "total_debt",
                        "assets_total", "holder_equity_total"],
        "value": ["6.03万亿", "5482.14亿", "5.48万亿", "6.03万亿", "5440.83亿"],
    })
    result = _parse_debt_new(long_df)
    # 浮点表示误差（如 5482.14*1e8=548214000000.00006）用 approx 比较
    assert result["2026-06-30"] == pytest.approx((548214000000.0, 6030000000000.0))
    assert result["2026-03-31"] == pytest.approx((544083000000.0, 6030000000000.0))

    # 旧表失败时自动回退新表
    mocker.patch("akshare.stock_financial_debt_ths",
                 side_effect=ConnectionError("mock: 旧表失败"))
    mocker.patch("akshare.stock_financial_debt_new_ths", return_value=long_df)
    fin_df = pd.DataFrame({
        "报告期": ["2026-06-30"], "营业总收入": [70000000000],
        "净利润": [25000000000], "基本每股收益": [1.32],
    })
    mocker.patch("akshare.stock_financial_abstract_ths", return_value=fin_df)
    results = AkShareAdapter()._fetch_financial("000001")
    assert results[0].total_assets == pytest.approx(6030000000000.0)
    assert results[0].total_equity == pytest.approx(548214000000.0)
