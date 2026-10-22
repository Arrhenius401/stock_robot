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
