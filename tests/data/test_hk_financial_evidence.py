from datetime import date

from data.schemas import FinancialData


def hk_row(**overrides):
    row = FinancialData(
        symbol="00700.HK", fiscal_quarter=date(2026, 9, 30),
        period_start=date(2026, 1, 1), period_basis="cumulative",
        reporting_basis="东方财富港股财报-报告期-国际会计准则",
        report_currency="HKD", amount_currency_verified=True,
        revenue=1000, net_profit=200, operating_cash_flow=300,
        cash_flow_basis="reported", cash_flow_period_start=date(2026, 1, 1),
        field_precision={"revenue": "source_native", "net_profit": "source_native",
                         "operating_cash_flow": "source_native"},
        field_sources={"revenue": "东方财富港股财报", "net_profit": "东方财富港股财报",
                       "operating_cash_flow": "东方财富港股财报"},
        field_positions={
            "revenue": {"definition": "revenue", "unit": "currency", "currency": "HKD",
                        "start": "2026-01-01", "basis": "cumulative", "scope": "consolidated",
                        "accounting": "国际会计准则", "revision": "当前源快照", "calculation": "reported"},
            "net_profit": {"definition": "parent_net_profit", "unit": "currency", "currency": "HKD",
                           "start": "2026-01-01", "basis": "cumulative", "scope": "consolidated",
                           "accounting": "国际会计准则", "revision": "当前源快照", "calculation": "reported"},
            "operating_cash_flow": {"definition": "operating_cash_flow", "unit": "currency",
                                    "currency": "HKD", "start": "2026-01-01", "basis": "cumulative",
                                    "scope": "consolidated", "accounting": "国际会计准则",
                                    "revision": "当前源快照", "calculation": "reported"},
        },
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def test_hk_positions_make_cashflow_compatible():
    from analysis.financial_scoring import cashflow_compatible

    assert cashflow_compatible(hk_row())


def test_hk_net_margin_is_derived_with_basis():
    row = hk_row(net_margin=0.2, net_margin_basis="计算值：净利润÷营业收入（报告期口径）")
    assert row.net_margin == 0.2
    assert "计算值" in row.net_margin_basis
