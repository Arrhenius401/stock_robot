"""财务整包选择与严格等效补位规则。"""

from dataclasses import replace
from datetime import date

import pytest

from data.financial_selection import (
    FinancialPackage,
    FinancialSelection,
    Position,
    select_financial,
)
from data.schemas import FinancialData

PERIOD = date(2026, 6, 30)
REQUIRED = frozenset({"revenue", "net_profit"})


def package(source="主源", revenue: float | None = 100.0, profit: float | None = 10.0, **changes):
    row = FinancialData(symbol="600519", fiscal_quarter=PERIOD, revenue=revenue, net_profit=profit, total_assets=200)
    positions = {
        field: Position(
            definition=field, unit="元", currency="CNY", start=date(2026, 1, 1),
            basis="cumulative", scope="合并", accounting="CAS", revision="2026中报原始版",
        ) for field in REQUIRED
    }
    return FinancialPackage(source=source, rows=[row], positions={PERIOD: positions}, **changes)


def select(*packages):
    return select_financial([(p.source, lambda p=p: p) for p in packages], symbol="600519", required=REQUIRED)


def selected_row(result: FinancialSelection) -> FinancialData:
    assert result.package is not None
    return result.package.rows[0]


def test_complete_primary_does_not_call_backup():
    primary = package()

    def forbidden():
        pytest.fail("完整主源不应访问备用")

    result = select_financial([("主源", lambda: primary), ("备用", forbidden)], symbol="600519", required=REQUIRED)
    assert result.package is primary
    assert result.supplements == {}


def test_complete_backup_precedes_stitching():
    primary = package(profit=None)
    backup = package("备用", revenue=101)
    result = select(primary, backup)
    assert result.package is backup
    assert primary.rows[0].net_profit is None


def test_equivalent_partial_packages_can_fill_only_missing():
    primary = package(profit=None)
    backup = package("备用", revenue=None)
    result = select(primary, backup)
    assert selected_row(result).net_profit == 10
    assert selected_row(result).revenue == 100
    assert result.supplements == {PERIOD: {"net_profit": "备用"}}
    assert primary.rows[0].net_profit is None
    assert result.complete


@pytest.mark.parametrize("field,value", [
    ("definition", "净利润含少数股东"), ("unit", "万元"), ("currency", "USD"),
    ("start", date(2026, 4, 1)), ("basis", "single"), ("scope", "母公司"),
    ("accounting", "IFRS"), ("revision", "修订版"), ("revision", ""),
])
def test_mismatched_or_unknown_position_is_not_supplemented(field, value):
    primary = package(profit=None)
    donor = package("备用", revenue=None)
    donor.positions[PERIOD]["net_profit"] = replace(donor.positions[PERIOD]["net_profit"], **{field: value})
    result = select(primary, donor)
    assert selected_row(result).net_profit is None
    assert not result.complete


def test_both_unknown_versions_are_not_equivalent():
    primary, donor = package(profit=None), package("备用", revenue=None)
    for p in (primary, donor):
        p.positions[PERIOD]["net_profit"] = replace(p.positions[PERIOD]["net_profit"], revision="")
    assert selected_row(select(primary, donor)).net_profit is None


@pytest.mark.parametrize("value", [0, -10])
def test_zero_and_negative_are_valid_and_not_overwritten(value):
    primary = package(profit=value)
    result = select(primary, package("备用", profit=999))
    assert selected_row(result).net_profit == value


def test_older_complete_does_not_replace_new_partial():
    primary = package(profit=None)
    older = package("旧备用")
    older.rows[0] = older.rows[0].model_copy(update={"fiscal_quarter": date(2026, 3, 31)})
    result = select(primary, older)
    assert selected_row(result).fiscal_quarter == PERIOD
    assert selected_row(result).net_profit is None
    assert not result.complete


def test_newer_partial_does_not_get_hidden_by_older_primary():
    primary = package(profit=None)
    newer = package("新备用", profit=None)
    newer.rows[0] = newer.rows[0].model_copy(update={"fiscal_quarter": date(2026, 9, 30)})
    result = select(primary, newer)
    assert selected_row(result).fiscal_quarter == date(2026, 9, 30)


def test_other_symbol_is_not_accepted():
    primary = package(profit=None)
    donor = package("备用")
    donor.rows[0] = donor.rows[0].model_copy(update={"symbol": "000001"})
    assert selected_row(select(primary, donor)).net_profit is None


def test_duplicate_period_is_rejected_instead_of_last_wins():
    primary = package(profit=None)
    donor = package("备用")
    donor.rows.append(donor.rows[0].model_copy(update={"net_profit": 99}))
    assert selected_row(select(primary, donor)).net_profit is None


def test_dependency_conflict_blocks_supplement():
    primary = package(profit=None)
    donor = package("备用", revenue=999)
    donor.rows[0].basic_eps = None
    result = select_financial(
        [(p.source, lambda p=p: p) for p in (primary, donor)],
        symbol="600519", required=REQUIRED | {"basic_eps"}, dependencies=(frozenset({"revenue", "net_profit"}),),
    )
    assert selected_row(result).net_profit is None


def test_failures_are_recorded_and_next_provider_is_tried():
    errors = []

    def failed():
        raise TimeoutError("不可直接暴露给用户的响应")

    backup = package("备用")
    result = select_financial(
        [("主源", failed), ("备用", lambda: backup)], symbol="600519", required=REQUIRED,
        on_error=lambda source, error: errors.append((source, type(error))),
    )
    assert result.package is backup
    assert errors == [("主源", TimeoutError)]


def test_all_failed_returns_empty_selection():
    result = select_financial([("空源", lambda: FinancialPackage("空源", [], {}))], symbol="600519", required=REQUIRED)
    assert result.package is None
    assert not result.complete


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_is_missing(value):
    primary = package(profit=value)
    donor = package("备用", revenue=None)
    assert selected_row(select(primary, donor)).net_profit == 10


def test_missing_evidence_does_not_prevent_whole_package_selection():
    primary = package()
    primary.positions.clear()
    assert select(primary).package is primary


def test_missing_evidence_prevents_stitching():
    primary = package(profit=None)
    primary.positions.clear()
    assert selected_row(select(primary, package("备用", revenue=None))).net_profit is None


def test_unknown_required_field_is_configuration_error():
    with pytest.raises(ValueError, match="字段"):
        select_financial([], symbol="600519", required=frozenset({"typo"}))


def test_first_provider_wrong_symbol_is_rejected():
    wrong = package()
    wrong.rows[0].symbol = "000001"
    assert select(wrong).package is None


@pytest.mark.parametrize("revision", ["unknown", "未知", "--", "N/A"])
def test_unknown_revision_placeholder_does_not_prove_equivalence(revision):
    primary, donor = package(profit=None), package("备用", revenue=None)
    for p in (primary, donor):
        p.positions[PERIOD]["net_profit"] = replace(p.positions[PERIOD]["net_profit"], revision=revision)
    assert selected_row(select(primary, donor)).net_profit is None


@pytest.mark.parametrize("changes", [
    {"report_currency": "USD"}, {"period_start": date(2026, 4, 1)}, {"period_basis": "single"},
])
def test_position_contradicting_model_is_rejected(changes):
    primary, donor = package(profit=None), package("备用", revenue=None)
    donor.rows[0] = donor.rows[0].model_copy(update=changes)
    assert selected_row(select(primary, donor)).net_profit is None


def test_future_start_is_not_accepted_as_period_evidence():
    primary, donor = package(profit=None), package("备用", revenue=None)
    for p in (primary, donor):
        p.positions[PERIOD]["net_profit"] = replace(p.positions[PERIOD]["net_profit"], start=date(2027, 1, 1))
    assert selected_row(select(primary, donor)).net_profit is None


def test_candidate_cannot_shrink_required_period_window_to_claim_complete():
    candidate = package()
    result = select_financial(
        [(candidate.source, lambda: candidate)], symbol="600519", required=REQUIRED,
        required_periods=frozenset({date(2026, 3, 31), PERIOD}),
    )
    assert not result.complete
    assert selected_row(result).revenue == 100


def test_stitching_does_not_append_missing_history_periods():
    primary, donor = package(profit=None), package("备用", revenue=None)
    donor.rows.append(donor.rows[0].model_copy(update={"fiscal_quarter": date(2026, 3, 31)}))
    result = select(primary, donor)
    assert result.package is not None
    assert [r.fiscal_quarter for r in result.package.rows] == [PERIOD]
    assert selected_row(result).net_profit == 10


@pytest.mark.parametrize("value", [0, -10])
def test_partial_primary_zero_or_negative_survives_supplementation(value):
    primary, donor = package(revenue=value, profit=None), package("备用", revenue=999, profit=10)
    result = select_financial(
        [(p.source, lambda p=p: p) for p in (primary, donor)], symbol="600519",
        required=REQUIRED | {"basic_eps"},
    )
    assert selected_row(result).revenue == value
    assert selected_row(result).net_profit == 10


def test_cash_flow_supplement_keeps_basis_and_period():
    primary, donor = package(profit=None), package("备用", revenue=None)
    donor.rows[0].operating_cash_flow = 0
    donor.rows[0].cash_flow_basis = "reported"
    donor.rows[0].cash_flow_period_start = date(2026, 1, 1)
    for p in (primary, donor):
        p.positions[PERIOD]["operating_cash_flow"] = replace(
            p.positions[PERIOD]["net_profit"], definition="经营活动现金流量净额", calculation="reported",
        )
    result = select(primary, donor)
    assert selected_row(result).operating_cash_flow == 0
    assert selected_row(result).cash_flow_basis == "reported"
    assert selected_row(result).cash_flow_period_start == date(2026, 1, 1)
    assert result.supplements[PERIOD]["operating_cash_flow"] == "备用"


@pytest.mark.parametrize("conflict", ["revision", "currency", "scope", "accounting", "start", "basis"])
def test_all_missing_dependency_group_cannot_mix_contexts(conflict):
    primary, donor = package(revenue=None, profit=None), package("备用")
    changed = {
        "revision": "修订版", "currency": "USD", "scope": "母公司",
        "accounting": "IFRS", "start": date(2026, 4, 1), "basis": "single",
    }[conflict]
    for p in (primary, donor):
        p.positions[PERIOD]["net_profit"] = replace(p.positions[PERIOD]["net_profit"], **{conflict: changed})
    result = select_financial(
        [(p.source, lambda p=p: p) for p in (primary, donor)], symbol="600519",
        required=REQUIRED | {"basic_eps"}, dependencies=(REQUIRED,),
    )
    assert selected_row(result).revenue is None
    assert selected_row(result).net_profit is None


@pytest.mark.parametrize("revision", ["null", "None", "-"])
def test_additional_unknown_version_placeholders_are_rejected(revision):
    position = package().positions[PERIOD]["net_profit"]
    assert not replace(position, revision=revision).proven()


def test_cash_flow_own_period_cannot_contradict_position():
    primary, donor = package(profit=None), package("备用", revenue=None)
    donor.rows[0].operating_cash_flow = 30
    donor.rows[0].cash_flow_basis = "reported"
    donor.rows[0].cash_flow_period_start = date(2026, 4, 1)
    for p in (primary, donor):
        p.positions[PERIOD]["operating_cash_flow"] = replace(
            p.positions[PERIOD]["net_profit"], definition="经营活动现金流量净额", calculation="reported",
        )
    assert selected_row(select(primary, donor)).operating_cash_flow is None


def test_estimated_cash_flow_cannot_claim_reported_position():
    primary, donor = package(profit=None), package("备用", revenue=None)
    donor.rows[0].operating_cash_flow = 30
    donor.rows[0].cash_flow_basis = "estimated"
    for p in (primary, donor):
        p.positions[PERIOD]["operating_cash_flow"] = replace(
            p.positions[PERIOD]["net_profit"], definition="经营活动现金流量净额", calculation="reported",
        )
    assert selected_row(select(primary, donor)).operating_cash_flow is None


@pytest.mark.parametrize("start", ["2026-01-01", 42])
def test_invalid_position_date_is_refused_without_crashing(start):
    primary, donor = package(profit=None), package("备用", revenue=None)
    for p in (primary, donor):
        p.positions[PERIOD]["net_profit"] = replace(p.positions[PERIOD]["net_profit"], start=start)
    assert selected_row(select(primary, donor)).net_profit is None


def test_newer_metadata_only_package_cannot_hide_older_facts():
    empty = FinancialPackage("主源", [FinancialData(symbol="600519", fiscal_quarter=date(2026, 9, 30))], {})
    donor = package("备用")
    result = select(empty, donor)
    assert result.package is donor
    assert selected_row(result).revenue == 100


def test_same_period_metadata_only_package_cannot_hide_partial_facts():
    empty = FinancialPackage("主源", [FinancialData(symbol="600519", fiscal_quarter=PERIOD)], {})
    result = select(empty, package("备用", profit=None))
    assert result.package is not None
    assert result.package.source == "备用"
    assert selected_row(result).revenue == 100
    assert not result.complete


def test_trailing_empty_row_cannot_occupy_latest_fact_date():
    primary = package(profit=None)
    primary.rows.append(FinancialData(symbol="600519", fiscal_quarter=date(2026, 9, 30)))
    backup = package("备用")
    result = select(primary, backup)
    assert result.package is backup
    assert result.complete
    assert len(primary.rows) == 2


def test_trailing_partial_row_with_real_zero_keeps_new_period_protection():
    primary = package(profit=None)
    primary.rows.append(FinancialData(symbol="600519", fiscal_quarter=date(2026, 9, 30), revenue=0))
    result = select(primary, package("备用"))
    assert result.package is not None
    assert max(row.fiscal_quarter for row in result.package.rows) == date(2026, 9, 30)
    assert not result.complete


@pytest.mark.parametrize("field", ["basic_eps", "deducted_net_profit", "common_equity"])
def test_non_snapshot_numeric_fields_also_protect_new_period(field):
    primary = package(profit=None)
    row = FinancialData(symbol="600519", fiscal_quarter=date(2026, 9, 30))
    setattr(row, field, 0)
    primary.rows.append(row)
    result = select(primary, package("备用"))
    assert result.package is not None
    assert result.package.rows[-1].fiscal_quarter == date(2026, 9, 30)
    assert getattr(result.package.rows[-1], field) == 0
    assert not result.complete
