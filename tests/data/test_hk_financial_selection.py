"""港股财务接入通用选择器：单源完整/部分、无额外请求、缓存往返。"""

from datetime import date

from data.akshare import AkShareAdapter
from data.schemas import FinancialData

PERIOD = date(2026, 6, 30)
EARLIER = date(2025, 6, 30)


def income_rows(period, revenue, profit):
    common = {
        "SECUCODE": "00700.HK", "SECURITY_CODE": "00700",
        "REPORT_DATE": period.isoformat(), "START_DATE": f"{period.year}-01-01",
        "CURRENCY": "人民币",
    }
    return [
        {**common, "STD_ITEM_CODE": "004001001", "AMOUNT": revenue},
        {**common, "STD_ITEM_CODE": "004025002", "AMOUNT": profit},
        {**common, "STD_ITEM_CODE": "004027002", "AMOUNT": 1.0},
        {**common, "STD_ITEM_CODE": "004007999", "AMOUNT": revenue * 0.5},
        {**common, "STD_ITEM_CODE": "004001999", "AMOUNT": revenue},
    ]


def balance_rows(period, assets, equity):
    common = {
        "SECUCODE": "00700.HK", "SECURITY_CODE": "00700",
        "REPORT_DATE": period.isoformat(), "START_DATE": f"{period.year}-01-01",
    }
    return [
        {**common, "STD_ITEM_CODE": "004009999", "AMOUNT": assets},
        {**common, "STD_ITEM_CODE": "004036999", "AMOUNT": equity},
        {**common, "STD_ITEM_CODE": "004030999", "AMOUNT": equity},
    ]


def cash_rows(period, amount):
    common = {
        "SECUCODE": "00700.HK", "SECURITY_CODE": "00700",
        "REPORT_DATE": period.isoformat(), "START_DATE": f"{period.year}-01-01",
    }
    return [{**common, "STD_ITEM_CODE": "003999", "AMOUNT": amount}]


def directory_rows():
    return [
        {
            "SECUCODE": "00700.HK", "SECURITY_CODE": "00700",
            "REPORT_DATE": period.isoformat(), "START_DATE": f"{period.year}-01-01",
            "CURRENCY": "人民币", "ACCOUNT_STANDARD": "国际会计准则",
        }
        for period in (EARLIER, PERIOD)
    ]


def install(mocker, *, cash=True):
    income = income_rows(EARLIER, 90, 9) + income_rows(PERIOD, 100, 10)
    balance = balance_rows(EARLIER, 450, 270) + balance_rows(PERIOD, 500, 300)
    cash_values = cash_rows(EARLIER, 18) + cash_rows(PERIOD, 20) if cash else []
    statement = mocker.patch(
        "data.akshare.financial_statement",
        side_effect=[income, balance, cash_values],
    )
    directory = mocker.patch(
        "data.akshare.financial_report_directory",
        return_value=directory_rows(),
    )
    return statement, directory


def test_hk_financial_uses_single_source_and_marks_complete(mocker):
    statement, directory = install(mocker)
    rows = AkShareAdapter().fetch("00700.HK", market="hk", data_type="financial")
    latest = max(rows, key=lambda row: row.fiscal_quarter)
    assert len(rows) == 2
    assert latest.revenue == 100
    assert latest.financial_source == "东方财富港股财报"
    assert latest.financial_selection_status == "complete"
    assert latest.financial_selection_note == "完整来源包"
    assert statement.call_count == 3
    directory.assert_called_once()


def test_missing_cash_flow_keeps_values_but_marks_partial(mocker):
    install(mocker, cash=False)
    latest = max(
        AkShareAdapter().fetch("00700.HK", market="hk", data_type="financial"),
        key=lambda row: row.fiscal_quarter,
    )
    assert latest.operating_cash_flow is None
    assert latest.financial_selection_status == "partial"
    assert "未能提供完整财务包" in latest.financial_selection_note


def test_hk_provenance_survives_cache_roundtrip(mocker):
    install(mocker)
    row = max(
        AkShareAdapter().fetch("00700.HK", market="hk", data_type="financial"),
        key=lambda item: item.fiscal_quarter,
    )
    restored = FinancialData.model_validate(row.model_dump())
    assert restored.financial_source == row.financial_source
    assert restored.financial_selection_status == "complete"
    assert restored.financial_selection_note == "完整来源包"


def test_old_hk_cache_without_provenance_stays_unknown():
    restored = FinancialData.model_validate({
        "symbol": "00700.HK", "fiscal_quarter": "2026-06-30", "revenue": 1.0,
    })
    assert restored.financial_selection_status == "unknown"
    assert restored.financial_source == ""
    assert restored.financial_selection_note == ""
