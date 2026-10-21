"""已有采集请求保留趋势口径，不扩采集或改变原始财务值。"""
from datetime import date

import pandas as pd
import pytest

from analysis.financial_trends import build_financial_trends
from data.akshare import AkShareAdapter
from data.schemas import FinancialData


@pytest.fixture(autouse=True)
def isolate_report_directory(mocker):
    """旧元信息样本不隐式触网；目录行为在专项测试中覆盖。"""
    mocker.patch("data.akshare.financial_report_directory", return_value=[])


def test_ths_period_and_estimated_cashflow_are_explicit(mocker):
    mocker.patch("akshare.stock_financial_abstract_ths", return_value=pd.DataFrame({
        "报告期": ["2026-06-30"], "营业总收入": [1000], "净利润": [100],
        "扣非净利润": [90], "每股经营现金流": [2], "基本每股收益": [1], "币种": ["CNY"],
    }))
    mocker.patch("akshare.stock_financial_debt_new_ths", return_value=pd.DataFrame())
    row = AkShareAdapter()._fetch_financial("688326")[0]
    assert row.period_basis == "cumulative" and row.period_start == date(2026, 1, 1)
    assert row.report_currency == "CNY"
    assert row.operating_cash_flow == 200 and row.cash_flow_basis == "estimated"


def test_ths_domestic_report_has_cny_contract_without_extra_request(mocker):
    summary = mocker.patch("akshare.stock_financial_abstract_ths", return_value=pd.DataFrame({
        "报告期": ["2025-06-30", "2026-06-30"], "营业总收入": [100, 120], "净利润": [10, 15],
    }))
    debt = mocker.patch("akshare.stock_financial_debt_new_ths", return_value=pd.DataFrame())
    network = mocker.patch("requests.get", side_effect=AssertionError("币种不应增加网络请求"))
    rows = AkShareAdapter()._fetch_financial("688326")
    assert all(row.report_currency == "CNY" for row in rows)
    assert [row.revenue for row in rows] == [100, 120]
    assert [row.net_profit for row in rows] == [10, 15]
    assert build_financial_trends(rows, "688326")["highlights"]
    summary.assert_called_once_with(symbol="688326")
    debt.assert_called_once_with(symbol="688326")
    network.assert_not_called()


@pytest.mark.parametrize("currency", ["CNY", "人民币", "人民币元", "RMB", " cny ", None, "", " "])
def test_ths_domestic_report_accepts_rmb_aliases_or_missing_currency(mocker, currency):
    mocker.patch("akshare.stock_financial_abstract_ths", return_value=pd.DataFrame({
        "报告期": ["2026-06-30"], "营业总收入": [100], "净利润": [10], "币种": [currency],
    }))
    mocker.patch("akshare.stock_financial_debt_new_ths", return_value=pd.DataFrame())
    assert AkShareAdapter()._fetch_financial("688326", market="a-shares")[0].report_currency == "CNY"


@pytest.mark.parametrize(("column", "attribute"), [("USD", None), ("HKD", None), ("未知", None),
    (float("nan"), None), ("CNY", "USD"), (None, "USD")])
def test_ths_currency_contradiction_preserves_values_but_blocks_amount_trends(mocker, caplog, column, attribute):
    frame = pd.DataFrame({"报告期": ["2025-06-30", "2026-06-30"], "营业总收入": [100, 120],
                          "净利润": [10, 15], "币种": [column, column]})
    if attribute is not None:
        frame.attrs["currency"] = attribute
    mocker.patch("akshare.stock_financial_abstract_ths", return_value=frame)
    mocker.patch("akshare.stock_financial_debt_new_ths", return_value=pd.DataFrame())
    rows = AkShareAdapter()._fetch_financial("688326")
    assert [r.revenue for r in rows] == [100, 120]
    assert [r.net_profit for r in rows] == [10, 15]
    assert all(r.report_currency == "" for r in rows)
    assert build_financial_trends(rows, "688326")["highlights"] == []
    assert "币种" in caplog.text and "人民币" in caplog.text


def test_cny_contract_does_not_apply_to_model_defaults_or_other_markets(mocker):
    mocker.patch("akshare.stock_financial_abstract_ths", return_value=pd.DataFrame({
        "报告期": ["2026-06-30"], "营业总收入": [100], "净利润": [10],
    }))
    mocker.patch("akshare.stock_financial_debt_new_ths", return_value=pd.DataFrame())
    assert AkShareAdapter()._fetch_financial("688326", market="us")[0].report_currency == ""
    for symbol in ("688326", "00700.HK"):
        row = FinancialData(symbol=symbol, fiscal_quarter=date(2026, 6, 30))
        assert row.report_currency == ""


def test_hk_uses_source_period_start_and_cashflow_metadata(mocker):
    def entry(item, start="2026-04-01", **extra):
        return {"SECUCODE": "00700.HK", "SECURITY_CODE": "00700", "REPORT_DATE": "2026-09-30",
                "START_DATE": start, "CURRENCY_CODE": "HKD", "STD_ITEM_CODE": item, "AMOUNT": 100, **extra}
    mocker.patch("data.akshare.financial_statement", side_effect=[
        [entry("004001001"), entry("004025002")], [], [entry("003999")],
    ])
    row = AkShareAdapter()._fetch_hk_financial("00700.HK")[0]
    assert row.period_start == date(2026, 4, 1)
    assert row.cash_flow_basis == "reported"
    assert row.cash_flow_period_start == row.period_start
    assert row.report_currency == "HKD"


def test_hk_mismatched_cashflow_start_or_ambiguous_income_is_not_reported_trend(mocker):
    def entry(item, start):
        return {"REPORT_DATE": "2026-06-30", "START_DATE": start, "STD_ITEM_CODE": item, "AMOUNT": 100}
    mocker.patch("data.akshare.financial_statement", side_effect=[
        [entry("004001001", "2026-01-01"), entry("004025002", "2026-04-01")],
        [], [entry("003999", "2026-01-01")],
    ])
    row = AkShareAdapter()._fetch_hk_financial("00700.HK")[0]
    assert row.revenue == 100 and row.net_profit == 100
    assert row.period_basis == "unknown" and row.period_start is None
    assert row.cash_flow_basis == "unknown"
