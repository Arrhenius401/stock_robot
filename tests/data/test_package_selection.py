"""通用整包选择核心：不依赖财务模型。"""

import copy
from dataclasses import dataclass, replace
from datetime import date

import pytest

from data.package_selection import Position, SourcePackage, select_package

PERIOD = date(2026, 6, 30)
FIELDS = frozenset({"alpha", "beta"})
REQUIRED = frozenset({"alpha", "beta"})


@dataclass
class FakeRow:
    code: str
    period: date
    alpha: float | None = None
    beta: float | None = None


def position(field: str) -> Position:
    return Position(
        definition=field, unit="元", currency="CNY", start=date(2026, 1, 1),
        basis="cumulative", scope="合并", accounting="CAS", revision="原始版",
    )


def package(source="主源", alpha=100.0, beta=10.0):
    row = FakeRow("X", PERIOD, alpha=alpha, beta=beta)
    return SourcePackage(source, [row], {PERIOD: {name: position(name) for name in FIELDS}})


def select(*packages, **kwargs):
    return select_package(
        [(p.source, lambda p=p: p) for p in packages],
        entity="X", fields=FIELDS,
        entity_of=lambda row: row.code, period_of=lambda row: row.period,
        required=REQUIRED, **kwargs,
    )


def test_complete_primary_does_not_call_backup():
    primary = package()

    def forbidden():
        pytest.fail("完整主源不应访问备用")

    result = select_package(
        [("主源", lambda: primary), ("备用", forbidden)], entity="X", fields=FIELDS,
        entity_of=lambda row: row.code, period_of=lambda row: row.period, required=REQUIRED,
    )
    assert result.package is primary
    assert result.complete


def test_complete_backup_precedes_partial_stitching():
    primary = package(beta=None)
    backup = package("备用", alpha=101)
    result = select(primary, backup)
    assert result.package is backup
    assert result.complete


def test_equivalent_partial_packages_fill_only_missing():
    primary, donor = package(beta=None), package("备用", alpha=None)
    result = select(primary, donor)
    assert result.package is not primary
    assert result.package.rows[0].alpha == 100
    assert result.package.rows[0].beta == 10
    assert result.supplements == {PERIOD: {"beta": "备用"}}
    assert primary.rows[0].beta is None
    assert result.complete


def test_position_mismatch_blocks_supplement():
    primary, donor = package(beta=None), package("备用", alpha=None)
    donor.positions[PERIOD]["beta"] = replace(donor.positions[PERIOD]["beta"], currency="USD")
    result = select(primary, donor)
    assert result.package.rows[0].beta is None
    assert not result.complete


def test_row_compatible_callback_blocks_supplement():
    primary, donor = package(beta=None), package("备用", alpha=None)
    result = select(primary, donor, row_compatible=lambda row, pos, name: name != "beta")
    assert result.package.rows[0].beta is None


def test_apply_metadata_runs_after_supplement():
    primary, donor = package(beta=None), package("备用", alpha=None)
    seen = []
    result = select(
        primary, donor,
        apply_metadata=lambda row, name, other: seen.append((name, donor.source)),
    )
    assert seen == [("beta", donor.source)]
    assert result.supplements == {PERIOD: {"beta": donor.source}}


def test_older_complete_does_not_replace_new_partial():
    primary = package(beta=None)
    older = package("旧备用")
    older.rows[0] = replace(older.rows[0], period=date(2026, 3, 31))
    result = select(primary, older)
    assert result.package.rows[0].period == PERIOD
    assert not result.complete


def test_newer_partial_is_not_hidden_by_older_primary():
    primary = package(beta=None)
    newer = package("新备用", beta=None)
    newer.rows[0] = replace(newer.rows[0], period=date(2026, 9, 30))
    result = select(primary, newer)
    assert result.package.rows[0].period == date(2026, 9, 30)


def test_empty_shell_does_not_block_valid_backup():
    primary = package()
    empty = SourcePackage("空壳", [FakeRow("X", date(2026, 9, 30))], {})
    result = select(empty, primary)
    assert result.package is primary


def test_wrong_entity_or_duplicate_period_is_rejected():
    wrong = package("错标的")
    wrong.rows[0] = replace(wrong.rows[0], code="Y")
    duplicate = package("重复")
    duplicate.rows.append(replace(duplicate.rows[0], alpha=1))
    assert select(wrong).package is None
    assert select(duplicate).package is None


def test_missing_required_period_prevents_complete():
    result = select(package(), required_periods=frozenset({date(2026, 3, 31)}))
    assert result.package is not None
    assert not result.complete


def test_unknown_configuration_field_is_rejected():
    with pytest.raises(ValueError, match="字段"):
        select_package(
            [], entity="X", fields=FIELDS, entity_of=lambda row: row.code,
            period_of=lambda row: row.period, required=frozenset({"typo"}),
        )


def test_provider_error_is_isolated_and_next_is_used():
    errors = []
    backup = package("备用")

    def failed():
        raise TimeoutError("boom")

    result = select_package(
        [("主源", failed), ("备用", lambda: backup)], entity="X", fields=FIELDS,
        entity_of=lambda row: row.code, period_of=lambda row: row.period, required=REQUIRED,
        on_error=lambda source, error: errors.append((source, type(error))),
    )
    assert result.package is backup
    assert errors == [("主源", TimeoutError)]


def test_supplement_does_not_mutate_input_packages():
    primary, donor = package(beta=None), package("备用", alpha=None)
    original = copy.deepcopy(primary)
    select(primary, donor)
    assert primary == original
