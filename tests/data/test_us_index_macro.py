"""美国指数宏观数据选择有效观测并保留日期。"""
from datetime import date

import pandas as pd

from data.akshare import AkShareAdapter
from data.schemas import AnalysisTarget, IndexAnalysisContext
from index.analysis.macro import MacroAnalyzer


def test_us_macro_latest_valid_and_dates(mocker):
    # AkShare 2026-11-18 探针的尾部两月，10 月报道结果尚未填充。
    cpi = mocker.patch("akshare.macro_usa_cpi_yoy", return_value=pd.DataFrame({
        "时间": [date(2026, 10, 1), date(2026, 9, 1)],
        "发布日期": [date(2026, 11, 10), date(2026, 10, 14)], "现值": [float("nan"), 3.7], "前值": [3.7, 3.4],
    }))
    yield10 = mocker.patch("akshare.bond_zh_us_rate", return_value=pd.DataFrame({
        "日期": [date(2026, 11, 17), date(2026, 11, 16)],
        "美国国债收益率10年": [5.1, 5.12],
    }))
    domestic = mocker.patch("akshare.macro_china_pmi")
    response = AkShareAdapter().fetch_result("SPX", data_type="index_macro", index_style="overseas", market="us")
    macro = response.rows[0]
    assert macro.us_cpi_yoy == 3.7
    assert macro.us_10y_yield == 5.1
    assert macro.indicator_dates == {
        "us_cpi_yoy": "2026-09-01", "us_cpi_yoy_release": "2026-10-14", "us_10y_yield": "2026-11-17",
    }
    cpi.assert_called_once_with()
    yield10.assert_called_once_with()
    domestic.assert_not_called()


def test_us_macro_preserves_independent_failure_and_stale_limit(mocker):
    mocker.patch("akshare.macro_usa_cpi_yoy", return_value=pd.DataFrame({"unexpected": [1]}))
    mocker.patch("akshare.bond_zh_us_rate", return_value=pd.DataFrame({
        "日期": [date(2020, 1, 3)], "美国国债收益率10年": [1.5],
    }))
    response = AkShareAdapter().fetch_result("SPX", data_type="index_macro", index_style="overseas", market="us")
    target = AnalysisTarget(target_type="index", symbol="SPX", name="标普500", market="us", index_style="overseas")
    result = MacroAnalyzer().analyze(IndexAnalysisContext(target=target, macro=response.rows[0]))
    assert result.status == "partial"
    assert result.metrics["us_10y_yield"] is None
    assert "过期" in result.summary and "缺少字段" in result.summary
    assert "1.5%" not in result.summary


def test_us_macro_both_requests_fail(mocker):
    mocker.patch("akshare.macro_usa_cpi_yoy", side_effect=TimeoutError("超时"))
    mocker.patch("akshare.bond_zh_us_rate", side_effect=ConnectionError("断开"))
    response = AkShareAdapter().fetch_result("SPX", data_type="index_macro", index_style="overseas", market="us")
    assert response.rows == []
    assert response.record is not None
    assert "请求失败" in response.record.error_message
