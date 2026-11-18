"""海外指数历史行情字段和回退契约。"""
from datetime import date

import pandas as pd

from data.akshare import AkShareAdapter


def test_eastmoney_spx_uses_historical_open_and_close(mocker):
    # 东方财富 SPX 2026-11-17 历史日线探针单行，字段口径与新浪同日交叉核验。
    eastmoney = mocker.patch("akshare.index_global_hist_em", return_value=pd.DataFrame([{
        "日期": "2026-11-17", "代码": "SPX", "名称": "标普500", "今开": 7411.02,
        "最新价": 7340.53, "最高": 7415.37, "最低": 7288.34, "振幅": 1.71,
    }]))
    sina = mocker.patch("akshare.index_us_stock_sina")

    result = AkShareAdapter().fetch("SPX", data_type="index_price", index_style="overseas")

    eastmoney.assert_called_once_with(symbol="标普500")
    sina.assert_not_called()
    assert len(result) == 1
    row = result[0]
    assert row.trade_date == date(2026, 11, 17)
    assert (row.open, row.high, row.low, row.close) == (7411.02, 7415.37, 7288.34, 7340.53)
    assert row._data_source == "东方财富全球指数历史"


def test_unparseable_eastmoney_frame_falls_back_to_sina(mocker):
    mocker.patch("akshare.index_global_hist_em", return_value=pd.DataFrame([{"日期": "2026-11-17"}]))
    sina = mocker.patch("akshare.index_us_stock_sina", return_value=pd.DataFrame([{
        "date": "2026-11-17", "open": 7411.02, "high": 7415.3701,
        "low": 7288.3398, "close": 7340.5298,
    }]))

    result = AkShareAdapter().fetch("SPX", data_type="index_price", index_style="overseas")

    sina.assert_called_once_with(symbol=".INX")
    assert [row.close for row in result] == [7340.5298]
    assert result[0]._data_source == "新浪全球指数历史（东方财富回退）"
