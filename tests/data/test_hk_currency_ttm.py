from datetime import date

import pytest

from data.financial_ttm import financial_ttm
from data.schemas import FinancialData


def row(period, start, value, *, verified=True, currency="HKD"):
    return FinancialData(symbol="00700.HK", fiscal_quarter=period, period_start=start,
                         revenue=value, net_profit=value / 10, report_currency=currency,
                         amount_currency_verified=verified, period_basis="cumulative",
                         field_precision={"revenue": "source_native", "net_profit": "source_native"})


def test_hk_ttm_requires_all_three_periods_verified():
    rows = [row(date(2024, 12, 31), date(2024, 1, 1), 100),
            row(date(2025, 6, 30), date(2025, 1, 1), 60),
            row(date(2025, 12, 31), date(2025, 1, 1), 110),
            row(date(2026, 6, 30), date(2026, 1, 1), 70)]
    assert financial_ttm(rows, "revenue", "hk") == pytest.approx(120)
    rows[-1].amount_currency_verified = False
    assert financial_ttm(rows, "revenue", "hk") is None


def test_hk_annual_latest_requires_currency_evidence():
    rows = [row(date(2025, 12, 31), date(2025, 1, 1), 110)]
    assert financial_ttm(rows, "revenue", "hk") == pytest.approx(110)
    rows[0].amount_currency_verified = False
    assert financial_ttm(rows, "revenue", "hk") is None
