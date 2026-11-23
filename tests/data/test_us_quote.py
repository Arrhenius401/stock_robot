"""美股新浪快照与名称解析的解析/回退测试。"""
import pandas as pd
import pytest

from data.us_stock import us_name_result, us_quote

SINA_BODY = (
    'var hq_str_gb_msft="微软,523.8800,0.17,2026-11-14 09:16:08,0.8800,519.7450,'
    '526.4200,513.8200,559.1500,349.2000,26189242,31067631,3890434802425,16.85,'
    '31.090000,0.00,0.00,0.00,0.00,7426194553,75,522.4620,-0.27,-1.42,'
    'Nov 13 07:59PM EST,Nov 13 04:00PM EST,523.0000,4634920,1,2026,'
    '13688497669.0000,547.5507,487.2331,2427768637.0361,523.8800,523.8800";'
)


class _Response:
    def __init__(self, body: str):
        self.content = body.encode("gbk")
        self.status_code = 200

    def raise_for_status(self):
        return None


def test_us_quote_parses_sina_fields(mocker):
    get = mocker.patch("data.us_stock.requests.get", return_value=_Response(SINA_BODY))
    quote = us_quote("msft")
    assert get.call_args.args[0].endswith("gb_msft")
    assert quote["symbol"] == "MSFT"
    assert quote["name"] == "微软"
    assert quote["price"] == pytest.approx(523.88)
    assert quote["change_pct"] == pytest.approx(0.17)
    assert quote["market_cap"] == pytest.approx(3890434802425)
    assert quote["quoted_at"] == "2026-11-14 09:16:08"
    assert quote["after_hours_price"] == pytest.approx(522.462)


def test_us_quote_rejects_mismatched_ticker(mocker):
    mocker.patch("data.us_stock.requests.get", return_value=_Response(SINA_BODY))
    with pytest.raises(ValueError, match="代码不匹配"):
        us_quote("AAPL")


def test_us_quote_rejects_empty_snapshot(mocker):
    body = 'var hq_str_gb_brk.b="";'
    mocker.patch("data.us_stock.requests.get", return_value=_Response(body))
    with pytest.raises(ValueError, match="为空或字段不足"):
        us_quote("BRK.B")


def test_us_name_result_uses_sina_name(mocker):
    mocker.patch("data.us_stock.us_quote", return_value={"name": "微软"})
    result = us_name_result("MSFT")
    assert result.value == "微软"
    assert result.record.source == "新浪美股快照"
    assert result.record.result_state == "complete"


def test_us_name_result_falls_back_to_f10(mocker):
    mocker.patch("data.us_stock.us_quote", side_effect=ValueError("snapshot unavailable"))
    mocker.patch(
        "akshare.stock_financial_us_analysis_indicator_em",
        return_value=pd.DataFrame([{"SECURITY_NAME_ABBR": "苹果", "SECURITY_CODE": "AAPL"}]),
    )
    result = us_name_result("AAPL")
    assert result.value == "苹果"
    assert result.record.source == "东方财富美股F10"


def test_us_name_result_reports_failure(mocker):
    mocker.patch("data.us_stock.us_quote", side_effect=ValueError("snapshot unavailable"))
    mocker.patch(
        "akshare.stock_financial_us_analysis_indicator_em",
        side_effect=TypeError("f10 unavailable"),
    )
    result = us_name_result("BRK.B")
    assert result.value is None
    assert result.record.result_state == "failed"
    assert result.record.error_kind == "fetch_failed"
