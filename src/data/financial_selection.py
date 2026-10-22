"""财务包选择规则：先整包，再以明确证据补充等效缺项。

本模块不采集网络数据、不猜测供应商口径，也不定义业务必需指标。
调用方负责提供包内所需报告期、必需字段和派生计算的依赖组。
"""

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date

from data.schemas import FinancialData

logger = logging.getLogger(__name__)

# 仅允许填补数值事实，来源、期间和算法标记不能作为普通字段覆盖。
NUMERIC_FIELDS = frozenset({
    "revenue", "net_profit", "deducted_net_profit", "total_assets", "total_equity",
    "common_equity", "operating_cash_flow", "operating_cash_flow_per_share",
    "roe", "gross_margin", "basic_eps",
})


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
            all(isinstance(value, str) and bool(value.strip()) and value.strip().lower() not in unknown for value in (
                self.definition, self.unit, self.currency, self.scope, self.accounting, self.revision,
            ))
            and self.basis in {"cumulative", "single", "instant"}
            and (type(self.start) is date or (self.start is None and self.basis == "instant"))
        )


@dataclass
class FinancialPackage:
    """一个来源的财务候选；positions 缺失时允许整包使用，但不允许跨源补位。"""

    source: str
    rows: list[FinancialData]
    positions: dict[date, dict[str, Position]]


@dataclass
class FinancialSelection:
    package: FinancialPackage | None = None
    complete: bool = False
    supplements: dict[date, dict[str, str]] = field(default_factory=dict)


def _valid(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _row_has_facts(row: FinancialData) -> bool:
    """选源保护全部已有数值字段，不把只有 EPS/扣非/普通权益的期间误当全空。"""
    return any(_valid(getattr(row, name)) for name in NUMERIC_FIELDS)


def _usable(package: FinancialPackage, symbol: str) -> bool:
    """混入其他标的或重复期间的包不参与选择，禁止隐式最后一条获胜。"""
    return (
        bool(package.source.strip()) and bool(package.rows)
        and len({row.symbol for row in package.rows}) == 1
        and package.rows[0].symbol == symbol
        and len({row.fiscal_quarter for row in package.rows}) == len(package.rows)
        and any(_row_has_facts(row) for row in package.rows)
    )


def _complete(
    package: FinancialPackage, required: frozenset[str], required_periods: frozenset[date],
    completeness: Callable[[FinancialPackage], bool] | None,
) -> bool:
    if not package.rows or not required_periods <= {row.fiscal_quarter for row in package.rows}:
        return False
    if completeness is not None:
        return completeness(package)
    return all(_valid(getattr(row, name)) for row in package.rows for name in required)


def _compatible(
    primary: FinancialPackage, donor: FinancialPackage, period: date, name: str,
) -> bool:
    left = primary.positions.get(period, {}).get(name)
    right = donor.positions.get(period, {}).get(name)
    if left is None or right is None or not left.proven() or not right.proven() or left != right:
        return False
    if left.start is not None and left.start > period:
        return False
    if name == "operating_cash_flow" and left.calculation not in {"reported", "estimated"}:
        return False
    for package in (primary, donor):
        row = next(row for row in package.rows if row.fiscal_quarter == period)
        if (
            (row.report_currency and row.report_currency != left.currency)
            or (left.basis != "instant" and row.period_start is not None and row.period_start != left.start)
            or (left.basis != "instant" and row.period_basis != "unknown" and row.period_basis != left.basis)
            or (name == "operating_cash_flow" and row.cash_flow_period_start is not None
                and row.cash_flow_period_start != left.start)
            or (name == "operating_cash_flow" and _valid(row.operating_cash_flow)
                and row.cash_flow_basis != left.calculation)
        ):
            return False
    return True


def _dependency_safe(
    primary: FinancialPackage, donor: FinancialPackage, row: FinancialData, other: FinancialData,
    name: str, dependencies: Sequence[frozenset[str]],
) -> bool:
    for group in dependencies:
        if name not in group:
            continue
        # 即使整组都缺失，也不能把不同修订版、合并范围或币种拼成可计算的一组。
        if not all(_compatible(primary, donor, row.fiscal_quarter, related) for related in group):
            return False
        positions = primary.positions[row.fiscal_quarter]
        contexts = {
            (positions[key].currency, positions[key].scope, positions[key].accounting, positions[key].revision)
            for key in group
        }
        periods = {(positions[key].start, positions[key].basis) for key in group if positions[key].basis != "instant"}
        if len(contexts) != 1 or len(periods) > 1:
            return False
        for related in group - {name}:
            existing = getattr(row, related)
            if not _valid(existing):
                continue
            # 候选必须能提供同版本的依赖事实；不以另一个字段的相等推断本字段等效。
            candidate = getattr(other, related)
            if (
                not _compatible(primary, donor, row.fiscal_quarter, related)
                or not _valid(candidate) or candidate != existing
            ):
                return False
    return True


def select_financial(
    providers: Sequence[tuple[str, Callable[[], FinancialPackage]]], *,
    symbol: str,
    required: frozenset[str],
    required_periods: frozenset[date] = frozenset(),
    completeness: Callable[[FinancialPackage], bool] | None = None,
    dependencies: Sequence[frozenset[str]] = (),
    on_error: Callable[[str, Exception], None] | None = None,
) -> FinancialSelection:
    """按优先级延迟取包；全部不完整后才进行严格等效补位。

    完整性针对包内全部报告期及调用方明确要求的期间。选择器不扩大窗口、不推断上市日，
    也不以旧完整包覆盖已经观察到的更新报告期。有效主值永不覆盖。
    """
    configured = required | frozenset().union(*dependencies)
    if not required or configured - NUMERIC_FIELDS:
        raise ValueError("财务必需字段和依赖字段必须为已知数值字段，且必需字段不能为空")
    if not symbol.strip():
        raise ValueError("财务标的不能为空")

    candidates: list[FinancialPackage] = []
    selected: FinancialPackage | None = None
    latest: date | None = None
    for label, fetch in providers:
        try:
            candidate = fetch()
        except Exception as error:  # noqa: BLE001 — 供应商调用隔离，失败不阻断后续来源
            logger.warning("财务候选来源失败 %s：%s", label, type(error).__name__)
            if on_error is not None:
                on_error(label, error)
            continue
        if not _usable(candidate, symbol):
            logger.debug("财务候选为空或标的/期间无效：%s", label)
            continue
        # 只有日期的空壳不是“更新的部分财报事实”。先检查原包身份/重复，再隔离空行，
        # 防止空日期阻断备用或被下游当作最新快照；有任一实际分析值的期间仍保留。
        facts = [row for row in candidate.rows if _row_has_facts(row)]
        if len(facts) != len(candidate.rows):
            candidate = FinancialPackage(
                candidate.source, facts,
                {row.fiscal_quarter: candidate.positions.get(row.fiscal_quarter, {}) for row in facts},
            )
        head = max(row.fiscal_quarter for row in candidate.rows)
        candidates.append(candidate)
        if latest is not None and head < latest:
            continue
        if _complete(candidate, required, required_periods, completeness):
            return FinancialSelection(package=candidate, complete=True)
        if selected is None or latest is None or head > latest:
            selected, latest = candidate, head

    if selected is None:
        return FinancialSelection()

    # 复制输入，避免本次补位污染供应商响应或其他线程持有的原始包。
    output = FinancialPackage(
        selected.source, [row.model_copy(deep=True) for row in selected.rows],
        {period: dict(positions) for period, positions in selected.positions.items()},
    )
    supplements: dict[date, dict[str, str]] = {}
    for donor in candidates:
        if donor is selected:
            continue
        rows = {row.fiscal_quarter: row for row in donor.rows}
        for row in output.rows:
            other = rows.get(row.fiscal_quarter)
            if other is None:
                continue
            # 同轮依赖核对基于补位前快照，避免字段遍历顺序影响决策。
            original = row.model_copy(deep=True)
            updates = {}
            for name in sorted(NUMERIC_FIELDS):
                if (
                    not _valid(getattr(original, name)) and _valid(getattr(other, name))
                    and _compatible(output, donor, row.fiscal_quarter, name)
                    and _dependency_safe(output, donor, original, other, name, dependencies)
                ):
                    updates[name] = getattr(other, name)
            # OCF、毛利率附带算法口径，不能只搬数字而丢掉披露/推算标记。
            for name, value in updates.items():
                setattr(row, name, value)
                supplements.setdefault(row.fiscal_quarter, {})[name] = donor.source
            if "operating_cash_flow" in updates:
                row.cash_flow_basis = other.cash_flow_basis
                row.cash_flow_period_start = other.cash_flow_period_start
            if "gross_margin" in updates:
                row.gross_margin_basis = other.gross_margin_basis
    return FinancialSelection(output, _complete(output, required, required_periods, completeness), supplements)
