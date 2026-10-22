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
