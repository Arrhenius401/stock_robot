from datetime import date

from analysis.financial_scoring import cashflow_compatible
from data.schemas import FinancialData


def base_row(**overrides):
    positions = {
        "net_profit": {
            "definition": "parent_net_profit", "unit": "currency", "currency": "CNY",
            "start": "2026-01-01", "basis": "cumulative", "scope": "consolidated",
            "accounting": "", "revision": "", "calculation": "reported",
        },
        "operating_cash_flow": {
            "definition": "operating_cash_flow", "unit": "currency", "currency": "CNY",
            "start": "2026-01-01", "basis": "cumulative", "scope": "consolidated",
            "accounting": "", "revision": "", "calculation": "reported",
        },
    }
    row = FinancialData(
        symbol="600519", fiscal_quarter=date(2026, 9, 30),
        period_start=date(2026, 1, 1), period_basis="cumulative",
        reporting_basis="同花顺财务摘要-按报告期", report_currency="CNY",
        net_profit=100, operating_cash_flow=120,
        cash_flow_basis="reported", cash_flow_period_start=date(2026, 1, 1),
        field_precision={"net_profit": "source_native", "operating_cash_flow": "source_native"},
        field_sources={"net_profit": "同花顺财报", "operating_cash_flow": "同花顺财报"},
        field_positions=positions,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def test_empty_accounting_and_revision_are_allowed_when_both_sides_agree():
    assert cashflow_compatible(base_row())


def test_one_sided_accounting_or_revision_still_fails():
    row = base_row()
    row.field_positions["operating_cash_flow"]["accounting"] = "中国企业会计准则"
    assert not cashflow_compatible(row)
    row = base_row()
    row.field_positions["net_profit"]["revision"] = "修订版"
    assert not cashflow_compatible(row)


def test_currency_or_start_mismatch_fails():
    row = base_row()
    row.field_positions["operating_cash_flow"]["currency"] = "HKD"
    assert not cashflow_compatible(row)
    row = base_row()
    row.field_positions["operating_cash_flow"]["start"] = "2026-04-01"
    assert not cashflow_compatible(row)


def test_mismatched_source_or_period_end_is_rejected():
    row = base_row()
    row.field_sources["operating_cash_flow"] = "其他来源"
    assert not cashflow_compatible(row)
    row = base_row()
    row.field_positions["net_profit"]["end"] = "2026-09-30"
    row.field_positions["operating_cash_flow"]["end"] = "2026-06-30"
    assert not cashflow_compatible(row)
