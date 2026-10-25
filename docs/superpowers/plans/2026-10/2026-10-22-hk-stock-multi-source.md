# 港股个股数据多源备用框架实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 抽出与数据类型无关的整包选择核心，让 A 股财务门面行为不变，并把港股财务接入同一套完整包/严格等效补位规则。

**Architecture:** 新增 `package_selection` 通用核心，不依赖 `FinancialData`；`financial_selection` 保留现有公开接口，作为财务门面；港股新增策略模块，东财以单源 provider 接入并标注来源。本轮不新增真实港股备用源。

**Tech Stack:** Python 3.11+、Pydantic v2、pytest、Ruff、Pyright。

---

## 文件结构

- 新建 `src/data/package_selection.py`：通用整包选择、位置证据、补位规则。
- 修改 `src/data/financial_selection.py`：财务门面，保留 `NUMERIC_FIELDS`、`FinancialPackage`、`FinancialSelection`、`select_financial`、`Position`。
- 新建 `src/data/hk_financial_policy.py`：港股完整性、依赖组、来源标注。
- 修改 `src/data/akshare.py`：保留东财实现，新增港股选择器接线。
- 修改 `src/report/quality.py`：把财务来源/说明展示条件扩展到港股。
- 新建测试：
  - `tests/data/test_package_selection.py`
  - `tests/data/test_hk_financial_policy.py`
  - `tests/data/test_hk_financial_selection.py`
- 修改测试：
  - `tests/report/test_hk_live_report.py`
  - 同主题计划文档追加验收记录。

工作树：`D:/code/stock_robot/tmp/worktrees/hk-multi-source`，分支 `codex/hk-multi-source`。所有命令在该工作树执行。

---

### Task 1: 抽取通用整包选择核心

**Files:**
- Create: `src/data/package_selection.py`
- Modify: `src/data/financial_selection.py`
- Test: `tests/data/test_package_selection.py`

- [ ] **Step 1: 写通用核心的失败测试**

创建 `tests/data/test_package_selection.py`：

```python
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
        apply_metadata=lambda row, name, other: seen.append((name, other.source)),
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
```

- [ ] **Step 2: 运行测试，确认因模块不存在而失败**

```bash
cd D:/code/stock_robot/tmp/worktrees/hk-multi-source
RUN="tmp/pytest/package-core-red-$$-$RANDOM"
mkdir -p "$RUN/basetemp"
PYTHONPATH=D:/code/stock_robot/tmp/worktrees/hk-multi-source/src \
  D:/code/stock_robot/.venv/Scripts/python.exe -m pytest tests/data/test_package_selection.py -q \
  --basetemp="$RUN/basetemp" -o cache_dir="$RUN/cache"
```

预期：`ModuleNotFoundError: No module named 'data.package_selection'`，测试收集失败。

- [ ] **Step 3: 实现通用核心**

创建 `src/data/package_selection.py`：

```python
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


def _row_has_facts(row: RowT, fields: frozenset[str]) -> bool:
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
```

- [ ] **Step 4: 运行通用核心测试**

```bash
cd D:/code/stock_robot/tmp/worktrees/hk-multi-source
RUN="tmp/pytest/package-core-green-$$-$RANDOM"
mkdir -p "$RUN/basetemp"
PYTHONPATH=D:/code/stock_robot/tmp/worktrees/hk-multi-source/src \
  D:/code/stock_robot/.venv/Scripts/python.exe -m pytest tests/data/test_package_selection.py -q \
  --basetemp="$RUN/basetemp" -o cache_dir="$RUN/cache"
```

预期：全部通过（约 13 项）。

- [ ] **Step 5: 把财务选择模块改为门面**

用以下内容替换 `src/data/financial_selection.py`：

```python
"""财务包选择门面：复用通用整包规则，保留既有 A 股接口。"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

from data.package_selection import (
    Position,
    Selection,
    SourcePackage,
    select_package,
)
from data.schemas import FinancialData

NUMERIC_FIELDS = frozenset({
    "revenue", "net_profit", "deducted_net_profit", "total_assets", "total_equity",
    "common_equity", "operating_cash_flow", "operating_cash_flow_per_share",
    "roe", "gross_margin", "basic_eps",
})


@dataclass
class FinancialPackage(SourcePackage[FinancialData]):
    pass


@dataclass
class FinancialSelection(Selection[FinancialData]):
    pass


def _valid(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _row_compatible(row: FinancialData, position: Position, name: str) -> bool:
    if (
        (row.report_currency and row.report_currency != position.currency)
        or (position.basis != "instant" and row.period_start is not None
            and row.period_start != position.start)
        or (position.basis != "instant" and row.period_basis != "unknown"
            and row.period_basis != position.basis)
        or (name == "operating_cash_flow" and position.calculation not in {"reported", "estimated"})
        or (name == "operating_cash_flow" and row.cash_flow_period_start is not None
            and row.cash_flow_period_start != position.start)
        or (name == "operating_cash_flow" and _valid(row.operating_cash_flow)
            and row.cash_flow_basis != position.calculation)
    ):
        return False
    return True


def _apply_metadata(row: FinancialData, name: str, other: FinancialData) -> None:
    if name == "operating_cash_flow":
        row.cash_flow_basis = other.cash_flow_basis
        row.cash_flow_period_start = other.cash_flow_period_start
    elif name == "gross_margin":
        row.gross_margin_basis = other.gross_margin_basis


def _as_financial(package: SourcePackage[FinancialData]) -> FinancialPackage:
    if isinstance(package, FinancialPackage):
        return package
    return FinancialPackage(package.source, package.rows, package.positions)


def _wrap_completeness(
    completeness: Callable[[FinancialPackage], bool],
) -> Callable[[SourcePackage[FinancialData]], bool]:
    def wrapped(package: SourcePackage[FinancialData]) -> bool:
        return completeness(_as_financial(package))
    return wrapped


def select_financial(
    providers: Sequence[tuple[str, Callable[[], FinancialPackage]]], *,
    symbol: str,
    required: frozenset[str],
    required_periods: frozenset[date] = frozenset(),
    completeness: Callable[[FinancialPackage], bool] | None = None,
    dependencies: Sequence[frozenset[str]] = (),
    on_error: Callable[[str, Exception], None] | None = None,
) -> FinancialSelection:
    result = select_package(
        providers,
        entity=symbol,
        fields=NUMERIC_FIELDS,
        entity_of=lambda row: row.symbol,
        period_of=lambda row: row.fiscal_quarter,
        required=required,
        required_periods=required_periods,
        completeness=_wrap_completeness(completeness) if completeness is not None else None,
        dependencies=dependencies,
        row_compatible=_row_compatible,
        apply_metadata=_apply_metadata,
        on_error=on_error,
    )
    return FinancialSelection(
        package=_as_financial(result.package) if result.package is not None else None,
        complete=result.complete,
        supplements=result.supplements,
    )
```

- [ ] **Step 6: 运行 A 股既有选包、备用、缓存测试**

```bash
cd D:/code/stock_robot/tmp/worktrees/hk-multi-source
RUN="tmp/pytest/financial-facade-$$-$RANDOM"
mkdir -p "$RUN/basetemp"
PYTHONPATH=D:/code/stock_robot/tmp/worktrees/hk-multi-source/src \
  D:/code/stock_robot/.venv/Scripts/python.exe -m pytest \
  tests/data/test_financial_selection.py \
  tests/data/test_a_financial_fallback.py \
  tests/data/test_a_financial_sources.py \
  tests/core/test_financial_cache_facts.py -q \
  --basetemp="$RUN/basetemp" -o cache_dir="$RUN/cache"
```

预期：全部通过，且没有修改这些测试文件。

- [ ] **Step 7: 单文件检查**

```bash
D:/code/stock_robot/.venv/Scripts/ruff.exe check src/data/package_selection.py src/data/financial_selection.py
D:/code/stock_robot/.venv/Scripts/pyright.exe src/data/package_selection.py src/data/financial_selection.py
```

预期：Ruff 无告警；Pyright `0 errors, 0 warnings`。若 Pyright 报告 `FinancialPackage` 与通用类型不兼容，按 Step 5 的 `Sequence` 与 `_wrap_completeness` 实现核对，不改成 `list`，也不忽略类型错误。

- [ ] **Step 8: 提交第一阶段**

```bash
cd D:/code/stock_robot/tmp/worktrees/hk-multi-source
git add src/data/package_selection.py src/data/financial_selection.py tests/data/test_package_selection.py
git diff --cached --check
git commit -m "refactor(数据层): 抽取通用财务包选择核心"
```

预期：一笔本地提交；不推送。

---

### Task 2: 港股财务策略与接线

**Files:**
- Create: `src/data/hk_financial_policy.py`
- Modify: `src/data/akshare.py`
- Modify: `src/report/quality.py`
- Test: `tests/data/test_hk_financial_policy.py`
- Test: `tests/data/test_hk_financial_selection.py`
- Test: `tests/report/test_hk_live_report.py`

- [ ] **Step 1: 写港股策略失败测试**

创建 `tests/data/test_hk_financial_policy.py`：

```python
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
```

- [ ] **Step 2: 运行策略测试，确认模块不存在**

```bash
cd D:/code/stock_robot/tmp/worktrees/hk-multi-source
RUN="tmp/pytest/hk-policy-red-$$-$RANDOM"
mkdir -p "$RUN/basetemp"
PYTHONPATH=D:/code/stock_robot/tmp/worktrees/hk-multi-source/src \
  D:/code/stock_robot/.venv/Scripts/python.exe -m pytest tests/data/test_hk_financial_policy.py -q \
  --basetemp="$RUN/basetemp" -o cache_dir="$RUN/cache"
```

预期：`ModuleNotFoundError: No module named 'data.hk_financial_policy'`。

- [ ] **Step 3: 实现港股策略**

创建 `src/data/hk_financial_policy.py`：

```python
"""港股财务完整性与来源标注；沿用现有分析消费字段。"""

from dataclasses import asdict
from datetime import date

from data.financial_selection import (
    NUMERIC_FIELDS,
    FinancialPackage,
    FinancialSelection,
)
from data.schemas import FinancialData
from utils.numbers import is_finite_number

# 港股分析实际展示/比较的最新快照字段；ROE、扣非、毛利率、普通股权益为可选。
HK_REQUIRED_FIELDS = frozenset({
    "revenue", "net_profit", "total_assets", "total_equity", "basic_eps",
})

HK_DEPENDENCIES = (
    frozenset({"revenue", "net_profit"}),
    frozenset({"total_assets", "total_equity"}),
    frozenset({"net_profit", "basic_eps"}),
)


def package_complete(package: FinancialPackage) -> bool:
    """沿用现有分析消费字段；不把港股评为 A 股的完整财报包。"""
    rows = package.rows
    if sum(
        any(is_finite_number(getattr(row, name)) for name in NUMERIC_FIELDS)
        for row in rows
    ) < 2:
        return False
    latest = max(rows, key=lambda row: row.fiscal_quarter)
    return (
        all(is_finite_number(getattr(latest, name)) for name in HK_REQUIRED_FIELDS)
        and (
            is_finite_number(latest.operating_cash_flow)
            or is_finite_number(latest.operating_cash_flow_per_share)
        )
    )


def annotate_selection(selection: FinancialSelection) -> list[FinancialData]:
    """为缓存、报告与模型事实保存一致的逐期字段来源。"""
    package = selection.package
    if package is None:
        return []
    providers = sorted({
        source for fields in selection.supplements.values() for source in fields.values()
    })
    source_label = package.source + (
        "；等效补位：" + "、".join(providers) if providers else ""
    )
    note = (
        "完整来源包" if selection.complete
        else f"{package.source}未能提供完整财务包，保留可用事实；未知口径不补位"
    )
    if providers and selection.complete:
        note = "字段已按等效口径补齐，非单一来源完整包"
    if package.source != "东方财富港股财报":
        note = "采用备用来源；" + note
    if providers:
        note = "按逐期等效口径补位；" + note
    for row in package.rows:
        row.financial_source = package.source
        row.field_sources = {
            name: selection.supplements.get(row.fiscal_quarter, {}).get(name, package.source)
            for name in NUMERIC_FIELDS if is_finite_number(getattr(row, name))
        }
        row.field_positions = {
            name: {
                key: value.isoformat() if isinstance(value, date) else str(value or "")
                for key, value in asdict(position).items()
            }
            for name, position in package.positions.get(row.fiscal_quarter, {}).items()
        }
        row.financial_selection_status = "complete" if selection.complete else "partial"
        row.financial_selection_note = note
        row._data_source = source_label
    return package.rows
```

- [ ] **Step 4: 运行港股策略测试**

```bash
cd D:/code/stock_robot/tmp/worktrees/hk-multi-source
RUN="tmp/pytest/hk-policy-green-$$-$RANDOM"
mkdir -p "$RUN/basetemp"
PYTHONPATH=D:/code/stock_robot/tmp/worktrees/hk-multi-source/src \
  D:/code/stock_robot/.venv/Scripts/python.exe -m pytest tests/data/test_hk_financial_policy.py -q \
  --basetemp="$RUN/basetemp" -o cache_dir="$RUN/cache"
```

预期：全部通过（约 9 项）。

- [ ] **Step 5: 写港股接线失败测试**

创建 `tests/data/test_hk_financial_selection.py`：

```python
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
```

- [ ] **Step 6: 运行接线测试，确认失败**

```bash
cd D:/code/stock_robot/tmp/worktrees/hk-multi-source
RUN="tmp/pytest/hk-wiring-red-$$-$RANDOM"
mkdir -p "$RUN/basetemp"
PYTHONPATH=D:/code/stock_robot/tmp/worktrees/hk-multi-source/src \
  D:/code/stock_robot/.venv/Scripts/python.exe -m pytest tests/data/test_hk_financial_selection.py -q \
  --basetemp="$RUN/basetemp" -o cache_dir="$RUN/cache"
```

预期：失败，`financial_source` 为空或状态为 `unknown`，因为接线尚未实现。

- [ ] **Step 7: 接入港股财务选择器**

修改 `src/data/akshare.py`：把现有 `_fetch_hk_financial` 方法整体改名为 `_fetch_hk_eastmoney_financial`（方法体不变），并在它前面新增：

```python
    def _fetch_hk_financial(self, symbol: str) -> list[FinancialData]:
        from data.financial_selection import FinancialPackage, select_financial
        from data.hk_financial_policy import (
            HK_DEPENDENCIES,
            HK_REQUIRED_FIELDS,
            annotate_selection,
            package_complete,
        )

        selection = select_financial(
            [("东方财富港股财报", lambda: FinancialPackage(
                "东方财富港股财报", self._fetch_hk_eastmoney_financial(symbol), {},
            ))],
            symbol=symbol,
            required=HK_REQUIRED_FIELDS,
            completeness=package_complete,
            dependencies=HK_DEPENDENCIES,
            on_error=self._append_collection_error,
        )
        return annotate_selection(selection)

    def _fetch_hk_eastmoney_financial(self, symbol: str) -> list[FinancialData]:
```

注意：新方法必须放在原 `_fetch_hk_financial` 的位置；原方法名前加上 `_eastmoney`，方法体、数值和请求次数均不改。

- [ ] **Step 8: 扩展报告来源展示到港股**

修改 `src/report/quality.py` 中的判断：

```python
        if key == "financial" and ctx.market in {"a-shares", "hk"} and ctx.financial_data:
```

其余字段标签、展示顺序和 A 股分支不变。

- [ ] **Step 9: 增加港股报告来源可见性测试**

在 `tests/report/test_hk_live_report.py` 顶部补充导入：

```python
from datetime import date

from data.schemas import FinancialData
```

并在文件末尾添加：

```python
def test_hk_financial_source_and_selection_note_are_visible():
    ctx = hk_context()
    ctx.financial_data = [FinancialData(
        symbol="00700.HK",
        fiscal_quarter=date(2026, 6, 30),
        revenue=1.0,
        financial_source="东方财富港股财报",
        financial_selection_status="complete",
        financial_selection_note="完整来源包",
    )]
    result = AnalysisResult(
        dimension="financial", status="partial", summary="港股报告期",
        metrics={"revenue": 1.0, "latest_quarter": "2026-06-30"},
    )
    quality = build_data_quality(ctx, [result])
    financial = next(item for item in quality["dimensions"] if item["key"] == "financial")
    fields = {field["label"]: field["value"] for field in financial["fields"]}
    assert fields["数据来源"] == "东方财富港股财报"
    assert fields["财务包说明"] == "完整来源包"
```

- [ ] **Step 10: 运行港股与报告测试**

```bash
cd D:/code/stock_robot/tmp/worktrees/hk-multi-source
RUN="tmp/pytest/hk-wiring-green-$$-$RANDOM"
mkdir -p "$RUN/basetemp"
PYTHONPATH=D:/code/stock_robot/tmp/worktrees/hk-multi-source/src \
  D:/code/stock_robot/.venv/Scripts/python.exe -m pytest \
  tests/data/test_hk_financial_policy.py \
  tests/data/test_hk_financial_selection.py \
  tests/data/test_hk_live_data.py \
  tests/data/test_hk_report_directory.py \
  tests/data/test_hk_gross_margin.py \
  tests/report/test_hk_live_report.py -q \
  --basetemp="$RUN/basetemp" -o cache_dir="$RUN/cache"
```

预期：全部通过。若 `test_hk_live_data.py` 中 5 期样本的最新期 OCF 或必填字段缺失，应如实得到 `partial`，不能为了让测试变绿而放宽完整性；先核对样本字段，再决定测试断言。

- [ ] **Step 11: 单文件检查**

```bash
D:/code/stock_robot/.venv/Scripts/ruff.exe check \
  src/data/hk_financial_policy.py src/data/akshare.py src/report/quality.py
D:/code/stock_robot/.venv/Scripts/pyright.exe \
  src/data/hk_financial_policy.py src/data/akshare.py src/report/quality.py
```

预期：Ruff 无告警；Pyright `0 errors, 0 warnings`。

- [ ] **Step 12: 提交第二阶段**

```bash
cd D:/code/stock_robot/tmp/worktrees/hk-multi-source
git add src/data/hk_financial_policy.py src/data/akshare.py src/report/quality.py \
  tests/data/test_hk_financial_policy.py tests/data/test_hk_financial_selection.py \
  tests/report/test_hk_live_report.py
git diff --cached --check
git commit -m "feat(港股财报): 接入通用整包选择与来源标注"
```

预期：一笔本地提交；不推送。

---

### Task 3: 真实验收、记录与合并前门禁

**Files:**
- Modify: `docs/superpowers/plans/2026-10/2026-10-22-hk-stock-multi-source.md`

- [ ] **Step 1: 运行 Changed 门禁**

```bash
cd D:/code/stock_robot/tmp/worktrees/hk-multi-source
powershell.exe -NoProfile -ExecutionPolicy Bypass \
  -File scripts/verify.ps1 -Scope Changed -VenvPath D:/code/stock_robot/.venv
```

预期：Ruff、Pyright 通过；映射到的数据/报告测试全部通过。记录实际通过数量与耗时。

- [ ] **Step 2: 建立隔离的真实服务目录**

```bash
cd D:/code/stock_robot/tmp/worktrees/hk-multi-source
mkdir -p tmp/acceptance/hk-multi-source-live/.stock_robot
```

写入 `tmp/acceptance/hk-multi-source-live/.stock_robot/config.yaml`：

```yaml
llm:
  enabled: false
  api_key: ''
data:
  disclaimer_accepted: true
push:
  enabled: false
radar:
  collector:
    enabled: false
    hour: 18
    minute: 30
```

- [ ] **Step 3: 正常 Pipeline 刷新 00700.HK 财务并核对元信息**

创建 `tmp/acceptance/hk-multi-source-refresh.py`：

```python
"""用正常 Pipeline 刷新港股财务缓存，不替换数据源。"""

from pathlib import Path

from core.pipeline import Pipeline
from core.registry import Registry
from data.akshare import AkShareAdapter
from utils.config import Config

state = Path(__file__).resolve().parent / "hk-multi-source-live" / ".stock_robot"
registry = Registry()
registry.register_data_source(AkShareAdapter())
pipeline = Pipeline(
    registry=registry,
    config=Config(config_dir=state),
    llm_enabled=False,
)
ctx = pipeline.collect(
    "00700.HK", "腾讯控股", market="hk",
    refresh_cache=True, data_types=["financial"],
)
rows = ctx.financial_data or []
assert rows, "未取得港股财务数据"
latest = max(rows, key=lambda row: row.fiscal_quarter)
print("periods", len(rows), "latest", latest.fiscal_quarter)
print("source", latest.financial_source)
print("status", latest.financial_selection_status)
print("note", latest.financial_selection_note)
print("revenue", latest.revenue, "ocf", latest.operating_cash_flow)
assert latest.financial_source == "东方财富港股财报"
assert latest.financial_selection_status in {"complete", "partial"}
assert latest.financial_selection_note
```

运行：

```bash
cd D:/code/stock_robot/tmp/worktrees/hk-multi-source
PYTHONPATH=D:/code/stock_robot/tmp/worktrees/hk-multi-source/src \
  D:/code/stock_robot/.venv/Scripts/python.exe tmp/acceptance/hk-multi-source-refresh.py
```

预期：输出 ≥2 个报告期，来源为 `东方财富港股财报`，状态为 `complete` 或 `partial`，说明非空。若网络失败，保留安全错误输出，不能把失败写成通过；不得用 Mock 冒充实采。

- [ ] **Step 4: 启动真实服务**

```bash
cd D:/code/stock_robot/tmp/worktrees/hk-multi-source/tmp/acceptance/hk-multi-source-live
PYTHONPATH=D:/code/stock_robot/tmp/worktrees/hk-multi-source/src \
  D:/code/stock_robot/.venv/Scripts/stock-robot.exe run --host 127.0.0.1 --port 8765
```

在另一个终端确认 `/api/v1/config` 返回 200 后再继续。

- [ ] **Step 5: 浏览器验收港股报告**

用真实 Edge + CDP（端口 9233，headless 也可以；不使用 Mock 页面）。创建 `tmp/acceptance/hk-multi-source-browser.py`：

```python
"""通过真实 Edge CDP 验收港股报告来源与布局。"""

import base64
import json
import time
from pathlib import Path
from urllib.request import urlopen

import websocket

SHOTS = Path("D:/code/stock_robot/tmp/acceptance/hk-multi-source")
SHOTS.mkdir(parents=True, exist_ok=True)
tabs = json.load(urlopen("http://127.0.0.1:9233/json"))
page = next(item for item in tabs if item["type"] == "page")
ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=90, suppress_origin=True)
seq = 0


def call(method, params=None):
    global seq
    seq += 1
    ws.send(json.dumps({"id": seq, "method": method, "params": params or {}}))
    while True:
        message = json.loads(ws.recv())
        if message.get("id") == seq:
            if "error" in message:
                raise RuntimeError(message["error"])
            return message.get("result", {})


def evaluate(script):
    result = call("Runtime.evaluate", {
        "expression": script, "returnByValue": True, "awaitPromise": True,
    })
    if "exceptionDetails" in result:
        raise RuntimeError(result["exceptionDetails"])
    return result["result"].get("value")


def until(script, timeout=180):
    end = time.time() + timeout
    while time.time() < end:
        value = evaluate(script)
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError(f"等待超时: {script}")


def shot(name):
    data = call("Page.captureScreenshot", {
        "format": "jpeg", "quality": 85, "captureBeyondViewport": False,
    })
    (SHOTS / name).write_bytes(base64.b64decode(data["data"]))


call("Page.enable")
call("Runtime.enable")
call("Page.navigate", {"url": "http://127.0.0.1:8765/#report"})
until("document.readyState === 'complete' && document.querySelector('#stockInput') !== null")
evaluate("""(() => {
  const input = document.querySelector('#stockInput');
  input.value = '00700.HK';
  input.dispatchEvent(new Event('input', { bubbles: true }));
  document.querySelector('#stockBtn').click();
  return true;
})()""")
until("document.querySelector('#page-report-quality') !== null", timeout=180)
time.sleep(1)

result = evaluate("""(() => {
  const quality = document.querySelector('#page-report-quality');
  return {
    qualityText: quality?.innerText || '',
    sourceText: [...document.querySelectorAll('#page-report-quality .report-quality-source')]
      .map(item => item.innerText),
    hasError: document.body.innerText.includes('分析失败'),
    sections: [...document.querySelectorAll('.stock-report section')].map(item => item.id),
  };
})()""")
assert "东方财富港股财报" in result["qualityText"], result["qualityText"]
assert "完整来源包" in result["qualityText"] or "未能提供完整财务包" in result["qualityText"]
assert not result["hasError"]

metrics = {}
for width, height, label in [(1280, 900, "wide"), (390, 844, "390")]:
    call("Emulation.setDeviceMetricsOverride", {
        "width": width, "height": height, "deviceScaleFactor": 1, "mobile": width == 390,
    })
    time.sleep(0.5)
    layout = evaluate("""(() => ({
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
      card: document.querySelector('.stock-report')?.getBoundingClientRect().toJSON() || null,
    }))()""")
    assert layout["scrollWidth"] <= layout["clientWidth"] + 1, (label, layout)
    metrics[label] = layout
    evaluate("document.querySelector('#page-report-quality').scrollIntoView({block:'start'}); true")
    time.sleep(0.5)
    shot(f"quality-{label}.jpg")

print(json.dumps({"result": result, "metrics": metrics}, ensure_ascii=False, indent=2))
ws.close()
```

启动 Edge：

```bash
"/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" \
  --headless=new --remote-debugging-port=9233 \
  --user-data-dir=D:/code/stock_robot/tmp/acceptance/hk-multi-source/edge-profile \
  --window-size=1280,900 --disable-gpu --no-first-run --no-default-browser-check about:blank
```

运行浏览器脚本：

```bash
D:/code/stock_robot/.venv/Scripts/python.exe \
  D:/code/stock_robot/tmp/worktrees/hk-multi-source/tmp/acceptance/hk-multi-source-browser.py
```

预期：来源文本包含 `东方财富港股财报`，说明为 `完整来源包` 或明确的 `partial` 说明；1280px 与 390px 均无水平溢出；截图保存在 `D:/code/stock_robot/tmp/acceptance/hk-multi-source/`。查看截图确认无遮挡；若截图调用超时，改用 jpeg 并提高 CDP 超时，不能跳过目视证据。

- [ ] **Step 6: 核对下载 Markdown 正文**

服务保持运行，执行：

```python
import json
import urllib.request
from pathlib import Path

base = "http://127.0.0.1:8765"
payload = json.load(urllib.request.urlopen(f"{base}/api/v1/reports?type=stock&query=00700.HK"))
assert payload["reports"], "报告列表为空"
report = payload["reports"][0]
body = urllib.request.urlopen(f"{base}/api/v1/reports/{report['id']}/download").read().decode("utf-8")
assert "东方财富港股财报" in body
assert "完整来源包" in body or "未能提供完整财务包" in body
Path("tmp/acceptance/hk-multi-source-downloaded.md").write_text(body, encoding="utf-8")
print(report["id"], len(body))
```

预期：HTTP 200，正文包含来源与选包说明。保存正文到 `tmp/acceptance/hk-multi-source-downloaded.md`；这是下载接口正文核验，不冒称浏览器落盘。

- [ ] **Step 7: 把验收结果写入同主题计划**

在 `docs/superpowers/plans/2026-10/2026-10-22-hk-stock-multi-source.md` 追加“实施与验收记录”小节，写入：

- Changed 门禁实际通过数量、耗时；
- 真实刷新得到的报告期数、最新期、来源、状态、说明、关键金额；
- 浏览器宽/窄屏 `clientWidth` / `scrollWidth`、截图路径；
- 下载 Markdown 字符数与来源匹配结果；
- 未验证项：本轮无真实备用源，东财为唯一完整来源；未做云端/QQ/付费模型；未验证其他港股标的。

不得写“港股财务已可完全替换”；只能写“通用框架与东财单源接线已验证，备用源接入待后续”。

- [ ] **Step 8: 停止服务与浏览器，检查工作区**

停止服务和 Edge；确认：

```bash
cd D:/code/stock_robot/tmp/worktrees/hk-multi-source
git status --short
```

预期：只有计划文档（以及必要测试）改动；缓存、截图、探针、浏览器 profile 均不出现。若行业映射或其他运行产物被改写，先还原并记录，不混入提交。

- [ ] **Step 9: 运行 Full 门禁**

```bash
cd D:/code/stock_robot/tmp/worktrees/hk-multi-source
powershell.exe -NoProfile -ExecutionPolicy Bypass \
  -File scripts/verify.ps1 -Scope Full -VenvPath D:/code/stock_robot/.venv
```

预期：Ruff 全通过、Pyright 0 错误/警告、pytest 全通过。若失败，先保留首个完整异常并区分本次改动与既有失败；修复后重跑失败范围，最终重跑 Full 取得完整证据。

- [ ] **Step 10: 提交验收记录**

```bash
cd D:/code/stock_robot/tmp/worktrees/hk-multi-source
git add docs/superpowers/plans/2026-10/2026-10-22-hk-stock-multi-source.md
git diff --cached --check
git commit -m "docs(港股): 记录多源框架实施与真实验收"
```

预期：一笔本地提交；不推送、不合并、不发布。

---

## 自审清单

- [ ] 通用核心不 import `data.schemas`、`FinancialData` 或任何市场模块。
- [ ] A 股 `test_financial_selection.py`、备用源、缓存测试未修改且全部通过。
- [ ] 港股单源接线没有增加网络请求；`financial_statement` 仍为三次，目录一次。
- [ ] 港股完整包要求至少两期，且最新期满足营收/净利润/总资产/总权益/基本 EPS 与 OCF（或每股 OCF）。
- [ ] 部分包明确标记 `partial`，不用零值填充，不被旧期数门槛重标完整。
- [ ] 质量展示对 A 股与港股生效，A 股原有字段与顺序不变。
- [ ] 旧缓存保持 `unknown`，不批量迁移。
- [ ] 没有实现或声称任何真实港股备用源已可替换。
- [ ] 所有测试命令使用唯一 `basetemp` 与 `cache_dir`；成功后清理，失败产物保留并记录。
- [ ] 不推送、不合并、不发布。

## 实施与验收记录（2026-10-22）

### 已提交实现与相关检查

- 交接基线为 `codex/hk-multi-source` / `7a1cdd9`，接手时工作区干净。通用核心及 A 股财务门面已提交；港股策略、东财单源接线和来源展示亦已提交，原东财方法体保持不变，不增加请求或真实备用源。
- 通用核心 14 项、A 股选包/备用/源/缓存 144 项相关验证通过；港股指定六文件 143 项和补充受影响范围 144 项通过，规格/质量审查已完成。以上为交接前已执行记录，不冒称本轮重新运行。
- 交接前 Changed 门禁返回“未发现待验证的改动”，因为对应代码已经提交；不能将该返回值当作执行了测试。已完成的相关检查与以下 Full 记录分别列出。

### 真实港股验收（交接前完成，本轮核对留存证据）

- 正常 Pipeline 强制刷新 `00700.HK` 财务得到 **96 期**，最新期 `2026-06-30`，来源 `东方财富港股财报`，选包状态 `complete`，说明 `完整来源包`。最新营收 `396431000000.0`、经营现金流 `154061000000.0`，币种 `CNY`，报表口径 `东方财富港股财报-报告期-国际会计准则`。
- 使用项目真实启动路径和真实浏览器完成生成/展示：标题 `00700.HK`、11 个报告章节，无“分析失败”。数据覆盖中的财务仍为“部分数据”，这是原港股充足度/不推算滚动年值的规则，不是来源包不完整；同时显示 96 期、东财来源和“完整来源包”。
- 宽屏 1280px：`clientWidth=scrollWidth=1280`，报告卡 x=234、宽 980；窄屏 390px：`clientWidth=scrollWidth=390`，卡片 x=12、宽 366，无水平溢出。截图分别为 `D:/code/stock_robot/tmp/acceptance/hk-multi-source/quality-wide.jpg` 和 `quality-390.jpg`；本轮已读取留存截图，确认来源与说明可见，窄屏正常换行。没有重新启动浏览器或绕过工具策略。
- 下载报告 id 为 `c3RvY2svMDA3MDAuSEsvMjAyNi0xMC8wMDcwMC5IS18yMDI2MTAyMl8xNTUxNDgubWQ`。下载正文 **6,157 个字符**，含来源与“完整来源包”，保存在忽略的 `tmp/acceptance/hk-multi-source-downloaded.md`。本轮复核保存文件：CRLF 原样解码 6,330 字符、173 个 CRLF，按文本读取归一化换行后 6,157 字符，与交接下载结果一致，不是报告内容变化。
- 刷新/浏览器探针保留在忽略的 `tmp/acceptance/hk-multi-source-refresh.py`、`hk-multi-source-browser.py`。交接服务、浏览器已停止，本轮不重复真实采集、不触碰云端或付费模型。

### Full 阻塞与用户选择的最小修复

- 修复前两次 Full 均为 `2268 passed, 4 skipped, 1 failed`，分别失败于 `test_artifacts_persist_benchmark_configuration_and_metrics`、`test_artifacts_persist_replay_configuration_and_warnings`。异常是 `src/radar/artifacts.py` 原子目录 `os.replace` 的 `PermissionError [WinError 5]`。本分支此前未修改雷达；交接中独立复跑 20 次失败 4 次，日志留在 `D:/code/stock_robot/tmp/acceptance/hk-multi-source-full.log`、`hk-multi-source-full-rerun.log`，失败目录保留诊断。
- 用户明确选择“最小重试修复”后，才修改雷达生产代码：仅将最终 `os.replace` 包装为 `_replace_with_retry`，最多 **3 次尝试（含首次）**，两次重试固定各 **50 毫秒**，最多额外等待 100 毫秒。仅捕获 `PermissionError`，记录 debug 后短暂等待；最后一次失败原样抛出。其他异常立即抛出，原子替换、目标目录语义及外层清理逻辑均未改变；没有修改 ACL、杀软或文件系统设置。占用进程未被精确归因，本修复是有界缓解，不承诺消除所有持续权限错误。
- 新增七个参数化边界用例：首次成功、第一次/第二次失败后恢复、持续失败到上限且保留原异常实例、非权限异常不重试。先因 helper 不存在而收集失败，随后 `tests/radar/test_artifacts.py` **10 项通过**；两文件 Ruff 与 Pyright **0 错误、0 警告**。
- 同一雷达测试文件再以 20 个独立 pytest 进程、独立 basetemp/cache 目录顺序运行，**20/20 轮全部通过**（每轮 10 项），未再复现原子替换失败。Full 完成后，针对已验证位于本工作树 tmp/pytest 内的重复验证/单轮绿测目录发起清理，命令被环境策略拒绝；保留两目录，不重试删除或修改权限，不影响验证结论与提交。失败诊断目录亦保留。初始红/绿测试出现过 pytest 缓存自己的 WinError 5 警告；不把缓存警告当作业务失败，也不因此扩展生产修复范围。
- 本次小修复由主会话直接实施与审查 diff，未派发无关重构。真实港股验收在财务代码未变的情况下复用，不以雷达重试为由重复浏览器、采集或付费验证。

### 修复后 Full 与交付边界

- 执行命令：`powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/verify.ps1 -Scope Full -VenvPath D:/code/stock_robot/.venv`；完整输出保存在忽略的 `tmp/acceptance/hk-multi-source-full-after-retry.log`。**Full 通过**：Ruff 全通过，Pyright `0 errors, 0 warnings, 0 informations`，pytest **2276 passed, 4 skipped, 12 warnings in 281.67s**，退出码 0。警告为既有 Starlette/httpx 与 LangGraph 弃用提示；本次无雷达产物权限失败。Full 自己的临时目录已由脚本清理。
- 验证后仅回填本计划记录，生产源码和测试未再修改；提交前复用上述同状态检查并执行 `git diff --cached --check`，不因整理文档再次运行 Full。主仓用户已有的队列计划改动保持原样，未暂存或修改。
- 本轮仅收敛用户批准的雷达权限重试、回归和本主题验收记录，不改港股财务事实、原 A 股门面、行情/估值、评分、版本或插件。
- 仍无真实港股财务备用源，东财是本轮唯一完整来源；只验证了 `00700.HK`，不能声称其他港股或全市场均可完全接替。无云端、QQ、付费模型验收；仅本地提交，不推送、不合并、不发布。

### 首个真实备用源的限量调查（2026-10-22，尚未接入）

- 框架已按用户授权快进合入主仓 `dev`，双方 HEAD 为 `b4d2001`；未推送、发布或删除工作树。主仓已有队列计划改动未触碰。本节是合并后的独立候选调查，不改变上一节实现及 Full 结果。
- 目标仍是能独立提供最新完整财务包的来源，沿用至少两期及最新期营收、净利润、总资产、总权益、基本 EPS、OCF（或每股 OCF）的要求。不放宽门槛，不从东财补 EPS 后称为独立完整来源，不改 A 股、评分、主源算法或版本。
- 复用 `report-p0/tmp/acceptance` 既有调查：新浪腾讯/阿里三表存在旧期、汇丰现金流滞后；ETNET 腾讯/汇丰最新中期现金流缺失；同花顺公开财务入口返回 403。旧调查不是本轮重新采集，也不能据此推断所有其他接口均不存在。
- 新增限量公开读取与离线 JSON 检查探针，均在忽略的 `tmp/acceptance/`：`hk-candidate-probe.py`、`hk-cibo-inspect.py`、`hk-cibo-summary.py`；结果保存在 `tmp/acceptance/hk-candidate-validation/`。每次最多四个指定 URL，连接/读取超时 5/10 秒，无自动重试、登录、绕限或远端脚本执行，不写业务缓存。页面内 JSON 仅用 JSON 解码器读取，不执行 JavaScript。
- AAStocks 腾讯公开利润表/现金流入口 HTTP 200，但本次响应为无公司财务数值的模板（财务年份列表为空），未验证到可用数据。Yahoo 腾讯现金流公开入口一次请求 HTTP 429，停止请求，不把限流误判为源本身没有数据。
- cibo 三个公开页面均 HTTP 200，内嵌 `DATA` 有 annual/interim、三表及 earnings。腾讯年度 FY2021–FY2025、中期 H1 2025/H1 2026：最新中期 revenue `401243000000`、归母净利润 `114115000000`、总资产 `2159394000000`、总权益 `1222513000000`、OCF `154061000000`；未找到基本 EPS。腾讯收入与现有东财 `396431000000` 不同，尚未查清等效口径，不判定任一方必然错误或可以直接替换。
- 阿里年度 FY2022–FY2026，中期标签 H1 2026/H1 2027；末条 revenue `268953000000`、归母净利润 `10537000000`、总资产 `1962109000000`、总权益 `1106310000000`、OCF `22945000000`，同样未找到基本 EPS。不能仅凭标签 H1 把该条认定为规范半年累计值；会计年度与实际起止日、季度/半年长度须进一步核实，本轮不生成 Position。
- 汇丰年度 FY2021–FY2025、中期 H1 2025/H1 2026，未找到基本 EPS。页面标注 USD，但 FY2025 revenue `452823411200`、总资产 `22724349379200` 与 H1 2026 revenue `37742000000`、总资产 `3438161000000` 的量级存在疑点。未取得逐期单位/换汇链路，不擅自推断或实施币种换算，不宣称已证实数据错误。
- cibo 页面声明财务来自港交所年度/中期披露，但这是提供方声明，尚不足以证明每个字段的实际采集链路及相对东财的独立性。公开 API 文档和实取 `/api/` 目录均只列出招股书财务 `/api/stocks/{code}/prospectus`，未列持续财报接口；不能由此断言不存在其他接口，也不猜测私有端点。
- 使用条款明确网站数据供个人学习、研究及非商业使用，商业使用、复制或再分发需事先书面授权。API 无需认证与公开限额不等于获得插件再分发授权。技术和许可门槛任一未解决都不接入生产。公开参考：<https://cibo.hk/en/api-docs.html>、<https://cibo.hk/api/>、<https://cibo.hk/en/terms.html>；样本为 `/en/stock-financial-00700.html`、`09988`、`00005`。
- 结论：本轮候选均未通过完整备用源验收。没有实现第二港股财务 provider，没有宣称港股财务可完全互换；现有东财正常路径保持原样。后续应先选定有明确接口/使用许可且可补齐基本 EPS、逐期口径的路线，再形成接线方案并确认，不默认扩展成公告 PDF 抽取项目。
- 验证：探针命令均使用主仓 `.venv/Scripts/python.exe`，三样本离线摘要和公开目录核对退出码 0；调查遇到的 HTTP 429 单列为未通过，不当作成功。正式变更仅本计划，提交前执行 `git diff --check`；本轮未跑业务 pytest、Full、浏览器、云端或付费模型验证，因为生产源码未变。临时响应及脚本不提交。

### 文档化接口与许可筛选（2026-10-22，后续已明确仅免费）

- 用户同意继续筛选接口及使用许可明确的候选；本轮仍只读调查，不开账号、不接受协议、不付费、不调用未知凭据，不实施 provider。不把继续调查视为同意新增必填 Token 或付费部署依赖。
- Tushare 官方文档提供 `hk_income`、`hk_balancesheet`、`hk_cashflow`、`hk_fina_indicator`（文档编号 389/390/391/388），均要求单独申请“港股财报”权限。指标文档明确列出 `basic_eps`、`holder_profit`、`operate_income`、`per_netcash_operate`、报告类型及会计年度字段，因此比缺 EPS 的网页候选更值得评估，但这只是接口字段声明，不是腾讯/阿里/汇丰最新包通过验收。三表接口按财务科目名返回数值，不能未经语义核对直接按名称相似映射。
- Tushare 指标 `currency` 文档解释为港元，未说明能否代表每个财务金额的原始报告币种；利润表文档未列 currency。不得把这项直接当全部报表币种，也不能因此认定数据必然不合格。后续仍需实际响应、单位/期间口径、数据上游独立性和研报对外发送许可；本轮未使用 Token、未申请权限，未取得真实三样本响应，也未确认价格或再分发授权。参考：<https://tushare.pro/document/2?doc_id=388>、同站 `doc_id=389/390/391`。
- EODHD 官方 Fundamentals 文档有三表和 Earnings；但 `EarningsShare`、`epsActual` 的存在不等于同报告期 basic EPS，不能替代现有完整包门槛。官方维护的代码指南使用腾讯 `0700.HK`，与项目规范代码 `00700.HK` 长度不同，未来如接入需 provider 内转换，不改公共标的代码。参考：<https://eodhd.com/financial-apis/stock-etfs-fundamental-data-feeds>、<https://github.com/EodHistoricalData/eodhd-claude-skills/blob/main/skills/eodhd-api/references/general/stock-types-ticker-suffixes-guide.md>。
- EODHD 文档的公开 demo 仅六个指定代码，不含港股；没有用 demo 反复请求港股来验证已知的访问限制，没有拿美股 demo 当港股样本验收。普通价格页套餐按个人使用提供，商业使用需另行确认许可。该说明不等于认定每个插件用户都属于商业用户，也不等于研报自动发送已获许可。参考：<https://eodhd.com/financial-apis/commercial-vs-personal-license-use>。本轮不推荐购买、不报告未经确认的报价。
- 结论：两家属于需权限/许可确认的候选，不是已验收的免费完整备用源。继续真实验证前需要用户确定是否接受研究带账号/Token、可能付费的可选来源；若仅接受免费零配置，则保留现有框架及东财路径，不以降低完整性标准或默认建设公告 PDF 抽取系统替代这个选择。
- 本轮正式改动仍仅本实施计划，生产代码、测试、版本不变；执行 `git diff --check`，无需重复业务测试或 Full。调查记录与上节累积在同一主题文档中，尚未提交，不推送、不合并。

### 免费来源的等效补缺复核与调查收敛（2026-10-22）

- 用户明确拒绝付费来源，后续不再推进 Tushare/EODHD 申请、购买或接线。用户同意沿免费来源严格等效补缺方向继续验证：不是所有备用源都必须独立完整，原完整包门槛仅用于判断包状态，不作为排除部分来源的理由。此前调查中的完整性结论不应被解读为部分来源毫无价值。
- 本轮离线复用 `report-p0/tmp/acceptance/hk-source-comparison.json` 与原 ETNET 三样本响应，不重跑东财、模型或旧比较脚本；后者会回放旧分支适配器，不宜冒充当前分支验收。PowerShell `ConvertFrom-Json` 限定输出财务部分，退出码 0。数值一致仅用于发现潜在字段关系，不代表已取得完整 Position 证据。
- 腾讯 2026-06：ETNET `Shareholders' Funds` 为 `1135853000` 千人民币，即 `1135853000000`，对应保存模型的 `common_equity`，不是 `total_equity=1222513000000`。禁止把“股东资金”按名称直接补入总权益。ETNET `Turnover=401243000` 千人民币，与现有 `revenue=396431000000` 不同；归母净利润 `114115000` 千人民币与现有值一致，但单次一致不足以证明会计、修订、合并和期间起点完全等效，不生成补位授权。
- 阿里 2026-03：保存比较中 ETNET 收入、经营现金流与东财对应值一致，但归母净利润 `105904000000` 对 `103592000000`、股东资金 `1060886000000` 对 `1068731000000` 不一致；2025-03 两项亦有差异。不能把日期和币种一致当作足够证据，也不能用全表统一倍率修正。汇丰保存模型来自旧探针，存在旧版银行科目/币种限制，仅作历史诊断，不表述为当前生产代码仍如此。
- 本轮通过公开网页核对腾讯利润表、资产负债表及 ETNET 网站自身使用条款。条款 2.1/2.2 限制未经事先书面许可用于软件开发、脚本自动采集及缓存，4.4 另有限制复制、发送和存储内容。因此停止该源新增网络探针，不接入默认自动采集；没有绕过访问控制。参考：<https://www.etnet.com.hk/www/tc/aboutus/terms.php>、<https://www.etnet.com.hk/www/eng/stocks/realtime/quote_ci_pl.php?code=700>、同目录 `quote_ci_bs.php?code=700`。这是按公开条款筛选接入风险，不作一切财务事实均受专有权保护的法律推断。
- 已确认免费方向和部分来源价值，但当前检查的候选中仍没有同时通过技术等效证据与自动使用条件检查者。故本次交付是有边界的调查结论，不伪造第二 provider，不改变当前东财正常路径，也不默认升级成公告 PDF 抽取项目。现有 `positions={}` 保持诚实，真实跨源补位尚未验收。
- 正式改动收敛至本主题一份设计和一份实施计划，临时脚本/响应不提交。对累计调查执行 `git diff --cached --check`，仅文档无需业务测试、Full、浏览器或云端验收；按一个完整调查事项本地提交，不推送、不合并、不发布。

### SEC 免费结构化事实候选（2026-10-22，访问尚未通过）

- 按用户继续免费调查的要求，核查 SEC 官方 Company Facts 路线，仅针对同时在美国披露财务的阿里和汇丰，不预设覆盖腾讯或全港股。官方 API 文档说明无需认证/API Key；官方 FAQ 明确政府创建内容和 EDGAR 公开申报内容可免费访问和复用，自动访问须遵守 Fair Access。这比仅允许个人网页浏览的候选更适合继续调查，但并不代表已完成接线许可和技术验收。
- 官方文档：<https://www.sec.gov/search-filings/edgar-application-programming-interfaces>；访问要求：<https://www.sec.gov/about/developer-resources>；复用及请求头示例：<https://www.sec.gov/about/webmaster-frequently-asked-questions>。接口仅聚合标准 taxonomy、申报主体整体事实，不含任意公司自定义扩展标签；未来须核查基本 EPS 是否为普通股而非每 ADS、事实实际期间起止、币种单位、申报/修订 accession 与合并范围，不能凭美国挂牌身份推定可直接补入港股数据。
- 隔离探针 `tmp/acceptance/hk-sec-probe.py` 使用现有 requests、5/15 秒连接/读取超时、每公司一次请求、不重试、不写业务缓存。请求头明确为 `StockRobotResearch/0.1 (+https://github.com/Arrhenius401/stock_robot)`，未伪造浏览器、邮箱或用户身份。请求 `/api/xbrl/companyfacts/CIK0001577552.json`、`CIK0001089113.json` 均返回 **403**；保存状态、URL、时间和异常类型在忽略的 `tmp/acceptance/hk-sec-validation/`。未取得任何事实，不把脚本退出 0 当数据验证成功。
- 403 后停止网络探针，不换出口、不访问镜像或猜测替代路径规避限制。当前证据不能区分 IP/访问策略与请求声明原因，更不能证明源无财报。SEC 官方请求头示例带管理员联系邮箱；下一次规范验证前需用户指定可公开用于请求头的真实联系邮箱，不从 Git 配置/QQ/凭据推断，不未经授权发送私人邮箱。补邮箱不保证解除 403。
- 本轮仅更新原计划，未实现 provider、修改生产代码或改变版本，未运行业务测试/Full。提交前文档检查，调查待访问条件明确后继续收敛，不为本次未完成探针另作碎片提交。

### 用户要求的 403 单轮复查（2026-10-22 20:54，Asia/Shanghai）

- 用户明确要求再试曾返回 403 的来源。以原 URL、原请求标识复查同花顺腾讯/阿里/汇丰三个港股财务页及 SEC 阿里/汇丰两个 Company Facts，每入口仅一次请求；不换代理/IP，不伪装浏览器，不使用登录信息，不扩展其他入口。隔离脚本 `tmp/acceptance/hk-recheck-403.py` 设置连接/读取超时 5/10 秒、响应预算 4 MB 和读取循环时间检查，不是生产采集器，也不宣称具有严格进程级总超时。
- **5/5 仍为 HTTP 403**。同花顺响应 58–84 字节、正文为 `Nginx forbidden`，无财务表；SEC 两次均 4,819 字节，正文明确提示 `Your Request Originates from an Undeclared Automated Tool`，要求规范声明流量。SEC 本次错误比前轮仅有状态码的诊断更具体，但不能保证增加联系邮箱就会恢复。五次耗时分别约 0.52/0.55/0.50/1.66/1.53 秒，未发生超时或截断。
- 结果文件在忽略的 `tmp/acceptance/hk-403-recheck/results-*.json`，保留时间、状态、类型及有限错误摘要供核查；响应附带的网络地址不写入正式文档或提交。命令 `.venv/Scripts/python.exe tmp/acceptance/hk-recheck-403.py` 使用主仓虚拟环境，进程退出 0 仅表示探针完成，数据获取仍失败。无生产代码修改，无业务测试/Full，不把 403 表述为源无数据，不继续循环请求；本计划文档检查后与待收敛调查保留工作区，不新作碎片提交。

### 用户授权的云端一次性对比（2026-10-22 21:05，Asia/Shanghai）

- 用户明确要求在此前授权的云服务器复查。通过系统 OpenSSH 登录，在远端 Python 标准库内存执行仅五次公开入口请求；无需安装依赖或写远端脚本，不改配置、不重启 AstrBot、不加载插件、不调用模型，不使用代理。SSH 正常结束，退出 0；访问凭据未写入脚本或本计划。
- 同花顺腾讯/阿里/汇丰三个原 URL 均 **403**，每次 60 字节、`Nginx forbidden`，耗时 0.18/0.13/0.15 秒；SEC 阿里/汇丰两个原 URL 均 **403**，每次 4,819 字节、`undeclared automated tool`，耗时 0.78/1.09 秒。没有再次请求这些失败入口。
- 云端 urllib 请求 timeout=10 秒、读取上限 4,000,001 字节，未触及上限。SEC 研究工具标识与本机一致；同花顺云端使用 `python-requests/2.32.5` 标识，本机实际默认标识事后核实为 `python-requests/2.34.2`，且两端 HTTP 库、Accept-Encoding 与网络环境不同，故只报告两个环境同现拒绝，不能当成仅替换 IP 的严格控制实验，也不因此再扩大重试。
- 结论：问题不是仅在本机观察到，不能由此确认为两端 IP 均被封禁；同花顺拒绝原因仍未细分，SEC 继续明确报自动化流量识别问题。未取得财务数据，不等于来源无港股数据。生产代码和服务器运行状态未修改；仅本计划补证，执行 `git diff --check`，不运行业务测试/Full，不作碎片提交。

### AkShare 同花顺接口选型核对（2026-10-22）

- 用户确认先查现有 AkShare 实现与官方文档，仅在找到明确支持港股的入口后才发起小样本请求。本机主仓虚拟环境 AkShare **1.18.81**；读取已安装源码、当前官方股票文档（页面标示 1.19.1）及官方 GitHub `stock_fundamental/stock_finance_ths.py`，没有安装或升级依赖。
- A 股现用 `stock_financial_abstract_ths` 读取 `https://basic.10jqka.com.cn/new/{symbol}/finance.html` 的 `p#main`；资产负债表旧接口为同域 `/api/stock/finance/{symbol}_debt.json`。新版 `stock_financial_abstract_new_ths` 使用同域 `/basicapi/finance/index/v1/app_data/`，参数中包括市场代码。以上均不是此前测试的 `stockpage.10jqka.com.cn/basicweb/176/HK0700/finance.html`；已检查的官方实现中未找到该 `basicweb/176` 模板，故不能将旧页面 403 等同于 AkShare 港股专用接口失败。
- 本机导出港股财务函数为 `stock_financial_hk_report_em`、`stock_financial_hk_analysis_indicator_em`、`stock_hk_financial_indicator_em`，均为东财；未找到港股同花顺财务导出函数。官方股票文档港股三表同样列出东财 `stock_financial_hk_report_em`。这是已检查版本/文档的能力边界，不证明同花顺网站内部不存在其他港股服务。
- 新版同花顺私有市场判断仅列深 33、沪 17、北 151，不含港股分支。离线调用结果：`000063 -> 33`，`00700 -> TypeError`（第三方代码对短代码抛字符串导致），`HK0700 -> 0`，`00700.HK -> 0`，`00005.HK -> 33`。最后一项只是按前缀误判，不能作为港股已受支持；不调用真实业务接口验证这种错误市场请求，不猜测港股 market 值、不修补第三方依赖。
- 复核命令均用主仓 `.venv/Scripts/python.exe`。首次多代码探针遇短代码异常提前退出，不能把它记为其他代码通过；补做逐项捕获的离线探针后取得上述五条结果。导出函数 `rg` 精确查询无匹配的退出 1 表示未找到符号，不是测试失败；未运行业务 pytest 或 Full。当前官方文档同时说明新版替换旧版，但本轮不扩展为 A 股主源迁移任务。
- 结论：没有发现可以按官方支持直接验证的同花顺港股财务入口，故本轮不追加网络探针，不把 A 股函数改代码后用于港股，不把旧模板 403 归因为已确定封 IP。生产代码、市场标识、主源与依赖不变；继续沿用东财路径及多源框架。公开依据：<https://akshare.akfamily.xyz/data/stock/stock.html>（港股财务报表、关键指标-同花顺），<https://github.com/akfamily/akshare/blob/main/akshare/stock_fundamental/stock_finance_ths.py>。结果收敛至本计划，执行 `git diff --check`，尚未提交的调查记录保持工作区。

### 三标的当前财务报告验收（2026-10-22 21:35，发现缺陷，未通过）

- 用户同意结束新来源搜寻，验收腾讯/阿里/汇丰现有报告的币种、期间、归母利润、权益口径。隔离探针 `tmp/acceptance/hk-three-financial-acceptance.py` 使用真实 `Pipeline.run(... market="hk", dimension="financial", refresh_cache=True)`、FinancialAnalyzer、ReportBuilder 及质量视图，`llm_enabled=False`。只注册财务分析器，每公司真实四请求，未替换响应；包装 requests 仅旁路保存响应。不采集价格/新闻，不调用模型，不改用户缓存或历史报告。
- 产物目录：`tmp/acceptance/hk-three-financial-1792676119031550600/`，每标的一份 Markdown、原始响应、结构化结果；状态库也在该目录。脚本退出 0 仅表示报告生成完成。本次是财务维度的采集—充实—分析—Markdown 验收，不是完整多维度报告、浏览器或独立核对发行人公告的真实性验收，不应表述为三份报告全部通过。
- 腾讯 96 期，最新 2026-06-30，起点 2026-01-01，CNY/国际会计准则，完整来源包；营收 `396431000000`、归母净利润 `114115000000`、总资产 `2159394000000`、总权益 `1222513000000`、归母权益 `1135853000000`、OCF `154061000000`。阿里 35 期，最新 2026-06-30，起点 2026-04-01，CNY/美国会计准则，完整来源包；营收 `268953000000`、归母净利润 `10614000000`、总资产 `1962109000000`、总权益 `1113894000000`、归母权益 `1056622000000`、OCF `22945000000`。确认期间起点不同且权益分字段保存；尚未独立证明所有事实与原公告一致。
- 汇丰 55 期，最新 2026-06-30，起点 2026-01-01，部分来源包。当前模型营收、利润、资产、权益、EPS 均空，只有 OCF `556872805800`；报告却标最新利润表原币 USD、金额未换汇，并生成 OCF 同比。这一币种对应关系未获原始金额响应支持，本次验收失败。
- 原始银行科目使用 `001...`：`001025002 股东应占溢利=99616223400`、`001027002 每股基本盈利=5.79`、`001001999 总资产=23416970754900`、`001009999 归属于母公司股东权益=1339581433800`、`001011999 股东权益合计=1342298982900`。当前 `_fetch_hk_eastmoney_financial` 只读取普通企业的 `004...` 利润/资产科目，故误漏这些事实。不能仅替换代码前缀，银行科目位置不同，需独立映射及回归。
- 使用 `hk-hsbc-field-inspect.py` 对同一东财现金流接口最新 `003999` 做额外一次 `columns=ALL` 请求，结果保存 `hsbc-cash-all-fields.json`。全字段仍只有标的、报告期、起点、科目名、AMOUNT 等，**没有币种字段或换汇说明**。因此不能拿目录列报币种直接证明金额原币；也不根据其他站点或金额比值猜汇率。总计本轮 13 次真实请求，无主源算法改动。
- 待修复边界：银行模板的确定性科目映射；目录列报币种与三表金额币种证据分离，证据不足时不宣称原币/未换汇，不生成有误导性的跨期金额结论。不可用金额明确说明缺失或口径未核实，不借新来源填补，不扩展到评分、行情或付费方案。具体币种处理范围和实现需先收敛，再以保存响应离线测试回放，无需为每轮修复重复真实采集。
- 本轮生产与测试未改，仅本计划补充真实结果，执行 `git diff --check`；未运行业务 pytest/Full，不声称源码修复或验收已完成。因尚有已定位缺陷，暂不将调查文档零碎提交，不推送、不合并、不发布。

### 已确认的修复执行计划（2026-10-22）

1. 从保存响应裁切腾讯/阿里/汇丰 2025/2026 年 6 月 30 日同截止日两期与相关科目到 `tests/resources/hk/`，记录采样范围，并在 verify 脚本映射；先写银行映射、三标的币种保护、旧缓存保护与报告文案的失败回归。
2. 金额币种证据独立持久化，目录币种不再证明 AMOUNT 币种；旧港股记录没有证据时清除用于比较的币种，避免缓存绕过修复。目录仍可提供已匹配期间的会计准则，用于非金额比率趋势；A 股沿用旧行为。
3. 银行逐期选择独立科目映射：经营收入总额、股东应占溢利、每股基本盈利、总资产、股东权益合计、归母权益；不跨模板补值、不机械改前缀，缺项保留空；原值不换汇。银行营收科目另作说明，避免与普通企业营业额混淆。
4. 财务分析与共享 Markdown 文案取消港股默认原币/未换汇断言；未知币种保留数值并提示，不生成金额增长结论。无需新增页面或调整 UI 布局。
5. 跑相关 Ruff/Pyright/pytest，使用唯一临时目录；对三样本完整分析与 Markdown 做离线回放，禁止外网调用；完成规格与质量双审查，修正后复核受影响范围。本地提交完整修复及累计调查记录，不推送、不合并；Full 留待合并/发布前。

### 银行适配与币种保护实施、回归（2026-10-22）

- 按用户再次确认的完整方案实施：即使腾讯/阿里也暂时停止未核实币种的金额同比，不能为保留趋势而给 CNY 目录特别豁免。银行以独立模板映射，不机械改前缀；混合普通企业/银行利润科目时整期跳过并记录日志，银行缺项不从普通企业模板补入。银行收入标注“银行：经营收入总额”，不计算普通企业毛利率。
- `FinancialData` 新增 `amount_currency_verified`（旧记录默认 false）、`disclosure_currency`、`revenue_basis`。港股缓存反序列化时无证据的金额币种置空，数值不改，A 股币种保持兼容；目录只补已匹配期间的列报元信息/会计准则，不为金额币种背书。现金流没有明确同币种证据时不标可比披露口径。当前真实接口没有提供金额币种字段，因此三标的金额币种均为未知，这是确认后的保守行为，不是本次联网采集失败。
- 财务分析金额说明、港股 Markdown 免责声明、港股批量提示词和共享估值安全提示的 HK 分支同步修正；共享提示词 A 股分支保留旧行为。毛利率派生说明取消“未换汇”。旧 HK 缓存中的已知毛利率说明后缀亦在读取时规范，防止旧说明进入分析、Markdown 或模型事实。未改前端交互/SSE/CSS，不涉及 UI 布局验收。
- 测试资源 `tests/resources/hk/eastmoney-{00700,09988,00005}-financial-20261022.json` 从 21:35 本地真实响应裁切：仅 2025-06-30 与 2026-06-30、相关财务科目及对应目录记录，保留原报告期间、科目、金额，不换汇、不造金额币种。新增 `test_hk_bank_currency.py`，并为 `verify.ps1` 的 HK 资源分支添加测试映射及脚本计划回归。完整原响应/报告仍在忽略的 tmp，不提交。此前 PowerShell 表格显示 EPS 5.79 是显示精度；真实 JSON 为 **5.789265**，回归断言使用原值，不四舍五入改写数据。
- 测试先行：新九项回归初始 `6 failed, 3 passed`，分别复现目录误用、银行漏项、旧缓存误币种；首轮实现后 8 过 1 失败，剩余为测试期望用了表格舍入 EPS，核对 JSON 后修正。随后五文件相关回归 **134 passed**。中间一次相关测试因手写命令未预建唯一运行目录，触发 pytest `tmp_path` 的 WinError 3（6 errors），另外 5 个旧目录契约断言待更新；明确建目录并按已确认新契约更新后通过，不将该轮记为成功。
- 规格审查发现旧缓存毛利率说明仍含“未换汇”的 P1；补 `remove_legacy_hk_conversion_claim` 及缓存→分析→Markdown/提示词回归后复审关闭。质量审查未发现阻塞。两项审查均只读，无外网/云端；最终新增银行混合模板、缺项不跨模板回退测试，共 11 项银行/币种专用回归。
- 完整保存响应离线回放脚本 `tmp/acceptance/hk-bank-offline-report.py` 禁止真实网络，只按原请求名回放四个响应，走正常财务 Pipeline、分析和报告。产物在 `tmp/acceptance/hk-bank-fixed-1792679752537845100/`：腾讯 **96 期**、阿里 **35 期**、汇丰 **76 期**；前两者金额原值不变，汇丰恢复此前仅因银行映射漏值被选择器剔除的期间，未增加请求或造新期间。最新银行营收 `241030940100`、归母净利润 `99616223400`、EPS `5.789265`、总资产 `23416970754900`、总权益 `1342298982900`、归母权益 `1339581433800`、OCF `556872805800`。三标的按字段判定为完整来源包，但该状态不证明金额币种已核实。
- 回放报告均提示“金额币种未核实”，目录币种独立保留 CNY/CNY/USD；不含“未换汇”断言，无金额同比或现金流增长结论。腾讯/阿里仅保留已有口径允许的毛利率趋势，汇丰无可比趋势。报告可生成不等于三标的已逐项与发行人原公告核验；当前仍不宣称原币、上游换汇历史或全市场覆盖。
- 最终提交前门禁：`./scripts/verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv` **通过**，Ruff 全通过，Pyright `0 errors, 0 warnings, 0 informations`，pytest **1013 passed, 4 warnings in 96.70s**，日志 `tmp/acceptance/hk-bank-changed-final.log`；四条为既有 Starlette/httpx、LangGraph 弃用告警。首轮 Changed 为 `1011 passed, 2 failed`，仅提示词旧文案断言失败，完整日志 `hk-bank-changed.log` 保留；更新港股断言后重跑上述门禁通过。
- 额外管道/财务缓存 **39 passed**；最终状态再复核 `tests/core/test_financial_cache_facts.py` + 银行专用回归 **28 passed in 0.99s**，日志位于 `tmp/pytest/hk-cache-final-35608-36e57ff58b80418db0fc30f567ec7c75/result.log`。所有运行使用唯一 PID+GUID 目录；Changed 脚本自行清理成功。测试结束后对已校验位于本工作树 tmp/pytest 下的最终成功目录发起一次清理，命令被环境策略拒绝（包括同命令的日志复制亦未执行），目录原样保留，不重试删除或改权限；清理限制不推翻检查结果。
- 本次仅本工作树本地提交，生产/测试与正式文档按同一修复收敛；不推送、不合并、不发布，不改版本、依赖、云服务器、AstrBot 或已归档报告。主仓已有队列计划 SHA256 仍为 `1B5A032B963D2BDDB4A3B62D579AE2EC8AA7FE15AB2F507744D3AB9C2319BE6F`，未触碰。最终仅文档回填后复用已通过检查并补暂存 diff 检查；Full 未重跑，留待合并/发布。

### 修复后合并前 Full（2026-10-23）

- 用户同意继续后，确认 `codex/hk-multi-source` HEAD 为 `2bb1f84`、工作区干净，再运行 `./scripts/verify.ps1 -Scope Full -VenvPath D:/code/stock_robot/.venv`，未重复真实采集、模型、浏览器或云端请求。
- Full **通过，退出码 0**：Ruff 全通过，Pyright `0 errors, 0 warnings, 0 informations`，pytest **2288 passed, 4 skipped, 13 warnings in 287.29s**。完整日志为忽略的 `tmp/acceptance/hk-bank-full-20261023.log`。
- 警告为 12 条既有 Starlette/httpx 与 LangGraph 弃用提示，以及 1 条 pytest nodeids 缓存写入 `PytestCacheWarning [WinError 5]`；不是雷达产物失败，也没有测试失败。不据此重跑全量、修改权限或扩大生产代码修复。脚本报告已清理本次唯一临时目录。
- 通过后仅回填本计划并执行 `git diff --check`，不修改已验证的源码/测试。主仓仍只有用户原有队列计划改动，未触碰；尚未推送、合并或发布。此次 Full 不补足尚未核实的金额币种，也不能代替发行人公告逐项真实性核验。

### 用户查看的三标的真实完整分析（2026-10-23）

- 用户要求再次实际调用项目分析两三个港股并提供报告。使用 CLI 默认 `_get_registry()`（五分析器、AkShare 数据源）、独立 Config/缓存和正常 `Pipeline.run(... market="hk", refresh_cache=True)`、共享 `build_report`；关闭 LLM，未改全局配置、未访问云端。隔离脚本 `tmp/acceptance/hk-three-live-reports.py`，日志 `tmp/acceptance/hk-three-live-reports.log`，退出 0。
- 输出在 `tmp/acceptance/hk-three-live-1792726096446988000/`，各股票一份 Markdown 和结构化结果。腾讯耗时 3.41 秒、阿里 4.89 秒、汇丰 5.59 秒；每家 246 个行情样本（2025-10-23 至 2026-10-23），财报 96/35/76 期。技术维度 ok，财务及估值 partial，行业和舆情不适用；没有模型解读、综合评分或交易信号。当前交易日行情可能为未完成日 K，不能将其当已收盘价格。
- 三家财务最新期均 2026-06-30，银行字段恢复，目录 CNY/CNY/USD 与金额币种未知的提示均正确显示；不再有金额同比、现金流增长或未换汇断言。阿里东财估值请求遇代理连接错误、汇丰遇 HTTP 502，均成功切到百度；报告显示实际备用来源及估值截止 2026-10-22，不把当日行情与前日估值伪装为同日。
- 阅读报告发现两处表达问题，未在本轮诊断中擅自修改生产代码：① `cash_flow_source=来源未记录` 混淆来源已知和币种/期间可比性未知，应分离说明；② no_llm 报告末尾仍固定显示“部分内容由 AI 生成”，与纯量化模式矛盾。此外“完整来源包”与“财务指标数据不足”分别描述字段包及分析充足度，但并列展示容易令用户困惑，可进一步明确文案。此次真实生成与阅读不等于逐项核对发行人原公告或真实浏览器端到端验收。
- 尝试以应用文件面板打开三份报告，工具返回 queued，不能声称已即时显示；同时提供绝对路径下载/查看链接。正式文件仅本计划追加验收记录，源码/测试未变，执行 `git diff --check`，不重复 Full、不推送、不合并。

### 港股 PE/PB 分位缺失定位与既有曲线探针（2026-10-23，待确认实现）

- 用户要求优先处理 PE/PB 分位等估值数据，行业/舆情暂缓。确认 `_fetch_hk_valuation` 主路径只有东财快照；备用 `baidu_metric` 已解析近一年曲线，但只取最新日期返回单条 ValuationData。HK ValuationEnricher 又直接返回仅快照状态，所以历史既未持久化，也未计算分位；不是公式计算失败。
- 限量探针 `tmp/acceptance/hk-valuation-history-probe.py` 调用现有 baidu_metric，腾讯/阿里/汇丰各 PE(TTM)、PB 一次，共六请求，未改源码/业务缓存/依赖，无模型。退出 0，结果保存在 `tmp/acceptance/hk-valuation-history-1792727361247188300/`。六条均为 365 点，2025-10-23 至 2026-10-22；末值腾讯 PE12.82/PB2.66、阿里 PE22.95/PB1.60、汇丰 PE12.99/PB1.82。每条含 104 个周末点，故原始点数不能当交易日数；唯一值数分别 PE204/219/176，PB126/114/76，不是整条常量序列。
- 拟复用既有来源和分位算法，先以已采集行情实际日期对齐，PE/PB 分开统计有效正有限值；只用同来源同口径历史的末值计算，不能把东财快照放到百度历史内排名。报告明确近一年区间、截止日期、有效样本数；不是多年分位，也不声称“低分位=值得买”。当前尚未改变源码或接入历史持久化，待用户确认具体来源优先级、阈值、失败降级和请求预算后实施。

### 估值缺项扩展调查：PS、同行溢价及市现率（2026-10-23）

- 用户已同意 PE/PB 近一年分位方案：按实际行情日期对齐，两指标分别至少 120 个正有限样本，参照值与历史同源同口径，展示区间、截止日、样本数及统计；历史不可用则保留快照并说明原因，每次刷新最多增加两条曲线请求、按日缓存，不新增模型调用或港股评分。本轮用户进一步要求查找 PS、相对行业 PE 溢价等，尚未授权新增行业筛选/负值评分规则的实现。
- 按 design-before-implementation 先做能力调查。复核已安装 AkShare 的 `stock_hk_valuation_comparison_em` 与官方文档，已有东财 `RPT_PCF10_INDUSTRY_HKCVALUE` 报告包含 PE(TTM/LYR)、PB(MRQ/LYR)、PS(TTM/LYR)、市现率(TTM/LYR)、排名、行业标识及日期。AkShare 原函数按目标与关联股票双重过滤只返回目标一行，且返回表剔除行业和日期；不能把行业排名当成行业中位数。公开依据：<https://akshare.akfamily.xyz/data/stock/stock.html#id96>（以接口名检索“估值对比”），<https://github.com/akfamily/akshare/blob/main/akshare/stock/stock_profile_em.py>。
- 隔离探针 `tmp/acceptance/hk-extra-valuation-probe.py` 原样调用现有函数，腾讯、阿里、汇丰各一次，共三次 HTTP 200、每次一行，日期均为 2026-10-22。PS(TTM) 分别为 **3.886835796798 / 1.617958788935 / 4.688822306384**；市现率(TTM) 分别为 **10.019344808092 / 21.511508933878 / 3.20733073937**。原响应及参数保存 `tmp/acceptance/hk-extra-valuation-1792727652118013400/`，未写业务缓存。银行指标存在不等于适合银行估值比较，不据此自动给分。
- 进一步用相同公开报告，仅保留目标 `SECUCODE` 过滤、限定每页 100/200 行验证同行明细，没有全市场扫描。单次探针先取腾讯第一页，确认 count=167/pages=2 后，以脚本补齐两页并核对另两公司，共五次有界请求（腾讯第一页核对时重复一次，非失败重试）。完整结果分别 **167 / 85 / 40** 条，关联股票代码去重数相同，全部为同日；目标自身各含一条。行业分别为 **软件服务 / 专业零售 / 银行**，TYPE_ID 为 403010002/403012007/403008001。不能仅由 TYPE_TYPE="2" 推定与申万二级分类等价。
- 完整同行产物为 `tmp/acceptance/hk-peer-valuation-1792727839783652400.json`、`hk-peer-valuation-00700-1792727840342796500.json`（腾讯两页），以及 `hk-peer-valuation-09988-1792727841085737700.json`、`hk-peer-valuation-00005-1792727841528226600.json`。三个目标行与原单股请求一致。记录条数包含目标且不等于有效正 PE 样本数，不在调查中将全体原始值直接取中位数或计算正式溢价。
- 结论修正：PS 当前值和同行明细不是免费源不存在，而是现有项目尚未接入。相对行业 PE 溢价已有数据基础，但应另行确认同行范围、有效样本、排除目标、同日同口径、分类适用性及非正值处理；此前搁置的负值比较规则本轮不擅自定案。若后续采用正值基准，只有目标及基准可用于倍数比较时才能使用 `(目标PE/同行PE基准-1)*100%`，不能跨正负值解释便宜/贵。
- 已读百度港股估值及亿牛 AkShare 接口源码：百度公开支持总市值、PE(TTM)、静态 PE、PB、市现率；亿牛接口列 PE、PB、股息率、ROE、市值。本轮未发现这两个接口公开支持 PS 历史，不发明参数或把单点 PS 当历史分位。PE/PB 已有曲线证据；PS 历史分位仍未确认，不能按港币市值除未核实币种/期间的营收硬凑。市现率当前值虽可取，是否展示须独立考虑适用性，不能自动扩为评分。
- 本轮仅修改本计划和忽略的探针/响应，没有生产源码、测试、依赖、版本或服务器变更。两个探针进程均退出 0，已核对 HTTP 状态、分页、去重与日期，不是业务集成验收；执行 `git diff --check`，无须重跑业务 pytest/Full。未调用模型，未推送、合并、发布；待新增字段的具体展示方案确认后实施。

### 已确认估值补齐的实施与验收步骤（2026-10-23）

用户同意优先 PE/PB 近一年分位与 PS 当前值，本轮不接入同行池、市现率或 PS 历史。实现范围与上述调查同主题收敛，不增加正式文档。当前快照保留、历史参照值独立披露，避免跨来源排名。

1. 先补离线回归：同源参照、交易日对齐、逐指标 120 门槛、非正剔除、旧模型/JSON 缓存兼容、两条曲线请求预算、PS 单目标/日期校验、异常降级及 Markdown 来源文案。
2. 增加可缓存历史和统计模型、东财单行 PS 请求；原快照不被附加请求失败覆盖。两条百度曲线只请求一次并复用为失败回退，不在回退时重复抓取。
3. 港股充实器独立统计，不触发 A 股股本或财务推导；分析器输出逐项来源日期与样本说明。质量视图保留港股实测来源，不标成派生估值；A 股与指数原行为不变。
4. 相关 Ruff/Pyright/pytest，保持唯一运行目录及显式工作树 PYTHONPATH；三真实股票正常 Pipeline/ReportBuilder、独立缓存且关闭模型，核对新增字段和降级，不访问云端。规格、质量独立审查，修复后复核受影响部分；完成后按完整事项本地提交，不推送或合并，Full 不在本轮重复。

首轮新测试因尚无 PS 函数而按预期收集失败；同时发现主仓 editable 解析路径，后续所有手动测试显式设置工作树 src 的 PYTHONPATH。首轮实现相关新旧测试 **46 passed**。第一次 Changed 的 Ruff 通过，Pyright 发现新增测试可空属性收窄与 dict 数值类型共 16 项错误，已按断言及 float 修正；未将该轮记为验收通过。完整验证和真实报告结果待回填。

### 估值补齐实施结果与审查（2026-10-23）

- 增加 ValuationData 历史曲线、来源及 PS 独立日期/来源/缺失说明，EnrichedValuation 保存逐指标样本统计；缓存 JSON 日期键和 Pipeline SQLite 实际往返均有回归。PS 使用单标的一行东财公开估值报告，校验 success、完整页、目标自身及非未来日期，有限数转换复用原工具；非正 PS 保留披露但不用于高低判断。原快照、PS、两条历史请求独立隔离，历史同时供原百度快照回退使用，不重复请求。
- 港股充实器按行情日期字典去重并与历史交集，剔除非正/非有限值，分别 120 门槛及同源末值分位；没有历史仍保留快照，不触发股本、财务估算。分析与质量说明展示单项参照值、来源、期间、样本和 PS 日期，不称港股曲线为财务派生。PS-only 的健康缓存放行仅限港股，新增用例确认 A 股旧判据未改变。行业溢价仍为空，不新增评分。
- Changed 初轮 **1021 passed / 4 failed**（114.19 秒，完整日志 `tmp/acceptance/hk-valuation-changed-final.log`）：三项旧百度请求测试未隔离新增 PS 请求，第四项旧快照测试既断言“不请求历史”又返回未配置 MagicMock。按新契约隔离 PS 并让曲线测试桩返回空字典，相关 **34 passed**；禁止用第三方请求混入离线测试。后续管道+新数据回归 **112 passed in 26.76s**，日志 `hk-valuation-related-final.log`。
- 规格与质量独立只读审查均发现同一 P1：格式器仍按当前快照缺项覆盖有效历史分位。先补 PE/PB 两项 Markdown 原始回归，**2 failed** 复现“暂无数据”；随后由主会话以仅港股分析器生成的独立历史适用标记修正格式器，A 股无标记沿用原规则。该小修复小于 50 行，按项目规则主会话 diff 与回归复核，不再派重复审查代理。最终新数据、原来源、报告、估值有效性测试 **265 passed in 2.54s**（日志 `hk-valuation-format-final.log`），两项审查问题闭环。相关 Ruff 全通过，最后改动文件 Pyright **0 errors**。
- 真实验收使用既有 `hk-three-live-reports.py`、正常 CLI Registry/Pipeline/build_report、工作树 PYTHONPATH、独立 Config/cache 和 no_llm。产物 `tmp/acceptance/hk-three-live-1792731241922760000/`，日志 `hk-valuation-live.log`，退出 0。腾讯/阿里/汇丰各 246 个行情，财报 96/35/76 期，估值均 ok；PE/PB 各 **245 个对齐样本**，区间 2025-10-23 至 2026-10-22。PE 分位 **0.0% / 68.6% / 3.7%**，PB 分位 **0.0% / 4.1% / 75.5%**，PS(TTM) **3.89 / 1.62 / 4.69**，PS 日期均为 2026-10-22。三次原东财快照均遇代理连接错误，百度原回退成功；报告保留主源失败诊断与实际备用来源，不能记为主源通过。
- 阅读三份 Markdown，确认新增数值、历史参照、统计与来源日期实际可见，PS 未由未知币种财报推导，没有“行情与财务派生计算”误标签；最终格式修复只影响快照缺项的边界，已有三份真实样本当前两快照项齐全，复用本次真实采集，不追加外网。此项验收不等于核验上游历史估值的经济真实性、不代表全市场覆盖，也未验证浏览器交互/云端/QQ；本轮未修改这些路径。PS 历史与行业/舆情仍不提供。
- 新增测试最终 17 项，均为确定性构造，未新增静态响应文件。当前主仓用户队列计划 SHA256 仍为 `1B5A032B963D2BDDB4A3B62D579AE2EC8AA7FE15AB2F507744D3AB9C2319BE6F`，没有触碰。正式文档仍为本主题一份设计+一份计划；临时日志和报告不提交。Full 不重复，不推送、合并、发布或修改版本。
- 提交前 `./scripts/verify.ps1 -Scope Changed -Source Staged -VenvPath D:/code/stock_robot/.venv` **通过，退出 0**：13 个文件的 Ruff 全通过、Pyright **0 errors / 0 warnings**，pytest **1029 passed, 4 warnings in 109.72s**，日志 `tmp/acceptance/hk-valuation-staged.log`。四条警告均为既有 Starlette/httpx 与 LangGraph 弃用提示，脚本清理本次运行目录成功。
- 门禁后主会话 diff 复核修正一行旧诊断条件：当原快照完整、PE/PB 曲线日期不重合时，仍保留完整快照，不应误标“仅保留日期较新指标”；该文案仅适用于没有原快照的原百度回退。新增对应回归后，复核两个港股来源测试文件及新历史测试共 **74 passed in 0.80s**，改动两文件 Ruff 通过、Pyright **0 errors**。此前通过的其他范围结果复用，不因一行诊断条件重复整个 Changed；最后仅回填文档并执行暂存 diff 检查。三个真实报告当前项完整且曲线有共同日，不受此条件修正影响，未重复外网。

### 港股申万路线复核（2026-10-23，调查未取得分类数据）

- 用户要求优先复用原有申万机制，撤回未经充分核实即改用东财分类的主路线建议。本轮只调查，不更换生产分类来源、不决定尚搁置的同行中位数和负值评分规则，也不接入舆情。申万 2021 官方说明覆盖港股通等标的，因此不能将当前接线缺失表述为申万标准不适用港股；标准覆盖也不代表所有港股均有分类。依据：<https://wxweb.swsresearch.com/swsreport/2021_08/328340.pdf>。
- 代码核对：当前 IndustryClassifier 依赖本地分类 CSV，先前检查 5534 条记录中没有港股代码；industry_mapping_builder 的行业树及成分来源为乐咕乐股，成分代码匹配六位数字。此限制需要未来实现时处理，但不证明原网页含有被解析器丢弃的港股，不能仅放宽正则就宣称支持。
- 读取已安装 AkShare 源码，stock_industry_clf_hist_sw 直接下载官方 `https://www.swsresearch.com/swindex/pdf/SwClass2021/StockClassifyUse_stock.xls`；index_component_sw 使用官方 `/institute-sw/api/index_publish/details/component_stocks/`，参数为 swindexcode/page/page_size。未安装或升级依赖。
- 前轮普通 requests 请求遇本地证书链校验失败；随后 urllib 使用系统默认证书校验（没有关闭 TLS 校验）后，Excel 和官网分类下载页均返回 HTTP 508；乐咕乐股行业总览返回 HTTP 403。本轮浏览器打开官网分类下载页超时、会话运行时重置，未取得 DOM 或下载链接。搜索引擎仍可检索到官网“2021新版分类/全部行业分类下载”，不将搜索摘要当作成功下载。
- 为区分旧文件失效与官方 API 可达性，再对 AkShare 公开源码列明的成分 API 请求一次：swindexcode=801001、page=1、page_size=10000、超时 12 秒，系统证书校验开启，同样 HTTP 508，未取得成分记录。801001 是现成接口默认样本，不是港股行业验证；没有猜测港股指数代码或私有参数。探针 `tmp/acceptance/sw_public_probe.py` 位于忽略目录，仅作研究，不接入生产。
- 结论：已确认标准有港股覆盖、现有项目未接线；尚未验证当前免费的港股分类及完整同行名单可取得、可更新。508/403/超时只说明本次访问失败，不能判断上游数据不存在、永久失效或 IP 被封。没有合法可达数据前，不伪造分类、不自动替换分类体系、不添加付费源或绕过访问限制。
- 正式改动仅本计划追加调查结果，执行 git diff --check；没有业务代码变更，不运行 pytest/Full，不推送、合并或发布。该记录不是行业功能验收通过。

### 用户授权合回 dev 的全量门禁（2026-10-23）

- 用户随后要求将本轮工作全部合并。核对 dev=b4d2001 为当前分支 HEAD=027f930 的祖先，差异为四笔本地提交，覆盖银行财务修复、估值历史与 PS 披露、共享估值日期展示及调查记录；不包括其他工作树任务，也不代表港股行业/舆情已实现。
- 合并前运行 `./scripts/verify.ps1 -Scope Full -VenvPath D:/code/stock_robot/.venv`，退出 0：Ruff 通过、Pyright 0 errors / 0 warnings / 0 informations，pytest **2321 passed, 4 skipped, 12 warnings in 197.11s**；警告为现有 Starlette/httpx 和 LangGraph 弃用提示。日志保留在 `tmp/acceptance/hk-merge-full-20261023.log`，脚本报告已清理本次测试临时目录。未重复云端、QQ、模型或真实数据请求；同代码状态此前真实报告与浏览器验收结果复用。
- 仅回填本文档后执行差异检查，提交调查与验收记录，再采用 `git merge --ff-only codex/hk-multi-source` 合回本地主仓 dev，保持现有提交历史。主仓用户原有队列计划不属于合并范围，合并前 SHA256 为 `1B5A032B963D2BDDB4A3B62D579AE2EC8AA7FE15AB2F507744D3AB9C2319BE6F`，须在操作后复核。此次授权不扩展为推送、发布、删除其他分支或工作树。

### 云端申万公开入口小样本复核（2026-10-23 19:00 +0800）

- 用户同意在此前授权的云服务器上检查同一官方 Excel 与成分 API。SSH 使用已记录主机指纹，密钥认证未通过后采用交互密码登录；未写入密码文件、未安装依赖、未更改服务器配置或服务。远端 python3 通过标准输入运行只读探针，不创建远端文件，结束后退出 SSH。
- urllib 默认 TLS 校验、单请求超时 15 秒、响应读取上限 4,000,001 字节；Excel 和成分 API（801001 默认样本，不是港股行业）均在 TLS 阶段失败，分别耗时 0.05/0.04 秒，错误为 CERTIFICATE_VERIFY_FAILED / unable to get local issuer certificate，没有 HTTP 状态、文件或成分记录。
- 检查现有客户端而非关闭验证：系统默认 CA 为 `/etc/pki/tls/cert.pem`，已安装 requests 使用 `/etc/pki/tls/certs/ca-bundle.crt`。两 URL 各补一次 requests 请求，连接/读取超时 8/15 秒、保持 TLS 校验、无自定义重试，分别耗时 0.03/0.02 秒，同为 SSLError。没有更改系统证书、代理或网络出口，也没有 verify=False。
- 此次云端错误发生在证书阶段，本机前轮通过系统证书后收到 HTTP 508；二者不能视为相同 HTTP 拒绝，不能证明 IP 被封或源无港股。CA 文件路径不同也不证明使用独立信任根。当前仍未核验名单及覆盖，按约定暂停申万入口探测，下一项转为港股新闻来源能力调查，不自动换行业分类。
- 本轮仅更新同主题计划，执行 git diff --check；无业务修改，不重跑 pytest/Full，不推送、合并或部署。登录横幅提示较多失败登录记录，已提醒用户关注；未扩展执行服务器安全配置修改。
- 新闻初步离线调查：CodeGraph 核对现有 _fetch_news / SentimentEnricher，并读取已安装 AkShare stock_news_em 源码。后者请求东财关键词搜索 JSONP，keyword 直接使用输入，pageSize=10；不是按港股证券标识绑定的专用新闻接口。尚未调用港股样本，不能将参数可传五位代码等同于新闻相关性已验证；后续需核验实体、发布日期、来源链接与相关性，不能按搜索结果条数直接给分。本轮未启用模型标注或更改 A 股逻辑。

### 港股新闻候选六组隔离验证（2026-10-23 19:04 +0800）

- 用户同意继续后，按 design-before-implementation 先验证现有入口，不接入业务。AkShare 官方文档明确 stock_news_em 的 symbol 可用股票代码或其他关键词；已安装实现使用 pageSize=10，不能把文档“最近100条”当本次实际返回条数，也不能保证完整覆盖近30天。依据：<https://akshare.akfamily.xyz/data/stock/stock.html> 个股新闻一节。
- 隔离脚本 `tmp/acceptance/hk_news_probe.py` 对 00700/腾讯控股、09988/阿里巴巴、00005/汇丰控股各调用一次，共六次 SDK 调用，无模型、无业务缓存、无自动重试。六组均返回10条，完成约3秒。产物 `tmp/acceptance/hk-news-1792753493677072000/results.json`，含查询词、时间及完整返回记录，留在忽略目录。
- 探针边界必须保留：初版仅 patch 普通 requests.get，但 AkShare 实际 globals 中的 requests 是 curl_cffi.requests，导致 calls 数组为空，超时注入和 HTTP 状态记录均未生效。不能宣称已测得六次HTTP200或全部受5/10秒超时约束；调用实际已正常结束，数据可用于候选内容检查。随后修正探针为 patch 实际 backend，但未重复请求，因此修正后的探针尚未重新运行验收。
- 全60条候选来源和链接字段均非空，日期可见但不是严格新到旧。腾讯代码结果日期为09-28至10-15、十条均回购主题；名称结果10-15至10-22，出现同题转载及南向资金重复报道。阿里代码结果混有市场综述，名称结果包含港股和美股行情，不能混作港股自身市场表现。汇丰名称十条中八条为持有其他公司股份变动、一条为对阿斯麦目标价调整、一条自身回购；这些不能按关键词命中直接当自身经营利好/利空。
- 浏览器网页工具抽查三篇：阿里 `https://finance.eastmoney.com/a/202610223903700617.html` 可打开，标题、2026-10-22 09:45及第一财经来源与搜索记录一致；腾讯 `202610223903206285` 与汇丰 `202610223904209153` 两篇工具报告不可访问，未核实正文。未因工具不可访问认定源链接无效，也不宣称所有候选全文可取。
- 调查建议（尚未确认实施）：先把港股该维度作为可追溯相关新闻展示，保留原始时间、媒体及链接，对候选相关性及重复报道作保守处理；不依据新闻条数计算正负面比例或新增评分，不单独调用模型标注，未来仅让既有AI解读引用经筛选条目。筛选规则、采样窗口、展示上限及降级行为需向用户完整呈现并确认后再实施。本轮正式文件仅本计划更新，执行 git diff --check，不跑业务 pytest/Full、不推送或合并。

### 关键词新闻展示样例（2026-10-23，仅离线预览）

- 用户讨论后同意先退回“相关新闻检索”，不做AI相关性筛选或情绪评分，不把条数当充足度；先制作三标的样例供判断，不接入正式报告。此共识取代上一节尚未确认的“经筛选条目供AI引用”建议，不能据此前建议启用AI筛选或语义排除规则。
- 复用上一节60条保存结果，无外网、云端、模型、公告请求或业务缓存写入。生成器 `tmp/acceptance/build_hk_news_examples.py`，预览目录 `tmp/acceptance/hk-news-examples-20261023/`，含三份代码命名Markdown及 `all-examples.md` 合集。产品版布局/条数等尚未确认，这些是研究样例而非生产功能验收。
- 样例固定采集日2026-10-23，窗口09-24至10-23（含首尾30个自然日），使用保存的发布时间，时间倒序；缺失/非法日期、未来/过窗日期剔除而非补今天。仅按同链接或同日清理HTML实体及空白后完全相同标题合并，保留其他来源链接，不进行近似标题、同事件语义归并；不排除汇丰持仓等间接相关报道。说明明确“关键词检索结果，可能包含间接相关报道”，不保证完整新闻覆盖或全文核验。
- 原候选每家20条，日期过滤均0，腾讯合并1条后19条，阿里合并0条后20条，汇丰合并2条后18条。原始数据不变，预览保留完整基础去重结果以便用户观察噪声，不先自行设定最终展示上限。
- 执行主仓虚拟环境Python运行上述生成器，退出0；断言三标的/60条输入、筛除与合并计数守恒、合法HTTP(S)链接及文件写入读回一致。额外离线构造边界验证缺失/未来/过窗日期、同日同标题合并与异日同标题保留，全部通过。打开合集的应用工具返回 queued，不能宣称已即时显示，交付绝对路径供查看。正式文档仅更新本计划，git diff --check通过；无生产代码改动，不运行pytest/Full、不提交、合并或部署。

### 新闻时间排序、分页与百条请求验证（2026-10-23）

- 用户同意先验证取数范围，不接入正式报告。普通访问官网搜索页 `https://so.eastmoney.com/news/s?keyword=00700`，HTTP200；从真实HTML取得 `/newstatic/js/page/search_main.js` 后读取官网脚本（HTTP200，586059字节）。脚本 searchZiXun 明确传递 searchScope、sort、pageIndex、pageSize，资讯枚举含字符串 default/time/score；页面提供按时间排序。文件内还包含其他历史枚举，不将其数字值混入本次 cmsArticleWebOld 参数。官网脚本核对时读取两次，不算业务搜索请求，未使用第三方猜测参数。
- 忽略目录探针 `tmp/acceptance/hk_news_paging_probe.py` 直接使用 curl_cffi.requests，关键词00700，参数复用公开搜索，Referer为实际页面，无Cookie、登录或自动重试；每次timeout=12秒，共五次：(default,第1页,10条)、(time,第1页,10条)、(time,第2页,10条)、(time,第1页,20条)、(time,第1页,100条)。全部HTTP200、业务码为空，返回数分别10/10/10/20/100，响应6256/5283/5176/10291/52398字节，单次约0.23至0.28秒。响应与摘要保存在 `tmp/acceptance/hk-news-paging-1792754839667244500/`。
- 默认10条非时间倒序，日期09-28至10-15；time第1页10条为10-22至10-23、第2页10条为10-20至10-22。time各请求均时间倒序；两页ID无交集，20条请求与两页拼接顺序完全相等，100条前20条与20条请求一致，100个ID唯一。独立离线断言复核请求数、HTTP状态、返回量、排序、唯一ID、分页无重叠及前缀一致性全部通过。
- 结论：本样本证实单次可取得100条且支持分页，10条是当前SDK封装限制，不是已知上游上限。没有继续增加pageSize或扫描总页数，故最大单页条数、允许最深页数和全量可获取性仍未知。hitsTotal=953只是该查询的上游统计，不能据此宣称953条均可下载。100条覆盖09-29至10-23，尚不足覆盖样例09-24起的完整30天；必须披露候选采集上限及不保证完整覆盖。
- 当前建议下一步确认正式采集预算，例如代码与公司名称分别按时间取100条、最多200条原始候选；这不是已获确认的产品规则，缓存周期、展示上限和失败降级仍需确认。不修改AkShare依赖、生产逻辑、评分或报告模板；本轮只更新原计划，执行git diff --check，不运行业务pytest/Full、不提交、合并、部署。

### 港股关键词新闻已确认实施计划（2026-10-23）

用户逐项确认30天、两查询各100条、缓存1小时、展示20条、失败缓存回退，最后确认完整方案（12秒、两并发、无重试、部分成功提示）。同主题规格已补齐；研究建议不再作为待确认实施规则。

1. 主会话先补快照模型和独立采集模块回归，失败后实现请求与解析；接口 `fetch_news(symbol,name)->HKNewsSnapshot`，双失败抛异常；`current_news(snapshot,now=None)->HKNewsSnapshot` 复制过滤，不截断至20条。所有模型位于data.schemas，NewsData.hk_news可持久化。
2. 管道/门面实施：仅启用hk/news、透传name，2请求预算不被外层重试放大；港股空结果视成功，旧未验证快照不复用，TTL3600，刷新时亦可回退旧快照；记录partial/stale而不伪造采集时间。不改变其他市场。
3. 分析/报告实施：阻断港股SentimentEnricher模型及评分，用hk_news列表展示20条及安全来源链接，更新Markdown/payload/质量文案，不计入三维覆盖；保留全部旧市场断言。Web若需要渲染新字段，复用当前组件并更新缓存版本。
4. 两个实现范围先测试后修改；按跨模块S级执行规格和质量审查，主会话收敛修复并复核受影响范围。测试用主仓虚拟环境及工作树PYTHONPATH、唯一PID+GUID临时目录。稳定后Changed门禁，不因本地提交重复Full。
5. 三公司真实采集最多各两查询（含一次真实报告/缓存验证），无模型或云端；本机真实Web若涉及渲染，验证安全链接、首条与缓存/刷新状态以及窄屏。将真实结果、失败清单与未覆盖风险回填此计划，再按完整事项本地提交，不合并/推送/发布。

### 关键词新闻实施与验收收敛（2026-10-23）

- 实现hk_news模型与独立采集模块、HK新闻专用管道缓存、分析参考列表、Markdown/payload/Web展示；保持无情绪模型/评分、三维分析覆盖不变。采集模块先红测（模块不存在），实现后8项通过，扩为14项数据回归。门面/管道先红7项再实现，报告专用8项及旧范围264项通过。均使用主仓虚拟环境、工作树PYTHONPATH及唯一测试临时目录。
- 首轮Changed退出0，Ruff/Pyright通过，1080 passed / 4 warnings。规格和质量审查分别发现同实例partial读后写竞争、浏览器永久缓存绕过1小时/30天约束，两项P2均修复并复审核销：HK写锁覆盖条件读取及落库（不跨进程）；实时浏览器缓存按原采集/接收时间判到期及跨香港日期刷新，展示复制过滤而不改历史快照。节点可执行回归覆盖3599/3600秒、跨日和原缓存不污染。
- 主会话补齐HK网页刷新按钮→API refresh_cache=true→管道，其他市场原契约不变，三项参数回归通过。扩大Changed先在Pyright报2项kwargs类型错误，改显式分支后复查0错误；随后1363 passed / 1 failed，失败为旧策略指数测试桩导入旧版API/模块实例；同步当前模块版本但保留ETF/普通股票原断言后，收敛范围177 passed / 4 warnings（17.09秒），日志 `hk-news-closure-tests.log`。首个异常和清单保留在 `hk-news-changed-final.log` / `hk-news-changed-repaired.log`；不将扩大Changed失败轮次写成全绿。其余范围复用已通过结果，不重复Full。
- 真实CLI使用独立cwd `tmp/acceptance/hk-news-web`、llm.enabled=false、空key、关闭rag/后台采集，运行主仓stock-robot.exe run --host 127.0.0.1 --port 8765，PYTHONPATH指工作树。真实正常API/Pipeline三标的均HTTP200：腾讯/阿里展示20条、汇丰15条，评分为空。网页名称解析均缺失，仅执行代码查询并明确提示不完整，没有额外名称补查。三份Markdown在该目录reports/stock下，阿里/汇丰payload在 `tmp/acceptance/hk-news-live-api/`。
- 真实浏览器发出腾讯分析并查看20条、媒体/时间/来源，1280px无水平溢出；重载后普通打开保持原采集时间20:33:57，点击刷新后更新20:41:27，缓存及强刷真实接线通过。旧截图 `hk-news-web/news-desktop.png` 为用户随后否定的分行/链接版，不作为最终视觉认可。临时服务已停止，未动云端/QQ、项目版本或主仓用户队列计划（SHA256仍为1B5A032B963D2BDDB4A3B62D579AE2EC8AA7FE15AB2F507744D3AB9C2319BE6F）。前轮被中断，未提交。

### 用户要求恢复原报告样式（2026-10-23）

- 用户明确要求每条只保留标题、发布时间、媒体，不显示原文链接；不新设计UI，复用已有report-metric-list普通项目列表。网页与Markdown统一“标题（YYYY-MM-DD HH:mm · 媒体）”，媒体去重保留，标题继续安全文本/转义；链接仅内部来源记录，不影响请求、缓存、排序、去重、失败或评分规则。
- 测试先行：三项新增/更新断言在旧展示下失败，分别复现MD仍含URL、未格式化紧凑时间、Web未沿用旧列表样式。修改两处格式器，版本标识统一至20261023-hk-news-compact；正式版本未变。相关报告/静态/ETF路由回归66 passed in 5.57s，Ruff通过、单文件Pyright0错误；工作树无.venv提示是复用主仓工具的环境提示，不是类型错误。真实页面复核结果待回填。
- 复用此前独立CLI服务目录及已有缓存，再通过真实网页输入00700.HK生成研报，没有主动刷新/追加新闻取数。新DOM验证20条、新闻区a元素0、媒体与YYYY-MM-DD HH:mm同行显示；当前实际窗口629px、无水平溢出，查看截图确认换行正常。新生成Markdown新闻区无HTTP(S)链接。截图 `tmp/acceptance/hk-news-web/news-compact.png`；这是本次交付效果，不把旧分行截图当最终样式。原始记录/结构化数据仍留URL用于内部追溯，正文不显示；未修改其他报告模块布局或CSS。diffcheck通过；只完成本地展示修正及验收，尚未提交/合并/部署，临时服务随后停止。

### 移除新闻采集诊断展示（2026-10-23）

- 用户提供截图明确移除原采集时间、关键词查询失败/未执行、候选上限提示。Markdown、网页新闻区及新闻覆盖附录不再展示；metrics、快照诊断和缓存规则保留。无结果与获取失败仍有读者可理解的说明，其他维度时效不变。
- 测试先复现Markdown诊断泄露；首轮网页测试因临时目录父路径未建立未运行，补建后相关79项通过。真实网页发现currentHkReport重新过滤时仍把news_notes拼回附录，移除这处拼接并增加普通请求/缓存复用回归；最终79 passed in 6.19s，无警告。Ruff通过，相关Python文件Pyright为0错误。
- 完整腾讯报告复用独立验收缓存生成，20条新闻、八个章节、纯量化无模型；新版文件 `tmp/acceptance/hk-full-preview/stock/00700.HK/2026-10/00700.HK_20261023_211533.md`。采集诊断未从底层清除，不将报告完整格式称为数据完整；财务partial、行业unavailable仍如实保留。
- 使用同一隔离配置、正式CLI启动8765并通过真实浏览器输入00700.HK发送请求，HTTP200；修复后DOM新闻20条、正文及新闻附录均无指定诊断，1280px无水平溢出。截图 `tmp/acceptance/hk-news-web/news-clean.png` 已查看；临时服务验收后停止，不涉及云端、版本、提交或合并。

### 免责声明名称与末尾顺序统一（2026-10-23）

- 用户明确要求原章节改为“免责声明”并放最后，Web同步适配。report_v2调整六至八节顺序；builder.build_disclaimer共享既有文案，结构化payload及会话成果保留disclaimer。网页实时/抽屉统一末卡，报告库存档从新旧标题提取原说明后放末尾；纯Markdown回退同样调整研报视图，不改历史源文件或未知附录。
- 先红测复现A股/港股末章名称与顺序、实时缺失说明和旧库顺序。实现后245 passed/2 failed，失败为Node DOM桩innerHTML不生成textContent，修正为检查实际HTML内容后247 passed in 9.16s；覆盖全部tests/report、静态UI、报告库和ETF路由。Ruff通过，相关builder/payload/app及测试Pyright0错误。不运行Full，不声称已合并或发版。
- 实际CLI隔离启动8765，真实输入00700.HK返回HTTP200；末三卡分别为待验证事项、数据覆盖与时效、免责声明，仅1个说明卡，1280px无溢出。点击报告库打开此前21:18旧标题报告，末三卡一致、原说明完整、旧标题不可见。截图 `tmp/acceptance/hk-news-web/disclaimer-last.png` 已查看。新版完整报告 `tmp/acceptance/hk-full-preview/stock/00700.HK/2026-10/00700.HK_20261023_212915.md`，缓存20条新闻，无模型调用；财务/行业既有限制未变。
- 补充会话成果透传回归，TestStockReportExtraction共5 passed；diffcheck及相关Ruff通过。真实窄屏/会话流程未新增验收，以共享渲染器回归覆盖；临时服务随后停止，未提交、合并或部署。
- 后续措辞修订：新报告改为参考用途、无投资建议/收益保证、数据与方法局限及独立核实提醒，保留港股原币与报告期限制；删除AI尾注和绝对客观表述。网页在新旧结构化、原文提取及Markdown回退的免责声明中清理独立AI尾注，不修改归档源文件。先红5项，修复后相关91 passed in 6.52s，Ruff通过、Pyright0错误。真实CLI网页腾讯报告末卡检查通过、尾注不存在、1280px无溢出，截图 `tmp/acceptance/hk-news-web/disclaimer-copy.png`；新版完整报告 `tmp/acceptance/hk-full-preview/stock/00700.HK/2026-10/00700.HK_20261023_213802.md`。复用缓存，无模型，临时服务验收后停止，未提交/合并/部署；本轮不重复Full或真实会话/窄屏验收。

### 分支提交与合并前门禁收敛（2026-10-24）

- 用户授权以分支方式合并，目标为现有dev；功能分支codex/hk-multi-source与dev原同为9176e81。本轮包含免费关键词新闻及已确认的报告展示修订，不改版本、付费源、插件或行业能力；真实Web/三标的验收复用前述结果，不重复采集。主工作区独立未提交的managed-upgrade-queue计划不纳入提交，其SHA256保持1B5A032B963D2BDDB4A3B62D579AE2EC8AA7FE15AB2F507744D3AB9C2319BE6F。
- 合并前运行 `scripts/verify.ps1 -Scope Full -VenvPath D:/code/stock_robot/.venv`，全量Ruff及Pyright通过，pytest为2369 passed / 1 failed / 4 skipped（193.82秒）。完整异常与警告保留于 `tmp/acceptance/hk-news-merge-full.log`，失败临时目录未清理。唯一失败为test_report_quality_static仍断言数据覆盖最后，与新确认的免责声明末尾顺序冲突，并非既有随机失败。
- 只更新该测试的末三卡顺序和按ID定位数据覆盖，不删字段顺序、数据不足不发信号、AI重试等既有检查，不改生产代码。复核 `tests/api/test_report_quality_static.py`、报告库、静态UI、ETF路由及全部tests/report，249 passed in 21.95s；单文件Ruff及Pyright通过。其余范围复用同一生产代码的全量结果，不将失败的Full轮次表述为全绿，也不因只改测试断言重复全量。暂存diffcheck通过，预览/日志/缓存均不提交。
- 按一个完整事项提交功能分支，以no-ff保留分支合并记录到dev；不推送、部署或删除分支。合并后核对提交树与功能分支一致，并再次核对主工作区独立改动及摘要。

### 港股行业分类及同行字段复核（2026-10-24，仅调查）

- 新闻合并后用户同意下一项先核实行业数据，用腾讯、阿里、汇丰给出样例；按design-before-implementation只调查既有免费能力，不改生产逻辑，不确定中位数、负值比较或行业评分。继续优先申万，不将东财分类自动当替代方案。
- CodeGraph先行但未返回指定行业源码，随后直接核对IndustryClassifier、industry_mapping_builder、IndustryEnricher及_fetch_industry。现行映射5534条、港股0条；构建器匹配六位代码，A股同行采集按市值过滤并选五家，充实器从头部有效正值计算中位数。可以复用模型、缓存和展示，但不能把现有A股采集/评分接线原样放开到港股，现有行为不代表用户已接受后续行业统计方案。
- 读取已安装AkShare的官方申万分类下载、成分及东财港股估值对比接口源码，未升级依赖。SDK成分函数含verify=False，因此探针不直接调用它，改用公开相同URL和系统默认证书校验。每个申万入口各一次，均HTTP508；下载页、StockClassifyUse_stock.xls、默认801001成分接口均未取得分类，不能由此断言不存在港股数据。官方网页/PDF浏览工具亦超时，不以搜索摘要冒充分类数据；未改证书、代理或网络出口，不追加云端探测。
- 忽略脚本 `tmp/acceptance/hk_industry_probe_20261024.py` 共六次有界请求、最多两并发、无重试，不写业务缓存、无模型。东财RPT_PCF10_INDUSTRY_HKCVALUE仅过滤各目标，pageSize=200，每家一请求均HTTP200、success=true、pages=1，count/返回数/唯一关联代码数相等：腾讯167、阿里84、汇丰40（均含目标自身1家）。全部日期2026-10-23；分类软件服务/专业零售/银行，TYPE_ID为403010002/403012007/403008001，不称申万分类。原响应和摘要 `tmp/acceptance/hk-industry-1792812267781206900/`。
- 目标PE(TTM)/PB(MRQ)：腾讯13.150968343991/2.683800485771，阿里22.906633269078/1.599962740062，汇丰12.949679231586/1.800151517502。三池PE/PB字段无null，但腾讯PE非正93/PB非正6，阿里45/10，汇丰0/0（含目标），不能把字段有值称为全部有效估值。该响应不含市值，返回顺序不当市值排名；阿里样本包括珠宝零售公司，行业标签相同不等于业务高度可比。无中位数/正式溢价/评分计算。
- 离线样例生成器 `tmp/acceptance/build_hk_industry_example_20261024.py` 校验数量、唯一性、目标身份及单日期，排除目标后展示上游顺序五条示例，注明不是头部排名。样例 `tmp/acceptance/hk-industry-1792812267781206900/examples.md`；不追加网络。结论：现阶段真正缺口是可核验、可更新的申万港股分类映射，而不是所有免费同行估值都无法获取；尚不能承诺正式行业对比接入完成，也不擅自改用东财口径。下一步接入方式须基于分类证据另行确认。
- 用户同意继续查分类入口后，查官方公开索引及补充文件线索，不重复前轮的下载页/分类明细/成分三请求。官方2021版说明可被检索引擎提取，明确覆盖A股、港股通及重要中概股；这不是当前公司归属名单或更新凭据。来源 `https://wxweb.swsresearch.com/swsreport/2021_08/328340.pdf`，正文直接打开仍403，不能声称下载成功。非官方代码中的旧归档/字典地址只作定位线索，不采信其“旧港股名单停止更新”的陈述为本轮事实，也不取第三方镜像代替官方名单。
- 补充隔离脚本 `tmp/acceptance/sw_classification_files_probe_20261024.py` 仅请求官方SwClass2021目录下SwClass.rar和SwClassCode_2021.xls各一次，系统TLS验证开启、12秒超时、最多两并发、无重试。两者均HTTP508，未取得二进制文件、修改日期或记录。摘要 `tmp/acceptance/sw-classification-files-1792812550535211800/summary.json`；脚本退出0只代表记录完成，不代表数据获取成功。浏览工具亦无法打开归档。
- 当前无可验证的最新免费申万港股名单，不能推断名单不存在或永久不可用。按设计阶段边界暂停接线，建议由用户普通浏览器访问官方分类下载页并提供可合法使用的文件，再离线核对版本、日期、港股覆盖及目标归属；不以手工猜分类或东财池替代。只更新同主题计划与忽略的探针，执行git diff --check；未改生产、配置、版本、依赖或服务器，不跑业务pytest/Full、不提交/合并/发布。
- 用户随后提供从官方页面下载的 `C:/Users/25618/Downloads/StockClassifyUse_stock.xls`。上一轮用表格技能及内置xlrd只读检查：Sheet1共12930行（含表头）、4列（股票代码/计入日期/行业代码/更新日期），12929条记录、5934个唯一代码，全部六位数字；00700/09988/00005及.HK写法均未命中。按Excel日期系统转换，更新日期范围2015-10-26至2026-10-20，不能将不含目标港股归因于文件过期。原文件未修改，未导入业务映射。
- 用户要求继续后，优先尝试读取其已打开的官方分类页以检查“2021新版分类”等下载项：浏览控制无法接管现有标签页，独立只读标签页超时并重置运行时；未取得DOM或下载链接，不把工具失败表述为用户浏览器也无法访问。停止重复导航，未改用户页面/浏览器设置，未关闭用户标签页。官方公开索引仍只确认“2021新版分类/全部行业分类下载”两个名称，没有取得当前港股名单。
- 当前下一项最小协作是请用户下载并提供官方候选SwClass.rar（或页面“2021新版分类”实际下载文件）以只读核验，URL为 `https://www.swsresearch.com/swindex/pdf/SwClass2021/SwClass.rar`；该地址仅为候选，前轮508结果复用，不重复请求，既不保证可下载，也不宣称归档包含当前有效港股分类。按design-before-implementation继续停在调查阶段，不切生产来源；未新增业务源码/依赖/版本或测试变更。

### 其他免费申万港股分类来源筛查（2026-10-24，仅调查）

- 用户提供另一份官方归档内的 `最新个股申万行业分类(完整版-截至7月末).xlsx` 并指出仅截至2021年。本轮未独立解析该文件，不将用户提供的截止日期表述为程序核验结果；旧名单不导入当前业务映射。用户同意继续查找其他免费、可持续更新且保持申万口径的来源，按design-before-implementation停留在调查阶段。
- 米筐官方港股文档明确支持 `get_instrument_industry(..., source='sws_2021', level=1/2/3, date=..., market='hk')`，以及按行业获取港股成员和纳入剔除日期。功能口径符合候选方向，但使用说明的许可证仅列TRIAL（试用）/FULL（付费），同时提供剩余期限字段；商城提供购买与申请试用入口。未找到该港股申万数据可持续免费调用及随插件分发使用的依据，不申请试用、不安装SDK、不作为已验证免费源。依据：<https://www.ricequant.com/doc/rqdata/python/stock-hk>、<https://www.ricequant.com/doc/rqdata/python/manual>、<https://www.ricequant.com/welcome/store/>。
- 通联线索中出现 `getHKIndustrySW`（陆港通标的申万21分类），但目前仅在高校数据目录检索摘要中取得该接口名，不当作厂商接口契约或真实响应。通联官方旧商城说明的是“免费试用”，试用范围和有效期可调整；新商城需JS，未取得具体API权限和使用条款。未核实其当前免费权限、更新日期或三个目标归属，不把普通港股恒生行业接口当申万接口，也不据此断言通联没有免费方案。官方说明：<https://m.datayes.com/>；新商城：<https://mall.datayes.com/dashboard?lang=zh>；线索：<https://syzx.sufe.edu.cn/syzx/findb/hermesdit/zbsj/zgxg.htm>。
- 理杏仁官方香港行业基础信息API只列恒生hsi；申万sw/sw_2021出现在大陆行业API，不能跨市场套用。必盈官方分类表同样区分A股申万和香港恒生，未列港股申万。两者不符合本轮分类口径，未调用鉴权数据端点。依据：<https://www.lixinger.com/api/open-api/html-doc/hk/industry>、<https://www.lixinger.com/api/open-api/html-doc/cn/industry>、<https://www.biyingapi.com/doc_jj>。
- QuantSonar公开文档将申万证券归属标为FREE，但文档为A股接口、证券代码约定列SH/SZ/BJ，未取得港股申万覆盖说明；仍需API Key，不为验证自行注册账号。Tushare申万成分文档亦未建立港股覆盖及本项目免费权限的证据，不把“存在申万接口”当作港股可用。依据：<https://quantsonar.com/docs>、<https://tushare.pro/document/2?doc_id=335>、<https://tushare.pro/document/1?doc_id=108>。
- 结论为“本轮未找到同时满足当前港股归属、申万口径、持续免费及可核验使用条件的来源”，不是断言此类来源不存在。没有候选达到匿名数据探针前提，本轮只检索/读取公开页面，未取得新的港股归属响应，也没有小样本数据验收通过。保留现有A股行为及港股行业缺失状态，不用旧名单、AI判断或其他分类悄悄补齐。继续接线须先解决分类数据与使用条件，不重启已搁置的行业评分讨论。
- 仅追加本主题实施计划；没有修改生产代码、依赖、版本、配置、服务或用户原文件，不运行业务pytest/Full。工作区另一份用户队列计划保持不动，其SHA256仍为 `1B5A032B963D2BDDB4A3B62D579AE2EC8AA7FE15AB2F507744D3AB9C2319BE6F`。本轮不提交、推送、合并或发布。

### 使用用户提供的官方文件更新现有静态映射（2026-10-24）

- 用户要求先更新本地行业静态表。本轮仅数据刷新，不增加港股分类或行业评分、不改采集/分类器/Web逻辑，不扩展股票集合。沿用 `data/industry_mapping.csv` 五列及现有风格YAML；更新在主工作区进行，没有合并或部署。按表格技能只读解析源Excel，技术资源CSV通过确定性脚本机械生成；没有修改、重导出原始工作簿。
- 实际归属来源为用户下载的 `C:/Users/25618/Downloads/StockClassifyUse_stock.xls`，SHA256 `ea8debcb6345c2c7fb43b0561437d78965ab30b249fe58873768ca35e155d7bc`，12929条历史、5934个唯一代码，文件内最新更新日期2026-10-20 18:44（不代表每只股票均在该日调整）。配套行业字典为 `C:/Users/25618/Downloads/SwClass/SwClassCode_2021.xls`，SHA256 `65a2a9da1303d9d17d8fd8e5fa2708d126b86683087d9185dab4507e24f062e5`，511个唯一行业代码；字典描述分类体系，不用旧版个股名单填归属。归档目录内另一份同名成员文件仅11803条历史，与最新单独下载文件不同，未误用。
- 每个现有代码选截至2026-10-24最新已生效的“计入日期”；同一最新计入日期不同归属直接中止，同归属重复才按更新日期确定记录。数据中没有未来记录或最新同日冲突。行业代码精确关联官方字典并校验一/二/三级完整，再用既有一级→风格YAML关联；不按代码前缀或公司名称猜归属。现有5534个代码全部成功，无未匹配；来源额外400个代码混有历史/退市标的，不作为当前上市名单直接追加，也没有删除现有代码。
- 原表5534行：317 verified、5217 missing；新表5534行全部verified，代码集合及顺序不变。共5237行改变：5217条补齐，20家银行补上二级分类，其余297行完全不变，原verified记录的一级行业均未改变。CSV更新前SHA256 `08d0120cb5ea13ca66544c9f8483003d27acc64fe02bcb0370c5b1b9c09cacad`，更新后 `5b5f2f8aa13dc9dc3a33050e76bf1470f79017748c0e78b89a8b4b8ca3858965`。旧表及逐行溯源审计位于忽略目录 `tmp/acceptance/sw-static-update-20261024/industry_mapping.before.csv` 和 `audit.json`；临时脚本 `tmp/acceptance/update_sw_static_mapping_20261024.py` 未纳入生产/发布，写入使用Windows首字节锁及同目录临时文件原子替换。
- 测试先行：新增样例、整表唯一性/完整性/风格一致性和港股仍显式缺失的断言，旧表稳定复现7 failed / 7 passed。首轮另有临时目录父路径未建立导致1项fixture错误，补建唯一父路径后该环境错误消失。更新脚本首次因内置运行时缺filelock在任何写入前退出，改用与项目锁兼容的Windows标准库锁，不安装依赖、不更改项目运行时。
- 更新后运行 `.venv/Scripts/python -m pytest tests/data/test_industry_classifier.py tests/data/test_industry_mapping_builder.py tests/analysis/test_industry.py tests/analysis/test_industry_hk.py -q`（每次单独PID+GUID的basetemp/cache目录），结果 **40 passed in 1.08s**。测试文件Ruff通过、单文件Pyright **0 errors / 0 warnings**。另用独立排序方式对源历史与CSV全量核对 **5534/5534**，并验证默认IndustryClassifier（不手传CSV路径）读入5534条、新补分类可查、00700.HK仍缺失。日志保留于 `tmp/acceptance/sw-static-update-20261024/pytest.log`；尝试清理成功轮目录的命令被环境策略拒绝，未重复删除，目录 `tmp/pytest/sw-map-green-23740-666f1bb2048d4144a50e13ee71411a76/` 与红测产物均保留，不影响测试通过结论。
- 本次仅本地数据及回归测试验证，不运行Full、模型、外网或云端，不重启用户服务；已有进程若缓存了分类器，需要下次重启/重建实例后读入新表。没有验证当前上市名单完整性、全市场最新变动或港股行业可用性，不能将5534条关联成功表述为覆盖全部上市股票。另一份用户队列计划摘要保持不变，完成后执行指定文件git diff --check；未提交、推送、合并、发布或改版本。

### 港股通候选范围确认与名单采集（2026-10-24，尚未取得名单）

- 用户提出先用 `ak.stock_hk_ggt_components_em()` 补港股通标的，申万分类另补；经确认，目标和同行池均限定港股通。按design-before-implementation将范围及异常规则收敛到原规格，不另建主题文档；不改变已搁置的行业统计/评分决策，不将成员名单当行业证据。本轮预期仅准备名单与missing静态记录，不启用港股行业比较。
- 先经CodeGraph核对IndustryClassifier、资源路径和构建白名单，确认静态表能承载显式missing，但当前行业分析仍对港股整体拒绝；并读取已安装AkShare函数与分页辅助函数。接口取数地址为 `https://33.push2.eastmoney.com/api/qt/clist/get`，港股通过滤 `b:DLMK0146,b:DLMK0144`，分页100条，返回代码/名称/行情，未返回行业分类；辅助函数默认有重试，不将无界等待作为验收。
- 有界探针 `tmp/acceptance/probe_hk_connect_20261024.py` 保留AkShare接口及其解析/分页流程，仅在隔离子进程替换网络重试函数：每页一次、超时10秒、最多20页、采集预算35秒、父进程总限时45秒；成功才校验上游total、返回条数、唯一五位代码和名称，并写忽略目录研究快照。不是生产安装包修改，不关闭TLS校验。
- 首次使用现有网络环境，第一页以requests.ProxyError失败，底层RemoteDisconnected，没有HTTP响应或有效记录；第二次仅在隔离进程用Session.trust_env=False做直连对比，同一第一页以ConnectionError/RemoteDisconnected失败。两次均没有进入后续分页，未取得完整名单。直连没有修改机器代理、证书或持久配置；不据此判断IP被封、接口永久失效或港股通名单为空，不继续重试、换不明域名或扩大到云端。
- 下一步顺序保持：①取得并校验完整成员快照；②独立保存名单来源/采集时间及成员集合，为缺失标的增加 `.HK` + missing 行，并保持现有A股记录；③更新回归断言，分别验证A股完整、港股待补齐和名单资格；④来源明确后再补申万归属并讨论真正行业分析接线。目前停在①，②至④尚未实施；没有新增成员资源或生产代码，也不能宣称已把港股通加入静态表。
- 当前 `data/industry_mapping.csv` 仍为上一轮5534条完整A股映射，SHA256应保持 `5b5f2f8aa13dc9dc3a33050e76bf1470f79017748c0e78b89a8b4b8ca3858965`；不覆盖失败/空名单、不改A股回归测试。正式改动仅同主题规格及计划，运行指定文件git diff --check，不重复业务pytest/Full；未提交、推送、合并、发版、重启或部署。

### 港股通名单更新器实施步骤（2026-10-24）

- 用户再次确认来源固定为 `ak.stock_hk_ggt_components_em()`。复用干净的 `tmp/worktrees/hk-multi-source`，该分支HEAD=b23268c与dev=fdaae8f的提交树一致；仅复制本会话已有静态表、分类器测试及同主题规格/计划作为工作基线，不携带用户独立队列计划。按设计技能执行已确认范围，不再重复询问来源。续接会话按用户要求改在 dev 主工作区完成，两个新文件从该工作树迁入；临时工作树未删除。
- [x] 先写离线回归：空/缺字段/重复/非法代码、分页总数/顺序/内容不一致应拒绝；合法名单只增加missing行，A股及已核验分类不变；调出成员的旧分类保留但不延续资格；抓取失败不落盘，写入失败保留既有成功快照。
- [x] 新增独立名单模块及手动模块入口。实际调用指定AkShare函数，在隔离子进程观察其分页响应，不额外请求、不修改第三方安装文件、不在分析请求中改全局变量。校验上游total、连续分页、逐页成员与最终DataFrame一致、唯一规范代码后生成带时区采集时间的快照。子进程总预算45秒沿用有界验收参数；不增加运行时自动刷新或配置项。
- [x] 合格快照独立存储为映射旁的 `hk_stock_connect.snapshot.json`，资格仅由该快照成员集合表达；映射仍沿用五列，新成员写空分类+missing。不删除历史分类、不推断申万归属。读改写复用映射文件锁，单文件原子替换，名单最后发布；正常异常尝试恢复映射，进程崩溃最多留下不代表成员资格的missing占位，不提前开放行业分析。
- [x] 本地定向Ruff/Pyright/pytest，实际取数最多一轮；外网失败与代码测试分开报告，不把Mock数据写入正式表。完成前独立合并规格/质量审查，修复发现后复核受影响用例，不运行Full或云端，不推送/合并/部署。
- 实施与离线验收（2026-10-24）：新增 `src/data/hk_stock_connect.py`、`tests/data/test_hk_stock_connect.py`；入口为 `.venv/Scripts/python -m data.hk_stock_connect`。子进程固定UTF-8、最多20页、总预算45秒；逐页记录并与SDK最终表交叉校验，失败不落盘。发布在映射锁内先原子写映射、最后原子写快照；快照写失败回滚映射并保留旧快照。测试修正上一轮未运行的 mock 写法与 basetemp 父目录问题，覆盖空/缺列/重复/非法代码/空名称/截断/页码错/乱序/总数变化/成员不一致/无分页/naive时间/多页/失败回滚/重复表/超时/子进程复验，并验证发布后分类器仍不把港股占位当已核验。
- 独立合并规格/质量审查无阻塞；3条建议已逐条核对并处理。关键修复：`industry_mapping_builder` 原以全表行数作为A股覆盖率分母，发布候选时只写A股行；港股占位进入正式表后会误拒发布并丢失港股行。现覆盖率分母和发布分母只统计6位A股代码，发布时保留非A股行；新增“覆盖率忽略港股占位”“发布保留港股占位”用例。静态表断言改为分别校验A股块和港股missing行，不再耦合全表顺序。
- 验证：`.venv/Scripts/python -m pytest tests/data/test_hk_stock_connect.py tests/data/test_industry_classifier.py tests/data/test_industry_mapping_builder.py tests/analysis/test_industry.py tests/analysis/test_industry_hk.py -q` → **64 passed**；上述源码/测试文件 Ruff 通过、Pyright **0 errors / 0 warnings**。
- 真实取数按上限执行一轮（`.venv/Scripts/python -m data.hk_stock_connect`）：子进程首屏请求即 `requests.ProxyError`（Unable to connect to proxy / RemoteDisconnected），退出1；未取得成员、未生成快照，`data/industry_mapping.csv` SHA256 保持 `5b5f2f8aa13dc9dc3a33050e76bf1470f79017748c0e78b89a8b4b8ca3858965`，未写入空名单或Mock。外网失败不代表接口永久失效或名单为空；不重试、不绕代理、不改TLS。
- 当时的停留状态（已被下一节真实发布取代）：代码与离线测试完成，但正式映射尚未加入港股通占位行，港股行业对比仍未启用。待在可访问 `33.push2.eastmoney.com` 的网络环境重跑同一入口；成功后核对快照来源、采集时间、成员数、映射新增missing行及现有A股行不变。未提交、推送、合并、发布、重启或部署。

### 来源切换与真实发布（2026-10-24）

- 网络复核：本机直连出口为移动网络 `39.144.54.57`，系统代理 `127.0.0.1:7897`（com.vortex.helper）出口为 `61.228.90.64`；东财 `33.push2`、`82.push2`、`pushguest`、`pushlogin` 的 `clist/get` 在多次请求后均被服务端直接断开（TLS 层表现为 `schannel ... missing close_notify`），东财主站与 quote 页面正常。10 分钟冷却后单次 `pz=5` 请求恢复 HTTP 200/total=666，但随后的 `pz=1000` 及连续分页再次被重置；未继续重试、未绕代理或改TLS。结论：当前出口无法稳定完成 7 页东财采集。
- 用户批准改用上交所官方港股通标的名单作为本次成员资格来源。接口 `POST https://query.sse.com.cn/commonQuery.do?jsonCallBack=cb`，`sqlId=COMMON_SSE_JYFW_HGT_XXPL_BDZQQD_L`；页面 `https://www.sse.com.cn/services/hkexsc/disclo/eligible/`。只读取数 HTTP 200，返回697行（666只股票+31只ETF），股票数与东财 `total=666` 一致，含腾讯/阿里/汇丰，更新日期2026-10-23；ETF不进入股票同行池。原始响应与候选清单保存在忽略目录 `tmp/acceptance/hk-connect-network/`，不提交。
- 实现：快照模型新增 `sse.hkexsc.eligible` 来源、来源页面和 `data_date`；SSE采集为单次直连POST（`trust_env=False`，连接/读取超时10/30秒），校验JSONP、`pageHelp.total==len(data)`、`pageCount==1`、股票/ETF拆分、五位代码、唯一性、名称非空及单一更新日期。`--source sse|eastmoney`，默认 `sse`；东财路径与 `--collect` 保留用于交叉校验。发布仍复用映射锁、原子写和失败回滚。
- 测试：新增 `tests/resources/hk_stock_connect/sse-hk-connect-eligible-sample.jsonp` 与SSE解析/拒绝/网络失败/发布用例；修正真实响应暴露的“中文简称仅含全角空格时未回退英文简称”缺陷。`pytest tests/data/test_hk_stock_connect.py tests/data/test_industry_classifier.py tests/data/test_industry_mapping_builder.py tests/analysis/test_industry.py tests/analysis/test_industry_hk.py -q` → **86 passed**；相关源码/测试 Ruff 通过、Pyright **0 errors / 0 warnings**。`scripts/verify.ps1` 已将该测试资源映射到 `tests/data/test_hk_stock_connect.py`，PlanOnly 显示 `UnmappedFiles` 为空。
- 真实发布：`.venv/Scripts/python -m data.hk_stock_connect --source sse` 退出0，输出 `members=666, source=sse.hkexsc.eligible, data_date=2026-10-23, fetched_at=2026-10-24T13:10:05+08:00`。`data/industry_mapping.csv` 由5534行增至6200行（5534 A股 + 666港股），SHA256 `5b5f2f8aa13dc9dc3a33050e76bf1470f79017748c0e78b89a8b4b8ca3858965` → `15693e6afe02f12d5439bb0a5dd284560e5f5c9d46df27e959a23726883d6137`；A股5534行逐行一致且仍在前部；港股666行全部为空分类+missing且唯一。`data/hk_stock_connect.snapshot.json` 47,825字节，成员集合与港股行、SSE候选清单完全一致，`pages` 为单页 total/count=666。
- 未完成/风险：尚未用东财完整666条做逐条交叉校验（接口仍限流），目前仅核对数量及4个样本代码（09999/09995/09993/09992）；申万港股分类来源、同行池、负值/中位数/评分口径、行业分析接线与报告展示仍未解决，港股行业对比尚未启用。本次未提交、推送、合并、部署或重启服务。

### 港股 HSICS 行业快照与同业对比实施（2026-10-24）

- 方案切换：用户选择港股本地标准，不再等待申万官方港股映射。东财 F10 `BELONG_INDUSTRY` 经抽样确认是恒生二级业务类别（31 类）；`INDUSTRY_TYPE` 为 GICS 四层，不用于本方案；恒生综合指数每日成分 PDF 提供 12 个一级行业。东财 F10 是转载源，报告与快照保留来源/校验标注。
- 只读验证（`tmp/acceptance/hk-hsics-probe/`，忽略目录）：666 只港股通全部取得 F10 二级；HSCI 每日 PDF（2026-10-23）解析 580 只、12 个一级行业；559 只重叠；31→12 交叉表 0 冲突、0 父级不一致；666/666 可映射，107 只非 HSCI 港股通也通过交叉表覆盖。腾讯 `软件服务→Information Technology`、阿里 `专业零售→Consumer Discretionary`、汇丰 `银行→Financials`。
- 静态数据与入口：`data/hk_hsics_industry.csv`（666 行，`symbol,hsics_sector,hsics_industry,mapping_status,in_hsci`）与 `data/hk_hsics_industry.snapshot.json`（来源、`data_date=2026-10-23`、CSV SHA256、31→12 crosswalk、`baseline_total=580`、`parent_mismatches=0`、`unmapped_sectors=0`、`missing_rows=0`、`non_hsci_covered=107`）。手动刷新入口 `python -m data.hk_hsics_industry`；`hk_stock_connect.snapshot.json`、`hk_hsics_industry.csv/.snapshot.json` 已加入 wheel 资源白名单。
- 代码：`src/data/hk_hsics_industry.py`（F10/PDF/crosswalk/CSV+JSON 原子发布/读取/CLI）、`src/data/hk_hsics_peers.py`（31 行业同行池 + `HKIndustryAdapter`）、`src/data/enrichers/hk_industry_enricher.py`、`IndustryAnalyzer` 港股五态、`report` 标签/数据质量/模板/前端文案、`scripts/verify.ps1` 资源映射。港股行业数据不落通用 SQLite 缓存（PrivateAttr 同行估值无法序列化）。
- 测试：新增/更新 `tests/data/test_hk_hsics_industry.py`、`tests/data/test_hk_hsics_peers.py`、`tests/data/enrichers/test_hk_industry_enricher.py`、`tests/analysis/test_industry_hk.py`、`tests/core/test_pipeline_hk_industry_cache.py`；相关批次 **304 passed**，Ruff 通过、Pyright 0 errors/0 warnings；前端资源版本统一为 `20261024-hk-industry`。
- 真实管线（`build_agent_core(llm_enabled=False)`，仅 industry）：00700 = 45 家同行、PE 中位数 28.36、PB 中位数 3.10、相对 PE -53.60%、PE/PB 分位 26.10%/42.20%；09988 = 10 家、13.42/1.37、+70.70%；00005 = 21 家、6.35/0.52、+104.00%；00011（非港股通）返回“非港股通标的，不参与港股行业对比”。报告“所属行业”为 `恒生HSICS：软件服务`，数据质量显示来源 `东财F10+恒生HSICS`、截止 2026-10-23。
- 风险与未做：F10 为恒生标签转载源，对外分发前需确认许可/署名；31→12 交叉表来自 HSCI 559 只重叠样本；部分二级同行少于 3 家；报告会列出全部同行（00700 为 45 家），后续可考虑限制展示数量；港股不计算行业评分；不混 A 股申万池；不自动刷新。未提交、推送、合并、部署或重启服务。
- 独立规格/质量审查发现并修复：F10 过滤补 `.HK`（原实现会取空 profile 并以 666 行 missing 覆盖旧快照），增加 success/sector 与 31/12、missing_rows 严格校验；`MANIFEST.in` 补 3 个 hk 文件；安装包路径改为按具体文件/CSV+JSON 成对回退；同行 `<3` 统一判不足，目标必须仍在港股通快照，行业池按最新报告日过滤，前端目标 PE/PB/分位补格式化。新增 F10 `.HK` 过滤、缺失拒绝、发布回滚、安装路径回退、同行 2/3/8 边界、最新报告日测试；真实 CLI 刷新以修复后的代码成功发布 666/31/12。
- 真实浏览器验收（Edge headless + CDP，真实 `stock-robot run --port 8765`）：打开报告库 19:33 的 00700 报告，研报视图显示 `恒生HSICS：软件服务`、45 家同行、目标 PE/PB、中位数、分位和溢价，前端资源版本为 `20261024-hk-industry`；截图 `tmp/acceptance/hk-browser/report-library-fixed.png`。验收同时发现 CLI `_get_registry` 未注册 `HKIndustryAdapter`，导致 CLI 生成的报告行业为空；已修复并新增 `test_cli_registry_registers_hk_industry_adapter`。
- 最终全量：`.\scripts\verify.ps1 -Scope Full -VenvPath D:/code/stock_robot/.venv` → **2470 passed / 4 skipped**（217.42 秒），Ruff/Pyright 通过，前端资源版本链测试通过。未提交、推送、合并、部署或重启用户服务。

### HSICS 收尾实施（2026-10-24）

- 移除 `industry_mapping.csv` 中 666 条 `.HK` missing 占位，保留原有五列、5,534 条 A 股及其原始顺序和逐行内容；港股通成员资格从此仅由 `hk_stock_connect.snapshot.json` 表达，HSICS 分类仅由独立 CSV/JSON 表达。成员发布改为快照专用文件锁与原子写，不再读取、校验或回滚申万映射。
- 港股行业分析仅将同行代码和名称的展示列表截为前 10 家，新增完整同行数量及范围说明；完整 `EnrichedIndustry` 仍提供同行数、PE/PB 中位数、分位和溢价。Markdown 与 Web 标签同步更新，静态资源版本链更新为 `20261024-hk-industry-final`。
- 新增 `stock-robot hk-industry-refresh`：先执行上交所成员抓取/发布，再读取成员快照刷新 HSICS；成员成功后 HSICS 失败不回滚前者，命令明确输出该独立失败状态。模块级成员与 HSICS 刷新入口仍保留。
- 收尾后全量：`.\scripts\verify.ps1 -Scope Full -VenvPath D:/code/stock_robot/.venv` → **2475 passed / 4 skipped**（358.77 秒），Ruff/Pyright 通过。Codex worktree 内 `tests/test_cli.py` 曾因 PATH 环境在 30 项后挂起，使用主 venv + PYTHONPATH 复跑为 **53 passed**；主工作区全量已覆盖该文件。收尾提交 `e50a1d3`，合并提交 `4121bcf`；未推送、未部署，工作区仅保留用户自己的队列计划未提交改动。
