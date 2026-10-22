"""港股财务完整性与来源标注。"""

from datetime import date

from data.financial_selection import FinancialPackage, FinancialSelection
from data.hk_financial_policy import annotate_selection, package_complete
from data.schemas import FinancialData

PERIOD = date(2026, 6, 30)
EARLIER = date(2025, 12, 31)


def row(period=PERIOD, **changes):
    values = dict(
        symbol="00700.HK", fiscal_quarter=period,
        revenue=100.0, net_profit=10.0, total_assets=500.0,
        total_equity=300.0, basic_eps=1.0, operating_cash_flow=20.0,
        report_currency="CNY", period_start=date(period.year, 1, 1),
        period_basis="cumulative", cash_flow_basis="reported",
    )
    values.update(changes)
    return FinancialData(**values)


def package(rows, source="东方财富港股财报"):
    return FinancialPackage(source, rows, {})


def test_two_periods_with_latest_required_fields_are_complete():
    assert package_complete(package([row(EARLIER), row()]))


def test_single_period_is_partial():
    assert not package_complete(package([row()]))


def test_missing_cash_flow_makes_latest_partial():
    assert not package_complete(package([row(EARLIER), row(operating_cash_flow=None)]))


def test_cash_flow_per_share_is_accepted():
    assert package_complete(package([
        row(EARLIER),
        row(operating_cash_flow=None, operating_cash_flow_per_share=2.0),
    ]))


def test_optional_metrics_and_unknown_currency_do_not_block_complete():
    assert package_complete(package([
        row(EARLIER),
        row(roe=None, gross_margin=None, common_equity=None, report_currency=""),
    ]))


def test_missing_latest_balance_field_is_partial():
    assert not package_complete(package([row(EARLIER), row(total_equity=None)]))


def test_required_and_dependency_fields_are_known():
    from data.financial_selection import NUMERIC_FIELDS
    from data.hk_financial_policy import HK_DEPENDENCIES, HK_REQUIRED_FIELDS

    assert HK_REQUIRED_FIELDS <= NUMERIC_FIELDS
    assert all(group <= NUMERIC_FIELDS for group in HK_DEPENDENCIES)


def test_annotation_records_source_status_and_note():
    result = FinancialSelection(package=package([row(EARLIER), row()]), complete=True)
    latest = max(annotate_selection(result), key=lambda item: item.fiscal_quarter)
    assert latest.financial_source == "东方财富港股财报"
    assert latest.financial_selection_status == "complete"
    assert latest.financial_selection_note == "完整来源包"
    assert latest.field_sources["revenue"] == "东方财富港股财报"
    assert latest._data_source == "东方财富港股财报"


def test_partial_annotation_names_source_and_keeps_missing():
    result = FinancialSelection(package=package([row()]), complete=False)
    latest = annotate_selection(result)[0]
    assert latest.financial_selection_status == "partial"
    assert "东方财富港股财报" in latest.financial_selection_note
    assert "未能提供完整财务包" in latest.financial_selection_note


def test_annotation_without_package_returns_empty():
    assert annotate_selection(FinancialSelection()) == []


def test_supplement_annotation_marks_mixed_source():
    result = FinancialSelection(
        package=package([row(EARLIER), row()]),
        complete=True,
        supplements={PERIOD: {"net_profit": "某备用源"}},
    )
    latest = max(annotate_selection(result), key=lambda item: item.fiscal_quarter)
    assert latest.field_sources["net_profit"] == "某备用源"
    assert "非单一来源完整包" in latest.financial_selection_note
