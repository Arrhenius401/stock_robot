"""通用来源整包选择：完整包优先，仅在明确等效位置补缺。

本模块不采集网络数据、不猜测供应商口径，也不认识具体业务模型。
"""

import copy
import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

RowT = TypeVar("RowT")


@dataclass(frozen=True)
class Position:
    """某指标在某报告期的等效位置；空值不是等效证据。

    金额与每股金额均须有币种，比例须明确归一化单位。
    revision 必须是可核对的同一披露/修订标识，不能填写供应商抓取时间。
    """

    definition: str
    unit: str
    currency: str
    start: date | None
    basis: str
    scope: str
    accounting: str
    revision: str
    calculation: str = ""

    def proven(self) -> bool:
        unknown = {"unknown", "n/a", "nan", "null", "none", "-", "--", "未知", "未记录", "不详"}
        return (
            all(
                isinstance(value, str)
                and bool(value.strip())
                and value.strip().lower() not in unknown
                for value in (
                    self.definition, self.unit, self.currency,
                    self.scope, self.accounting, self.revision,
                )
            )
            and self.basis in {"cumulative", "single", "instant"}
            and (type(self.start) is date or (self.start is None and self.basis == "instant"))
        )


@dataclass
class SourcePackage(Generic[RowT]):
    """一个来源的候选包；positions 缺失时允许整包使用，但不允许跨源补位。"""

    source: str
    rows: list[RowT]
    positions: dict[date, dict[str, Position]]


@dataclass
class Selection(Generic[RowT]):
    package: SourcePackage[RowT] | None = None
    complete: bool = False
    supplements: dict[date, dict[str, str]] = field(default_factory=dict)


def _valid(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _row_has_facts(row: object, fields: frozenset[str]) -> bool:
    return any(_valid(getattr(row, name)) for name in fields)


def _usable(
    package: SourcePackage[RowT],
    entity: str,
    entity_of: Callable[[RowT], str],
    period_of: Callable[[RowT], date],
    fields: frozenset[str],
) -> bool:
    """混入其他标的或重复期间的包不参与选择，禁止隐式最后一条获胜。"""
    return (
        bool(package.source.strip())
        and bool(package.rows)
        and len({entity_of(row) for row in package.rows}) == 1
        and entity_of(package.rows[0]) == entity
        and len({period_of(row) for row in package.rows}) == len(package.rows)
        and any(_row_has_facts(row, fields) for row in package.rows)
    )


def _complete(
    package: SourcePackage[RowT],
    required: frozenset[str],
    required_periods: frozenset[date],
    completeness: Callable[[SourcePackage[RowT]], bool] | None,
    period_of: Callable[[RowT], date],
) -> bool:
    if not package.rows or not required_periods <= {period_of(row) for row in package.rows}:
        return False
    if completeness is not None:
        return completeness(package)
    return all(_valid(getattr(row, name)) for row in package.rows for name in required)


def _compatible(
    primary: SourcePackage[RowT],
    donor: SourcePackage[RowT],
    period: date,
    name: str,
    period_of: Callable[[RowT], date],
    row_compatible: Callable[[RowT, Position, str], bool] | None,
) -> bool:
    left = primary.positions.get(period, {}).get(name)
    right = donor.positions.get(period, {}).get(name)
    if left is None or right is None or not left.proven() or not right.proven() or left != right:
        return False
    if left.start is not None and left.start > period:
        return False
    for package in (primary, donor):
        row = next(row for row in package.rows if period_of(row) == period)
        if row_compatible is not None and not row_compatible(row, left, name):
            return False
    return True


def _dependency_safe(
    primary: SourcePackage[RowT],
    donor: SourcePackage[RowT],
    row: RowT,
    other: RowT,
    name: str,
    dependencies: Sequence[frozenset[str]],
    period_of: Callable[[RowT], date],
    row_compatible: Callable[[RowT, Position, str], bool] | None,
) -> bool:
    for group in dependencies:
        if name not in group:
            continue
        if not all(
            _compatible(primary, donor, period_of(row), related, period_of, row_compatible)
            for related in group
        ):
            return False
        positions = primary.positions[period_of(row)]
        contexts = {
            (positions[key].currency, positions[key].scope,
             positions[key].accounting, positions[key].revision)
            for key in group
        }
        periods = {
            (positions[key].start, positions[key].basis)
            for key in group if positions[key].basis != "instant"
        }
        if len(contexts) != 1 or len(periods) > 1:
            return False
        for related in group - {name}:
            existing = getattr(row, related)
            if not _valid(existing):
                continue
            candidate = getattr(other, related)
            if (
                not _compatible(primary, donor, period_of(row), related, period_of, row_compatible)
                or not _valid(candidate)
                or candidate != existing
            ):
                return False
    return True


def select_package(
    providers: Sequence[tuple[str, Callable[[], SourcePackage[RowT]]]], *,
    entity: str,
    fields: frozenset[str],
    entity_of: Callable[[RowT], str],
    period_of: Callable[[RowT], date],
    required: frozenset[str],
    required_periods: frozenset[date] = frozenset(),
    completeness: Callable[[SourcePackage[RowT]], bool] | None = None,
    dependencies: Sequence[frozenset[str]] = (),
    row_compatible: Callable[[RowT, Position, str], bool] | None = None,
    apply_metadata: Callable[[RowT, str, RowT], None] | None = None,
    on_error: Callable[[str, Exception], None] | None = None,
) -> Selection[RowT]:
    """按优先级延迟取包；全部不完整后才进行严格等效补位。

    完整性针对包内全部报告期及调用方明确要求的期间。选择器不扩大窗口、不推断上市日，
    也不以旧完整包覆盖已经观察到的更新报告期。有效主值永不覆盖。

    row_compatible 对当前包与供体包使用同一 Position 校验；apply_metadata 在字段写入后调用，
    用于同步现金流披露/推算或毛利率口径等附随字段。
    """
    configured = required | frozenset().union(*dependencies)
    if not required or configured - fields:
        raise ValueError("必需字段和依赖字段必须为已知数值字段，且必需字段不能为空")
    if not entity.strip():
        raise ValueError("标的不能为空")

    candidates: list[SourcePackage[RowT]] = []
    selected: SourcePackage[RowT] | None = None
    latest: date | None = None
    for label, fetch in providers:
        try:
            candidate = fetch()
        except Exception as error:  # noqa: BLE001 — 供应商调用隔离，失败不阻断后续来源
            logger.warning("候选来源失败 %s：%s", label, type(error).__name__)
            if on_error is not None:
                on_error(label, error)
            continue
        if not _usable(candidate, entity, entity_of, period_of, fields):
            logger.debug("候选为空或标的/期间无效：%s", label)
            continue
        facts = [row for row in candidate.rows if _row_has_facts(row, fields)]
        if len(facts) != len(candidate.rows):
            candidate = SourcePackage(
                candidate.source,
                facts,
                {period_of(row): candidate.positions.get(period_of(row), {}) for row in facts},
            )
        head = max(period_of(row) for row in candidate.rows)
        candidates.append(candidate)
        if latest is not None and head < latest:
            continue
        if _complete(candidate, required, required_periods, completeness, period_of):
            return Selection(package=candidate, complete=True)
        if selected is None or latest is None or head > latest:
            selected, latest = candidate, head

    if selected is None:
        return Selection()

    output = SourcePackage(
        selected.source,
        copy.deepcopy(selected.rows),
        {period: dict(positions) for period, positions in selected.positions.items()},
    )
    supplements: dict[date, dict[str, str]] = {}
    for donor in candidates:
        if donor is selected:
            continue
        rows = {period_of(row): row for row in donor.rows}
        for row in output.rows:
            other = rows.get(period_of(row))
            if other is None:
                continue
            original = copy.deepcopy(row)
            updates = {}
            for name in sorted(fields):
                if (
                    not _valid(getattr(original, name))
                    and _valid(getattr(other, name))
                    and _compatible(output, donor, period_of(row), name, period_of, row_compatible)
                    and _dependency_safe(
                        output, donor, original, other, name, dependencies, period_of, row_compatible,
                    )
                ):
                    updates[name] = getattr(other, name)
            for name, value in updates.items():
                setattr(row, name, value)
                supplements.setdefault(period_of(row), {})[name] = donor.source
                if apply_metadata is not None:
                    apply_metadata(row, name, other)
    return Selection(
        output,
        _complete(output, required, required_periods, completeness, period_of),
        supplements,
    )
