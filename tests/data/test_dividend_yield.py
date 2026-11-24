"""三市场个股股息率接口的身份、单位和失败边界。"""
from datetime import date

import pandas as pd
import pytest

from data.dividend_yield import (
    a_share_dividend_yield,
    hk_dividend_yield,
    us_dividend_yield,
)


def test_a_share_uses_latest_implemented_plan_and_converts_ratio(mocker):
    frame = pd.DataFrame([
        {"方案进度": "实施分配", "现金分红-股息率": 0.01, "股权登记日": "2026-05-01",
         "除权除息日": "2026-05-02"},
        {"方案进度": "实施分配", "现金分红-股息率": 0.02, "股权登记日": "2026-07-01",
         "除权除息日": "2026-07-02"},
        {"方案进度": "预披露", "现金分红-股息率": 0.09, "股权登记日": "2026-08-01"},
    ])
    mocker.patch("data.dividend_yield.ak.stock_fhps_detail_em", return_value=frame)
    result = a_share_dividend_yield("600519")
    assert result.value == pytest.approx(2.0)
    assert result.as_of == date(2026, 7, 1)
    assert "已实施" in result.basis


def test_a_share_without_implemented_plan_is_explicitly_missing(mocker):
    frame = pd.DataFrame([{"方案进度": "预披露", "现金分红-股息率": 0.09, "股权登记日": "2026-08-01"}])
    mocker.patch("data.dividend_yield.ak.stock_fhps_detail_em", return_value=frame)
    result = a_share_dividend_yield("600519")
    assert result.value is None
    assert "没有已实施" in result.reason


def test_hk_f10_uses_latest_valid_report_period(mocker):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"success": True, "result": {"data": [
                {"SECUCODE": "00700.HK", "REPORT_DATE": "2026-06-30", "DIVIDEND_RATE": 1.42},
                {"SECUCODE": "00700.HK", "REPORT_DATE": "2026-09-30", "DIVIDEND_RATE": 1.55},
            ]}}

    mocker.patch("data.dividend_yield.requests.get", return_value=Response())
    result = hk_dividend_yield("00700")
    assert result.value == pytest.approx(1.55)
    assert result.as_of == date(2026, 9, 30)


def test_hk_f10_rejects_wrong_identity(mocker):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"success": True, "result": {"data": [
                {"SECUCODE": "00005.HK", "REPORT_DATE": "2026-09-30", "DIVIDEND_RATE": 1.55},
            ]}}

    mocker.patch("data.dividend_yield.requests.get", return_value=Response())
    result = hk_dividend_yield("00700")
    assert result.value is None
    assert "未提供" in result.reason


def test_us_tencent_quote_reads_field_52_with_identity_and_currency(mocker):
    fields = ["0"] * 53
    fields[2] = "AAPL.OQ"
    fields[30] = "2026-11-23 16:00:01"
    fields[35] = "USD"
    fields[52] = "0.31"

    class Response:
        content = ('v_usAAPL="' + "~".join(fields) + '";').encode("gbk")
        def raise_for_status(self):
            return None

    mocker.patch("data.dividend_yield.requests.get", return_value=Response())
    result = us_dividend_yield("AAPL")
    assert result.value == pytest.approx(0.31)
    assert result.as_of == date(2026, 11, 23)
    assert "快照" in result.basis


def test_us_tencent_quote_rejects_wrong_currency(mocker):
    fields = ["0"] * 53
    fields[2] = "AAPL.OQ"
    fields[30] = "2026-11-23 16:00:01"
    fields[35] = "HKD"
    fields[52] = "0.31"

    class Response:
        content = ('v_usAAPL="' + "~".join(fields) + '";').encode("gbk")
        def raise_for_status(self):
            return None

    mocker.patch("data.dividend_yield.requests.get", return_value=Response())
    result = us_dividend_yield("AAPL")
    assert result.value is None
    assert "暂不可用" in result.reason
