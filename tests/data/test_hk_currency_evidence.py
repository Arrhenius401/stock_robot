import json
from datetime import date
from pathlib import Path

from data.hk_currency_evidence import verify_cash_flow, verify_period


def indicator(code):
    data = json.loads((Path(__file__).parents[1] / f"resources/hk/eastmoney-{code}-indicator-20261124.json").read_text(encoding="utf-8"))
    return {str(row["REPORT_DATE"])[:10]: row for row in data["rows"]}


def income(code, period, start, revenue, profit,
           codes=("004001001", "004001002", "004025002")):
    amounts = (revenue, 0, profit) if len(codes) == 3 else (revenue, profit)
    return [{"SECUCODE": f"{code}.HK", "SECURITY_CODE": code,
             "REPORT_DATE": period, "START_DATE": start,
             "STD_ITEM_CODE": item_code, "AMOUNT": amount}
            for item_code, amount in zip(codes, amounts, strict=True)]


def test_standard_company_verifies_currency_and_amounts():
    evidence = verify_period(date(2025, 12, 31), date(2025, 1, 1),
                             income("00700", "2025-12-31", "2025-01-01",
                                    751766000000, 224842000000),
                             indicator("00700")["2025-12-31"], bank=False)
    assert evidence.verified is True
    assert evidence.currency == "HKD"


def test_bank_uses_its_own_codes():
    evidence = verify_period(date(2025, 12, 31), date(2025, 1, 1),
                             income("00005", "2025-12-31", "2025-01-01",
                                    452823411200, 148321737600,
                                    codes=("001003999", "001025002")),
                             indicator("00005")["2025-12-31"], bank=True)
    assert evidence.verified is True


def test_currency_or_amount_mismatch_fails():
    row = dict(indicator("00700")["2025-12-31"])
    row["CURRENCY"] = "CNY"
    evidence = verify_period(date(2025, 12, 31), date(2025, 1, 1),
                             income("00700", "2025-12-31", "2025-01-01",
                                    751766000000, 224842000000),
                             row, bank=False)
    assert evidence.verified is False
    assert "币种" in evidence.reason or "金额" in evidence.reason


def test_missing_statement_amount_is_not_zero():
    row = dict(indicator("00700")["2025-12-31"])
    row["OPERATE_INCOME"] = row["HOLDER_PROFIT"] = 0
    evidence = verify_period(date(2025, 12, 31), date(2025, 1, 1), [], row, bank=False)
    assert evidence.verified is False


def test_indicator_period_must_match():
    row = dict(indicator("00700")["2025-12-31"])
    row["REPORT_DATE"] = "2024-12-31"
    evidence = verify_period(date(2025, 12, 31), date(2025, 1, 1),
        income("00700", "2025-12-31", "2025-01-01", 751766000000, 224842000000), row, bank=False)
    assert evidence.verified is False


def test_cash_flow_ratio_verifies_currency_and_amount():
    row = {"START_DATE": "2026-01-01", "OPERATE_INCOME": 1000, "OCF_SALES": 25}
    verified, reason = verify_cash_flow(date(2026, 1, 1), 250, row, "HKD")
    assert verified is True and reason == ""


def test_cash_flow_mismatch_or_missing_ratio_fails():
    row = {"START_DATE": "2026-01-01", "OPERATE_INCOME": 1000, "OCF_SALES": 25}
    assert verify_cash_flow(date(2026, 1, 1), 260, row, "HKD")[0] is False
    assert verify_cash_flow(date(2026, 1, 1), 250, {"START_DATE": "2026-01-01"}, "HKD")[0] is False
    assert verify_cash_flow(date(2026, 1, 1), 250, row, "")[0] is False


def test_balance_assets_and_parent_equity_verify_currency():
    from data.hk_currency_evidence import verify_balance
    indicator = {'REPORT_DATE': '2026-09-30', 'CURRENCY': 'HKD',
                 'TOTAL_ASSETS': 1000, 'TOTAL_PARENT_EQUITY': 400}
    rows = [{'SECUCODE': '00700.HK', 'REPORT_DATE': '2026-09-30',
             'STD_ITEM_CODE': code, 'AMOUNT': value}
            for code, value in [('004009999', 1000), ('004030999', 400)]]
    assert verify_balance(date(2026, 9, 30), rows, indicator) == (True, 'HKD', '')
    indicator['TOTAL_ASSETS'] = 1200
    assert verify_balance(date(2026, 9, 30), rows, indicator)[0] is False
