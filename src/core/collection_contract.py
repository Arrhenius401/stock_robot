"""取数入口契约：迁移能力、遗留清单和静态检查。"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

MIGRATED_CAPABILITIES: frozenset[tuple[str, str]] = frozenset({
    ("a-shares", "financial"),
    ("hk", "financial"),
    ("a-shares", "price"),
    ("hk", "price"),
    ("a-shares", "news"),
})


DEDICATED_CAPABILITIES: frozenset[tuple[str, str]] = frozenset({
    ("hk", "news"),
    ("a-shares", "valuation"),
    ("hk", "valuation"),
    ("a-shares", "industry"),
    ("hk", "industry"),
})
GOLD_CAPABILITIES: frozenset[tuple[str, str]] = frozenset({
    ("gold", key) for key in (
        "domestic", "international", "real_yield", "dollar", "fx",
        "china_reserves", "holdings", "global_reserves",
    )
})
CONTRACT_CAPABILITIES = MIGRATED_CAPABILITIES | DEDICATED_CAPABILITIES | GOLD_CAPABILITIES


@dataclass(frozen=True)
class FetchSite:
    path: str
    function: str
    data_types: tuple[str | None, ...]
    reason: str
    phase: str


LEGACY_FETCH_SITES: tuple[FetchSite, ...] = (
    FetchSite(
        "src/data/base.py", "DataSource.fetch_result", (None,),
        "遗留适配器兼容桥；五组能力必须显式覆盖", "随来源迁移逐步淘汰",
    ),
)

ALLOWED_FETCH_RESULT_CALLS: frozenset[tuple[str, str]] = frozenset({
    ("src/backtest/data.py", "HistoricalPriceProvider.fetch_stock_result"),
    ("src/core/resilient_collection.py", "ResilientCollector._collect"),
    ("src/core/pipeline.py", "Pipeline.collect.fetch_one"),
    ("src/core/pipeline.py", "Pipeline._collect_hk_news"),
    ("src/gold/data.py", "GoldCollector._request"),
    ("src/index/collector.py", "IndexDataCollector.collect._fetch_price"),
    ("src/index/collector.py", "IndexDataCollector.collect._fetch_valuation"),
    ("src/index/collector.py", "IndexDataCollector.collect._fetch_capital_flow"),
    ("src/index/collector.py", "IndexDataCollector.collect._fetch_macro"),
    ("src/index/collector.py", "IndexDataCollector.collect._fetch_sentiment"),
})


@dataclass(frozen=True)
class _CallSite:
    path: str
    function: str
    line: int
    method: str
    data_type: str | None


class _CallVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self._path = path
        self._stack: list[str] = []
        self.calls: list[_CallSite] = []
        self._gold_fetch_names: set[str] = {"fetch_source"}

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == "gold.sources":
            self._gold_fetch_names.update(alias.asname or alias.name for alias in node.names if alias.name == "fetch_source")
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        in_gold = self._path.startswith("src/gold/")
        gold_provider = (
            isinstance(node.func, ast.Attribute)
            and (node.func.attr == "fetch_source" or (in_gold and node.func.attr == "request_executor"))
            or isinstance(node.func, ast.Name)
            and (
                node.func.id in self._gold_fetch_names
                or (in_gold and node.func.id == "bounded_provider_call")
            )
        )
        if gold_provider:
            self.calls.append(_CallSite(
                path=self._path, function=".".join(self._stack) or "<module>",
                line=node.lineno, method="gold_provider", data_type=None,
            ))
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"fetch", "fetch_result"}:
            data_type, has_data_type, has_kwargs = self._call_shape(node)
            if node.func.attr == "fetch_result" or has_data_type or has_kwargs:
                self.calls.append(_CallSite(
                    path=self._path,
                    function=".".join(self._stack) or "<module>",
                    line=node.lineno,
                    method=node.func.attr,
                    data_type=data_type if has_data_type else None,
                ))
        self.generic_visit(node)

    @staticmethod
    def _call_shape(node: ast.Call) -> tuple[str | None, bool, bool]:
        data_type: str | None = None
        has_data_type = False
        has_kwargs = any(keyword.arg is None for keyword in node.keywords)
        for keyword in node.keywords:
            if keyword.arg == "data_type":
                has_data_type = True
                if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                    data_type = keyword.value.value
        return data_type, has_data_type, has_kwargs


def _matches(site: FetchSite, call: _CallSite) -> bool:
    return (
        site.path == call.path
        and site.function == call.function
        and (None in site.data_types or call.data_type in site.data_types)
    )


def find_violations(root: Path, sites: tuple[FetchSite, ...] = LEGACY_FETCH_SITES) -> list[str]:
    violations: list[str] = []
    used: set[FetchSite] = set()
    source_root = root / "src"
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except (OSError, SyntaxError) as error:
            violations.append(f"{relative}: 无法解析：{error}")
            continue
        visitor = _CallVisitor(relative)
        visitor.visit(tree)
        for call in visitor.calls:
            if call.method == "gold_provider":
                if (call.path, call.function) != ("src/gold/adapter.py", "GoldSourceAdapter._collect"):
                    violations.append(f"{call.path}:{call.line} {call.function} 绕过黄金来源适配器")
                continue
            if call.method == "fetch_result":
                if (call.path, call.function) not in ALLOWED_FETCH_RESULT_CALLS:
                    violations.append(
                        f"{call.path}:{call.line} {call.function} 调用 fetch_result() 未声明"
                    )
                continue
            matches = [site for site in sites if _matches(site, call)]
            if not matches:
                data_type = call.data_type or "<动态>"
                violations.append(
                    f"{call.path}:{call.line} {call.function} 新增未声明的 fetch(data_type={data_type})"
                )
            else:
                used.update(matches)
    for site in sites:
        if site not in used:
            violations.append(
                f"{site.path} 清单条目 {site.function} 已不存在，请更新迁移台账"
            )
    return violations


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    violations = find_violations(root)
    if not violations:
        print("数据取数入口检查通过")
        return 0
    for violation in violations:
        print(violation, file=sys.stderr)
    return 1
