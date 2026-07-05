import pytest
import pandas as pd
from datetime import date
from src.data.schemas import PriceData, FinancialData, ValuationData, IndustryData, NewsData


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
            "资产总计": [500000000000, 490000000000, 485000000000, 475000000000],
            "股东权益合计": [45000000000, 44000000000, 43500000000, 43000000000],
            "经营活动现金流量净额": [12000000000, 9000000000, 6000000000, 3000000000],
        })

    mocker.patch("akshare.stock_zh_a_hist", side_effect=_mock_history)
    mocker.patch("akshare.stock_financial_abstract_ths", side_effect=_mock_financial)
    return {"history": _mock_history, "financial": _mock_financial}
