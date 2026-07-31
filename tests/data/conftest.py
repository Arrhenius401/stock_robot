import pytest
import pandas as pd
from datetime import date
from data.schemas import PriceData, FinancialData, ValuationData, IndustryData, NewsData


@pytest.fixture
def mock_akshare(mocker):
    """Mock AkShare 接口返回模拟 DataFrame"""

    def _mock_history(symbol, period, start_date, end_date, adjust):
        return pd.DataFrame([
            {"日期": "2026-07-01", "开盘": "10.0", "最高": "11.0", "最低": "9.5", "收盘": "10.5", "成交量": 1000000},
            {"日期": "2026-06-30", "开盘": "10.2", "最高": "10.8", "最低": "9.8", "收盘": "10.0", "成交量": 800000},
        ])

    def _mock_financial(symbol):
        return pd.DataFrame({
            "报告期": ["2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31"],
            "营业总收入": [45000000000, 33000000000, 22000000000, 11000000000],
            "净利润": [8500000000, 6200000000, 4100000000, 2000000000],
            "扣非净利润": [8000000000, 6000000000, 4000000000, 1900000000],
            "净资产收益率": [18.5, 14.2, 10.1, 5.3],
            "销售净利率": [18.88, 18.78, 18.63, 18.18],
            "每股经营现金流": [2.5, 1.8, 1.2, 0.6],
        })

    mocker.patch("akshare.stock_zh_a_hist", side_effect=_mock_history)
    mocker.patch("akshare.stock_financial_abstract_ths", side_effect=_mock_financial)
    return {"history": _mock_history, "financial": _mock_financial}


@pytest.fixture(autouse=True)
def _clear_info_cache():
    """每个测试前清空个股信息缓存，防止测试间交叉污染"""
    from data.akshare import clear_info_cache
    clear_info_cache()
