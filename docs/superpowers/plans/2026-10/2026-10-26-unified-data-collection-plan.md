# 统一数据获取规范 Phase 0 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 A/HK 财务、A/HK 行情和 A 股新闻五组能力迁移到“统一结果入口＋公共机制＋业务策略”，并增加运行时与静态入口门禁，保持现有用户可见行为和专用路径不变。

**Architecture:** `DataSource.fetch_result()` 返回 `CollectionResult(rows, record)`；`ResilientCollector` 只负责缓存、TTL、失败等待、刷新、断路器、并发和参考隔离；`collection_policies.py` 按 `(market, kind)` 判断业务状态。`collection_contract.py` 维护迁移能力常量、运行时契约检查和静态遗留清单。旧 `fetch()` 保留给指数、回测、港股新闻等遗留路径。

**Tech Stack:** Python 3.11、Pydantic v2、SQLite、pytest、pytest-mock、Ruff、Pyright、PowerShell 5+。

**依据设计：** `docs/superpowers/specs/2026-10/2026-10-26-unified-data-collection-design.md`。

**执行约束：**

- 日常只跑改动范围检查；`.\scripts\verify.ps1 -Scope Full` 只在合并、发布或用户明确要求时执行。
- Python 命令显式使用 `.venv/Scripts/python.exe`、`.venv/Scripts/ruff.exe`、`.venv/Scripts/pyright.exe`。
- pytest 使用 PID＋GUID 唯一临时目录；不要复用固定目录。手动执行 pytest 前必须先运行 `New-Item -ItemType Directory -Path $run -Force | Out-Null`；否则 `--basetemp="$run/basetemp"` 的父目录不存在，pytest 会在 fixture 初始化时报 `FileNotFoundError`。
- 提交按完整事项组织；本计划建议 4 笔：设计/计划文档、基础契约、收集器迁移、入口门禁。每笔提交前运行对应范围检查和 `git diff --cached --check`。
- 不推送、不合并、不发布；不修改港股估值、港股新闻、行业、指数、雷达和辅助补取的业务逻辑。

---

## Task 1: 增加结果状态与统一取数结果

**Files:**
- Modify: `src/data/schemas.py`
- Modify: `src/data/base.py`
- Test: `tests/data/test_schemas.py`
- Test: `tests/data/test_base.py`

- [ ] **Step 1: 写失败测试（结果状态默认值与往返）**

在 `tests/data/test_schemas.py` 顶部的 `data.schemas` 导入列表中加入 `DataCollectionRecord`，然后在文件末尾添加：

```python
def test_collection_record_defaults_result_state_unknown():
    record = DataCollectionRecord()
    assert record.result_state == "unknown"
    assert record.cache_state == "unknown"


def test_collection_record_result_state_roundtrip():
    record = DataCollectionRecord(result_state="partial", cache_state="degraded")
    restored = DataCollectionRecord.model_validate_json(record.model_dump_json())
    assert restored.result_state == "partial"
    assert restored.cache_state == "degraded"


def test_legacy_collection_record_without_result_state_is_unknown():
    record = DataCollectionRecord.model_validate_json(
        '{"source": "旧来源", "cache_state": "complete"}'
    )
    assert record.result_state == "unknown"
    assert record.cache_state == "complete"
```

在 `tests/data/test_base.py` 顶部的导入区改为：

```python
from datetime import date

import pytest

from data.base import CollectionResult, DataSource
from data.schemas import DataCollectionRecord, PriceData
```

然后在文件末尾添加：

```python
class DiagnosticSource(DataSource):
    def supports(self, market, data_type):
        return market == "a-shares" and data_type == "price"

    def fetch(self, symbol, **kwargs):
        return [PriceData(
            symbol=symbol, trade_date=date(2026, 10, 20),
            open=1, high=2, low=0.5, close=1.5, volume=10,
        )]

    def get_collection_error(self):
        return DataCollectionRecord(
            source="测试源", error_kind="timeout", error_message="请求超时"
        )


def test_fetch_result_returns_rows_and_diagnostic():
    result = DiagnosticSource().fetch_result(
        "000001", data_type="price", market="a-shares"
    )
    assert isinstance(result, CollectionResult)
    assert result.rows[0].symbol == "000001"
    assert result.record is not None
    assert result.record.error_kind == "timeout"
```

- [ ] **Step 2: 运行测试确认失败**

```powershell
$run = "tmp/pytest/phase0-task1-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/data/test_schemas.py tests/data/test_base.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

预期：失败，`DataCollectionRecord` 没有 `result_state`，`DiagnosticSource` 没有 `fetch_result`。

- [ ] **Step 3: 在 `src/data/schemas.py` 增加结果状态**

在 `DataCollectionRecord` 定义前增加类型别名：

```python
ResultState = Literal["complete", "partial", "empty", "not_applicable", "failed", "unknown"]
```

把 `DataCollectionRecord` 改为：

```python
class DataCollectionRecord(BaseModel):
    """采集出处与时效，旧缓存未知值保持空缺。"""
    source: str = ""
    collected_at: datetime | None = None
    cache_status: Literal["live", "cache", "stale", "unknown"] = "unknown"
    error_kind: str = ""
    error_message: str = ""
    cache_state: Literal["complete", "degraded", "not_applicable", "unknown"] = "unknown"
    result_state: ResultState = "unknown"
```

- [ ] **Step 4: 在 `src/data/base.py` 增加 `CollectionResult` 和默认兼容桥**

把文件改为：

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from data.schemas import DataCollectionRecord


@dataclass(frozen=True)
class CollectionResult:
    """统一取数结果：数据和诊断在同一次调用中返回。"""
    rows: list[Any]
    record: DataCollectionRecord | None = None


class DataSource(ABC):
    """数据源抽象接口 — 所有数据适配器需实现此接口"""

    @abstractmethod
    def supports(self, market: str, data_type: str) -> bool:
        """是否支持指定市场和数据类型"""
        ...

    @abstractmethod
    def fetch(self, symbol: str, **kwargs) -> list:
        """获取数据，返回 Pydantic 模型列表"""
        ...

    def fetch_result(self, symbol: str, **kwargs) -> CollectionResult:
        """未迁移适配器的兼容桥；五组能力的数据源必须显式覆盖。"""
        rows = self.fetch(symbol, **kwargs)
        reader = getattr(self, "get_collection_error", None)
        diagnostic = reader() if callable(reader) else None
        return CollectionResult(
            rows=rows or [],
            record=diagnostic if isinstance(diagnostic, DataCollectionRecord) else None,
        )
```

- [ ] **Step 5: 运行测试确认通过**

```powershell
$run = "tmp/pytest/phase0-task1-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/data/test_schemas.py tests/data/test_base.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

预期：全部通过。

- [ ] **Step 6: 单文件检查**

```powershell
.\.venv\Scripts\ruff.exe check src/data/schemas.py src/data/base.py tests/data/test_schemas.py tests/data/test_base.py
.\.venv\Scripts\pyright.exe src/data/schemas.py src/data/base.py tests/data/test_schemas.py tests/data/test_base.py
```

预期：无错误。本任务暂不提交，等 Task 2、Task 3 完成后按基础契约整体提交。

---

## Task 2: 增加契约常量、遗留清单与五组业务策略

**Files:**
- Create: `src/core/collection_contract.py`
- Create: `src/data/collection_policies.py`
- Test: `tests/data/test_collection_policies.py`
- Test: `tests/core/test_collection_contract.py`

- [ ] **Step 1: 写策略与契约测试**

创建 `tests/data/test_collection_policies.py`：

```python
"""五组能力的有效性、状态和参考窗口判断。"""
from datetime import date, datetime, timedelta

from data.collection_policies import POLICIES, policy_for
from data.hk_news import HK_TZ
from data.schemas import (
    DataCollectionRecord,
    FinancialData,
    NewsData,
    PriceData,
    RawSentimentData,
    RawSentimentItem,
)

NOW = datetime(2026, 10, 26, 12, tzinfo=HK_TZ)


def prices(count: int = 150, symbol: str = "000001") -> list[PriceData]:
    return [
        PriceData(
            symbol=symbol,
            trade_date=date(2026, 10, 25) - timedelta(days=index),
            open=10, high=11, low=9, close=10, volume=100,
        )
        for index in range(count)
    ]


def news_item(title: str, publish_date: date | None = None) -> RawSentimentItem:
    return RawSentimentItem(
        title=title, source="news", publish_date=publish_date or NOW.date()
    )


def news(rows: list[RawSentimentItem], symbol: str = "000001") -> list[NewsData]:
    row = NewsData(symbol=symbol, date=NOW.date(), headlines=[item.title for item in rows])
    row._raw_sentiment = RawSentimentData(
        symbol=symbol, fetch_date=NOW.date(), items=rows
    )
    return [row]


def financial_rows(status: str = "complete", symbol: str = "000001") -> list[FinancialData]:
    return [
        FinancialData(
            symbol=symbol,
            fiscal_quarter=date(2026, 9, 30) - timedelta(days=90 * index),
            revenue=100,
            net_profit=10,
            total_assets=100,
            total_equity=50,
            operating_cash_flow=12,
            basic_eps=1,
            report_currency="CNY",
            financial_selection_status=status,
        )
        for index in range(4)
    ]


def test_policy_registry_covers_migrated_capabilities():
    expected = {
        ("a-shares", "financial"),
        ("hk", "financial"),
        ("a-shares", "price"),
        ("hk", "price"),
        ("a-shares", "news"),
    }
    assert set(POLICIES) == expected
    assert all(policy.source_attempts == 2 for policy in POLICIES.values())


def test_financial_policy_complete_partial_failed_and_not_applicable():
    policy = policy_for("a-shares", "financial")
    assert policy is not None
    assert policy.classify("000001", financial_rows("complete"), None, None, NOW) == "complete"
    assert policy.classify("000001", financial_rows("partial"), None, None, NOW) == "partial"
    assert policy.classify("000001", [], None, None, NOW) == "failed"
    diagnostic = DataCollectionRecord(cache_state="not_applicable")
    assert policy.classify("000001", [], diagnostic, None, NOW) == "not_applicable"


def test_price_policy_coverage_and_invalid_rows():
    policy = policy_for("a-shares", "price")
    assert policy is not None
    full = prices(150)
    short = prices(20)
    assert policy.classify("000001", full, None, None, NOW) == "complete"
    assert policy.classify("000001", short, None, full, NOW) == "partial"
    assert policy.fetch_constraints(full, NOW)["minimum_points"] == 150
    invalid = prices(1)
    invalid[0].close = -1
    assert policy.classify("000001", invalid, None, None, NOW) == "failed"
    diagnostic = DataCollectionRecord(cache_state="not_applicable")
    assert policy.classify("000001", short, diagnostic, full, NOW) == "not_applicable"


def test_news_policy_empty_error_partial_and_complete():
    policy = policy_for("a-shares", "news")
    assert policy is not None
    empty = news([])
    assert policy.classify("000001", empty, None, None, NOW) == "empty"
    error = DataCollectionRecord(error_kind="timeout", error_message="新闻接口失败")
    assert policy.classify("000001", empty, error, None, NOW) == "failed"
    one = news([news_item("消息")])
    assert policy.classify("000001", one, error, None, NOW) == "partial"
    assert policy.classify("000001", one, None, None, NOW) == "complete"


def test_news_policy_requires_raw_sentiment_and_rejects_future_items():
    policy = policy_for("a-shares", "news")
    assert policy is not None
    without_raw = [NewsData(symbol="000001", date=NOW.date(), headlines=["标题"])]
    assert policy.classify("000001", without_raw, None, None, NOW) == "failed"
    future = news([news_item("未来", NOW.date() + timedelta(days=1))])
    assert policy.classify("000001", future, None, None, NOW) == "failed"


def test_news_reference_filters_30_day_window_and_rebuilds_headlines():
    policy = policy_for("a-shares", "news")
    assert policy is not None
    old = news_item("旧消息", NOW.date() - timedelta(days=40))
    recent = news_item("新消息", NOW.date() - timedelta(days=1))
    reference = policy.reference_rows(news([old, recent]), NOW)
    assert reference is not None
    assert reference[0].headlines == ["新消息"]
```

创建 `tests/core/test_collection_contract.py`：

```python
"""五组能力常量和遗留清单的基本事实。"""
from core.collection_contract import LEGACY_FETCH_SITES, MIGRATED_CAPABILITIES


def test_migrated_capabilities_are_the_five_expected_pairs():
    assert MIGRATED_CAPABILITIES == frozenset({
        ("a-shares", "financial"),
        ("hk", "financial"),
        ("a-shares", "price"),
        ("hk", "price"),
        ("a-shares", "news"),
    })


def test_legacy_fetch_sites_declare_current_migration_ledger():
    paths = {site.path for site in LEGACY_FETCH_SITES}
    assert {
        "src/data/base.py",
        "src/core/pipeline.py",
        "src/index/collector.py",
        "src/backtest/data.py",
    } <= paths
    assert all(site.phase for site in LEGACY_FETCH_SITES)
```

- [ ] **Step 2: 运行测试确认失败**

```powershell
$run = "tmp/pytest/phase0-task2-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/data/test_collection_policies.py tests/core/test_collection_contract.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

预期：失败，模块不存在。

- [ ] **Step 3: 创建 `src/core/collection_contract.py` 的常量和清单**

```python
"""取数入口契约：迁移能力、遗留清单和静态检查。"""
from __future__ import annotations

from dataclasses import dataclass

MIGRATED_CAPABILITIES: frozenset[tuple[str, str]] = frozenset({
    ("a-shares", "financial"),
    ("hk", "financial"),
    ("a-shares", "price"),
    ("hk", "price"),
    ("a-shares", "news"),
})


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
    FetchSite(
        "src/core/pipeline.py", "Pipeline.collect.fetch_one", (None,),
        "旧路径；运行时保护禁止五组能力进入", "Phase 1",
    ),
    FetchSite(
        "src/core/pipeline.py", "Pipeline._collect_hk_news", ("news",),
        "港股新闻专用双查询预算，保留专用缓存", "Phase 1",
    ),
    FetchSite(
        "src/index/collector.py", "IndexDataCollector.collect._fetch_price", ("index_price",),
        "指数专用采集", "Phase 2",
    ),
    FetchSite(
        "src/index/collector.py", "IndexDataCollector.collect._fetch_valuation", ("index_valuation",),
        "指数专用采集", "Phase 2",
    ),
    FetchSite(
        "src/index/collector.py", "IndexDataCollector.collect._fetch_capital_flow", ("index_capital_flow",),
        "指数专用采集", "Phase 2",
    ),
    FetchSite(
        "src/index/collector.py", "IndexDataCollector.collect._fetch_macro", ("index_macro",),
        "指数专用采集", "Phase 2",
    ),
    FetchSite(
        "src/index/collector.py", "IndexDataCollector.collect._fetch_sentiment", ("index_sentiment",),
        "指数专用采集", "Phase 2",
    ),
    FetchSite(
        "src/backtest/data.py", "HistoricalPriceProvider.fetch_stock", ("price",),
        "回测自管区间、连续性和缓存语义，不共享报告缓存", "Phase 3",
    ),
)

ALLOWED_FETCH_RESULT_CALLS: frozenset[tuple[str, str]] = frozenset({
    ("src/core/resilient_collection.py", "ResilientCollector._collect"),
})
```

本步骤先只放常量；静态检查算法在 Task 5 加入。

- [ ] **Step 4: 创建 `src/data/collection_policies.py`**

```python
"""五组能力的业务策略：只判断有效性和结果状态，不管理缓存。"""
import math
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from data.hk_news import HK_TZ
from data.schemas import DataCollectionRecord, NewsData, ResultState
from data.validation import financial_has_facts


class CollectionPolicy(Protocol):
    kind: str
    ttl_key: str
    reference_age: int
    source_attempts: int

    def validate(self, symbol: str, rows: list[Any], now: datetime) -> bool: ...

    def classify(
        self,
        symbol: str,
        rows: list[Any],
        diagnostic: DataCollectionRecord | None,
        previous_rows: list[Any] | None,
        now: datetime,
    ) -> ResultState: ...

    def reference_rows(self, rows: list[Any], now: datetime) -> list[Any] | None: ...

    def fetch_constraints(self, previous_rows: list[Any] | None, now: datetime) -> dict[str, Any]: ...


class _Policy:
    source_attempts = 2

    def reference_rows(self, rows: list[Any], now: datetime) -> list[Any] | None:
        return rows

    def fetch_constraints(self, previous_rows: list[Any] | None, now: datetime) -> dict[str, Any]:
        return {}

    @staticmethod
    def _not_applicable(diagnostic: DataCollectionRecord | None) -> bool:
        return diagnostic is not None and diagnostic.cache_state == "not_applicable"


class FinancialPolicy(_Policy):
    kind = "financial"
    ttl_key = "quarterly"
    reference_age = 30 * 86400

    def __init__(self, market: str):
        self.market = market

    def validate(self, symbol: str, rows: list[Any], now: datetime) -> bool:
        today = now.astimezone(HK_TZ).date()
        if not rows or any(row.symbol != symbol for row in rows):
            return False
        if not financial_has_facts(rows, symbol):
            return False
        if len({row.fiscal_quarter for row in rows}) != len(rows):
            return False
        return all(row.fiscal_quarter <= today for row in rows)

    def classify(
        self,
        symbol: str,
        rows: list[Any],
        diagnostic: DataCollectionRecord | None,
        previous_rows: list[Any] | None,
        now: datetime,
    ) -> ResultState:
        if self._not_applicable(diagnostic) and not rows:
            return "not_applicable"
        if not self.validate(symbol, rows, now):
            return "failed"
        if self._not_applicable(diagnostic):
            return "not_applicable"
        latest = max(rows, key=lambda row: row.fiscal_quarter)
        if latest.financial_selection_status == "complete":
            return "complete"
        if latest.financial_selection_status == "partial":
            return "partial"
        from data.financial_selection import FinancialPackage
        if self.market == "hk":
            from data.hk_financial_policy import package_complete
        else:
            from data.a_financial_policy import package_complete
        package = FinancialPackage("缓存", rows, {})
        return "complete" if package_complete(package) else "partial"


class PricePolicy(_Policy):
    kind = "price"
    ttl_key = "daily"
    reference_age = 7 * 86400

    def __init__(self, market: str):
        self.market = market

    def validate(self, symbol: str, rows: list[Any], now: datetime) -> bool:
        today = now.astimezone(HK_TZ).date()
        if not rows or any(row.symbol != symbol for row in rows):
            return False
        if len({row.trade_date for row in rows}) != len(rows):
            return False
        return all(
            row.trade_date <= today
            and all(math.isfinite(value) and value >= 0 for value in
                    (row.open, row.high, row.low, row.close, row.volume))
            and min(row.open, row.close, row.low) > 0
            and row.high >= max(row.open, row.close, row.low)
            and row.low <= min(row.open, row.close, row.high)
            for row in rows
        )

    def _requirements(self, previous_rows: list[Any] | None, now: datetime) -> tuple[int, date | None]:
        if not previous_rows:
            return 60, None
        today = now.astimezone(HK_TZ).date()
        start = today - timedelta(days=365 if self.market == "hk" else 250)
        periods = [row.trade_date for row in previous_rows if start <= row.trade_date <= today]
        if not periods:
            return 60, None
        return max(60, len(periods)), max(periods)

    def classify(
        self,
        symbol: str,
        rows: list[Any],
        diagnostic: DataCollectionRecord | None,
        previous_rows: list[Any] | None,
        now: datetime,
    ) -> ResultState:
        if self._not_applicable(diagnostic) and not rows:
            return "not_applicable"
        if not self.validate(symbol, rows, now):
            return "failed"
        if self._not_applicable(diagnostic):
            return "not_applicable"
        minimum_points, minimum_end = self._requirements(previous_rows, now)
        if len(rows) < minimum_points:
            return "partial"
        if minimum_end is not None and max(row.trade_date for row in rows) < minimum_end:
            return "partial"
        return "complete"

    def fetch_constraints(self, previous_rows: list[Any] | None, now: datetime) -> dict[str, Any]:
        points, end = self._requirements(previous_rows, now)
        result: dict[str, Any] = {"minimum_points": points}
        if end is not None:
            result["minimum_end_date"] = end
        return result


class NewsPolicy(_Policy):
    kind = "news"
    ttl_key = "news"
    reference_age = 86400

    def validate(self, symbol: str, rows: list[Any], now: datetime) -> bool:
        today = now.astimezone(HK_TZ).date()
        if len(rows) != 1:
            return False
        news = rows[0]
        if not isinstance(news, NewsData) or news.symbol != symbol or news.date > today:
            return False
        raw = news._raw_sentiment
        if raw is None or raw.symbol != symbol:
            return False
        return all(item.publish_date <= today for item in raw.items)

    def classify(
        self,
        symbol: str,
        rows: list[Any],
        diagnostic: DataCollectionRecord | None,
        previous_rows: list[Any] | None,
        now: datetime,
    ) -> ResultState:
        if self._not_applicable(diagnostic) and not rows:
            return "not_applicable"
        if not self.validate(symbol, rows, now):
            return "failed"
        if self._not_applicable(diagnostic):
            return "not_applicable"
        raw = rows[0]._raw_sentiment
        assert raw is not None
        has_error = bool(diagnostic and (diagnostic.error_kind or diagnostic.error_message))
        if not raw.items:
            return "failed" if has_error else "empty"
        return "partial" if has_error else "complete"

    def reference_rows(self, rows: list[Any], now: datetime) -> list[Any] | None:
        if not rows:
            return None
        news = rows[0].model_copy(deep=True)
        raw = news._raw_sentiment
        if raw is None:
            return None
        end = now.astimezone(HK_TZ).date()
        raw.items = [
            item for item in raw.items
            if end - timedelta(days=29) <= item.publish_date <= end
        ]
        news.headlines = [item.title for item in raw.items]
        return [news]


POLICIES: dict[tuple[str, str], CollectionPolicy] = {
    ("a-shares", "financial"): FinancialPolicy("a-shares"),
    ("hk", "financial"): FinancialPolicy("hk"),
    ("a-shares", "price"): PricePolicy("a-shares"),
    ("hk", "price"): PricePolicy("hk"),
    ("a-shares", "news"): NewsPolicy(),
}


def policy_for(market: str, kind: str) -> CollectionPolicy | None:
    return POLICIES.get((market, kind))
```

- [ ] **Step 5: 运行策略测试确认通过**

```powershell
$run = "tmp/pytest/phase0-task2-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/data/test_collection_policies.py tests/core/test_collection_contract.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

预期：全部通过。若 `financial_has_facts` 对测试行返回 False，检查测试行是否包含至少一个允许的有限事实；测试行已包含 `revenue` 等字段。

- [ ] **Step 6: 单文件检查**

```powershell
.\.venv\Scripts\ruff.exe check src/core/collection_contract.py src/data/collection_policies.py tests/data/test_collection_policies.py tests/core/test_collection_contract.py
.\.venv\Scripts\pyright.exe src/core/collection_contract.py src/data/collection_policies.py tests/data/test_collection_policies.py tests/core/test_collection_contract.py
```

预期：无错误。未使用的 `PriceData` 等导入若被 Ruff 报 F401，从测试文件删除；不要改成 `noqa`，除非是必要的协议导入。

- [ ] **Step 7: 基础层暂不提交**

本任务完成后，基础层仍未被收集器使用；等 Task 3 完成后统一提交，避免提交未使用代码。

---

## Task 3: 适配器统一入口、测试替身与旧路径保护

**Files:**
- Modify: `src/data/akshare.py`
- Modify: `src/core/pipeline.py`
- Create: `tests/support.py`
- Modify: `tests/core/test_pipeline.py`
- Modify: `tests/core/test_pipeline_health.py`
- Modify: `tests/core/test_report_data.py`
- Test: `tests/data/test_akshare.py`
- Test: `tests/core/test_pipeline.py`

- [ ] **Step 1: 写适配器与旧路径保护的失败测试**

在 `tests/data/test_akshare.py` 末尾添加：

```python
def test_fetch_result_returns_rows_and_diagnostic(monkeypatch):
    from data.akshare import AkShareAdapter

    adapter = AkShareAdapter()

    def broken(*args, **kwargs):
        raise TimeoutError("timeout")

    monkeypatch.setattr(adapter, "_fetch_price", broken)
    result = adapter.fetch_result("000001", data_type="price", market="a-shares")
    assert result.rows == []
    assert result.record is not None
    assert result.record.error_kind == "timeout"


def test_fetch_wrapper_still_returns_rows(monkeypatch):
    from data.akshare import AkShareAdapter
    from data.schemas import PriceData
    from datetime import date

    adapter = AkShareAdapter()
    monkeypatch.setattr(
        adapter, "_fetch_price",
        lambda *args, **kwargs: [PriceData(
            symbol="000001", trade_date=date(2026, 10, 20),
            open=10, high=11, low=9, close=10, volume=100,
        )],
    )
    rows = adapter.fetch("000001", data_type="price", market="a-shares")
    assert rows[0].symbol == "000001"
```

在 `tests/core/test_pipeline.py` 末尾添加：

```python
def test_migrated_capability_is_rejected_by_legacy_branch(tmp_path, monkeypatch):
    from core.registry import Registry
    from tests.support import FetchResultMixin

    class Source(FetchResultMixin, DataSource):
        def __init__(self):
            self.calls = []

        def supports(self, market, data_type):
            return True

        def fetch(self, symbol, **kwargs):
            self.calls.append(kwargs)
            return []

    source = Source()
    registry = Registry()
    registry.register_data_source(source)
    pipeline = Pipeline(registry, Config(config_dir=tmp_path), llm_enabled=False)
    monkeypatch.setattr("core.resilient_collection.supports", lambda market, kind: False)
    ctx = pipeline.collect("000001", "测试", data_types=["financial"])
    assert ctx.collection_records["financial"].error_kind == "contract_error"
    assert source.calls == []
```

- [ ] **Step 2: 运行测试确认失败**

```powershell
$run = "tmp/pytest/phase0-task3-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/data/test_akshare.py tests/core/test_pipeline.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

预期：`fetch_result` 不存在；旧路径没有 `contract_error` 保护。

- [ ] **Step 3: 重构 `AkShareAdapter.fetch` 为私有实现**

在 `src/data/akshare.py` 顶部导入改为：

```python
from data.base import CollectionResult, DataSource
```

把现有 `fetch` 方法（当前约 338-370 行）替换为以下三个方法，原方法体只改两个返回点：

```python
    def fetch(self, symbol: str, **kwargs) -> list:
        """遗留路径兼容包装；新路径使用 fetch_result。"""
        return self._fetch_impl(symbol, **kwargs)[0]

    def fetch_result(self, symbol: str, **kwargs) -> CollectionResult:
        """统一取数入口：数据与诊断在同一次调用中返回。"""
        rows, record = self._fetch_impl(symbol, **kwargs)
        return CollectionResult(rows=rows, record=record)

    def _fetch_impl(self, symbol: str, **kwargs) -> tuple[list, DataCollectionRecord | None]:
        data_type = kwargs.get("data_type", "price")
        self._collection_diagnostics.record = None
        labels = {
            "financial": "同花顺财报", "valuation": "腾讯估值快照",
            "industry": "AkShare 行业与同行池", "news": "东方财富新闻与公告",
        }
        if kwargs.get("market") == "hk":
            labels.update(
                price="东方财富港股行情（不复权）",
                financial="东方财富港股财报",
                valuation="东方财富港股估值快照",
                news="东方财富关键词新闻检索",
            )
        if data_type == "index_price":
            return self._fetch_index_price(symbol, kwargs.get("index_style", "broad")), self.get_collection_error()
        if data_type == "index_valuation":
            return self._fetch_index_valuation(symbol), self.get_collection_error()
        if data_type == "index_capital_flow":
            return self._fetch_capital_flow(symbol, kwargs.get("index_style", "broad")), self.get_collection_error()
        if data_type == "index_macro":
            return self._fetch_index_macro(symbol, kwargs.get("index_style", "broad")), self.get_collection_error()
        if data_type == "index_sentiment":
            return self._fetch_index_sentiment(symbol), self.get_collection_error()
        try:
            method = getattr(self, f"_fetch_{data_type}", None)
            if method is None:
                logger.warning(f"AkShare 不支持数据类型: {data_type}")
                return [], self.get_collection_error()
            results = method(symbol, **kwargs)
            for item in results:
                if not getattr(item, "_data_source", ""):
                    item._data_source = labels.get(data_type, "AkShare")
            return results, self.get_collection_error()
        except Exception as e:
            logger.error(f"AkShare 数据获取失败 {symbol}/{data_type}: {e}")
            self._record_collection_error(labels.get(data_type, "AkShare"), e)
            return [], self.get_collection_error()
```

注意：原 `fetch` 的 index 分支是直接 `return`，不应用 `_data_source` 标签；上面的实现保持这一点。`_fetch_impl` 返回 tuple，`fetch` 只取 rows，`fetch_result` 同时返回诊断。

- [ ] **Step 4: 在 `src/core/pipeline.py` 增加旧路径保护**

在文件顶部导入：

```python
from core.collection_contract import MIGRATED_CAPABILITIES
```

在 `fetch_one` 的 `if supports(market, data_type): ...` 分支之后、`if market == "hk" and data_type == "news":` 之前加：

```python
            if (market, data_type) in MIGRATED_CAPABILITIES:
                logger.error("已迁移能力不得走旧取数路径 %s/%s", market, data_type)
                return data_type, None, DataCollectionRecord(
                    cache_status="live", cache_state="degraded", result_state="failed",
                    error_kind="contract_error",
                    error_message="已迁移能力不得走旧取数路径",
                )
```

- [ ] **Step 5: 创建测试替身 mixin 并更新受影响的假数据源**

创建 `tests/support.py`：

```python
"""测试数据源统一入口辅助。"""
from typing import Any

from data.base import CollectionResult


class FetchResultMixin:
    """为测试假数据源显式实现 fetch_result，满足五组能力契约。"""

    def fetch_result(self, symbol: str, **kwargs: Any) -> CollectionResult:
        fetch = getattr(self, "fetch", None)
        if not callable(fetch):
            raise TypeError("测试数据源必须实现 fetch")
        rows = fetch(symbol, **kwargs)
        reader = getattr(self, "get_collection_error", None)
        diagnostic = reader() if callable(reader) else None
        return CollectionResult(rows=rows or [], record=diagnostic)
```

修改以下测试文件：

1. `tests/core/test_pipeline.py`：
   - 顶部加 `from tests.support import FetchResultMixin`。
   - `class MockDataSource(DataSource)` 改为 `class MockDataSource(FetchResultMixin, DataSource)`。
   - `class EmptyNewsSource(DataSource)` 改为 `class EmptyNewsSource(FetchResultMixin, DataSource)`。
   - `MockDataSource.fetch` 的 news 分支改为带原始舆情对象：

```python
            elif data_type == "news":
                news = NewsData(
                    symbol=symbol, date=date(2026, 7, 1), headlines=["利好公告"]
                )
                news._raw_sentiment = RawSentimentData(
                    symbol=symbol,
                    fetch_date=date(2026, 7, 1),
                    items=[RawSentimentItem(
                        title="利好公告", source="news", publish_date=date(2026, 7, 1)
                    )],
                )
                return [news]
```

   如果文件没有导入 `RawSentimentData`/`RawSentimentItem`，在现有 `data.schemas` 导入中补上。

2. `tests/core/test_pipeline_health.py`：
   - 顶部加 `from tests.support import FetchResultMixin`。
   - `class FakeSource(DataSource)` 改为 `class FakeSource(FetchResultMixin, DataSource)`。

3. `tests/core/test_report_data.py`：
   - 在需要的位置导入 `from tests.support import FetchResultMixin`（文件顶部或各测试函数内均可；优先顶部）。
   - `ShortSource`、`EmptySource`、`FailedSource` 都改为先继承 `FetchResultMixin`，再继承 `DataSource`。

4. `tests/core/test_resilient_collection.py`（Task 4 会继续改）：
   - 顶部加 `from tests.support import FetchResultMixin`。
   - `class Source(DataSource)` 改为 `class Source(FetchResultMixin, DataSource)`。

不要修改 `tests/core/test_registry.py`、`tests/data/test_base.py`、`tests/core/test_peer_pipeline.py`、`tests/core/test_hk_news_pipeline.py` 的假数据源；它们当前不经过五组能力的 Pipeline 构造检查。

- [ ] **Step 6: 运行受影响测试确认通过**

```powershell
$run = "tmp/pytest/phase0-task3-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/data/test_akshare.py tests/core/test_pipeline.py tests/core/test_pipeline_health.py tests/core/test_report_data.py tests/core/test_resilient_collection.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

预期：全部通过。此时收集器仍调用 `fetch()`，新 `fetch_result()` 尚未被使用；旧路径保护在 `supports` 正常时不可达，测试通过 monkeypatch 覆盖。

- [ ] **Step 7: 单文件检查并提交基础契约**

```powershell
.\.venv\Scripts\ruff.exe check src/data/schemas.py src/data/base.py src/core/collection_contract.py src/data/collection_policies.py src/data/akshare.py src/core/pipeline.py tests/support.py tests/data/test_schemas.py tests/data/test_base.py tests/data/test_collection_policies.py tests/core/test_collection_contract.py tests/data/test_akshare.py tests/core/test_pipeline.py tests/core/test_pipeline_health.py tests/core/test_report_data.py tests/core/test_resilient_collection.py
.\.venv\Scripts\pyright.exe src/data/schemas.py src/data/base.py src/core/collection_contract.py src/data/collection_policies.py src/data/akshare.py src/core/pipeline.py tests/support.py tests/data/test_schemas.py tests/data/test_base.py tests/data/test_collection_policies.py tests/core/test_collection_contract.py tests/data/test_akshare.py tests/core/test_pipeline.py tests/core/test_pipeline_health.py tests/core/test_report_data.py tests/core/test_resilient_collection.py
```

提交基础契约（不包含收集器迁移）：

```powershell
git add src/data/schemas.py src/data/base.py src/core/collection_contract.py src/data/collection_policies.py src/data/akshare.py src/core/pipeline.py tests/support.py tests/data/test_schemas.py tests/data/test_base.py tests/data/test_collection_policies.py tests/core/test_collection_contract.py tests/data/test_akshare.py tests/core/test_pipeline.py tests/core/test_pipeline_health.py tests/core/test_report_data.py tests/core/test_resilient_collection.py
git diff --cached --check
git commit -m "feat(数据采集): 建立统一结果入口与采集策略"
```

注意：`tests/core/test_resilient_collection.py` 此时只加了 mixin 继承，收集器迁移在 Task 4 完成；如果提交时该文件尚未改完，可不暂存它，等 Task 4 一起提交。按完整事项原则，建议把 Task 4 的收集器迁移也并入本提交；如果选择合并，跳过本步骤的 commit，在 Task 4 末尾统一提交。

---

## Task 4: 收集器迁移到统一入口与策略

**Files:**
- Modify: `src/core/resilient_collection.py`
- Modify: `tests/core/test_resilient_collection.py`
- Test: `tests/core/test_collection_contract.py`
- Test: `tests/core/test_resilient_collection.py`

- [ ] **Step 1: 更新和新增收集器测试**

在 `tests/core/test_resilient_collection.py` 中：

1. 把现有 `no_network` fixture 加上 `mocker.patch("core.resilient_collection.time.sleep")`，避免每源两次尝试产生真实 1 秒等待。
2. 把现有 `from data.base import DataSource` 改为 `from data.base import CollectionResult, DataSource`，并在导入区加入：

```python
from data.collection_policies import NewsPolicy
from tests.support import FetchResultMixin
```

3. `Source` 已改为 `class Source(FetchResultMixin, DataSource)`（Task 3）；补一个显式统一入口测试：

```python
class UnifiedSource(DataSource):
    def __init__(self, rows):
        self.rows = rows

    def supports(self, market, data_type):
        return True

    def fetch(self, symbol, **kwargs):
        raise AssertionError("五组能力不应调用 fetch")

    def fetch_result(self, symbol, **kwargs):
        return CollectionResult(rows=self.rows, record=None)


def test_migrated_capability_uses_fetch_result_only(tmp_path):
    symbol = "000001"
    source = UnifiedSource(rows("price", symbol))
    registry = Registry()
    registry.register_data_source(source)
    pipeline = Pipeline(registry, Config(config_dir=tmp_path), llm_enabled=False)
    ctx = collect(pipeline, symbol, "price")
    assert ctx.price_data
    assert ctx.collection_records["price"].result_state == "complete"
```

4. 在现有断言中补充 `result_state`：
   - `test_partial_retries_after_30_minutes_and_keeps_independent_complete_reference`：
     ```python
     assert second.collection_records[kind].result_state == "partial"
     ```
   - `test_successful_empty_news_has_normal_ttl_and_is_not_a_network_failure`：
     ```python
     assert ctx.collection_records["news"].result_state == "empty"
     ```
   - `test_explicit_normal_short_history_does_not_trigger_frequent_retry`：
     ```python
     assert ctx.collection_records["price"].result_state == "not_applicable"
     ```
   - `test_explicit_normal_empty_result_uses_normal_ttl`：
     ```python
     assert ctx.collection_records[kind].result_state == "not_applicable"
     ```
   - `test_failed_without_previous_data_is_throttled_but_force_refresh_bypasses_wait`：把循环改为：

     ```python
     source.rows = []
     for _ in range(2):
         ctx = collect(pipeline, symbol, "financial")
         assert ctx.financial_data is None
         assert ctx.collection_records["financial"].result_state == "failed"
     assert len(source.calls) == 2
     ```

     保留后续 `refresh=True`、`clock.return_value = 2801` 和调用次数断言不变。
   - `test_failed_attempt_preserves_newer_partial_facts_as_reference`：在现有 `for ctx in (failed, again)` 循环内加：

     ```python
     assert ctx.collection_records["financial"].result_state == "failed"
     ```

     保留参考数据和 `last_complete` 断言不变。

5. `test_hk_business_date_is_used_on_a_utc_host` 改为：

```python
def test_hk_business_date_is_used_on_a_utc_host(tmp_path, mocker):
    from core.resilient_collection import Snapshot
    from data.cache import CacheEntry
    from data.hk_news import HK_TZ

    instant = datetime(2026, 10, 26, 16, 30, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz or UTC)

    mocker.patch("core.resilient_collection.datetime", Clock)
    today = instant.astimezone(HK_TZ).date()
    news = NewsData(symbol="000001", date=today, headlines=["当日消息"])
    news._raw_sentiment = RawSentimentData(symbol=news.symbol, fetch_date=today, items=[
        RawSentimentItem(title="当日消息", source="news", publish_date=today)])
    assert NewsPolicy().validate("000001", [news], instant)
    pipeline, _, _ = setup(tmp_path, "news")
    snapshot = Snapshot(
        [news],
        DataCollectionRecord(source="已验证来源", collected_at=instant-timedelta(minutes=15)),
        CacheEntry("[]", 1000, 21600, None),
    )
    reference = pipeline._resilient_collector._reference(
        "news", "a-shares", snapshot, "刷新失败"
    )
    assert reference and reference.news_data and reference.news_data.headlines == ["当日消息"]
```

6. 新增结果状态与契约测试：

```python
def test_news_zero_items_with_error_is_failed_not_partial(tmp_path):
    pipeline, source, symbol = setup(tmp_path, "news")
    empty = NewsData(symbol=symbol, date=datetime.now().astimezone().date())
    empty._raw_sentiment = RawSentimentData(
        symbol=symbol, fetch_date=empty.date, items=[]
    )
    source.rows = [empty]
    source.diagnostic = DataCollectionRecord(
        error_kind="timeout", error_message="公告接口失败"
    )
    ctx = collect(pipeline, symbol, "news")
    assert ctx.news_data is None
    assert ctx.collection_records["news"].result_state == "failed"
    assert pipeline._cache.get_entry("news", symbol, "latest") is None
    assert pipeline._cache.get_entry("news", symbol, "last_attempt") is not None


def test_failed_then_recovered_complete_updates_last_complete(tmp_path, mocker):
    clock = mocker.patch("data.cache.time.time", return_value=1000)
    pipeline, source, symbol = setup(tmp_path, "financial")
    source.rows = []
    failed = collect(pipeline, symbol, "financial")
    assert failed.collection_records["financial"].result_state == "failed"
    source.rows = rows("financial", symbol)
    clock.return_value = 2801
    recovered = collect(pipeline, symbol, "financial")
    assert recovered.financial_data
    assert recovered.collection_records["financial"].result_state == "complete"
    assert pipeline._cache.get_entry("financial", symbol, "last_complete") is not None


def test_inconsistent_record_does_not_pollute_cache(tmp_path):
    pipeline, _, symbol = setup(tmp_path, "price")
    record = DataCollectionRecord(
        source="测试", cache_state="degraded", result_state="complete"
    )
    pipeline._resilient_collector._store(
        "price", symbol, rows("price", symbol), record, "a-shares"
    )
    assert pipeline._cache.get_entry("price", symbol, "latest") is None


def test_contradictory_cached_record_is_ignored(tmp_path):
    pipeline, _, symbol = setup(tmp_path, "price")
    record = DataCollectionRecord(
        source="测试", cache_state="degraded", result_state="complete"
    )
    pipeline._cache.put(
        "price", symbol, "latest", "[]", metadata=record.model_dump_json()
    )
    assert pipeline._resilient_collector._read(
        "price", symbol, "latest", "a-shares"
    ) is None
```

在 `tests/core/test_collection_contract.py` 顶部把导入改为：

```python
import pytest

from core.collection_contract import LEGACY_FETCH_SITES, MIGRATED_CAPABILITIES
from core.pipeline import Pipeline
from core.registry import Registry
from data.base import DataSource
from data.schemas import DataCollectionRecord
from utils.config import Config
```

然后在文件末尾追加：

```python
class BadSource(DataSource):
    def supports(self, market, data_type):
        return True

    def fetch(self, symbol, **kwargs):
        return []


def test_pipeline_rejects_source_without_explicit_fetch_result(tmp_path):
    registry = Registry()
    registry.register_data_source(BadSource())
    with pytest.raises(ValueError, match="fetch_result"):
        Pipeline(registry, Config(config_dir=tmp_path), llm_enabled=False)


def test_late_registered_bad_source_returns_contract_error_without_cache(tmp_path, mocker):
    mocker.patch("core.pipeline.time.sleep")
    pipeline = Pipeline(Registry(), Config(config_dir=tmp_path), llm_enabled=False)
    pipeline._registry.register_data_source(BadSource())
    ctx = pipeline.collect("000001", "测试", data_types=["price"])
    record = ctx.collection_records["price"]
    assert record.error_kind == "contract_error"
    assert record.result_state == "failed"
    assert pipeline._cache.get_entry("price", "000001", "last_attempt") is None
```

- [ ] **Step 2: 运行测试确认失败**

```powershell
$run = "tmp/pytest/phase0-task4-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/core/test_resilient_collection.py tests/core/test_collection_contract.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

预期：失败，收集器仍使用 `fetch()` 和旧 `_valid/_state`；`_reference` 签名仍为 3 参数；没有运行时契约检查。

- [ ] **Step 3: 重写 `src/core/resilient_collection.py`**

```python
"""财务、行情、A股新闻的当前事实与历史参考隔离；不管理其他数据路径。"""
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Any, Literal

from core.circuit_breaker import CircuitBreaker
from core.collection_contract import MIGRATED_CAPABILITIES
from core.registry import Registry
from data.base import CollectionResult, DataSource
from data.cache import CacheEntry, CacheManager
from data.collection_policies import POLICIES, CollectionPolicy, policy_for
from data.hk_news import HK_TZ
from data.schemas import (
    DataCollectionRecord,
    DataReference,
    FinancialData,
    NewsData,
    PriceData,
    RawSentimentData,
    ResultState,
)
from utils.config import Config

logger = logging.getLogger(__name__)
Kind = Literal["financial", "price", "news"]
State = Literal["complete", "degraded", "not_applicable", "unknown"]
RETRY_TTL = 1800
CACHE_STATE: dict[ResultState, State] = {
    "complete": "complete",
    "partial": "degraded",
    "empty": "complete",
    "not_applicable": "not_applicable",
    "failed": "degraded",
    "unknown": "unknown",
}


@dataclass
class Snapshot:
    rows: list[Any]
    record: DataCollectionRecord
    entry: CacheEntry


@dataclass
class CollectionOutcome:
    rows: list[Any] | None
    record: DataCollectionRecord
    reference: DataReference | None = None


def supports(market: str, kind: str) -> bool:
    return (market, kind) in MIGRATED_CAPABILITIES


class CollectionContractError(ValueError):
    """已迁移能力的策略或统一入口缺失。"""


class ResilientCollector:
    def __init__(self, cache: CacheManager, registry: Registry, breaker: CircuitBreaker, config: Config):
        self.cache, self.registry, self.breaker, self.config = cache, registry, breaker, config
        self._lock = Lock()
        self._request_locks: dict[tuple[str, Kind], Lock] = {}
        self._validate_contract()

    @staticmethod
    def _has_explicit_fetch_result(source: DataSource) -> bool:
        return type(source).fetch_result is not DataSource.fetch_result

    def _validate_contract(self) -> None:
        if set(POLICIES) != set(MIGRATED_CAPABILITIES):
            raise CollectionContractError("五组能力的策略注册不完整")
        for market, kind in MIGRATED_CAPABILITIES:
            policy = policy_for(market, kind)
            if policy is None:
                raise CollectionContractError(f"缺少采集策略：{market}/{kind}")
            sources = self.registry.get_data_sources(market, kind)
            if not isinstance(sources, list):
                continue
            for source in sources:
                if not self._has_explicit_fetch_result(source):
                    raise CollectionContractError(
                        f"数据源 {source.__class__.__name__} 未实现统一取数入口 fetch_result"
                    )

    def _read(self, kind: Kind, symbol: str, key: str, market: str) -> Snapshot | None:
        entry = self.cache.get_entry(kind, symbol, key)
        if entry is None:
            return None
        try:
            data = json.loads(entry.value)
            if not isinstance(data, list):
                return None
            model = {"financial": FinancialData, "price": PriceData, "news": NewsData}[kind]
            rows = [model.model_validate(item) for item in data]
            if kind == "news" and rows and data[0].get("_raw_sentiment") is not None:
                rows[0]._raw_sentiment = RawSentimentData.model_validate(data[0]["_raw_sentiment"])
            record = (
                DataCollectionRecord.model_validate_json(entry.metadata)
                if entry.metadata else DataCollectionRecord()
            )
            if record.result_state != "unknown" and not self._record_consistent(record):
                logger.error("缓存采集记录状态矛盾 %s/%s/%s", symbol, kind, key)
                return None
            policy = policy_for(market, kind)
            if policy is None:
                return None
            now = datetime.now(HK_TZ)
            if key == "last_attempt" and record.result_state == "unknown":
                record = record.model_copy(
                    update={"result_state": "failed", "cache_state": "degraded"}
                )
            if rows:
                if not policy.validate(symbol, rows, now):
                    return None
                if record.result_state in ("unknown", "failed"):
                    previous = (
                        self._read(kind, symbol, "last_complete", market)
                        if key == "latest" else None
                    )
                    state = policy.classify(
                        symbol, rows, record,
                        previous.rows if previous and previous.rows else None, now,
                    )
                    record = record.model_copy(
                        update={"result_state": state, "cache_state": CACHE_STATE[state]}
                    )
            elif record.result_state == "unknown" and record.cache_state == "not_applicable":
                record = record.model_copy(update={"result_state": "not_applicable"})
            elif record.result_state not in ("not_applicable", "failed"):
                return None
            return Snapshot(rows, record, entry)
        except (ValueError, TypeError, AttributeError, KeyError):
            logger.debug("可靠性缓存格式无效 %s/%s/%s", symbol, kind, key)
            return None

    @staticmethod
    def _fresh(snapshot: Snapshot) -> bool:
        age = time.time() - snapshot.entry.created_at
        ttl = snapshot.entry.ttl_seconds
        if snapshot.record.result_state in ("partial", "failed", "unknown"):
            ttl = RETRY_TTL if ttl < 0 else min(ttl, RETRY_TTL)
        return age >= 0 and (ttl < 0 or age <= ttl)

    def _reference(self, kind: Kind, market: str, previous: Snapshot | None,
                   reason: str) -> DataReference | None:
        if previous is None or not previous.rows:
            return None
        stamp = previous.record.collected_at
        if stamp is None or stamp.utcoffset() is None:
            return None
        policy = policy_for(market, kind)
        if policy is None:
            return None
        age = (datetime.now().astimezone() - stamp).total_seconds()
        if not 0 <= age <= policy.reference_age:
            return None
        rows = policy.reference_rows(previous.rows, datetime.now(HK_TZ))
        if rows is None:
            return None
        ref = DataReference(
            data_type=kind,
            record=previous.record.model_copy(deep=True),
            reason=reason,
        )
        if kind == "financial":
            ref.financial_data = [row.model_copy(deep=True) for row in rows]
        elif kind == "price":
            ref.price_data = [row.model_copy(deep=True) for row in rows]
        else:
            news = rows[0].model_copy(deep=True)
            if news._raw_sentiment is None:
                return None
            ref.news_data = news
        return ref

    def _normal_ttl(self, policy: CollectionPolicy) -> int:
        return int(self.config.get(f"data.cache_ttl.{policy.ttl_key}", 86400))

    def _wait_ttl(self, policy: CollectionPolicy) -> int:
        normal = self._normal_ttl(policy)
        return RETRY_TTL if normal < 0 else min(normal, RETRY_TTL)

    @staticmethod
    def _record_consistent(record: DataCollectionRecord) -> bool:
        return record.cache_state == CACHE_STATE.get(record.result_state)

    def _store(self, kind: Kind, symbol: str, rows: list[Any],
               record: DataCollectionRecord, market: str) -> None:
        try:
            with self._lock:
                policy = policy_for(market, kind)
                if (policy is None or record.result_state == "unknown"
                        or not self._record_consistent(record)):
                    logger.error("拒绝写入不一致的采集记录 %s/%s", symbol, kind)
                    return
                if record.error_kind == "contract_error":
                    logger.error("拒绝缓存契约错误 %s/%s", symbol, kind)
                    return
                if record.result_state == "failed":
                    self.cache.put(
                        kind, symbol, "last_attempt", "[]",
                        ttl_seconds=self._wait_ttl(policy),
                        metadata=record.model_dump_json(),
                    )
                    return
                previous = self._read(kind, symbol, "latest", market)
                backup = self._read(kind, symbol, "last_complete", market)
                if (previous and previous.record.result_state in ("complete", "empty")
                        and record.result_state not in ("complete", "empty")
                        and self._can_promote(previous, backup)):
                    self.cache.copy_entry(kind, symbol, "latest", "last_complete")
                data = []
                for row in rows:
                    item = row.model_dump(mode="json")
                    if kind == "news" and row._raw_sentiment is not None:
                        item["_raw_sentiment"] = row._raw_sentiment.model_dump(mode="json")
                    data.append(item)
                ttl = self._normal_ttl(policy)
                if record.result_state == "partial":
                    ttl = self._wait_ttl(policy)
                self.cache.put(
                    kind, symbol, "latest", json.dumps(data, ensure_ascii=False),
                    ttl_seconds=ttl, metadata=record.model_dump_json(),
                )
                self.cache.invalidate(kind, symbol, "last_attempt")
                if record.result_state in ("complete", "empty"):
                    self.cache.copy_entry(kind, symbol, "latest", "last_complete")
                self.cache.cleanup_old_entries(
                    kind, symbol, "latest", preserve_date_keys=("last_complete",)
                )
        except Exception as error:  # noqa: BLE001 — 缓存隔离不影响本次数据披露
            logger.warning("可靠性缓存写入失败 %s/%s: %s", symbol, kind, error)

    @staticmethod
    def _can_promote(candidate: Snapshot, backup: Snapshot | None) -> bool:
        stamp = candidate.record.collected_at
        if not candidate.record.source or stamp is None or stamp.utcoffset() is None:
            return False
        if stamp > datetime.now(HK_TZ):
            return False
        previous_stamp = backup.record.collected_at if backup else None
        return previous_stamp is None or previous_stamp.utcoffset() is None or stamp >= previous_stamp

    def collect(self, symbol: str, market: str, kind: Kind, refresh: bool) -> CollectionOutcome:
        with self._lock:
            request_lock = self._request_locks.setdefault((symbol, kind), Lock())
        with request_lock:
            return self._collect(symbol, market, kind, refresh)

    def _collect(self, symbol: str, market: str, kind: Kind, refresh: bool) -> CollectionOutcome:
        policy = policy_for(market, kind)
        if policy is None:
            return CollectionOutcome(None, self._contract_failure("", "缺少采集策略"))
        latest = self._read(kind, symbol, "latest", market)
        complete = self._read(kind, symbol, "last_complete", market)
        attempt = self._read(kind, symbol, "last_attempt", market)
        if complete is None and latest and latest.record.result_state in ("complete", "empty"):
            complete = latest
        if attempt and not refresh and self._fresh(attempt):
            record = attempt.record.model_copy(update={"cache_status": "cache"})
            reference = self._reference(
                kind, market, latest, "本次刷新失败；以下仅为历史参考，不计分"
            )
            return CollectionOutcome(
                None, record,
                reference or self._reference(
                    kind, market, complete, "本次刷新失败；以下仅为历史参考，不计分"
                ),
            )
        if latest and attempt is None and not refresh and self._fresh(latest):
            record = latest.record.model_copy(update={"cache_status": "cache"})
            if latest.rows:
                self.breaker.record_success(symbol, kind)
            reference = (
                self._reference(
                    kind, market, complete, "本次数据不完整；以下仅为历史参考，不计分"
                )
                if record.result_state == "partial" else None
            )
            return CollectionOutcome(latest.rows or None, record, reference)

        failure = DataCollectionRecord(
            cache_status="live", cache_state="degraded", result_state="failed",
            error_kind="empty", error_message="本次未取得有效数据",
        )
        contract_error = False
        if refresh or not self.breaker.is_open(symbol, kind):
            sources = self.registry.get_data_sources(market, kind)
            for source in sources:
                if not self._has_explicit_fetch_result(source):
                    contract_error = True
                    failure = self._contract_failure(
                        source.__class__.__name__, "数据源未实现统一取数入口 fetch_result"
                    )
                    continue
                for attempt_index in range(policy.source_attempts):
                    try:
                        kwargs: dict[str, Any] = {"data_type": kind, "market": market}
                        if kind == "price":
                            kwargs.update(self._price_constraints(policy, complete, latest))
                        result = source.fetch_result(symbol, **kwargs)
                        if not isinstance(result, CollectionResult):
                            contract_error = True
                            failure = self._contract_failure(
                                source.__class__.__name__, "fetch_result 返回类型无效"
                            )
                            continue
                        rows = result.rows or []
                        diagnostic = result.record
                        state = policy.classify(
                            symbol, rows, diagnostic,
                            complete.rows if complete and complete.rows else None,
                            datetime.now(HK_TZ),
                        )
                        if state == "not_applicable":
                            record = self._live_record(
                                self._source_label(rows, source, diagnostic), state, diagnostic
                            )
                            self._store(kind, symbol, rows, record, market)
                            self.breaker.record_success(symbol, kind)
                            return CollectionOutcome(rows or None, record)
                        if state == "failed":
                            failure = self._failure_record(source, diagnostic)
                            continue
                        if self._is_late(kind, rows, latest):
                            failure = self._failure_record(
                                source, diagnostic, "invalid_data",
                                "返回数据早于已观察事实",
                            )
                            continue
                        record = self._live_record(
                            self._source_label(rows, source, diagnostic), state, diagnostic
                        )
                        if state == "partial" and not record.error_message:
                            record = record.model_copy(update={
                                "error_kind": "incomplete",
                                "error_message": "字段或历史覆盖不足，暂按降级结果等待后续请求重试",
                            })
                        self._store(kind, symbol, rows, record, market)
                        self.breaker.record_success(symbol, kind)
                        reference = (
                            self._reference(
                                kind, market, complete,
                                "本次数据不完整；以下仅为历史参考，不计分",
                            )
                            if state == "partial" else None
                        )
                        return CollectionOutcome(rows or None, record, reference)
                    except Exception as error:  # noqa: BLE001 — 单个来源失败仍尝试已有备用
                        logger.warning(
                            "可靠性采集失败 %s/%s/%s: %s", symbol, kind,
                            source.__class__.__name__, type(error).__name__,
                        )
                        timeout = "Timeout" in type(error).__name__
                        failure = DataCollectionRecord(
                            source=source.__class__.__name__, cache_status="live",
                            cache_state="degraded", result_state="failed",
                            error_kind="timeout" if timeout else "fetch_failed",
                            error_message="数据请求超时" if timeout else "数据请求失败",
                        )
                    if attempt_index == 0 and policy.source_attempts > 1:
                        time.sleep(1)
            if not contract_error:
                self.breaker.record_failure(symbol, kind)
        else:
            failure = failure.model_copy(update={
                "error_kind": "circuit_open",
                "error_message": "数据源暂时熔断，等待后续请求重试",
            })
        if not contract_error:
            self._store(kind, symbol, [], failure, market)
        reference = self._reference(
            kind, market, latest, "本次刷新失败；以下仅为历史参考，不计分"
        )
        return CollectionOutcome(
            None, failure,
            reference or self._reference(
                kind, market, complete, "本次刷新失败；以下仅为历史参考，不计分"
            ),
        )

    @staticmethod
    def _price_constraints(policy: CollectionPolicy, complete: Snapshot | None,
                           latest: Snapshot | None) -> dict[str, Any]:
        constraints = [
            policy.fetch_constraints(snapshot.rows, datetime.now(HK_TZ))
            for snapshot in (complete, latest) if snapshot and snapshot.rows
        ]
        if not constraints:
            return {}
        result: dict[str, Any] = {
            "minimum_points": max(int(item.get("minimum_points", 60)) for item in constraints)
        }
        ends = [item["minimum_end_date"] for item in constraints if item.get("minimum_end_date")]
        if ends:
            result["minimum_end_date"] = max(ends)
        return result

    @staticmethod
    def _is_late(kind: Kind, rows: list[Any], latest: Snapshot | None) -> bool:
        if latest is None or not latest.rows or not rows:
            return False
        if kind == "financial":
            return max(row.fiscal_quarter for row in rows) < max(
                row.fiscal_quarter for row in latest.rows
            )
        if kind == "price":
            return max(row.trade_date for row in rows) < max(
                row.trade_date for row in latest.rows
            )
        return False

    @staticmethod
    def _source_label(rows: list[Any], source: DataSource,
                      diagnostic: DataCollectionRecord | None) -> str:
        if rows:
            return getattr(rows[0], "_data_source", "") or (
                diagnostic.source if diagnostic and diagnostic.source
                else source.__class__.__name__
            )
        return diagnostic.source if diagnostic and diagnostic.source else source.__class__.__name__

    @staticmethod
    def _live_record(source: str, state: ResultState,
                     diagnostic: DataCollectionRecord | None) -> DataCollectionRecord:
        return DataCollectionRecord(
            source=source,
            collected_at=datetime.now().astimezone(),
            cache_status="live",
            error_kind=diagnostic.error_kind if diagnostic else "",
            error_message=diagnostic.error_message if diagnostic else "",
            cache_state=CACHE_STATE[state],
            result_state=state,
        )

    @staticmethod
    def _failure_record(source: DataSource, diagnostic: DataCollectionRecord | None,
                        error_kind: str = "empty",
                        error_message: str = "本次未取得有效数据") -> DataCollectionRecord:
        if diagnostic is not None and (diagnostic.error_kind or diagnostic.error_message):
            return diagnostic.model_copy(update={
                "source": diagnostic.source or source.__class__.__name__,
                "cache_status": "live",
                "cache_state": "degraded",
                "result_state": "failed",
                "collected_at": None,
            })
        return DataCollectionRecord(
            source=source.__class__.__name__, cache_status="live",
            cache_state="degraded", result_state="failed",
            error_kind=error_kind, error_message=error_message,
        )

    @staticmethod
    def _contract_failure(source: str, message: str) -> DataCollectionRecord:
        return DataCollectionRecord(
            source=source, cache_status="live", cache_state="degraded",
            result_state="failed", error_kind="contract_error", error_message=message,
        )
```

- [ ] **Step 4: 运行收集器测试确认通过**

```powershell
$run = "tmp/pytest/phase0-task4-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/core/test_resilient_collection.py tests/core/test_collection_contract.py tests/core/test_pipeline.py tests/core/test_pipeline_health.py tests/core/test_report_data.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

预期：全部通过。

常见修正：
- 若 `test_explicit_normal_empty_result_uses_normal_ttl` 对 financial 的 `cache_state` 断言失败，检查 `_read` 是否把空行 + `not_applicable` 推导为 `result_state=not_applicable`。
- 若 `test_failed_attempt_preserves_newer_partial_facts_as_reference` 失去参考，检查 `_store` 在部分结果覆盖前是否把旧完整 `latest` 提升到 `last_complete`。
- 若 `test_hk_business_date...` 失败，检查 `_reference` 是否使用 `policy.reference_age` 和 `policy.reference_rows`。
- 若 `test_news_zero_items_with_error...` 仍写 `latest`，检查 `NewsPolicy.classify` 的零条 + 错误分支是否为 `failed`。

- [ ] **Step 5: 运行相关回归**

```powershell
$run = "tmp/pytest/phase0-task4-reg-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/core tests/report/test_reference_quality.py tests/agent/test_reference_isolation.py tests/data/test_akshare.py tests/data/test_a_financial_fallback.py tests/data/test_price_source_validation.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

预期：全部通过。若出现与 `DataCollectionRecord.result_state` 默认值相关的断言失败，先比较实际记录和预期记录，再更新测试中的显式预期，不能把失败掩盖成 `unknown`。

- [ ] **Step 6: 单文件检查**

```powershell
.\.venv\Scripts\ruff.exe check src/core/resilient_collection.py src/core/pipeline.py src/data/akshare.py tests/core/test_resilient_collection.py tests/core/test_collection_contract.py tests/core/test_pipeline.py tests/core/test_pipeline_health.py tests/core/test_report_data.py tests/support.py
.\.venv\Scripts\pyright.exe src/core/resilient_collection.py src/core/pipeline.py src/data/akshare.py tests/core/test_resilient_collection.py tests/core/test_collection_contract.py tests/core/test_pipeline.py tests/core/test_pipeline_health.py tests/core/test_report_data.py tests/support.py
```

预期：无错误。

- [ ] **Step 7: 提交收集器迁移**

```powershell
git add src/core/resilient_collection.py src/core/pipeline.py src/data/akshare.py src/data/schemas.py src/data/base.py src/core/collection_contract.py src/data/collection_policies.py tests/support.py tests/data/test_schemas.py tests/data/test_base.py tests/data/test_collection_policies.py tests/core/test_collection_contract.py tests/data/test_akshare.py tests/core/test_pipeline.py tests/core/test_pipeline_health.py tests/core/test_report_data.py tests/core/test_resilient_collection.py
git diff --cached --check
git commit -m "refactor(数据采集): 五组能力迁移到统一取数契约"
```

如果 Task 3 已提交基础契约，本提交只暂存收集器、测试和必要修正；提交信息改为 `refactor(数据采集): 五组能力迁移到统一取数契约`。

---

## Task 5: 静态入口检查与 verify 接线

**Files:**
- Modify: `src/core/collection_contract.py`
- Create: `scripts/check-data-entrypoints.py`
- Modify: `scripts/verify.ps1`
- Test: `tests/test_data_entrypoints.py`
- Test: `tests/test_verify_script.py`

- [ ] **Step 1: 写静态检查测试**

创建 `tests/test_data_entrypoints.py`：

```python
"""检查数据取数入口：遗留清单、动态调用和静态绕行。"""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from core.collection_contract import FetchSite, find_violations

REPOSITORY = Path(__file__).resolve().parents[1]


def write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")


def test_declared_literal_fetch_is_allowed(tmp_path):
    write(tmp_path, "src/core/legacy.py", """
        def run(source):
            source.fetch("000001", data_type="price")
    """)
    sites = (FetchSite("src/core/legacy.py", "run", ("price",), "测试", "test"),)
    assert find_violations(tmp_path, sites) == []


def test_declared_dynamic_fetch_is_allowed(tmp_path):
    write(tmp_path, "src/core/legacy.py", """
        def run(source, data_type):
            source.fetch("000001", **{"data_type": data_type})
    """)
    sites = (FetchSite("src/core/legacy.py", "run", (None,), "测试", "test"),)
    assert find_violations(tmp_path, sites) == []


def test_new_unlisted_fetch_is_rejected(tmp_path):
    write(tmp_path, "src/core/new.py", """
        def run(source):
            source.fetch("000001", data_type="financial")
    """)
    violations = find_violations(tmp_path, ())
    assert violations
    assert "financial" in violations[0]


def test_fetch_result_outside_collector_is_rejected(tmp_path):
    write(tmp_path, "src/core/new.py", """
        def run(source):
            source.fetch_result("000001")
    """)
    violations = find_violations(tmp_path, ())
    assert any("fetch_result" in violation for violation in violations)


def test_stale_manifest_entry_is_rejected(tmp_path):
    write(tmp_path, "src/core/legacy.py", """
        def run():
            return None
    """)
    sites = (FetchSite("src/core/legacy.py", "missing", ("price",), "测试", "test"),)
    violations = find_violations(tmp_path, sites)
    assert any("已不存在" in violation for violation in violations)


def test_repository_entrypoints_are_clean():
    assert find_violations(REPOSITORY) == []


def test_check_script_runs_from_repository():
    env = dict(
        os.environ,
        PYTHONPATH=f"{REPOSITORY / 'src'}{os.pathsep}{REPOSITORY}",
        PYTHONIOENCODING="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(REPOSITORY / "scripts/check-data-entrypoints.py")],
        cwd=REPOSITORY, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    assert result.returncode == 0, result.stderr
```

在 `tests/test_verify_script.py` 增加：

```python
@pytest.mark.parametrize("path,expected", [
    ("src/core/collection_contract.py", "tests/core"),
    ("src/core/resilient_collection.py", "tests/report"),
    ("src/data/collection_policies.py", "tests/core"),
    ("src/data/akshare.py", "tests/core"),
    ("scripts/check-data-entrypoints.py", "tests/test_data_entrypoints.py"),
])
def test_contract_changes_pull_related_tests(path, expected):
    result = _run("-PlanOnly", "-Files", path)
    assert result.returncode == 0, result.stderr
    targets = [item.replace("\\", "/") for item in json.loads(result.stdout)["TestTargets"]]
    assert expected in targets
```

- [ ] **Step 2: 运行测试确认失败**

```powershell
$run = "tmp/pytest/phase0-task5-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/test_data_entrypoints.py tests/test_verify_script.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

预期：`find_violations` 不存在；`check-data-entrypoints.py` 不存在；`verify.ps1` 映射未覆盖。

- [ ] **Step 3: 在 `src/core/collection_contract.py` 增加静态检查算法**

在文件顶部加入：

```python
import ast
import sys
from pathlib import Path
```

在现有常量后追加：

```python
@dataclass(frozen=True)
class _CallSite:
    path: str
    function: str
    line: int
    method: str
    data_type: str | None
    has_keywords: bool


class _CallVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self._path = path
        self._stack: list[str] = []
        self.calls: list[_CallSite] = []

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
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"fetch", "fetch_result"}:
            data_type, has_data_type, has_kwargs = self._call_shape(node)
            if node.func.attr == "fetch_result" or has_data_type or has_kwargs:
                self.calls.append(_CallSite(
                    path=self._path,
                    function=".".join(self._stack) or "<module>",
                    line=node.lineno,
                    method=node.func.attr,
                    data_type=data_type if has_data_type else None,
                    has_keywords=has_kwargs,
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
```

- [ ] **Step 4: 创建 `scripts/check-data-entrypoints.py`**

```python
"""静态检查数据取数入口，防止新增未声明绕行。"""
from core.collection_contract import main

if __name__ == "__main__":
    raise SystemExit(main())
```

脚本依赖 `PYTHONPATH` 包含 `src`；`verify.ps1` 已设置。直接运行时使用：

```powershell
$env:PYTHONPATH = "$(Resolve-Path .\src);$(Get-Location)"
.\.venv\Scripts\python.exe .\scripts\check-data-entrypoints.py
```

- [ ] **Step 5: 修改 `scripts/verify.ps1`**

在 `$path -startsWith "src\data"` 的通用分支之前插入：

```powershell
    if ($path -eq "scripts\check-data-entrypoints.py") {
        Add-UniqueTarget $testTargets "tests/test_data_entrypoints.py"
        continue
    }
    if ($path -eq "src\data\akshare.py") {
        Add-UniqueTarget $testTargets "tests/data"
        Add-UniqueTarget $testTargets "tests/core"
        Add-UniqueTarget $testTargets "tests/report"
        continue
    }
    if ($path -in @("src\data\base.py", "src\data\schemas.py", "src\data\collection_policies.py")) {
        Add-UniqueTarget $testTargets "tests/data"
        Add-UniqueTarget $testTargets "tests/core"
        continue
    }
    if ($path -eq "src\core\collection_contract.py") {
        Add-UniqueTarget $testTargets "tests/core"
        Add-UniqueTarget $testTargets "tests/data"
        Add-UniqueTarget $testTargets "tests/test_data_entrypoints.py"
        continue
    }
    if ($path -in @("src\core\resilient_collection.py", "src\core\pipeline.py")) {
        Add-UniqueTarget $testTargets "tests/core"
        Add-UniqueTarget $testTargets "tests/data"
        Add-UniqueTarget $testTargets "tests/report"
        Add-UniqueTarget $testTargets "tests/agent"
        continue
    }
```

位置必须在 `if ($path.StartsWith("src\data\", ...))` 和 `if ($path.StartsWith("src\core\", ...))` 之前，否则 `continue` 不会命中。

在 Pyright 检查之后、pytest 之前插入：

```powershell
    if ($requiresFullSuite -or $pythonTargets.Count -gt 0) {
        & $pythonPath (Join-Path $repositoryRoot "scripts/check-data-entrypoints.py")
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
```

`-PlanOnly` 不执行该检查；`-Scope Changed` 有 Python 改动时执行；`-Scope Full` 执行。

- [ ] **Step 6: 运行静态检查测试和 repo 检查**

```powershell
$run = "tmp/pytest/phase0-task5-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/test_data_entrypoints.py tests/test_verify_script.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
$env:PYTHONPATH = "$(Resolve-Path .\src);$(Get-Location)"
.\.venv\Scripts\python.exe .\scripts\check-data-entrypoints.py
```

预期：测试通过；脚本输出“数据取数入口检查通过”并返回 0。

如果脚本报告清单条目“已不存在”，按实际函数名修正 `LEGACY_FETCH_SITES`；如果报告新增未声明 `fetch`，检查该调用是否应迁移或应加入清单。不要为了让检查通过而删除真实调用。

- [ ] **Step 7: 运行范围检查**

```powershell
.\scripts\verify.ps1 -Files src/core/collection_contract.py
.\scripts\verify.ps1 -PlanOnly -Files scripts/verify.ps1
```

预期：第一条实际执行 Ruff、Pyright、静态入口检查和相关测试；第二条只输出映射计划，确认 `tests/test_verify_script.py` 被选中。若 `tests/test_verify_script.py` 因 PowerShell 不存在被跳过，记录跳过原因；在 Windows 开发环境应执行。

- [ ] **Step 8: 提交入口门禁**

```powershell
git add src/core/collection_contract.py scripts/check-data-entrypoints.py scripts/verify.ps1 tests/test_data_entrypoints.py tests/test_verify_script.py
git diff --cached --check
git commit -m "chore(门禁): 增加数据取数入口检查与迁移台账"
```

---

## Task 6: 综合回归、CLI Markdown 验收与交付记录

**Files:**
- Verify: 全部受影响测试与脚本
- Record: `tmp/acceptance/unified-data-collection/`（本地验收日志，不提交）

- [ ] **Step 1: 运行受影响范围回归**

```powershell
.\scripts\verify.ps1 -Files src/core/resilient_collection.py
.\scripts\verify.ps1 -Files src/data/akshare.py
.\scripts\verify.ps1 -Files scripts/verify.ps1
```

逐项记录通过的测试目录和任何既有失败。不要因为某个既有失败与本轮无关就直接声称全部通过；在交付说明中区分本轮失败与既有失败。

- [ ] **Step 2: 运行完整受影响测试集**

```powershell
$run = "tmp/pytest/phase0-accept-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/core tests/data tests/report/test_reference_quality.py tests/agent/test_reference_isolation.py tests/index/test_collector.py tests/backtest -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

预期：通过。若 `tests/backtest` 耗时或网络受限，保留失败/跳过原因，记录覆盖范围。

- [ ] **Step 3: 用 CLI 生成 A 股 Markdown**

```powershell
mkdir tmp\acceptance\unified-data-collection -Force
.\.venv\Scripts\stock-robot.exe analyze 600519 --no-llm --verbose 2>&1 | Tee-Object -FilePath tmp\acceptance\unified-data-collection\a-share-cli.log
```

检查命令返回 0，并在 `reports/stock/` 下确认生成了 A 股 Markdown。若 CLI 返回非零，先查日志和配置，不要改用 Mock 页面或伪造报告。

- [ ] **Step 4: 用 CLI 生成港股 Markdown**

```powershell
.\.venv\Scripts\stock-robot.exe analyze 00700 --no-llm --verbose 2>&1 | Tee-Object -FilePath tmp\acceptance\unified-data-collection\hk-cli.log
```

检查命令返回 0，并在 `reports/stock/` 下确认生成了港股 Markdown。

- [ ] **Step 5: 检查两份 Markdown**

```powershell
Get-ChildItem reports\stock\*.md | Sort-Object LastWriteTime -Descending | Select-Object -First 5 FullName,LastWriteTime
```

打开最新的 A 股和港股 Markdown，检查：

- 报告成功生成，数据覆盖、来源和采集时间可见。
- 历史参考只在预期场景出现，且不进入评分、信号或 AI 事实。
- 港股财务、行情、估值、关键词新闻和行业专用路径没有回归。
- 估值组件失败补位、负值语义、逐项日期没有退化；关键词新闻的专用缓存和 30 天窗口没有回归。
- 不使用 Mock 报告替代真实 CLI 生成结果。

本轮按用户要求跳过真实浏览器发送/响应验收；CLI 仍走真实 Pipeline 和取数路径。关键报告路径和检查结论记录在聊天和 `tmp/acceptance/unified-data-collection/` 日志中，不提交。

- [ ] **Step 6: 记录验收结果**

在聊天中记录：

- 实际 CLI 命令、A 股/港股 Markdown 路径和生成时间。
- 报告生成结果、关键检查项和未覆盖路径。
- 运行过的验证命令及结果，明确区分本轮失败与既有失败。
- 是否存在既有失败；如果有，引用首个异常和失败清单。
- 确认未推送、未合并、未发布。

不要把验收日志或完整报告复制进仓库；`tmp/` 保持本地，报告保留在 `reports/stock/`（按项目既有忽略规则处理）。

- [ ] **Step 7: 提交设计与计划文档**

```powershell
git add docs/superpowers/specs/2026-10/2026-10-26-unified-data-collection-design.md docs/superpowers/plans/2026-10/2026-10-26-unified-data-collection-plan.md
git diff --cached --check
git commit -m "docs(数据采集): 统一取数规范设计与 Phase 0 实施计划"
```

如果设计/计划已在更早的提交中提交，跳过本步骤；不要为同一文档重复造提交。

- [ ] **Step 8: 合并/发布前门禁（非本轮自动执行）**

只有用户明确要求合并或发布时，才运行：

```powershell
.\scripts\verify.ps1 -Scope Full
```

Full 必须 Ruff、Pyright、pytest 全部通过。失败时保留首个完整异常和失败清单，区分本轮改动与既有失败。

---

## 自审清单

- [ ] 设计中的 `result_state` 六种取值、`cache_state` 投影和非法组合都有测试。
- [ ] 五组能力都有策略，`POLICIES` 与 `MIGRATED_CAPABILITIES` 一致，`source_attempts` 为 2。
- [ ] `DataSource.fetch_result()` 是五组能力的统一入口；`AkShareAdapter` 显式覆盖，旧 `fetch()` 仍可供指数/回测使用。
- [ ] 失败只写 `last_attempt`，部分结果不覆盖 `last_complete`，完整/空结果更新 `last_complete`。
- [ ] 正常 TTL、1800 秒等待、等待不续期、手动刷新跳过等待都有测试。
- [ ] 旧缓存缺 `result_state` 时按策略推导；空列表不被猜成正常空结果；旧失败不占正常 TTL。
- [ ] 参考限龄 30/7/1 天、新闻 30 天窗口、未知时间不伪造、参考不进入评分/信号/AI。
- [ ] 运行时拒绝缺策略、缺 `fetch_result`、矛盾记录；契约错误不写缓存。
- [ ] 静态检查覆盖 `fetch` 的 `data_type` 字面量、动态 `**kwargs`、`fetch_result` 越权调用和清单失效。
- [ ] `verify.ps1` 在 Changed（有 Python 改动）和 Full 时执行静态检查；PlanOnly 不执行。
- [ ] 港股估值、港股新闻、行业、指数、雷达、回测和辅助补取的回归保持通过。
- [ ] CLI A 股/港股 Markdown 验收完成，未用 Mock 报告替代；按用户要求跳过浏览器。
- [ ] 设计与计划各一份，未追加到已收尾的 2026-10-22 港股多源项目。

---

# Phase 1 实施计划：港股新闻、A/HK 估值、A/HK 行业

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让港股新闻、A/HK 估值和 A/HK 行业使用统一 `fetch_result` 结果/诊断契约和 `result_state`，同时保留各自专用缓存、合并、资格复核、逐项日期和查询预算。

**Architecture:** Phase 0 的五组能力继续由 `ResilientCollector` 管理；Phase 1 的五个能力走 `DEDICATED_CAPABILITIES`，在 `Pipeline` 的专用分支调用 `fetch_result`，由专用策略分类状态，`make_record` 构造记录；缓存和合并逻辑留在原位置。共享状态映射和记录校验放入 `src/data/collection_state.py`。

**Tech Stack:** Python 3.11、Pydantic v2、SQLite、pytest、Ruff、Pyright、Windows PowerShell。

**依据设计：** `docs/superpowers/specs/2026-10/2026-10-26-unified-data-collection-design.md` 第 15 节。

**执行约束：**

- 在 `feat/unified-data-collection-phase1` 分支实施；不推送、不合并。
- Phase 1 只迁移港股新闻、A/HK 估值、A/HK 行业；同行财务/年度 ROE/总股本/名称解析/指数/雷达/回测不动。
- 保留所有现有 TTL、缓存键、组件合并、非正值语义、资格复核、双查询预算和失败回退。
- 不新增 `DataReference`；Phase 1 能力不生成历史参考。
- 手动 pytest 前先创建 `$run` 目录；使用 `.venv/Scripts/python.exe`、`ruff.exe`、`pyright.exe`。
- `dev` 上既有前端静态测试失败是基线问题；Phase 1 交付说明中必须明确记录，不能声称 Full 全绿。

---

### Task 7: 共享记录状态与专用策略

**Files:**
- Create: `src/data/collection_state.py`
- Modify: `src/data/collection_policies.py`
- Modify: `src/core/resilient_collection.py`
- Test: `tests/data/test_collection_state.py`
- Test: `tests/data/test_dedicated_policies.py`

- [ ] **Step 1: 写共享状态测试**

创建 `tests/data/test_collection_state.py`：

```python
from data.collection_state import CACHE_STATE, make_record, record_consistent
from data.schemas import DataCollectionRecord


def test_cache_state_projection_covers_all_result_states():
    assert CACHE_STATE == {
        "complete": "complete",
        "partial": "degraded",
        "empty": "complete",
        "not_applicable": "not_applicable",
        "failed": "degraded",
        "unknown": "unknown",
    }


def test_make_record_sets_result_and_cache_state():
    record = make_record("测试源", "partial", error_kind="incomplete", error_message="缺项")
    assert record.source == "测试源"
    assert record.result_state == "partial"
    assert record.cache_state == "degraded"
    assert record.error_kind == "incomplete"
    assert record.error_message == "缺项"
    assert record.collected_at is None


def test_record_consistency_rejects_contradiction():
    assert record_consistent(DataCollectionRecord(result_state="complete", cache_state="complete"))
    assert not record_consistent(DataCollectionRecord(result_state="complete", cache_state="degraded"))
    assert record_consistent(DataCollectionRecord(result_state="unknown", cache_state="complete"))
```

- [ ] **Step 2: 写专用策略测试**

创建 `tests/data/test_dedicated_policies.py`，至少覆盖：

```python
from datetime import datetime

from data.collection_policies import dedicated_policy_for
from data.hk_news import HK_TZ
from data.schemas import (
    HKNewsItem, HKNewsLink, HKNewsSnapshot,
    IndustryData, NewsData, ValuationData,
)

NOW = datetime(2026, 10, 27, 12, tzinfo=HK_TZ)


def hk_news(rows=1, errors=False):
    snapshot = HKNewsSnapshot(
        fetched_at=NOW, queries=["00700"], query_errors=["名称查询失败"] if errors else [],
        items=[HKNewsItem(title=f"消息{i}", published_at=NOW, links=[
            HKNewsLink(media="测试", url="https://example.com/news")]) for i in range(rows)],
    )
    row = NewsData(symbol="00700.HK", date=NOW.date(), hk_news=snapshot,
                   headlines=[item.title for item in snapshot.items])
    return [row]


def test_hk_news_policy_states():
    policy = dedicated_policy_for("hk", "news")
    assert policy is not None
    assert policy.classify("00700.HK", hk_news(), None, None, NOW) == "complete"
    assert policy.classify("00700.HK", hk_news(rows=0), None, None, NOW) == "empty"
    assert policy.classify("00700.HK", hk_news(errors=True), None, None, NOW) == "partial"
    assert policy.classify("00700.HK", [], None, None, NOW) == "failed"


def test_valuation_policy_states():
    a = dedicated_policy_for("a-shares", "valuation")
    hk = dedicated_policy_for("hk", "valuation")
    assert a is not None and hk is not None
    assert a.classify("000001", [ValuationData(symbol="000001", date=NOW.date(), pe_ttm=10)], None, None, NOW) == "complete"
    assert a.classify("000001", [ValuationData(symbol="000001", date=NOW.date())], None, None, NOW) == "failed"
    assert hk.classify("00700.HK", [ValuationData(
        symbol="00700.HK", date=NOW.date(), pe_ttm=-1, pb=-2)], None, None, NOW) == "partial"
    assert hk.classify("00700.HK", [ValuationData(
        symbol="00700.HK", date=NOW.date(), pe_ttm=10, cache_degraded=True)], None, None, NOW) == "partial"
    assert hk.classify("00700.HK", [ValuationData(
        symbol="00700.HK", date=NOW.date(), pe_ttm=10)], None, None, NOW) == "complete"


def test_industry_policy_states():
    a = dedicated_policy_for("a-shares", "industry")
    assert a is not None
    complete = IndustryData(symbol="000001", industry="银行", sector="金融",
                            comparison_ready=True, comparison_snapshot={"errors": []})
    partial = complete.model_copy(update={"comparison_snapshot": {"errors": ["同行报价失败"]}})
    failed = IndustryData(symbol="000001", industry="未知", sector="")
    assert a.classify("000001", [complete], None, None, NOW) == "complete"
    assert a.classify("000001", [partial], None, None, NOW) == "partial"
    assert a.classify("000001", [failed], None, None, NOW) == "failed"
```

- [ ] **Step 3: 运行测试确认失败**

```powershell
$run = "tmp/pytest/phase1-task7-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/data/test_collection_state.py tests/data/test_dedicated_policies.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

预期：模块/策略不存在。

- [ ] **Step 4: 创建 `src/data/collection_state.py`**

```python
"""共享结果状态与采集记录构造。"""
from typing import Literal

from data.schemas import DataCollectionRecord, ResultState

State = Literal["complete", "degraded", "not_applicable", "unknown"]
CACHE_STATE: dict[ResultState, State] = {
    "complete": "complete",
    "partial": "degraded",
    "empty": "complete",
    "not_applicable": "not_applicable",
    "failed": "degraded",
    "unknown": "unknown",
}


def make_record(source: str, state: ResultState, *,
                diagnostic: DataCollectionRecord | None = None,
                collected_at=None, cache_status: str = "live",
                error_kind: str = "", error_message: str = "") -> DataCollectionRecord:
    return DataCollectionRecord(
        source=source,
        collected_at=collected_at,
        cache_status=cache_status,
        error_kind=error_kind or (diagnostic.error_kind if diagnostic else ""),
        error_message=error_message or (diagnostic.error_message if diagnostic else ""),
        cache_state=CACHE_STATE[state],
        result_state=state,
    )


def with_result_state(record: DataCollectionRecord, state: ResultState) -> DataCollectionRecord:
    return record.model_copy(update={
        "cache_state": CACHE_STATE[state],
        "result_state": state,
    })


def record_consistent(record: DataCollectionRecord) -> bool:
    return record.cache_state == CACHE_STATE.get(record.result_state)
```

- [ ] **Step 5: 在 `collection_policies.py` 增加专用策略**

在现有 `_Policy` 后增加（并按现有文件导入 `IndustryData`、`ValuationData`）：

```python
class HKNewsPolicy(_Policy):
    kind = "news"
    ttl_key = "news"
    reference_age = 0
    source_attempts = 1

    def validate(self, symbol: str, rows: list[Any], now: datetime) -> bool:
        if len(rows) != 1:
            return False
        news = rows[0]
        snapshot = getattr(news, "hk_news", None)
        return (
            isinstance(news, NewsData)
            and news.symbol == symbol
            and now.astimezone(HK_TZ).date() >= news.date
            and snapshot is not None
            and snapshot.fetched_at.utcoffset() is not None
            and snapshot.fetched_at <= now
        )

    def classify(self, symbol, rows, diagnostic, previous_rows, now):
        if not self.validate(symbol, rows, now):
            return "failed"
        snapshot = rows[0].hk_news
        assert snapshot is not None
        if snapshot.query_errors:
            return "partial"
        return "empty" if not snapshot.items else "complete"

    def reference_rows(self, rows, now):
        return None


class ValuationPolicy(_Policy):
    kind = "valuation"
    ttl_key = "daily"
    reference_age = 0
    source_attempts = 2

    def __init__(self, market: str):
        self.market = market

    def validate(self, symbol, rows, now):
        return (len(rows) == 1 and isinstance(rows[0], ValuationData)
                and rows[0].symbol == symbol)

    def classify(self, symbol, rows, diagnostic, previous_rows, now):
        if not self.validate(symbol, rows, now):
            return "failed"
        item = rows[0]
        values = [value for value in (item.pe_ttm, item.pb, item.ps_ttm)
                  if isinstance(value, (int, float)) and not isinstance(value, bool)
                  and math.isfinite(value)]
        if not values:
            return "failed"
        if self.market == "hk":
            positive = any(value > 0 for value in values)
            return "complete" if positive and not item.cache_degraded else "partial"
        return "complete" if (item.pe_ttm is not None or item.pb is not None) else "failed"

    def reference_rows(self, rows, now):
        return None


class IndustryPolicy(_Policy):
    kind = "industry"
    ttl_key = "quarterly"
    reference_age = 0
    source_attempts = 1

    def __init__(self, market: str):
        self.market = market

    def validate(self, symbol, rows, now):
        return (len(rows) == 1 and isinstance(rows[0], IndustryData)
                and rows[0].symbol == symbol
                and rows[0].industry not in ("", "未知"))

    def classify(self, symbol, rows, diagnostic, previous_rows, now):
        if not self.validate(symbol, rows, now):
            return "failed"
        item = rows[0]
        errors = item.comparison_snapshot.get("errors") or []
        if errors:
            return "partial"
        return "complete" if item.comparison_ready else "partial"

    def reference_rows(self, rows, now):
        return None


DEDICATED_POLICIES: dict[tuple[str, str], CollectionPolicy] = {
    ("hk", "news"): HKNewsPolicy(),
    ("a-shares", "valuation"): ValuationPolicy("a-shares"),
    ("hk", "valuation"): ValuationPolicy("hk"),
    ("a-shares", "industry"): IndustryPolicy("a-shares"),
    ("hk", "industry"): IndustryPolicy("hk"),
}


def dedicated_policy_for(market: str, kind: str) -> CollectionPolicy | None:
    return DEDICATED_POLICIES.get((market, kind))
```

`POLICIES`、`policy_for` 保持五组收集器策略不变。

- [ ] **Step 6: `resilient_collection.py` 复用共享状态**

- 删除本地 `CACHE_STATE` 和 `_record_consistent`，改为从 `data.collection_state` 导入 `CACHE_STATE`、`record_consistent`、`with_result_state`。
- `_read` 的矛盾检查改调 `record_consistent(record)`。
- `_live_record` 可改为 `make_record(...)`，但必须保持现有 `source`、`collected_at`、`cache_status`、诊断字段行为。
- 五组收集器测试必须全部通过。

- [ ] **Step 7: 运行测试和静态检查**

```powershell
$run = "tmp/pytest/phase1-task7-reg-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/data/test_collection_state.py tests/data/test_dedicated_policies.py tests/core/test_resilient_collection.py tests/core/test_collection_contract.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
.\.venv\Scripts\ruff.exe check src/data/collection_state.py src/data/collection_policies.py src/core/resilient_collection.py tests/data/test_collection_state.py tests/data/test_dedicated_policies.py
.\.venv\Scripts\pyright.exe src/data/collection_state.py src/data/collection_policies.py src/core/resilient_collection.py tests/data/test_collection_state.py tests/data/test_dedicated_policies.py
```

预期：通过。

- [ ] **Step 8: 提交**

```powershell
git add src/data/collection_state.py src/data/collection_policies.py src/core/resilient_collection.py tests/data/test_collection_state.py tests/data/test_dedicated_policies.py
git diff --cached --check
git commit -m "feat(数据采集): 增加 Phase 1 共享状态与专用策略"
```

---

### Task 8: 港股行业适配器统一入口与测试替身

**Files:**
- Modify: `src/data/hk_hsics_peers.py`
- Modify: `tests/core/test_hk_news_pipeline.py`
- Modify: `tests/core/test_peer_pipeline.py`
- Test: `tests/data/test_hk_hsics_peers.py`

- [ ] **Step 1: 写失败测试**

在 `tests/data/test_hk_hsics_peers.py` 增加：

```python
def test_adapter_fetch_result_returns_rows_and_no_diagnostic(monkeypatch, mocker, tmp_path):
    monkeypatch.setattr(peers, "load_hsics_members", lambda: MEMBERS)
    monkeypatch.setattr(peers, "load_connect_symbols", lambda: {s.removesuffix(".HK") for s in CONNECT})
    rows = [pool_row("00700.HK", "腾讯", 13, 2), pool_row("00268.HK", "金蝶", 30, 4)]
    mocker.patch.object(peers, "fetch_sector_pool", return_value=(rows, "2026-10-23"))
    result = peers.HKIndustryAdapter().fetch_result("00700.HK", comparison_cache=CacheManager(tmp_path / "cache.db"))
    assert result.rows and result.rows[0].industry == "软件服务"
    assert result.record is None
```

- [ ] **Step 2: 运行确认失败**

```powershell
$run = "tmp/pytest/phase1-task8-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/data/test_hk_hsics_peers.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

预期：`fetch_result` 不存在。

- [ ] **Step 3: 重构 `HKIndustryAdapter`**

在 `src/data/hk_hsics_peers.py` 导入 `CollectionResult`：

```python
from data.base import CollectionResult, DataSource
```

把现有 `fetch` 主体改名为 `_fetch_impl`，并增加两个包装：

```python
    def fetch(self, symbol: str, **kwargs) -> list:
        return self._fetch_impl(symbol, **kwargs)[0]

    def fetch_result(self, symbol: str, **kwargs) -> CollectionResult:
        rows, record = self._fetch_impl(symbol, **kwargs)
        return CollectionResult(rows=rows, record=record)

    def _fetch_impl(self, symbol: str, **kwargs) -> tuple[list, None]:
        # 原 fetch 主体逐行保留；所有 return [] 改为 return [], None；
        # 所有 return [data] 改为 return [data], None；异常继续抛出。
```

不要改变资格复核、`peer_hk_industry_v2`、3600 秒缓存、逐行日期、采集预算和错误快照行为。

- [ ] **Step 4: 更新测试替身**

- `tests/core/test_hk_news_pipeline.py::NewsSource` 改为 `class NewsSource(FetchResultMixin, DataSource)`，并导入 `tests.support.FetchResultMixin`。
- `tests/core/test_peer_pipeline.py::CachedIndustrySource`、`PartialIndustrySource` 改为 `FetchResultMixin, DataSource`。
- 其他支持 `("hk","industry")`、`("a-shares","industry")`、`("hk","news")`、`("a-shares","valuation")`、`("hk","valuation")` 且会构造 `Pipeline` 的测试假源，也必须显式实现 `fetch_result`。

- [ ] **Step 5: 运行测试**

```powershell
$run = "tmp/pytest/phase1-task8-reg-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/data/test_hk_hsics_peers.py tests/core/test_peer_pipeline.py tests/core/test_hk_news_pipeline.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
.\.venv\Scripts\ruff.exe check src/data/hk_hsics_peers.py tests/data/test_hk_hsics_peers.py tests/core/test_hk_news_pipeline.py tests/core/test_peer_pipeline.py
.\.venv\Scripts\pyright.exe src/data/hk_hsics_peers.py tests/data/test_hk_hsics_peers.py tests/core/test_hk_news_pipeline.py tests/core/test_peer_pipeline.py
```

预期：通过。

- [ ] **Step 6: 提交**

```powershell
git add src/data/hk_hsics_peers.py tests/data/test_hk_hsics_peers.py tests/core/test_hk_news_pipeline.py tests/core/test_peer_pipeline.py
git diff --cached --check
git commit -m "refactor(港股行业): 增加统一 fetch_result 入口"
```

---

### Task 9: 港股新闻接入统一结果契约

**Files:**
- Modify: `src/core/pipeline.py`
- Modify: `tests/core/test_hk_news_pipeline.py`

- [ ] **Step 1: 写/更新测试**

在 `tests/core/test_hk_news_pipeline.py`：

- `NewsSource` 已使用 `FetchResultMixin`（Task 8）。
- 增加一个只实现 `fetch_result`、`fetch` 抛错的新闻源，验证 `_collect_hk_news` 不再调用 `fetch`。
- 在现有成功/空/部分/失败测试中断言：
  - 成功有条目 → `result_state=="complete"`。
  - 成功零条 → `result_state=="empty"`。
  - 部分查询错误 → `result_state=="partial"`。
  - 双查询失败且无旧快照 → `result_state=="failed"`。
  - 失败回退旧快照 → `cache_status=="stale"`，`result_state` 按旧快照内容为 `complete/empty/partial`。
- 保留 3600 秒 TTL、单次调用预算、部分不覆盖完整、旧快照时间等断言。

- [ ] **Step 2: 运行确认旧行为失败**

```powershell
$run = "tmp/pytest/phase1-task9-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/core/test_hk_news_pipeline.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

预期：新断言失败（当前记录没有 `result_state` 或仍调用 `fetch`）。

- [ ] **Step 3: 修改 `Pipeline._collect_hk_news`**

- 导入 `DataSource`、`make_record`、`with_result_state`、`CACHE_STATE`、`dedicated_policy_for`。
- 将 `result = sources[0].fetch(...)` 改为：

```python
source = sources[0]
if type(source).fetch_result is DataSource.fetch_result:
    return None, make_record(
        SOURCE, "failed", cache_status="live",
        error_kind="contract_error",
        error_message="港股新闻来源未实现统一取数入口 fetch_result",
    )
collection = source.fetch_result(
    symbol, market="hk", data_type="news", name=name
)
result = collection.rows or []
diagnostic = collection.record
```

- 成功分支用 `dedicated_policy_for("hk", "news").classify(...)` 得到状态：

```python
snapshot = result[0].hk_news
policy = dedicated_policy_for("hk", "news")
assert policy is not None
state = policy.classify(symbol, result, diagnostic, None, datetime.now(HK_TZ))
record = make_record(
    SOURCE, state, diagnostic=diagnostic,
    collected_at=snapshot.fetched_at, cache_status="live",
    error_kind="partial" if snapshot.query_errors else "",
    error_message="部分关键词未成功检索，结果不完整" if snapshot.query_errors else "",
)
```

- 缓存命中分支：若元数据记录 `result_state=="unknown"`，用缓存快照调用策略推导，再 `with_result_state(record, state)`；保留 `collected_at`。
- 旧快照回退分支：若记录 `result_state=="unknown"`，用旧快照推导；设置 `cache_status="stale"`、`error_kind="live_unavailable"`、现有错误说明；保留旧 `collected_at`。
- 双失败无旧快照：返回 `make_record(SOURCE, "failed", cache_status="live", error_kind="fetch_failed", error_message="新闻暂时获取失败")`；不写缓存。
- `_set_cache`/`_store_cache` 不改；它们继续写 3600 秒 TTL、保护部分快照。

- [ ] **Step 4: 运行测试**

```powershell
$run = "tmp/pytest/phase1-task9-reg-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/core/test_hk_news_pipeline.py tests/core/test_pipeline.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
.\.venv\Scripts\ruff.exe check src/core/pipeline.py tests/core/test_hk_news_pipeline.py
.\.venv\Scripts\pyright.exe src/core/pipeline.py tests/core/test_hk_news_pipeline.py
```

预期：通过。

- [ ] **Step 5: 提交**

```powershell
git add src/core/pipeline.py tests/core/test_hk_news_pipeline.py
git diff --cached --check
git commit -m "refactor(港股新闻): 接入统一取数结果与状态"
```

---

### Task 10: A/HK 估值接入统一结果契约

**Files:**
- Modify: `src/core/pipeline.py`
- Modify: `tests/core/test_pipeline_health.py`
- Modify: `tests/core/test_pipeline.py`（如需补 A 股估值假源）
- Test: `tests/data/test_hk_valuation_history.py`

- [ ] **Step 1: 写/更新测试**

在 `tests/core/test_pipeline_health.py`：

- 确认 `FakeSource` 已使用 `FetchResultMixin`。
- 给现有港股估值测试补充 `result_state` 断言：
  - 正常估值 → `complete`。
  - `cache_degraded` / 沿用旧组件 → `partial`。
  - 负值-only → `partial`。
  - 空结果/全缺失 → `failed`。
- 新增 A 股估值成功缓存测试：`FakeSource({"valuation":[ValuationData(symbol="000001", date=..., pe_ttm=10)]})`，两次 collect，第二次 `cache_status=="cache"`、`result_state=="complete"`，源只调用一次。
- 保留 `last_complete`、1800 秒、负值、PS、合并和断路器断言。

- [ ] **Step 2: 运行确认失败**

```powershell
$run = "tmp/pytest/phase1-task10-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/core/test_pipeline_health.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

预期：新增 `result_state`/A 股估值断言失败。

- [ ] **Step 3: 修改 `Pipeline.collect.fetch_one`**

- 导入 `CollectionResult`、`make_record`、`with_result_state`、`dedicated_policy_for`。
- 将 `result = source.fetch(symbol, **fetch_kwargs)` 改为：

```python
collection = source.fetch_result(symbol, **fetch_kwargs)
if not isinstance(collection, CollectionResult):
    failure_record = make_record(
        source.__class__.__name__, "failed", cache_status="live",
        error_kind="contract_error", error_message="fetch_result 返回类型无效",
    )
    continue
result = collection.rows or []
diagnostic = collection.record
```

- 删除 `get_collection_error` 旁路读取；诊断来自 `collection.record`。
- 在进入估值分支前初始化 `used_stale = False`（当前代码只在港股合并分支内赋值，避免静态分析报可能未绑定）。
- 在估值结果合并、`_is_healthy` 判断之后、写缓存前计算状态：

```python
policy = dedicated_policy_for(market, data_type)
if data_type in ("valuation", "industry") and policy is not None:
    state = policy.classify(symbol, result, diagnostic, None, datetime.now(HK_TZ))
    if data_type == "valuation" and market == "hk" and used_stale:
        state = "partial"
    record = with_result_state(record, state)
```

- 缓存命中分支：若 `record.result_state=="unknown"`，用 `dedicated_policy_for(...).classify(symbol, cached, None, None, now)` 推导并 `with_result_state`；保留 `collected_at`。
- 过期缓存回退分支：同样推导，保留旧采集时间；设置 `cache_status="stale"`。
- 估值 `_set_cache`/`_store_cache` 不改；港股 `last_complete`、1800 秒、非正值语义保持不变。

- [ ] **Step 4: 运行估值回归**

```powershell
$run = "tmp/pytest/phase1-task10-reg-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/core/test_pipeline_health.py tests/data/test_hk_valuation_history.py tests/core/test_pipeline.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
.\.venv\Scripts\ruff.exe check src/core/pipeline.py tests/core/test_pipeline_health.py tests/data/test_hk_valuation_history.py
.\.venv\Scripts\pyright.exe src/core/pipeline.py tests/core/test_pipeline_health.py tests/data/test_hk_valuation_history.py
```

预期：通过；港股专用回归保持。

- [ ] **Step 5: 提交**

```powershell
git add src/core/pipeline.py tests/core/test_pipeline_health.py tests/core/test_pipeline.py tests/data/test_hk_valuation_history.py
git diff --cached --check
git commit -m "refactor(估值): A/HK 估值接入统一结果与状态"
```

---

### Task 11: A/HK 行业接入统一结果契约

**Files:**
- Modify: `src/core/pipeline.py`
- Modify: `tests/core/test_peer_pipeline.py`
- Test: `tests/data/test_hk_hsics_peers.py`

- [ ] **Step 1: 写/更新测试**

- `CachedIndustrySource`、`PartialIndustrySource` 已使用 `FetchResultMixin`（Task 8）。
- 在现有行业测试中断言：
  - 正常 A 股行业 → `result_state=="complete"`。
  - 有 `comparison_snapshot.errors` → `partial`。
  - 行业未知/无数据 → `failed`。
  - 港股行业缓存命中/刷新失败快照 → 状态与 `comparison_snapshot` 一致；`_cacheable` 仍为 False。
- 增加港股行业 `fetch_result` 与 `fetch` 返回一致测试。

- [ ] **Step 2: 运行确认失败**

```powershell
$run = "tmp/pytest/phase1-task11-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/core/test_peer_pipeline.py tests/data/test_hk_hsics_peers.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

预期：新状态断言失败。

- [ ] **Step 3: 修改 `Pipeline.collect.fetch_one` 行业分支**

- 行业分支已经通过 `source.fetch_result` 取得 `result`/`diagnostic`（Task 10）。
- 在行业返回前，用 `IndustryPolicy` 计算状态并写入记录：

```python
policy = dedicated_policy_for(market, "industry")
if policy is not None:
    state = policy.classify(symbol, result, diagnostic, None, datetime.now(HK_TZ))
    record = with_result_state(record, state)
```

- 保留当前行业专用逻辑：
  - `comparison_cache` / `refresh_cache` / `comparison_cache_only` 参数。
  - `_cacheable("hk","industry") is False`、`_cacheable("a-shares","industry") is False`。
  - 断路器：仅非缓存结果变化时记成功/失败。
  - 即使 `result_state=="failed"` 也返回已有 `IndustryData`，不丢弃 `comparison_snapshot` 诊断。
- 不修改 `HKIndustryAdapter` 的资格复核、缓存键/TTL、逐行日期和预算（Task 8 只加包装）。

- [ ] **Step 4: 运行行业回归**

```powershell
$run = "tmp/pytest/phase1-task11-reg-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/core/test_peer_pipeline.py tests/core/test_pipeline_hk_industry_cache.py tests/data/test_hk_hsics_peers.py tests/data/test_peer_comparison.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
.\.venv\Scripts\ruff.exe check src/core/pipeline.py tests/core/test_peer_pipeline.py tests/data/test_hk_hsics_peers.py
.\.venv\Scripts\pyright.exe src/core/pipeline.py tests/core/test_peer_pipeline.py tests/data/test_hk_hsics_peers.py
```

预期：通过。

- [ ] **Step 5: 提交**

```powershell
git add src/core/pipeline.py tests/core/test_peer_pipeline.py tests/data/test_hk_hsics_peers.py
git diff --cached --check
git commit -m "refactor(行业): A/HK 行业接入统一结果与状态"
```

---

### Task 12: Phase 1 运行时与静态门禁

**Files:**
- Modify: `src/core/collection_contract.py`
- Modify: `src/core/pipeline.py`
- Modify: `tests/core/test_collection_contract.py`
- Modify: `tests/test_data_entrypoints.py`
- Modify: `tests/test_verify_script.py`

- [ ] **Step 1: 写门禁测试**

- `DEDICATED_CAPABILITIES` 正好包含 5 个能力，`CONTRACT_CAPABILITIES` 为五组与专用能力的并集。
- `LEGACY_FETCH_SITES` 不再包含 `Pipeline.collect.fetch_one`、`Pipeline._collect_hk_news`；仍包含 `src/data/base.py`、`src/index/collector.py`、`src/backtest/data.py`。
- `ALLOWED_FETCH_RESULT_CALLS` 包含 `ResilientCollector._collect`、`Pipeline.collect.fetch_one`、`Pipeline._collect_hk_news`。
- 构造只支持 `("hk","news")`、缺少显式 `fetch_result` 的假源 → `Pipeline` 构造失败。
- 迟到注册的同类假源 → 采集返回 `contract_error`，不写缓存。
- `tests/test_data_entrypoints.py::test_repository_entrypoints_are_clean` 继续通过。

- [ ] **Step 2: 运行确认失败**

```powershell
$run = "tmp/pytest/phase1-task12-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/core/test_collection_contract.py tests/test_data_entrypoints.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

预期：新常量/清单断言失败。

- [ ] **Step 3: 修改 `collection_contract.py`**

```python
DEDICATED_CAPABILITIES: frozenset[tuple[str, str]] = frozenset({
    ("hk", "news"),
    ("a-shares", "valuation"),
    ("hk", "valuation"),
    ("a-shares", "industry"),
    ("hk", "industry"),
})
CONTRACT_CAPABILITIES = MIGRATED_CAPABILITIES | DEDICATED_CAPABILITIES
```

- 从 `LEGACY_FETCH_SITES` 删除 `Pipeline.collect.fetch_one`、`Pipeline._collect_hk_news` 两项。
- 在 `ALLOWED_FETCH_RESULT_CALLS` 增加 `Pipeline.collect.fetch_one`、`Pipeline._collect_hk_news`。
- 保留 base 兼容桥、指数、回测条目。

- [ ] **Step 4: `Pipeline` 增加专用能力契约检查**

在 `Pipeline.__init__` 末尾：

```python
self._validate_dedicated_contract()
```

实现：

```python
def _validate_dedicated_contract(self) -> None:
    for market, kind in DEDICATED_CAPABILITIES:
        sources = self._registry.get_data_sources(market, kind)
        if not isinstance(sources, list):
            continue
        for source in sources:
            if type(source).fetch_result is DataSource.fetch_result:
                raise ValueError(
                    f"数据源 {source.__class__.__name__} 未实现统一取数入口 fetch_result"
                )
```

- `Pipeline.fetch_one` 在调用 `fetch_result` 前再次检查来源；缺失时 `contract_error`，不写缓存。
- `_collect_hk_news` 同样检查。
- `Pipeline` 需要导入 `DataSource`、`DEDICATED_CAPABILITIES`。

- [ ] **Step 5: 更新 verifier 映射/测试**

- `tests/test_verify_script.py` 保持并补充：
  - `src/core/pipeline.py` 仍拉取 `tests/core`、`tests/data`、`tests/report`、`tests/agent`。
  - `src/data/collection_policies.py`、`src/data/collection_state.py`、`src/data/hk_hsics_peers.py` 拉取相关测试。
- `scripts/verify.ps1` 必要时把 `src/data/collection_state.py`、`src/data/hk_hsics_peers.py` 映射到 `tests/data`、`tests/core`。
- 静态检查实际运行：

```powershell
.\.venv\Scripts\python.exe scripts\check-data-entrypoints.py
```

必须输出“数据取数入口检查通过”。

- [ ] **Step 6: 运行门禁测试**

```powershell
$run = "tmp/pytest/phase1-task12-reg-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/core/test_collection_contract.py tests/test_data_entrypoints.py tests/test_verify_script.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
.\.venv\Scripts\ruff.exe check src/core/collection_contract.py src/core/pipeline.py tests/core/test_collection_contract.py tests/test_data_entrypoints.py tests/test_verify_script.py
.\.venv\Scripts\pyright.exe src/core/collection_contract.py src/core/pipeline.py tests/core/test_collection_contract.py tests/test_data_entrypoints.py tests/test_verify_script.py
```

预期：通过。

- [ ] **Step 7: 提交**

```powershell
git add src/core/collection_contract.py src/core/pipeline.py tests/core/test_collection_contract.py tests/test_data_entrypoints.py tests/test_verify_script.py scripts/verify.ps1
git diff --cached --check
git commit -m "chore(门禁): 覆盖 Phase 1 专用取数能力"
```

---

### Task 13: Phase 1 回归与 CLI 验收

**Files:**
- Verify: 全部受影响测试与脚本
- Record: `tmp/acceptance/unified-data-collection-phase1/`（本地，不提交）

- [ ] **Step 1: 运行范围回归**

```powershell
.\scripts\verify.ps1 -Files src/core/pipeline.py
.\scripts\verify.ps1 -Files src/data/hk_hsics_peers.py
.\scripts\verify.ps1 -Files src/data/collection_policies.py
.\scripts\verify.ps1 -Files src/core/collection_contract.py
```

记录每个命令的测试目录和结果；既有前端失败单独记录，不能算 Phase 1 失败或通过。

- [ ] **Step 2: 运行完整受影响测试集**

```powershell
$run = "tmp/pytest/phase1-accept-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/core tests/data tests/report/test_reference_quality.py tests/agent/test_reference_isolation.py tests/index/test_collector.py tests/backtest -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

预期：本轮相关测试通过；若出现 `dev` 既有前端静态测试失败，引用测试名并判定与 Phase 1 无关。

- [ ] **Step 3: CLI 生成 A 股/港股 Markdown**

```powershell
mkdir tmp\acceptance\unified-data-collection-phase1 -Force
.\.venv\Scripts\stock-robot.exe analyze 600519 --no-llm --verbose 2>&1 | Tee-Object -FilePath tmp\acceptance\unified-data-collection-phase1\a-share-cli.log
.\.venv\Scripts\stock-robot.exe analyze 00700 --no-llm --verbose 2>&1 | Tee-Object -FilePath tmp\acceptance\unified-data-collection-phase1\hk-cli.log
```

检查：

- 两条命令返回 0，`reports/stock/` 生成最新 Markdown。
- A/HK 估值来源、日期、负值/组件补位、PS 展示无回归。
- 港股新闻关键词检索、缓存和 30 天窗口无回归。
- A/HK 行业同行池、资格复核和缓存无回归。
- 不生成 Phase 1 历史参考；报告不因 `result_state` 增加新文案。
- 跳过真实浏览器验收；CLI 走真实 Pipeline 和取数路径。

- [ ] **Step 4: 记录和提交文档**

在聊天中记录实际命令、报告路径、测试结果、既有失败和未覆盖风险。把 Phase 1 设计/计划更新提交到当前分支；不要提交 `tmp/`、`reports/` 或无关文件。

```powershell
git add docs/superpowers/specs/2026-10/2026-10-26-unified-data-collection-design.md docs/superpowers/plans/2026-10/2026-10-26-unified-data-collection-plan.md
git diff --cached --check
git commit -m "docs(数据采集): 增加 Phase 1 设计与实施计划"
```

- [ ] **Step 5: 合并/发布前门禁**

只有用户明确要求合并或发布时，才运行：

```powershell
.\scripts\verify.ps1 -Scope Full
```

`dev` 既有前端静态测试失败需在 Full 结果中单独说明；不能将 Phase 1 表述为 Full 全绿。

---

## Phase 1 实施记录（2026-10-27）

Task 7–12 已实现并按任务执行测试先行及相关回归；复审补充无效标的、非有限估值、新闻无效快照、旧 PS 补位和重试间隔测试。按仓库完整事项提交约定，Phase 1 汇总为一笔本地提交，未推送或合并。

CLI 真实生成 A 股 600519 和港股 00700 Markdown，按用户要求跳过浏览器。港股东财名称和估值快照接口发生代理连接失败，报告展示补位和新闻部分失败；这些上游成功路径以离线测试覆盖，真实完整成功未验证。完整受影响测试集 961 项通过（运行期间随后仅修复无效估值重试间隔）；最终 Pipeline 范围门禁 1334 项通过。未运行合并/发布前 Full 门禁；dev 前端静态测试既有失败不归因本阶段。

## Phase 1 自审清单

- [ ] `DEDICATED_CAPABILITIES` 与 `DEDICATED_POLICIES` 一一对应，五组 `POLICIES` 不变。
- [ ] 港股新闻只调用一次 `fetch_result`，3600 秒缓存、部分保护和旧快照回退不变。
- [ ] A/HK 估值 `fetch_result`、状态、`last_complete`、1800 秒、负值和 PS 语义不变。
- [ ] A/HK 行业 `fetch_result`、专用缓存、资格复核、逐行日期和断路器不变。
- [ ] `HKIndustryAdapter.fetch_result` 显式实现，`fetch` 兼容包装仍可用。
- [ ] 旧缓存缺 `result_state` 时按策略推导，不迁移数据库。
- [ ] `Pipeline` 构造和采集前检查专用来源显式 `fetch_result`；契约错误不写缓存。
- [ ] 静态清单移除两个 Pipeline 遗留条目，允许三个 `fetch_result` 调用点；真实仓库检查通过。
- [ ] Phase 1 不新增 `DataReference`、数据源、依赖、评分公式或报告文案。
- [ ] CLI A 股/港股 Markdown 验收完成，跳过浏览器；既有 Full 失败单独记录。
- [ ] Phase 1 设计/计划仍为同一主题的一份设计＋一份计划。

---

# Phase 2 实施计划：辅助补取、指数/策略、静态刷新

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 2A 辅助补取/名称解析、2B 指数/策略、2C 静态刷新使用统一结果/诊断记录，同时保留现有缓存、原子发布、旧快照回退和失败语义。

**Architecture:** 2A 在函数边界返回 `CollectionResult`/`ValueResult`；2B 在 `IndexAnalysisContext` 增加内部 `collection_records`，由 `IndexDataCollector` 生成记录，策略提供器内部缓存不动；2C 使用独立 `RefreshRecord`，保留现有刷新/发布函数作为兼容包装。三者不进入 `ResilientCollector`，不新增 `DataReference`。

**Tech Stack:** Python 3.11、Pydantic v2、SQLite、pytest、Ruff、Pyright、PowerShell。

**依据设计：** `docs/superpowers/specs/2026-10/2026-10-26-unified-data-collection-design.md` 第 16 节。

**执行约束：**

- 在 `feat/unified-data-collection-phase2` 分支实施；不推送、不合并。
- 只做 2A/2B/2C；不顺手改评分、报告文案、前端、依赖或数据源。
- 保留现有缓存键/TTL、SQLite 策略缓存、原子发布、文件锁、旧快照保留和失败回退。
- 2A/2B/2C 不生成 `DataReference`；不进入评分、信号或 AI 事实输入。
- 静态刷新验收只用临时路径或离线 fixture，不覆盖仓库内正式数据。
- `dev` 既有前端静态测试失败按基线记录；不能声称 Full 全绿。
- 手动 pytest 前创建 `$run` 目录。

---

### Task 14: 共享结果类型扩展

**Files:**
- Modify: `src/data/base.py`
- Modify: `src/data/collection_state.py`
- Test: `tests/data/test_collection_state.py`

- [ ] **Step 1: 写失败测试**

在 `tests/data/test_collection_state.py` 增加：

```python
from datetime import datetime, timezone

from data.base import CollectionResult
from data.collection_state import (
    RefreshRecord, ValueResult, make_refresh_record,
)


def test_collection_result_keeps_error_details():
    result = CollectionResult(rows=[{"symbol": "000001"}], errors=["批次失败"])
    assert result.rows == [{"symbol": "000001"}]
    assert result.errors == ["批次失败"]


def test_value_result_and_refresh_record():
    value = ValueResult(value=1.0, record=make_refresh_record("测试源", "complete"))
    assert value.value == 1.0
    record = make_refresh_record("测试源", "partial", error_message="部分失败")
    assert record.result_state == "partial"
    assert record.published is False
    assert isinstance(record, RefreshRecord)
    assert record.attempted_at.tzinfo is not None
```

- [ ] **Step 2: 运行确认失败**

```powershell
$run = "tmp/pytest/phase2-task14-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/data/test_collection_state.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

- [ ] **Step 3: 扩展 `CollectionResult`**

在 `src/data/base.py`：

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)
class CollectionResult:
    rows: list[Any]
    record: DataCollectionRecord | None = None
    errors: list[str] = field(default_factory=list)
```

现有适配器调用不变，`errors` 默认空列表。

- [ ] **Step 4: 增加 `ValueResult`、`RefreshRecord`、`make_refresh_record`**

在 `src/data/collection_state.py`：

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class ValueResult(Generic[T]):
    value: T | None
    record: DataCollectionRecord


@dataclass(frozen=True)
class RefreshRecord:
    source: str
    attempted_at: datetime
    result_state: ResultState
    error_kind: str = ""
    error_message: str = ""
    published: bool = False


def make_refresh_record(source: str, state: ResultState, *,
                        attempted_at: datetime | None = None,
                        error_kind: str = "", error_message: str = "",
                        published: bool = False) -> RefreshRecord:
    if state not in ("complete", "partial", "failed"):
        raise ValueError("静态刷新只允许 complete/partial/failed")
    return RefreshRecord(
        source=source,
        attempted_at=attempted_at or datetime.now().astimezone(),
        result_state=state,
        error_kind=error_kind,
        error_message=error_message,
        published=published,
    )
```

- [ ] **Step 5: 运行测试和静态检查**

```powershell
$run = "tmp/pytest/phase2-task14-reg-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/data/test_collection_state.py tests/core/test_resilient_collection.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
.\.venv\Scripts\ruff.exe check src/data/base.py src/data/collection_state.py tests/data/test_collection_state.py
.\.venv\Scripts\pyright.exe src/data/base.py src/data/collection_state.py tests/data/test_collection_state.py
```

- [ ] **Step 6: 提交**

```powershell
git add src/data/base.py src/data/collection_state.py tests/data/test_collection_state.py
git diff --cached --check
git commit -m "feat(数据采集): 增加 Phase 2 结果与刷新记录类型"
```

---

### Task 15: 2A 同行财务批量契约

**Files:**
- Modify: `src/data/peer_comparison_fetch.py`
- Modify: `src/data/peer_comparison.py`
- Modify: `src/data/financial_scoring_facts.py`
- Test: `tests/data/test_peer_comparison_fetch.py`

- [ ] **Step 1: 写失败测试**

在 `tests/data/test_peer_comparison_fetch.py` 增加/更新：

```python
def test_fetch_financial_rows_returns_record_and_errors(monkeypatch, tmp_path):
    cache = CacheManager(tmp_path / "cache.db")
    # 复用现有分页/响应 fixture，让函数成功返回；按实际 fixture 调整。
    result = fetch_financial_rows(
        ["000001", "600519"], "a-shares", start="2025-01-01",
        deadline=time.monotonic() + 60, cache=cache,
    )
    assert result.rows
    assert result.errors == []
    assert result.record is not None
    assert result.record.result_state == "complete"
    assert result.record.cache_status == "live"


def test_fetch_financial_rows_failure_preserves_old_snapshot(monkeypatch, tmp_path):
    # 先写入成功快照，再让请求失败；断言 rows 来自旧快照、cache_status="stale"、
    # result_state="partial"、errors 保留失败原因。
    ...
```

实际测试使用现有 `requests`/分页 mock；不要联网。

- [ ] **Step 2: 运行确认失败**

```powershell
$run = "tmp/pytest/phase2-task15-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/data/test_peer_comparison_fetch.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

- [ ] **Step 3: 增加带保存时间的快照读取**

在 `src/data/peer_comparison_fetch.py`：

```python
def read_snapshot_entry(cache, category, key, ttl) -> tuple[Any, bool, float | None]:
    if cache is None:
        return None, False, None
    raw = cache.get_stale(category, key, "v1")
    if not raw:
        return None, False, None
    try:
        envelope = json.loads(raw)
        saved_at = float(envelope["saved_at"])
        age = time.time() - saved_at
        return envelope["payload"], 0 <= age < ttl, saved_at
    except (ValueError, TypeError, KeyError):
        logger.warning("同行比较缓存格式无效：%s", category)
        return None, False, None


def read_snapshot(cache, category, key, ttl) -> tuple[Any, bool]:
    payload, fresh, _ = read_snapshot_entry(cache, category, key, ttl)
    return payload, fresh
```

- [ ] **Step 4: `fetch_financial_rows` 返回 `CollectionResult`**

- 使用 `read_snapshot_entry` 保留 `saved_at`。
- 增加记录构造：

```python
from datetime import datetime, timezone
from data.base import CollectionResult
from data.collection_state import make_record

SOURCE = "东财主要财务指标"

def _collected_at(saved_at: float | None):
    return (datetime.fromtimestamp(saved_at, tz=timezone.utc).astimezone()
            if saved_at is not None else None)
```

- 新鲜缓存：`result_state="complete"`, `cache_status="cache"`, `collected_at=_collected_at(saved_at)`。
- 新请求成功且无错误/覆盖完整：`complete`, `live`, `collected_at=now`。
- 有行但覆盖不足/有错误：`partial`, `live` 或 `stale`。
- 有旧快照且请求失败：`partial`, `stale`, `collected_at=_collected_at(saved_at)`, `errors` 保留原失败和“使用原报告期成功快照”。
- 无行：`failed`, `live`, `error_kind="empty"`；`errors` 保留。
- 返回 `CollectionResult(rows=rows, record=record, errors=errors)`。

保持 `peer_financial` 21600 秒 TTL、预算、两并发、分页完整性。

- [ ] **Step 5: 更新消费方**

`src/data/peer_comparison.py`：

```python
financial = fetch_financial_rows(...)
rows, errors = financial.rows, financial.errors
ctx.collection_records["peer_financial"] = financial.record
```

`src/data/financial_scoring_facts.py`：

```python
financial = fetch_financial_rows(...)
rows, errors = financial.rows, financial.errors
ctx.collection_records["annual_roe"] = financial.record
```

现有 `comparison["errors"]` 和年度事实 `reason` 继续使用 `errors`；不把它们改为记录展示。

- [ ] **Step 6: 运行回归和静态检查**

```powershell
$run = "tmp/pytest/phase2-task15-reg-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/data/test_peer_comparison_fetch.py tests/core/test_peer_commentary.py tests/core/test_peer_pipeline.py tests/data/test_financial_scoring_facts.py tests/report -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
.\.venv\Scripts\ruff.exe check src/data/peer_comparison_fetch.py src/data/peer_comparison.py src/data/financial_scoring_facts.py tests/data/test_peer_comparison_fetch.py
.\.venv\Scripts\pyright.exe src/data/peer_comparison_fetch.py src/data/peer_comparison.py src/data/financial_scoring_facts.py tests/data/test_peer_comparison_fetch.py
```

- [ ] **Step 7: 提交**

```powershell
git add src/data/peer_comparison_fetch.py src/data/peer_comparison.py src/data/financial_scoring_facts.py tests/data/test_peer_comparison_fetch.py
git diff --cached --check
git commit -m "refactor(辅助补取): 同行财务接入统一结果记录"
```

---

### Task 16: 2A 总股本与名称解析

**Files:**
- Modify: `src/data/akshare.py`
- Modify: `src/utils/symbols.py`
- Modify: `src/data/enrichers/valuation_enricher.py`
- Test: `tests/data/test_akshare.py`、`tests/utils/test_symbols.py`（如存在）、`tests/data/enrichers/`

- [ ] **Step 1: 写失败测试**

- `get_total_shares_result` 成功时 `.value > 0`，`record.result_state=="complete"`，`record.source` 为实际来源。
- 三步都失败时 `.value is None`，`record.result_state=="failed"`。
- `resolve_name_result("600519")` 成功时 `.value` 非空；失败时 `.value is None`，记录 failed。
- `get_total_shares`/`resolve_name` 兼容包装仍返回原类型。

- [ ] **Step 2: 运行确认失败**

```powershell
$run = "tmp/pytest/phase2-task16-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/data/test_akshare.py tests/utils/test_symbols.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

- [ ] **Step 3: 实现 `get_total_shares_result`**

在 `src/data/akshare.py`：

```python
from data.collection_state import ValueResult, make_record

def get_total_shares_result(symbol: str, financials: list | None = None) -> ValueResult[float]:
    # 复用现有三步：东财轻量接口 → 新浪流通股本 → 财报反推。
    # 成功：ValueResult(value, make_record(来源, "complete", collected_at=now))
    # 失败：ValueResult(None, make_record("总股本", "failed",
    #       error_kind="empty", error_message="总股本来源均不可用"))
    ...

def get_total_shares(symbol: str, financials: list | None = None) -> float | None:
    return get_total_shares_result(symbol, financials).value
```

每一步的异常隔离和来源标签保持原有行为；不新增网络请求。

- [ ] **Step 4: 实现 `resolve_name_result`**

在 `src/utils/symbols.py`：

```python
from data.collection_state import ValueResult, make_record

def resolve_name_result(symbol: str) -> ValueResult[str]:
    # 现有 HK 东财快照/A 股 AkShare 代码名称逻辑不变。
    # 成功：ValueResult(name, make_record(来源, "complete", collected_at=now))
    # 失败：ValueResult("", make_record("名称解析", "failed", error_kind="fetch_failed", ...))
    ...

def resolve_name(symbol: str) -> str:
    return resolve_name_result(symbol).value or ""
```

- [ ] **Step 5: 更新估值充实器**

`ValuationEnricher._get_total_shares` 的最后兜底改为：

```python
return get_total_shares_result(ctx.symbol, ctx.financial_data).value
```

不要改变估值计算或报告文案。

- [ ] **Step 6: 运行回归和静态检查**

```powershell
$run = "tmp/pytest/phase2-task16-reg-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/data/test_akshare.py tests/data/enrichers tests/core/test_pipeline.py tests/report -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
.\.venv\Scripts\ruff.exe check src/data/akshare.py src/utils/symbols.py src/data/enrichers/valuation_enricher.py tests/data/test_akshare.py
.\.venv\Scripts\pyright.exe src/data/akshare.py src/utils/symbols.py src/data/enrichers/valuation_enricher.py tests/data/test_akshare.py
```

- [ ] **Step 7: 提交**

```powershell
git add src/data/akshare.py src/utils/symbols.py src/data/enrichers/valuation_enricher.py tests/data/test_akshare.py tests/utils
git diff --cached --check
git commit -m "refactor(辅助补取): 总股本与名称解析接入统一结果"
```

---

### Task 17: 2B 普通指数采集记录

**Files:**
- Modify: `src/data/schemas.py`
- Modify: `src/data/collection_policies.py`
- Modify: `src/index/collector.py`
- Test: `tests/index/test_collector.py`
- Test: `tests/data/test_index_schemas.py`

- [ ] **Step 1: 写失败测试**

- `IndexAnalysisContext` 默认有 `collection_records == {}`。
- 普通指数采集后，`ctx.collection_records` 包含本次执行的 `index_price`、`index_valuation`、`index_capital_flow`、`index_macro`、`index_sentiment` 中实际采集的键。
- 成功数据 `result_state=="complete"`；空数据 `failed`；指数舆情空标题为 `empty`。
- `IndexDataCollector` 通过 `AkShareAdapter.fetch_result`，不再调用 `fetch`（可用 spy/假适配器断言）。

- [ ] **Step 2: 运行确认失败**

```powershell
$run = "tmp/pytest/phase2-task17-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/index/test_collector.py tests/data/test_index_schemas.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

- [ ] **Step 3: 增加 `IndexAnalysisContext.collection_records`**

在 `src/data/schemas.py` 的 `IndexAnalysisContext` 增加：

```python
collection_records: dict[str, DataCollectionRecord] = Field(default_factory=dict)
```

旧 JSON/缓存默认空；不改变报告字段。

- [ ] **Step 4: 增加 `IndexPolicy`**

在 `src/data/collection_policies.py`：

```python
class IndexPolicy(_Policy):
    def __init__(self, kind: str):
        self.kind = kind
        self.ttl_key = "daily"
        self.reference_age = 0
        self.source_attempts = 1

    def validate(self, symbol, rows, now):
        return bool(rows)

    def classify(self, symbol, rows, diagnostic, previous_rows, now):
        if not rows:
            return "failed"
        if self.kind == "index_sentiment":
            news = rows[0]
            return "complete" if getattr(news, "headlines", []) else "empty"
        return "complete"

    def reference_rows(self, rows, now):
        return None


INDEX_POLICIES = {
    kind: IndexPolicy(kind)
    for kind in ("index_price", "index_valuation", "index_capital_flow",
                 "index_macro", "index_sentiment")
}


def index_policy_for(kind: str) -> CollectionPolicy | None:
    return INDEX_POLICIES.get(kind)
```

如果模型存在但关键字段全空，可在对应分支返回 `partial`；至少保持“有数据 complete、空 failed”。

- [ ] **Step 5: 修改 `IndexDataCollector`**

- 每个 `_fetch_*` 用 `self._adapter.fetch_result(...)`。
- 统一记录：

```python
from data.base import CollectionResult
from data.collection_state import make_record
from data.collection_policies import index_policy_for

def _index_record(ctx, data_type, collection, source="AkShare"):
    policy = index_policy_for(data_type)
    assert policy is not None
    state = policy.classify(ctx.target.symbol, collection.rows or [], collection.record,
                            None, datetime.now(HK_TZ))
    ctx.collection_records[data_type] = make_record(
        source, state, diagnostic=collection.record,
        collected_at=datetime.now().astimezone(), cache_status="live",
    )
```

- 每个闭包在成功/空结果时写记录；异常时写 `failed` 记录，保持现有 `try/except` 隔离。
- `_collect_strategy` 的记录在 Task 18 处理。
- 不改指数分析、报告字段或策略缓存。

- [ ] **Step 6: 运行测试和静态检查**

```powershell
$run = "tmp/pytest/phase2-task17-reg-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/index tests/data/test_index_schemas.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
.\.venv\Scripts\ruff.exe check src/data/schemas.py src/data/collection_policies.py src/index/collector.py tests/index/test_collector.py
.\.venv\Scripts\pyright.exe src/data/schemas.py src/data/collection_policies.py src/index/collector.py tests/index/test_collector.py
```

- [ ] **Step 7: 提交**

```powershell
git add src/data/schemas.py src/data/collection_policies.py src/index/collector.py tests/index/test_collector.py tests/data/test_index_schemas.py
git diff --cached --check
git commit -m "refactor(指数): 普通指数采集接入结果记录"
```

---

### Task 18: 2B 策略指数记录与静态门禁

**Files:**
- Modify: `src/index/collector.py`
- Modify: `src/core/collection_contract.py`
- Modify: `tests/index/test_collector.py`
- Modify: `tests/index/test_strategy_valuation_reports.py`（按实际测试名）
- Modify: `tests/test_data_entrypoints.py`

- [ ] **Step 1: 写失败测试**

- 策略指数运行后，`ctx.collection_records` 至少有 `index_price`、`index_valuation`、`strategy_data`、`benchmark`、`etf` 中实际执行的键。
- 有数据无错误 → `complete`；有数据但有 `strategy_data.errors` → `partial`；无数据/关键异常 → `failed`。
- 策略提供器缓存/官方快照行为不变。
- 静态检查清单不再包含 index 五个 `.fetch` 条目；允许五个 `fetch_result` 调用点。

- [ ] **Step 2: 运行确认失败**

```powershell
$run = "tmp/pytest/phase2-task18-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/index/test_collector.py tests/test_data_entrypoints.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

- [ ] **Step 3: 为 `_collect_strategy` 增加记录**

在 `IndexDataCollector._collect_strategy`：

- 价格成功后写 `index_price`（complete/failed）。
- `strategy_data = provider.collect(ctx.target)` 后写 `strategy_data`：`errors` 非空 → `partial`，否则 `complete`；异常/空 → `failed`。
- `valuation_data = provider.fetch(...)` 成功后写 `index_valuation`（complete/failed）；异常保留现有 risk flag 并写 failed。
- benchmark/ETF 同理写 `benchmark`/`etf` 记录。
- 不改变 provider 调用顺序、缓存、错误列表或 risk flags。

- [ ] **Step 4: 更新静态清单**

`src/core/collection_contract.py`：

- 删除五条 `IndexDataCollector.collect._fetch_*` `.fetch` 条目。
- 在 `ALLOWED_FETCH_RESULT_CALLS` 增加这五个方法。
- 保留 base 兼容桥和回测条目。

- [ ] **Step 5: 运行策略/门禁测试**

```powershell
$run = "tmp/pytest/phase2-task18-reg-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/index tests/test_data_entrypoints.py tests/core/test_collection_contract.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
.\.venv\Scripts\ruff.exe check src/index/collector.py src/core/collection_contract.py tests/index tests/test_data_entrypoints.py
.\.venv\Scripts\pyright.exe src/index/collector.py src/core/collection_contract.py tests/index tests/test_data_entrypoints.py
PYTHONPATH="$PWD/src;$PWD" .\.venv\Scripts\python.exe scripts\check-data-entrypoints.py
```

- [ ] **Step 6: 提交**

```powershell
git add src/index/collector.py src/core/collection_contract.py tests/index tests/test_data_entrypoints.py
git diff --cached --check
git commit -m "refactor(指数): 策略采集记录并收口静态门禁"
```

---

### Task 19: 2C 港股通与 HSICS 刷新记录

**Files:**
- Modify: `src/data/hk_stock_connect.py`
- Modify: `src/data/hk_hsics_industry.py`
- Modify: `src/stock_robot/cli.py`（`hk-industry-refresh` 编排）
- Test: `tests/data/test_hk_stock_connect.py`
- Test: `tests/data/test_hk_hsics_industry.py`

- [ ] **Step 1: 写失败测试**

- `refresh_local_mapping_result` 成功返回 snapshot + `RefreshRecord(result_state="complete", published=True)`。
- 请求失败返回 `None` + `failed`/`published=False`，旧快照文件不被覆盖。
- `refresh_result` 成功/异常同理；部分步骤失败时记录 `partial`，旧文件保留。
- 原 `refresh_local_mapping`/`refresh` 仍保持失败抛异常、成功返回 snapshot 的旧行为。

- [ ] **Step 2: 运行确认失败**

```powershell
$run = "tmp/pytest/phase2-task19-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/data/test_hk_stock_connect.py tests/data/test_hk_hsics_industry.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

- [ ] **Step 3: 港股通刷新结果包装**

```python
def refresh_local_mapping_result(snapshot_path: Path,
                                 source: SnapshotSource = "sse"
                                 ) -> tuple[HKStockConnectSnapshot | None, RefreshRecord]:
    try:
        snapshot = _fetch_snapshot_for(source)
        publish_snapshot(snapshot, snapshot_path)
        return snapshot, make_refresh_record(
            snapshot.source, "complete", published=True)
    except Exception as error:  # noqa: BLE001 — 第三方/文件发布边界
        logger.warning("港股通刷新失败: %s", error)
        return None, make_refresh_record(
            source, "failed", error_kind="fetch_failed",
            error_message="港股通刷新失败，保留旧快照")


def refresh_local_mapping(snapshot_path: Path, source: SnapshotSource = "sse"):
    snapshot, record = refresh_local_mapping_result(snapshot_path, source)
    if snapshot is None:
        raise HKStockConnectError(record.error_message)
    return snapshot
```

`main` 改为使用 result 包装，输出/记录 summary；失败返回非零。

- [ ] **Step 4: HSICS 刷新结果包装**

```python
def refresh_result(csv_path: Path, json_path: Path, *,
                   connect_symbols: set[str],
                   session: requests.Session | None = None,
                   strict_counts: bool = True
                   ) -> tuple[HsicsSnapshot | None, RefreshRecord]:
    try:
        snapshot = refresh(csv_path, json_path, connect_symbols=connect_symbols,
                           session=session, strict_counts=strict_counts)
        return snapshot, make_refresh_record(
            "东财F10+HSCI", "complete", published=True)
    except Exception as error:  # noqa: BLE001 — 第三方/发布边界
        logger.warning("HSICS 刷新失败: %s", error)
        return None, make_refresh_record(
            "东财F10+HSCI", "failed", error_kind="fetch_failed",
            error_message="HSICS 刷新失败，保留旧快照")
```

原 `refresh` 不变；CLI 编排先成员、后 HSICS。成员成功、HSICS 失败时，组合记录为 `partial`、`published=False`，保留新成员快照和旧 HSICS 文件，按现有 CLI 行为报错。

- [ ] **Step 5: 运行测试**

```powershell
$run = "tmp/pytest/phase2-task19-reg-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/data/test_hk_stock_connect.py tests/data/test_hk_hsics_industry.py tests/test_cli.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
.\.venv\Scripts\ruff.exe check src/data/hk_stock_connect.py src/data/hk_hsics_industry.py src/stock_robot/cli.py tests/data/test_hk_stock_connect.py tests/data/test_hk_hsics_industry.py
.\.venv\Scripts\pyright.exe src/data/hk_stock_connect.py src/data/hk_hsics_industry.py src/stock_robot/cli.py tests/data/test_hk_stock_connect.py tests/data/test_hk_hsics_industry.py
```

- [ ] **Step 6: 提交**

```powershell
git add src/data/hk_stock_connect.py src/data/hk_hsics_industry.py src/stock_robot/cli.py tests/data/test_hk_stock_connect.py tests/data/test_hk_hsics_industry.py
git diff --cached --check
git commit -m "refactor(静态刷新): 港股通与 HSICS 记录刷新结果"
```

---

### Task 20: 2C A 股行业映射刷新记录

**Files:**
- Modify: `src/data/industry_mapping_builder.py`
- Modify: `scripts/rebuild_industry_mapping_runner.py`
- Test: `tests/data/test_industry_mapping_builder.py`

- [ ] **Step 1: 写失败测试**

- `rebuild_all` 成功返回结果 dict 包含 `refresh_record`，状态 `partial`、`published=False`（只生成候选）。
- `publish_candidate` 成功后包含 `refresh_record`，状态 `complete`、`published=True`。
- 校验失败/发布失败时记录 failed，不覆盖正式 CSV；原异常/返回键保持兼容。

- [ ] **Step 2: 运行确认失败**

```powershell
$run = "tmp/pytest/phase2-task20-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/data/test_industry_mapping_builder.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

- [ ] **Step 3: 在结果 dict 增加 `refresh_record`**

- `rebuild_all` 的返回 dict 增加：
  ```python
  result["refresh_record"] = make_refresh_record(
      "乐咕申万", "partial", published=False,
      error_message="仅生成候选映射，尚未发布",
  )
  ```
- `publish_candidate` 成功后增加：
  ```python
  result["refresh_record"] = make_refresh_record(
      "乐咕申万", "complete", published=True)
  ```
- 失败路径保持现有异常；runner/CLI 捕获后打印 failed 记录，不覆盖正式 CSV。
- 现有键、校验计数、候选/正式路径和 checkpoint 语义不变。

- [ ] **Step 4: 更新 runner/CLI 输出**

`scripts/rebuild_industry_mapping_runner.py` 打印 `refresh_record` 的字段（source、result_state、published、error_message）；不改变退出码语义。

- [ ] **Step 5: 运行测试**

```powershell
$run = "tmp/pytest/phase2-task20-reg-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/data/test_industry_mapping_builder.py tests/data/test_industry_classifier.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
.\.venv\Scripts\ruff.exe check src/data/industry_mapping_builder.py scripts/rebuild_industry_mapping_runner.py tests/data/test_industry_mapping_builder.py
.\.venv\Scripts\pyright.exe src/data/industry_mapping_builder.py scripts/rebuild_industry_mapping_runner.py tests/data/test_industry_mapping_builder.py
```

- [ ] **Step 6: 提交**

```powershell
git add src/data/industry_mapping_builder.py scripts/rebuild_industry_mapping_runner.py tests/data/test_industry_mapping_builder.py
git diff --cached --check
git commit -m "refactor(静态刷新): A 股行业映射记录刷新结果"
```

---

### Task 21: Phase 2 门禁、回归与 CLI 验收

**Files:**
- Verify: 全部受影响测试与脚本
- Record: `tmp/acceptance/unified-data-collection-phase2/`（本地，不提交）

- [ ] **Step 1: 运行范围门禁**

```powershell
.\scripts\verify.ps1 -Files src/data/peer_comparison_fetch.py
.\scripts\verify.ps1 -Files src/index/collector.py
.\scripts\verify.ps1 -Files src/data/hk_stock_connect.py
.\scripts\verify.ps1 -Files src/data/industry_mapping_builder.py
```

逐项记录结果；既有前端静态失败单独记录，不归因 Phase 2。

- [ ] **Step 2: 运行完整受影响测试集**

```powershell
$run = "tmp/pytest/phase2-accept-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/core tests/data tests/index tests/report/test_reference_quality.py tests/agent/test_reference_isolation.py tests/backtest -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

- [ ] **Step 3: CLI 验收**

```powershell
mkdir tmp\acceptance\unified-data-collection-phase2 -Force
.\.venv\Scripts\stock-robot.exe analyze 600519 --no-llm --verbose 2>&1 | Tee-Object -FilePath tmp\acceptance\unified-data-collection-phase2\a-share-cli.log
.\.venv\Scripts\stock-robot.exe analyze 00700 --no-llm --verbose 2>&1 | Tee-Object -FilePath tmp\acceptance\unified-data-collection-phase2\hk-cli.log
.\.venv\Scripts\stock-robot.exe index 000300 --no-llm 2>&1 | Tee-Object -FilePath tmp\acceptance\unified-data-collection-phase2\index-cli.log
```

检查 A/HK Markdown、指数报告；静态刷新只用临时路径或离线 fixture，不覆盖仓库正式数据。跳过浏览器。

- [ ] **Step 4: 记录与提交文档**

在聊天中记录实际命令、报告路径、测试结果、既有失败和未覆盖风险。提交 Phase 2 设计/计划更新；不提交 `tmp/`、`reports/` 或正式静态数据。

```powershell
git add docs/superpowers/specs/2026-10/2026-10-26-unified-data-collection-design.md docs/superpowers/plans/2026-10/2026-10-26-unified-data-collection-plan.md
git diff --cached --check
git commit -m "docs(数据采集): 增加 Phase 2 设计与实施计划"
```

- [ ] **Step 5: 合并/发布前 Full**

只有用户明确要求合并或发布时才运行：

```powershell
.\scripts\verify.ps1 -Scope Full
```

`dev` 既有前端静态测试失败必须在 Full 结果中单独说明；不能将 Phase 2 表述为 Full 全绿。

---

## Phase 2 自审清单

- [ ] 2A 同行财务返回 `CollectionResult`，保留 `peer_financial` TTL、预算、分页和旧快照回退。
- [ ] 2A 总股本/名称使用 `ValueResult`，旧包装仍返回原类型；无新增缓存。
- [ ] 2B `IndexAnalysisContext` 增加内部记录，普通/策略指数都有 complete/partial/failed/empty 状态。
- [ ] 2B 策略提供器缓存、官方快照、错误列表和 risk flags 不变。
- [ ] 2C 三个刷新入口保留旧函数，新增结果包装；原子发布、文件锁和旧快照保留不变。
- [ ] 2C 验收只用临时路径/离线 fixture，不覆盖正式静态数据。
- [ ] 指数五个 `.fetch` 清单条目移除，允许对应 `fetch_result`；真实仓库检查通过。
- [ ] 不新增 `DataReference`、数据源、依赖、评分公式或报告文案。
- [ ] CLI A 股/港股/指数验收完成；静态刷新离线验证；跳过浏览器。
- [ ] Phase 2 设计/计划仍为同一主题的一份设计＋一份计划。

---

# Phase 3 实施计划：雷达数据提供器、回测行情

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让雷达 ETF 提供器、海外指数与回测行情/基准统一返回“数据＋采集记录”，同时保留雷达调度、缓存、回退和回测连续性语义不变。

**Architecture:** 复用 `ValueResult` 作为帧/序列/列表的结果契约，在各来源边界新增 `*_result()` 入口并保留旧方法为兼容包装；雷达同步层和回测 runner 消费 record，并把诊断写入既有快照状态或回测 manifest。不新增雷达表列，不改变缓存键、TTL、队列、租约和评分。

**Tech Stack:** Python 3.13、pandas、Pydantic、SQLite、pytest、AkShare、PowerShell 验证脚本。

**执行前置：**

- 使用仓库 `.venv`，不要使用 Anaconda 的 `python`：
  `.\\.venv\\Scripts\\python.exe -m pytest ...`
- 每次 pytest 使用唯一临时目录，避免 Windows 锁文件：
  ```powershell
  $run = "tmp/pytest/phase3-$PID-$([guid]::NewGuid().ToString('N'))"
  New-Item -ItemType Directory -Path $run -Force | Out-Null
  .\.venv\Scripts\python.exe -m pytest <tests> -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
  ```
- 每个任务独立提交；不推送、不合并；不提交 `tmp/`、`reports/`。
- 若任务改动 Python 源文件，提交前运行：
  ```powershell
  .\scripts\verify.ps1 -Files <changed file> -Staged
  ```
  只运行 Changed/Staged 门禁，不自动触发 Full。

## 文件结构

| 文件 | 责任 | 本阶段动作 |
| --- | --- | --- |
| `src/data/collection_state.py` | 共享结果类型 | `ValueResult` 增加 `errors` |
| `src/data/akshare.py` | 数据源适配器与中证基准底层 | 新增 `fetch_index_history_result` |
| `src/radar/data.py` | 雷达 ETF 提供器协议与多源链 | 新增 `fetch_daily_result`、统一失败记录 |
| `src/radar/history.py` | 雷达日线缓存/同步 | 消费结果入口，`prepare_result` |
| `src/radar/refresh.py` | 雷达快照刷新 | 使用 record.source 写 `data_source` |
| `src/radar/overseas.py` | 海外指数取数与缓存 | 新增 `_fetch_result`、统一安全失败分类 |
| `src/radar/benchmarks.py` | 雷达基准网络回退 | 改用共享中证基准结果入口 |
| `src/backtest/data.py` | 回测行情与基准 | 新增 `fetch_stock_result`/`fetch_benchmark_result` |
| `src/backtest/runner.py` | 回测编排 | 收集 price/benchmark records |
| `src/backtest/models.py` | 回测结果模型 | 增加 `collection_records` 默认字段 |
| `src/backtest/artifacts.py` | 回测产物 | manifest 增加 `collection_records` |
| `src/core/collection_contract.py` | 取数入口门禁 | 移除最后一个遗留 fetch 条目 |
| 对应测试 | 契约/回归 | 每个任务同步更新 |

---

## Task 1: 扩展共享结果类型与中证基准入口

**Files:**
- Modify: `src/data/collection_state.py:11-15`
- Modify: `src/data/akshare.py:178-182`
- Test: `tests/data/test_collection_state.py`
- Test: `tests/data/test_akshare_index.py`

- [ ] **Step 1: 写失败测试 — `ValueResult` 保留多源错误**

在 `tests/data/test_collection_state.py` 末尾追加：

```python
def test_value_result_keeps_multi_source_errors():
    value = ValueResult(value="ok", record=make_record("测试源", "partial"))
    assert value.errors == []

    failed = ValueResult(
        value=None,
        record=make_record("测试源", "failed", error_message="源A失败"),
        errors=["源A失败"],
    )
    assert failed.errors == ["源A失败"]
```

- [ ] **Step 2: 运行测试确认失败**

```powershell
$run = "tmp/pytest/phase3-task1-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/data/test_collection_state.py::test_value_result_keeps_multi_source_errors -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: FAIL，`ValueResult` 没有 `errors` 属性。

- [ ] **Step 3: 实现最小改动**

把 `src/data/collection_state.py` 顶部导入改为：

```python
from dataclasses import dataclass, field
```

把 `ValueResult` 改为：

```python
@dataclass(frozen=True)
class ValueResult(Generic[T]):
    value: T | None
    record: DataCollectionRecord
    errors: list[str] = field(default_factory=list)
```

- [ ] **Step 4: 运行测试确认通过**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/data/test_collection_state.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: PASS。

- [ ] **Step 5: 写失败测试 — 中证基准结果入口**

在 `tests/data/test_akshare_index.py` 末尾追加：

```python
def test_index_history_result_reports_source_and_state(monkeypatch):
    frame = pd.DataFrame({"date": ["2024-01-02"], "close": [3500.0]})
    monkeypatch.setattr("data.akshare._ak_csindex", lambda **_: frame)
    from data.akshare import fetch_index_history_result

    result = fetch_index_history_result("000300", "20240101", "20240131")

    assert result.value is frame
    assert result.record.source == "中证指数官网"
    assert result.record.result_state == "complete"
    assert result.record.collected_at is not None


def test_index_history_result_empty_and_safe_failure(monkeypatch):
    from data.akshare import fetch_index_history_result

    monkeypatch.setattr("data.akshare._ak_csindex", lambda **_: None)
    empty = fetch_index_history_result("000300", "20240101", "20240131")
    assert empty.value is None
    assert empty.record.result_state == "empty"

    def fail(**kwargs):
        raise TimeoutError("敏感的上游细节")

    monkeypatch.setattr("data.akshare._ak_csindex", fail)
    failed = fetch_index_history_result("000300", "20240101", "20240131")
    assert failed.value is None
    assert failed.record.result_state == "failed"
    assert failed.record.error_kind == "timeout"
    assert "敏感的上游细节" not in failed.record.error_message
```

- [ ] **Step 6: 运行测试确认失败**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/data/test_akshare_index.py::test_index_history_result_reports_source_and_state tests/data/test_akshare_index.py::test_index_history_result_empty_and_safe_failure -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: FAIL，`fetch_index_history_result` 不存在。

- [ ] **Step 7: 实现共享中证基准入口**

在 `src/data/akshare.py` 的 `_ak_csindex` 之后加入：

```python
def _index_error_kind(error: Exception) -> str:
    if isinstance(error, TimeoutError) or "Timeout" in type(error).__name__ or "超时" in str(error):
        return "timeout"
    return "fetch_failed"


def fetch_index_history_result(symbol: str, start_date: str, end_date: str) -> ValueResult[Any]:
    """中证指数日线的统一结果入口；失败只暴露安全诊断。"""
    try:
        frame = _ak_csindex(symbol=symbol, start_date=start_date, end_date=end_date)
    except Exception as error:  # noqa: BLE001 — 第三方网络与响应边界
        kind = _index_error_kind(error)
        message = "中证指数行情请求超时" if kind == "timeout" else "中证指数行情请求失败"
        return ValueResult(
            None,
            make_record("中证指数官网", "failed", error_kind=kind, error_message=message),
        )
    if frame is None or len(frame) == 0:
        return ValueResult(
            None,
            make_record("中证指数官网", "empty", error_message="中证指数在请求区间没有数据"),
        )
    return ValueResult(
        frame,
        make_record("中证指数官网", "complete", collected_at=datetime.now().astimezone()),
    )
```

`ValueResult` 与 `make_record` 已在 `src/data/akshare.py` 导入，无需新增导入。

- [ ] **Step 8: 运行测试确认通过**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/data/test_collection_state.py tests/data/test_akshare_index.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: PASS。

- [ ] **Step 9: 提交**

```powershell
git add src/data/collection_state.py src/data/akshare.py tests/data/test_collection_state.py tests/data/test_akshare_index.py
git diff --cached --check
git commit -m "feat(数据契约): 增加多源错误与中证基准结果"
```

---

## Task 2: 统一 AkShare/新浪/腾讯 ETF 提供器结果

**Files:**
- Modify: `src/radar/data.py:1-20`（导入与公共辅助）
- Modify: `src/radar/data.py:73-129`（AkShare）
- Modify: `src/radar/data.py:181-236`（新浪）
- Modify: `src/radar/data.py:239-309`（腾讯）
- Test: `tests/radar/test_data.py`
- Test: `tests/radar/test_tencent_data.py`

- [ ] **Step 1: 写失败测试 — AkShare 结果入口**

在 `tests/radar/test_data.py` 末尾追加：

```python
def test_akshare_daily_result_reports_source_and_state():
    daily = pd.DataFrame(
        {
            "日期": ["2024-01-02"], "开盘": [3.7], "最高": [3.9], "最低": [3.6],
            "收盘": [3.8], "成交量": [10], "成交额": [100],
        }
    )
    provider = AkShareETFDataProvider(daily_fetcher=lambda **_: daily)

    result = provider.fetch_daily_result("510300", date(2024, 1, 1), date(2024, 1, 31))

    assert result.record.source == "AkShareETFDataProvider"
    assert result.record.result_state == "complete"
    assert result.value is not None
    assert result.value.loc[0, "close"] == 3.8


def test_akshare_daily_result_keeps_safe_failure_and_compatibility():
    provider = AkShareETFDataProvider(
        daily_fetcher=lambda **_: (_ for _ in ()).throw(ConnectionError("敏感细节"))
    )

    result = provider.fetch_daily_result("510300", date(2024, 1, 1), date(2024, 1, 31))

    assert result.value is None
    assert result.record.result_state == "failed"
    assert result.record.error_kind == "fetch_failed"
    assert "敏感细节" not in result.record.error_message
    with pytest.raises(RadarDataError, match="熔断"):
        provider.fetch_daily("510500", date(2024, 1, 1), date(2024, 1, 31))
```

- [ ] **Step 2: 运行测试确认失败**

```powershell
$run = "tmp/pytest/phase3-task2-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/radar/test_data.py::test_akshare_daily_result_reports_source_and_state tests/radar/test_data.py::test_akshare_daily_result_keeps_safe_failure_and_compatibility -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: FAIL，`fetch_daily_result` 不存在。

- [ ] **Step 3: 加入公共辅助**

在 `src/radar/data.py` 顶部加入：

```python
from data.collection_state import ValueResult, make_record
```

并把 `from datetime import date` 改为：

```python
from datetime import date, datetime
```

在 `RadarDataProvider` 协议之前加入：

```python
def provider_label(provider: Any) -> str:
    """与快照 data_source 保持一致的来源名称。"""
    return str(getattr(provider, "source_label", type(provider).__name__))


def error_kind(error: BaseException) -> str:
    """把第三方异常压成安全类别，不暴露上游细节。"""
    text = str(error)
    if isinstance(error, TimeoutError) or "超时" in text or "Timeout" in type(error).__name__:
        return "timeout"
    if any(word in text for word in ("缺少", "格式", "无效", "不是")):
        return "invalid_data"
    return "fetch_failed"


def frame_result(
    source: str,
    state: str,
    *,
    value: pd.DataFrame | None = None,
    error_kind: str = "",
    error_message: str = "",
) -> ValueResult[pd.DataFrame]:
    record = make_record(
        source,
        state,
        error_kind=error_kind,
        error_message=error_message,
        collected_at=datetime.now().astimezone(),
    )
    errors = [error_message] if state == "failed" and error_message else []
    return ValueResult(value=value, record=record, errors=errors)
```

在 `RadarDataProvider` 协议中加入：

```python
def fetch_daily_result(self, symbol: str, start: date, end: date) -> ValueResult[pd.DataFrame]:
    """获取日线并同时返回采集记录。"""
    raise NotImplementedError
```

- [ ] **Step 4: 实现 AkShare 结果入口**

把 `AkShareETFDataProvider.fetch_daily` 替换为：

```python
def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
    """兼容包装：失败继续抛 RadarDataError。"""
    result = self.fetch_daily_result(symbol, start, end)
    if result.value is None:
        raise RadarDataError(result.record.error_message)
    return result.value

def fetch_daily_result(self, symbol: str, start: date, end: date) -> ValueResult[pd.DataFrame]:
    """获取 ETF 前复权日线并返回统一结果。"""
    if start > end:
        raise ValueError("start 不能晚于 end")
    source = provider_label(self)
    try:
        frame = self._fetch_and_normalize(
            lambda: self._daily_fetcher(
                symbol=symbol,
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust="qfq",
            ),
            self._DAILY_COLUMNS,
            ["date", "open", "high", "low", "close", "amount"],
        )
    except RadarDataError as exc:
        return frame_result(source, "failed", error_kind=error_kind(exc), error_message=str(exc))
    if frame.empty:
        return frame_result(source, "empty", value=frame, error_message="AkShare ETF 日线在请求区间没有数据")
    return frame_result(source, "complete", value=frame)
```

- [ ] **Step 5: 运行 AkShare 测试确认通过**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/radar/test_data.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: PASS，含原有熔断/校验测试。

- [ ] **Step 6: 写失败测试 — 新浪空区间与兼容包装**

在 `tests/radar/test_data.py` 追加：

```python
def test_sina_daily_result_reports_empty_and_wrapper_raises():
    raw = pd.DataFrame({
        "date": ["2023-01-02"], "open": [3.7], "high": [3.9], "low": [3.6],
        "close": [3.8], "volume": [100],
    })
    provider = SinaETFDataProvider(fetcher=lambda symbol: raw)

    result = provider.fetch_daily_result("159915", date(2024, 2, 1), date(2024, 2, 29))

    assert result.value is None
    assert result.record.source == "SinaETFDataProvider"
    assert result.record.result_state == "empty"
    with pytest.raises(RadarDataError, match="没有数据"):
        provider.fetch_daily("159915", date(2024, 2, 1), date(2024, 2, 29))
```

```powershell
.\.venv\Scripts\python.exe -m pytest tests/radar/test_data.py::test_sina_daily_result_reports_empty_and_wrapper_raises -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: FAIL。

- [ ] **Step 7: 实现新浪结果入口**

把 `SinaETFDataProvider.fetch_daily` 替换为：

```python
def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
    """兼容包装：失败继续抛 RadarDataError。"""
    result = self.fetch_daily_result(symbol, start, end)
    if result.value is None:
        raise RadarDataError(result.record.error_message)
    return result.value

def fetch_daily_result(self, symbol: str, start: date, end: date) -> ValueResult[pd.DataFrame]:
    """取得全量历史后在本地截取区间，成交额以收盘价乘成交量估算。"""
    if start > end:
        raise ValueError("start 不能晚于 end")
    source = provider_label(self)
    if self._circuit_open:
        return frame_result(source, "failed", error_message="新浪 ETF 数据源本轮已熔断")
    exchange_symbol = _sina_symbol(symbol)
    try:
        frame = self._fetcher(exchange_symbol)
    except (requests.RequestException, URLError, ProviderRequestError) as exc:
        self._circuit_open = True
        return frame_result(
            source, "failed", error_kind=error_kind(exc),
            error_message="新浪 ETF 网络请求失败，已熔断本轮请求",
        )
    except Exception as exc:  # noqa: BLE001 — 第三方 SDK/network 边界需转换为统一异常
        return frame_result(
            source, "failed", error_kind=error_kind(exc),
            error_message="新浪 ETF 日线请求失败",
        )
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        return frame_result(
            source, "failed", error_kind="invalid_data",
            error_message=f"新浪 ETF 日线缺少字段: {', '.join(sorted(missing))}",
        )
    normalized = frame.copy()
    try:
        normalized["date"] = pd.to_datetime(normalized["date"], errors="raise").dt.date
    except (ValueError, TypeError):
        return frame_result(source, "failed", error_kind="invalid_data", error_message="ETF 日线日期格式无效")
    for column in ("open", "high", "low", "close", "volume"):
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    normalized["amount"] = normalized["close"] * normalized["volume"]
    result = normalized[(normalized["date"] >= start) & (normalized["date"] <= end)]
    if result.empty:
        return frame_result(source, "empty", error_message="新浪 ETF 日线在请求区间没有数据")
    return frame_result(
        source, "complete",
        value=cast(pd.DataFrame, result[["date", "open", "high", "low", "close", "volume", "amount"]]),
    )
```

- [ ] **Step 8: 运行新浪相关测试确认通过**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/radar/test_data.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: PASS。

- [ ] **Step 9: 写失败测试 — 腾讯结果入口**

在 `tests/radar/test_tencent_data.py` 追加：

```python
def test_tencent_daily_result_reports_failure_and_circuit():
    provider = TencentETFDataProvider(
        fetcher=lambda symbol, start, end: (_ for _ in ()).throw(requests.ConnectionError("offline"))
    )

    result = provider.fetch_daily_result("510300", date(2025, 1, 1), date(2025, 1, 3))

    assert result.value is None
    assert result.record.source == "TencentETFDataProvider"
    assert result.record.result_state == "failed"
    with pytest.raises(RadarDataError, match="已熔断"):
        provider.fetch_daily("510500", date(2025, 1, 1), date(2025, 1, 3))
```

需要在 `tests/radar/test_tencent_data.py` 顶部导入 `requests`：

```python
import requests
```

```powershell
.\.venv\Scripts\python.exe -m pytest tests/radar/test_tencent_data.py::test_tencent_daily_result_reports_failure_and_circuit -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: FAIL。

- [ ] **Step 10: 实现腾讯结果入口**

把 `TencentETFDataProvider.fetch_daily` 替换为：

```python
def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
    """兼容包装：失败继续抛 RadarDataError。"""
    result = self.fetch_daily_result(symbol, start, end)
    if result.value is None:
        raise RadarDataError(result.record.error_message)
    return result.value

def fetch_daily_result(self, symbol: str, start: date, end: date) -> ValueResult[pd.DataFrame]:
    """取得前复权日线，并以收盘价乘成交量估算成交额。"""
    if start > end:
        raise ValueError("start 不能晚于 end")
    source = provider_label(self)
    if self._circuit_open:
        return frame_result(source, "failed", error_message="腾讯 ETF 数据源本轮已熔断")
    try:
        frame = self._fetcher(symbol, start, end)
    except (requests.RequestException, ProviderRequestError) as exc:
        self._circuit_open = True
        return frame_result(
            source, "failed", error_kind=error_kind(exc),
            error_message="腾讯 ETF 网络请求失败，已熔断本轮请求",
        )
    except (KeyError, TypeError, ValueError) as exc:
        return frame_result(
            source, "failed", error_kind=error_kind(exc),
            error_message="腾讯 ETF 日线请求失败",
        )
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        return frame_result(
            source, "failed", error_kind="invalid_data",
            error_message=f"腾讯 ETF 日线缺少字段: {', '.join(sorted(missing))}",
        )
    normalized = frame.copy()
    try:
        normalized["date"] = pd.to_datetime(normalized["date"], errors="raise").dt.date
    except (ValueError, TypeError):
        return frame_result(source, "failed", error_kind="invalid_data", error_message="ETF 日线日期格式无效")
    for column in ("open", "high", "low", "close", "volume"):
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    normalized["amount"] = normalized["close"] * normalized["volume"]
    result = normalized[(normalized["date"] >= start) & (normalized["date"] <= end)]
    if result.empty:
        return frame_result(source, "empty", error_message="腾讯 ETF 日线在请求区间没有数据")
    return frame_result(
        source, "complete",
        value=cast(pd.DataFrame, result[["date", "open", "high", "low", "close", "volume", "amount"]]),
    )
```

- [ ] **Step 11: 运行全部 ETF 提供器测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/radar/test_data.py tests/radar/test_tencent_data.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: PASS。

- [ ] **Step 12: 提交**

```powershell
git add src/radar/data.py tests/radar/test_data.py tests/radar/test_tencent_data.py
git diff --cached --check
git commit -m "feat(雷达数据): 统一 ETF 提供器取数结果"
```

---

## Task 3: 统一交易所、本地与 Fallback 结果链

**Files:**
- Modify: `src/radar/data.py:312-573`
- Test: `tests/radar/test_exchange_data.py`
- Test: `tests/radar/test_local_data.py`
- Test: `tests/radar/test_data.py`

- [ ] **Step 1: 写失败测试 — 交易所结果入口**

在 `tests/radar/test_exchange_data.py` 末尾追加：

```python
def test_official_exchange_result_reports_source_and_failure():
    with TemporaryDirectory(dir=Path.cwd()) as tmp_dir:
        config = Path(tmp_dir) / "sources.yaml"
        _config(config, "https://query.sse.com.cn/etf/{symbol}")
        provider = OfficialExchangeETFDataProvider(
            config_path=config,
            downloader=lambda url: "日期,开盘,最高,最低,收盘,成交额\n2024-01-02,1,2,0.5,1.5,100\n".encode(),
        )

        result = provider.fetch_daily_result("510300", date(2024, 1, 1), date(2024, 1, 3))

        assert result.record.source == "OfficialExchangeETFDataProvider"
        assert result.record.result_state == "complete"
        assert result.value is not None
        assert result.value.iloc[0].close == 1.5

        unavailable = OfficialExchangeETFDataProvider(
            config_path=config,
            downloader=lambda url: (_ for _ in ()).throw(URLError("offline")),
        )
        failed = unavailable.fetch_daily_result("510300", date(2024, 1, 1), date(2024, 1, 3))
        assert failed.value is None
        assert failed.record.result_state == "failed"
        assert "offline" not in failed.record.error_message
```

```powershell
.\.venv\Scripts\python.exe -m pytest tests/radar/test_exchange_data.py::test_official_exchange_result_reports_source_and_failure -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: FAIL。

- [ ] **Step 2: 实现交易所结果入口**

把 `OfficialExchangeETFDataProvider.fetch_daily` 替换为：

```python
def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
    """兼容包装：失败继续抛 RadarDataError。"""
    result = self.fetch_daily_result(symbol, start, end)
    if result.value is None:
        raise RadarDataError(result.record.error_message)
    return result.value

def fetch_daily_result(self, symbol: str, start: date, end: date) -> ValueResult[pd.DataFrame]:
    if start > end:
        raise ValueError("start 不能晚于 end")
    source = provider_label(self)
    if self._circuit_open:
        return frame_result(source, "failed", error_message="交易所 ETF 数据源本轮已熔断")
    try:
        frame = self._daily_fetcher(symbol, start, end) if self._daily_fetcher else self._fetch_official(symbol, start, end)
    except RadarDataError as exc:
        return frame_result(source, "failed", error_kind=error_kind(exc), error_message=str(exc))
    except Exception as exc:  # noqa: BLE001 — 注入的下载器/解析边界需转换为统一异常
        return frame_result(source, "failed", error_kind=error_kind(exc), error_message="交易所日终数据请求失败")
    required = {"date", "open", "high", "low", "close", "amount"}
    frame = frame.rename(columns=self._DAILY_COLUMNS).copy()
    missing = required - set(frame.columns)
    if missing:
        return frame_result(
            source, "failed", error_kind="invalid_data",
            error_message=f"交易所日终文件缺少字段: {', '.join(sorted(missing))}",
        )
    if "volume" not in frame:
        frame["volume"] = pd.NA
    try:
        frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.date
    except (TypeError, ValueError):
        return frame_result(source, "failed", error_kind="invalid_data", error_message="交易所 ETF 日期格式无效")
    for column in required - {"date"}:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    result = frame[(frame["date"] >= start) & (frame["date"] <= end)]
    if result.empty:
        return frame_result(source, "empty", error_message="交易所日终文件在请求区间没有数据")
    return frame_result(
        source, "complete",
        value=cast(pd.DataFrame, result[["date", "open", "high", "low", "close", "volume", "amount"]]),
    )
```

- [ ] **Step 3: 运行交易所测试确认通过**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/radar/test_exchange_data.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: PASS。

- [ ] **Step 4: 写失败测试 — 本地提供器结果**

在 `tests/radar/test_local_data.py` 追加：

```python
def test_local_provider_result_reports_missing_file(tmp_path):
    provider = LocalETFDataProvider(tmp_path / "local")

    result = provider.fetch_daily_result("510300", date(2024, 1, 1), date(2024, 1, 31))

    assert result.value is None
    assert result.record.source == "本地导入 CSV"
    assert result.record.result_state == "failed"
    with pytest.raises(RadarDataError, match="未导入"):
        provider.fetch_daily("510300", date(2024, 1, 1), date(2024, 1, 31))
```

```powershell
.\.venv\Scripts\python.exe -m pytest tests/radar/test_local_data.py::test_local_provider_result_reports_missing_file -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: FAIL。

- [ ] **Step 5: 实现本地结果入口**

把 `LocalETFDataProvider.fetch_daily` 替换为：

```python
def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
    """兼容包装：失败继续抛 RadarDataError。"""
    result = self.fetch_daily_result(symbol, start, end)
    if result.value is None:
        raise RadarDataError(result.record.error_message)
    return result.value

def fetch_daily_result(self, symbol: str, start: date, end: date) -> ValueResult[pd.DataFrame]:
    if start > end:
        raise ValueError("start 不能晚于 end")
    source = provider_label(self)
    try:
        path = self._path_for(symbol)
        if not path.exists():
            raise RadarDataError("本地未导入该标的日线")
        frame = self._read_csv(path)
        normalized = self._normalize(frame, symbol)
    except RadarDataError as exc:
        return frame_result(source, "failed", error_kind=error_kind(exc), error_message=str(exc))
    result = normalized[(normalized["date"] >= start) & (normalized["date"] <= end)]
    if result.empty:
        return frame_result(source, "empty", error_message="本地日线在请求区间没有数据")
    return frame_result(source, "complete", value=cast(pd.DataFrame, result))
```

- [ ] **Step 6: 运行本地测试确认通过**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/radar/test_local_data.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: PASS。

- [ ] **Step 7: 写失败测试 — Fallback 结果链**

在 `tests/radar/test_data.py` 追加：

```python
def test_fallback_result_is_partial_when_backup_succeeds():
    primary = AkShareETFDataProvider(
        daily_fetcher=lambda **_: (_ for _ in ()).throw(ConnectionError("offline"))
    )
    raw = pd.DataFrame({
        "date": ["2024-01-02"], "open": [3.7], "high": [3.9], "low": [3.6],
        "close": [3.8], "volume": [100],
    })
    provider = FallbackETFDataProvider((primary, SinaETFDataProvider(fetcher=lambda symbol: raw)))

    result = provider.fetch_daily_result("510300", date(2024, 1, 1), date(2024, 1, 31))

    assert result.record.result_state == "partial"
    assert result.record.source == "SinaETFDataProvider"
    assert result.errors
    assert result.value is not None


def test_fallback_result_reports_failed_and_empty():
    failed = FallbackETFDataProvider((
        AkShareETFDataProvider(daily_fetcher=lambda **_: (_ for _ in ()).throw(ConnectionError("offline"))),
    ))
    result = failed.fetch_daily_result("510300", date(2024, 1, 1), date(2024, 1, 31))
    assert result.value is None
    assert result.record.result_state == "failed"
    assert "AkShareETFDataProvider" in result.record.error_message

    raw = pd.DataFrame({
        "date": ["2023-01-02"], "open": [3.7], "high": [3.9], "low": [3.6],
        "close": [3.8], "volume": [100],
    })
    empty = FallbackETFDataProvider((SinaETFDataProvider(fetcher=lambda symbol: raw),))
    result = empty.fetch_daily_result("510300", date(2024, 2, 1), date(2024, 2, 29))
    assert result.value is None
    assert result.record.result_state == "empty"
```

```powershell
.\.venv\Scripts\python.exe -m pytest tests/radar/test_data.py::test_fallback_result_is_partial_when_backup_succeeds tests/radar/test_data.py::test_fallback_result_reports_failed_and_empty -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: FAIL。

- [ ] **Step 8: 实现 Fallback 结果链**

把 `FallbackETFDataProvider.fetch_daily` 与 `fetch_daily_with_source` 替换为：

```python
def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
    """兼容包装：失败继续抛 RadarDataError。"""
    frame, _ = self.fetch_daily_with_source(symbol, start, end)
    return frame

def fetch_daily_with_source(
    self, symbol: str, start: date, end: date, *, require_target: bool = False,
    trading_days: Sequence[date] | None = None,
) -> tuple[pd.DataFrame, str]:
    """兼容包装：失败继续抛 RadarDataError，成功返回实际来源。"""
    result = self.fetch_daily_result(
        symbol, start, end, require_target=require_target, trading_days=trading_days
    )
    if result.value is None:
        raise RadarDataError(result.record.error_message)
    return result.value, result.record.source

def fetch_daily_result(
    self, symbol: str, start: date, end: date, *, require_target: bool = False,
    trading_days: Sequence[date] | None = None,
) -> ValueResult[pd.DataFrame]:
    """逐源尝试并保留所有失败诊断。"""
    errors: list[str] = []
    all_empty = True
    for provider in self._providers:
        source = provider_label(provider)
        method = getattr(provider, "fetch_daily_result", None)
        if callable(method):
            result = method(symbol, start, end)
        else:
            try:
                frame = provider.fetch_daily(symbol, start, end)
                result = ValueResult(
                    frame,
                    make_record(source, "complete", collected_at=datetime.now().astimezone()),
                )
            except RadarDataError as exc:
                result = frame_result(source, "failed", error_kind=error_kind(exc), error_message=str(exc))
        if result.value is None or result.value.empty:
            all_empty = all_empty and result.record.result_state == "empty"
            errors.append(f"{type(provider).__name__}: {result.record.error_message}")
            continue
        frame = result.value
        try:
            if require_target:
                from radar.history import normalize_history

                frame = normalize_history(frame, start, end)
                if frame.iloc[-1]["date"] != end:
                    raise RadarDataError(f"行情尚未更新到目标交易日 {end}")
                if trading_days:
                    actual_start = max(start, frame.iloc[0]["date"])
                    missing = {day for day in trading_days if actual_start <= day <= end} - set(frame["date"])
                    if missing:
                        raise RadarDataError("行情缺失交易日: " + ", ".join(str(day) for day in sorted(missing)[:5]))
        except RadarDataError as exc:
            all_empty = False
            errors.append(f"{type(provider).__name__}: {exc}")
            continue
        frame.attrs["adjustment"] = getattr(provider, "adjustment", "unknown")
        errors.extend(result.errors)
        state = "partial" if errors or result.record.result_state == "partial" else "complete"
        return ValueResult(
            frame,
            make_record(source, state, error_message="；".join(errors), collected_at=datetime.now().astimezone()),
            errors,
        )
    state = "empty" if all_empty and errors else "failed"
    message = "；".join(errors) or "所有 ETF 数据源在请求区间没有数据"
    return ValueResult(None, make_record("FallbackETFDataProvider", state, error_message=message), errors)
```

- [ ] **Step 9: 运行 Fallback/本地/交易所测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/radar/test_data.py tests/radar/test_local_data.py tests/radar/test_exchange_data.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: PASS，含原有目标日/缺口切源测试。

- [ ] **Step 10: 提交**

```powershell
git add src/radar/data.py tests/radar/test_data.py tests/radar/test_local_data.py tests/radar/test_exchange_data.py
git diff --cached --check
git commit -m "feat(雷达数据): 统一交易所、本地与备用链结果"
```

---

## Task 4: 同步层与刷新层消费采集记录

**Files:**
- Modify: `src/radar/history.py:146-227`
- Modify: `src/radar/refresh.py:74-82`
- Test: `tests/radar/test_history.py`
- Test: `tests/radar/test_refresh.py`

- [ ] **Step 1: 写失败测试 — `prepare_result` 返回来源和记录**

在 `tests/radar/test_history.py` 末尾追加：

```python
def test_prepare_result_exposes_provider_and_cache_records(tmp_path):
    from data.collection_state import ValueResult, make_record

    class ResultProvider(Provider):
        def fetch_daily_result(self, symbol, start, end):
            return ValueResult(
                self.fetch_daily(symbol, start, end),
                make_record("自定义源", "complete"),
            )

    sync = HistorySynchronizer(HistoryStore(tmp_path / "radar.db"), ResultProvider(frame(28, 29, 30)))
    result, source, record = sync.prepare_result("510300", "cn", date(2026, 9, 28), date(2026, 9, 30))
    assert result.iloc[-1]["date"] == date(2026, 9, 30)
    assert source == "自定义源"
    assert record.result_state == "complete"
    assert record.cache_status == "live"

    cached_result, cached_source, cached_record = sync.prepare_result(
        "510300", "cn", date(2026, 9, 28), date(2026, 9, 30), reuse=True
    )
    assert cached_result.iloc[-1]["date"] == date(2026, 9, 30)
    assert cached_source == "自定义源"
    assert cached_record.cache_status == "cache"
```

```powershell
$run = "tmp/pytest/phase3-task4-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/radar/test_history.py::test_prepare_result_exposes_provider_and_cache_records -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: FAIL，`prepare_result` 不存在。

- [ ] **Step 2: 实现同步层结果入口**

在 `src/radar/history.py` 顶部加入：

```python
from data.collection_state import ValueResult, make_record
```

把 `HistorySynchronizer.prepare` 整体替换为 `prepare_result` + `prepare`：

```python
def prepare_result(
    self, symbol: str, market: str, start: date, end: date, *, reuse: bool = False,
    trading_days: Sequence[date] | None = None,
) -> tuple[pd.DataFrame, str, Any]:
    key = self.store.selected(symbol, market, self.provider_id, end)
    coverage = self.store.coverage(key) if key else None
    sync_start = min(start, date.fromisoformat(coverage["first_date"])) if coverage else start
    cached = self.store.read(key, sync_start, end) if key else pd.DataFrame(columns=_COLUMNS)
    known_gaps = {date.fromisoformat(day) for day in coverage["missing_dates"] if date.fromisoformat(day) <= end} if coverage else set()
    if reuse and key is not None:
        if coverage is None or coverage["requested_start"] > start.isoformat():
            raise RadarDataError("缓存未覆盖请求开始日期，需要扩展历史行情")
        self._validate(cached, sync_start, end, trading_days)
        unresolved = known_gaps - set(cached["date"])
        if unresolved:
            raise RadarDataError("行情缺失交易日: " + ", ".join(str(day) for day in sorted(unresolved)[:5]))
        record = make_record(
            key.source, "complete", cache_status="cache",
            collected_at=datetime.fromisoformat(coverage["updated_at"]) if coverage.get("updated_at") else None,
        )
        return self.store.read(key, start, end), key.source, record
    request_start = sync_start
    if coverage and coverage["requested_start"] <= start.isoformat() and not cached.empty:
        request_start = max(sync_start, end - timedelta(days=10))
    missing = set(self._gaps(cached, sync_start, end, trading_days)) | (known_gaps - set(cached["date"]))
    if missing:
        request_start = min(request_start, min(missing))
    downloaded, source, adjustment, record = self._fetch_result(symbol, request_start, end, trading_days)
    new_key = HistoryKey(source, market, adjustment, symbol)
    if key != new_key and request_start > sync_start:
        downloaded, source, adjustment, record = self._fetch_result(symbol, sync_start, end, trading_days)
        new_key = HistoryKey(source, market, adjustment, symbol)
        request_start = sync_start
    downloaded = normalize_history(downloaded, request_start, end)
    old = self.store.read(new_key, sync_start, end)
    changed_adjustment = adjustment in {"qfq", "hfq", "unknown"} and self._overlap_changed(old, downloaded)
    sync_end = end
    if changed_adjustment:
        previous_coverage = self.store.coverage(new_key)
        request_start = min(sync_start, date.fromisoformat(previous_coverage["first_date"])) if previous_coverage else sync_start
        sync_end = max(end, date.fromisoformat(previous_coverage["last_date"])) if previous_coverage else end
        downloaded, refetched_source, refetched_adjustment, record = self._fetch_result(
            symbol, request_start, sync_end, trading_days
        )
        if (refetched_source, refetched_adjustment) != (source, adjustment):
            raise RadarDataError("复权修订重取时数据源发生变化，请重试")
        downloaded = normalize_history(downloaded, request_start, sync_end)
        self._validate(downloaded, request_start, sync_end, trading_days)
        rebuild_gaps = {date.fromisoformat(day) for day in previous_coverage["missing_dates"]} if previous_coverage else set()
        if unresolved := rebuild_gaps - set(downloaded["date"]):
            raise RadarDataError("行情缺失交易日: " + ", ".join(str(day) for day in sorted(unresolved)[:5]))
    combined = downloaded if changed_adjustment or old.empty else pd.concat([old, downloaded], ignore_index=True).drop_duplicates("date", keep="last").sort_values("date")
    gaps = set(self._gaps(combined, sync_start, sync_end, trading_days))
    if key == new_key:
        gaps |= known_gaps - set(combined["date"])
    self.store.merge(new_key, downloaded, request_start, sync_end, invalidate=changed_adjustment, missing_dates=sorted(gaps))
    self.store.select(new_key, self.provider_id)
    result = self.store.read(new_key, sync_start, end)
    self._validate(result, sync_start, end, trading_days)
    if gaps:
        raise RadarDataError("行情缺失交易日: " + ", ".join(str(day) for day in sorted(gaps)[:5]))
    self.store.select(new_key, self.provider_id, end)
    return self.store.read(new_key, start, end), source, record

def prepare(
    self, symbol: str, market: str, start: date, end: date, *, reuse: bool = False,
    trading_days: Sequence[date] | None = None,
) -> tuple[pd.DataFrame, str]:
    frame, source, _ = self.prepare_result(
        symbol, market, start, end, reuse=reuse, trading_days=trading_days
    )
    return frame, source
```

把 `_fetch` 替换为：

```python
def _fetch_result(
    self, symbol: str, start: date, end: date,
    trading_days: Sequence[date] | None = None,
) -> tuple[pd.DataFrame, str, str, Any]:
    if isinstance(self.provider, FallbackETFDataProvider):
        result = self.provider.fetch_daily_result(
            symbol, start, end, require_target=True, trading_days=trading_days
        )
        if result.value is None:
            raise RadarDataError(result.record.error_message)
        frame = result.value
    else:
        method = getattr(self.provider, "fetch_daily_result", None)
        if callable(method):
            result = method(symbol, start, end)
            if result.value is None:
                raise RadarDataError(result.record.error_message)
            frame = result.value
        else:
            frame = self.provider.fetch_daily(symbol, start, end)
            result = ValueResult(
                frame,
                make_record(provider_label(self.provider), "complete",
                            collected_at=datetime.now().astimezone()),
            )
    adjustment = str(frame.attrs.get("adjustment", getattr(self.provider, "adjustment", "unknown")))
    source = result.record.source or provider_label(self.provider)
    return frame, source, adjustment, result.record
```

顶部导入改为：

```python
from radar.data import FallbackETFDataProvider, RadarDataError, RadarDataProvider, provider_label
```

- [ ] **Step 3: 运行历史测试确认通过**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/radar/test_history.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: PASS，含原有覆盖/缺口/复权测试。

- [ ] **Step 4: 写失败测试 — 刷新使用 record 来源**

在 `tests/radar/test_refresh.py` 末尾追加：

```python
def test_refresh_uses_record_source_for_snapshot_item(tmp_path):
    from data.collection_state import ValueResult, make_record

    class _RecordProvider(_DailyProvider):
        def fetch_daily_result(self, symbol, start, end):
            return ValueResult(
                self.fetch_daily(symbol, start, end),
                make_record("自定义行情源", "complete"),
            )

    provider = _RecordProvider()
    store = RadarStore(tmp_path / "radar.db")
    refresher = RadarRefresher(_Repository(_universe()), provider, store)

    run_id = refresher.refresh("test_etf", as_of=date(2026, 9, 30))

    snapshot = store.get_snapshot(run_id)
    assert snapshot is not None
    assert {item["data_source"] for item in snapshot["items"]} == {"自定义行情源"}
```

```powershell
.\.venv\Scripts\python.exe -m pytest tests/radar/test_refresh.py::test_refresh_uses_record_source_for_snapshot_item -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: FAIL，快照来源仍是 `_RecordProvider`。

- [ ] **Step 5: 刷新层使用 record.source**

把 `src/radar/refresh.py` 中：

```python
history, source = self.history.prepare(
    instrument.symbol, instrument.market, start, target_date,
    reuse=retry_symbols is not None and instrument.symbol not in retry_symbols,
    trading_days=trading_days,
)
histories[instrument.symbol] = history
data_sources[instrument.symbol] = source
```

改为：

```python
history, source, record = self.history.prepare_result(
    instrument.symbol, instrument.market, start, target_date,
    reuse=retry_symbols is not None and instrument.symbol not in retry_symbols,
    trading_days=trading_days,
)
histories[instrument.symbol] = history
data_sources[instrument.symbol] = record.source or source
```

- [ ] **Step 6: 运行刷新测试确认通过**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/radar/test_refresh.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: PASS。

- [ ] **Step 7: 提交**

```powershell
git add src/radar/history.py src/radar/refresh.py tests/radar/test_history.py tests/radar/test_refresh.py
git diff --cached --check
git commit -m "feat(雷达刷新): 使用采集记录推导行情状态"
```

---

## Task 5: 统一海外指数与雷达基准结果

**Files:**
- Modify: `src/radar/overseas.py:1-30`、`101-136`、`221-241`
- Modify: `src/radar/benchmarks.py:1-16`、`125-165`
- Test: `tests/radar/test_overseas.py`
- Test: `tests/radar/test_benchmarks.py`

- [ ] **Step 1: 写失败测试 — 海外结果记录**

在 `tests/radar/test_overseas.py` 末尾追加：

```python
def test_fetch_result_reports_source_and_safe_failure(tmp_path, monkeypatch):
    service = OverseasService(tmp_path / 'radar.db')
    mapping = service.mappings['513100']
    monkeypatch.setattr(
        service, '_fetch',
        lambda _: sample(mapping['source'], mapping['code'].lstrip('^')),
    )

    result = service._fetch_result(mapping)

    assert result.record.result_state == 'complete'
    assert result.record.source in {'东方财富海外指数', 'Yahoo Finance'}
    assert result.value is not None

    def fail(_):
        raise OSError('offline')

    monkeypatch.setattr(service, '_fetch', fail)
    failed = service._fetch_result(mapping)
    assert failed.value is None
    assert failed.record.result_state == 'failed'
    assert 'offline' not in failed.record.error_message
```

```powershell
$run = "tmp/pytest/phase3-task5-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/radar/test_overseas.py::test_fetch_result_reports_source_and_safe_failure -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: FAIL，`_fetch_result` 不存在。

- [ ] **Step 2: 实现海外结果入口并接入 refresh**

在 `src/radar/overseas.py` 顶部加入：

```python
from data.collection_state import ValueResult, make_record
```

在 `OverseasService` 内加入：

```python
@staticmethod
def _source_label(mapping: dict[str, Any]) -> str:
    return "东方财富海外指数" if mapping.get("source") == "eastmoney" else "Yahoo Finance"

def _fetch_result(self, mapping: dict[str, Any]) -> ValueResult[dict[str, Any]]:
    label = self._source_label(mapping)
    try:
        payload = self._fetch(mapping)
    except Exception as exc:  # noqa: BLE001 — 第三方网络/响应隔离
        reason = self._reason(exc)
        return ValueResult(
            None,
            make_record(label, "failed", error_kind=self._error_kind(exc), error_message=reason),
            [reason],
        )
    return ValueResult(
        payload,
        make_record(label, "complete", collected_at=datetime.now().astimezone()),
    )

@staticmethod
def _error_kind(error: Exception) -> str:
    if isinstance(error, TimeoutError) or "超时" in str(error) or "Timeout" in type(error).__name__:
        return "timeout"
    if isinstance(error, (ValueError, KeyError, TypeError, IndexError)):
        return "invalid_data"
    return "fetch_failed"

@staticmethod
def _reason(error: Exception) -> str:
    if isinstance(error, (requests.RequestException, OSError)):
        return '海外行情源连接失败，请稍后重试'
    if isinstance(error, (ValueError, KeyError, TypeError, IndexError)):
        return '海外行情源返回的数据未通过日期或指数口径校验'
    return '海外行情源暂不可用，请稍后重试'
```

把 `refresh` 内这段：

```python
for candidate in self._candidates(mapping):
    key = self._key(candidate)
    previous = self._cached(key)
    try:
        rows = parse_daily(candidate, self._fetch(candidate), now)
        # 只在同一来源缓存中合并，不拼接跨源收盘。
        merged = {row['date']: row for row in previous['rows']}
        merged.update({row['date']: row for row in rows})
        history = [merged[day] for day in sorted(merged)]
        self._save(key, now, history, None)
        if all(self._complete(history, cutoff) for cutoff in cutoffs):
            break
    except Exception as exc:  # noqa: BLE001 — 第三方网络及响应隔离，不影响其他指数
        logger.warning('海外指数 %s 更新失败: %s', candidate['code'], exc)
        if isinstance(exc, (requests.RequestException, OSError)):
            reason = '海外行情源连接失败，请稍后重试'
        elif isinstance(exc, (ValueError, KeyError, TypeError, IndexError)):
            reason = '海外行情源返回的数据未通过日期或指数口径校验'
        else:
            reason = '海外行情源暂不可用，请稍后重试'
        self._save(key, now, previous['rows'], reason)
```

改为：

```python
for candidate in self._candidates(mapping):
    key = self._key(candidate)
    previous = self._cached(key)
    result = self._fetch_result(candidate)
    if result.value is None:
        self._save(key, now, previous['rows'], result.record.error_message)
        continue
    try:
        rows = parse_daily(candidate, result.value, now)
        # 只在同一来源缓存中合并，不拼接跨源收盘。
        merged = {row['date']: row for row in previous['rows']}
        merged.update({row['date']: row for row in rows})
        history = [merged[day] for day in sorted(merged)]
        self._save(key, now, history, None)
        if all(self._complete(history, cutoff) for cutoff in cutoffs):
            break
    except Exception as exc:  # noqa: BLE001 — 第三方网络及响应隔离，不影响其他指数
        logger.warning('海外指数 %s 更新失败: %s', candidate['code'], exc)
        self._save(key, now, previous['rows'], self._reason(exc))
```

- [ ] **Step 3: 运行海外测试确认通过**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/radar/test_overseas.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: PASS，含缓存失败保留与展示字段不变测试。

- [ ] **Step 4: 更新雷达基准测试并写失败测试**

把 `tests/radar/test_benchmarks.py` 中 `test_local_benchmark_is_used_when_network_is_unavailable` 的 patch 改为：

```python
from data.collection_state import ValueResult, make_record

monkeypatch.setattr(
    "radar.benchmarks.fetch_index_history_result",
    lambda **_: ValueResult(None, make_record(
        "中证指数官网", "failed", error_kind="fetch_failed", error_message="中证指数行情请求失败"
    )),
)
```

并追加：

```python
def test_network_benchmark_uses_shared_result_entry(monkeypatch):
    from data.collection_state import ValueResult, make_record

    raw = pd.DataFrame({"date": ["2024-01-02", "2024-01-03"], "close": [3500.0, 3510.0]})
    monkeypatch.setattr(
        "radar.benchmarks.fetch_index_history_result",
        lambda **_: ValueResult(raw, make_record("中证指数官网", "complete")),
    )
    benchmark = RadarBenchmark(id="csi_300", name="沪深300", symbol="000300")

    result = fetch_benchmark_closes(benchmark, date(2024, 1, 1), date(2024, 1, 31))

    assert result.tolist() == [3500.0, 3510.0]
```

```powershell
.\.venv\Scripts\python.exe -m pytest tests/radar/test_benchmarks.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: FAIL，`radar.benchmarks.fetch_index_history_result` 不存在。

- [ ] **Step 5: 接入共享结果入口**

把 `src/radar/benchmarks.py` 的导入：

```python
from data.akshare import _ak_csindex
```

改为：

```python
from data.akshare import fetch_index_history_result
```

把 `fetch_benchmark_closes` 中：

```python
try:
    raw = _ak_csindex(
        symbol=benchmark.symbol,
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )
except Exception as exc:  # 上游 AkShare 边界需统一为领域错误
    raise RadarBenchmarkError(f"基准 {benchmark.name} 行情请求失败") from exc
if raw is None or raw.empty:
    raise RadarBenchmarkError(f"基准 {benchmark.name} 在请求区间无数据")
```

改为：

```python
result = fetch_index_history_result(
    benchmark.symbol,
    start.strftime("%Y%m%d"),
    end.strftime("%Y%m%d"),
)
if result.record.result_state == "failed":
    raise RadarBenchmarkError(f"基准 {benchmark.name} 行情请求失败")
if result.value is None:
    raise RadarBenchmarkError(f"基准 {benchmark.name} 在请求区间无数据")
raw = result.value
```

- [ ] **Step 6: 运行雷达基准测试确认通过**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/radar/test_benchmarks.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: PASS。

- [ ] **Step 7: 提交**

```powershell
git add src/radar/overseas.py src/radar/benchmarks.py tests/radar/test_overseas.py tests/radar/test_benchmarks.py
git diff --cached --check
git commit -m "feat(雷达海外): 统一海外与基准取数结果"
```

---

## Task 6: 回测行情、基准结果与 manifest

**Files:**
- Modify: `src/backtest/data.py:1-139`
- Modify: `src/backtest/runner.py:49-140`、`_align_benchmark`
- Modify: `src/backtest/models.py:1-15`、`133-149`
- Modify: `src/backtest/artifacts.py:90-104`
- Test: `tests/backtest/test_data.py`
- Test: `tests/backtest/test_runner.py`
- Test: `tests/backtest/test_backtest_integration.py`
- Test: `tests/backtest/test_artifacts.py`

- [ ] **Step 1: 写失败测试 — 回测行情结果入口**

把 `tests/backtest/test_data.py` 的 `test_stock_history_passes_requested_dates_to_adapter` 替换为：

```python
def test_stock_history_result_passes_requested_dates_to_adapter(mocker):
    adapter = mocker.Mock()
    adapter.fetch_result.return_value = CollectionResult(
        rows=[_price(date(2022, 1, 4))], record=None
    )
    provider = HistoricalPriceProvider(adapter)

    result = provider.fetch_stock_result("000001", date(2022, 1, 1), date(2022, 12, 31))

    adapter.fetch_result.assert_called_once_with(
        "000001", data_type="price", start_date="20220101", end_date="20221231"
    )
    assert result.value == [_price(date(2022, 1, 4))]
    assert result.record.source == "AkShare"
    assert result.record.result_state == "complete"
    assert provider.fetch_stock("000001", date(2022, 1, 1), date(2022, 12, 31)) == result.value
```

在文件顶部加入：

```python
from data.base import CollectionResult
```

```powershell
$run = "tmp/pytest/phase3-task6-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/backtest/test_data.py::test_stock_history_result_passes_requested_dates_to_adapter -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: FAIL，`fetch_stock_result` 不存在。

- [ ] **Step 2: 实现回测结果入口**

把 `src/backtest/data.py` 顶部导入改为：

```python
from datetime import date, datetime, timedelta

from data.akshare import AkShareAdapter, fetch_index_history_result
from data.collection_state import ValueResult, make_record
```

把 `HistoricalPriceProvider.fetch_stock` 替换为：

```python
def fetch_stock(self, symbol: str, start: date, end: date) -> list[PriceData]:
    """兼容包装：返回行情列表。"""
    return self.fetch_stock_result(symbol, start, end).value or []

def fetch_stock_result(self, symbol: str, start: date, end: date) -> ValueResult[list[PriceData]]:
    """按指定区间获取个股日线并返回采集记录。"""
    if start > end:
        raise BacktestDataError(f"日期区间无效: start={start} 晚于 end={end}")
    result = self._adapter.fetch_result(
        symbol,
        data_type="price",
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )
    rows = result.rows or []
    if rows:
        record = make_record(
            result.record.source if result.record else "AkShare",
            "complete",
            collected_at=datetime.now().astimezone(),
        )
    elif result.record is not None:
        record = make_record(
            result.record.source or "AkShare",
            "failed",
            diagnostic=result.record,
            collected_at=datetime.now().astimezone(),
        )
    else:
        record = make_record("AkShare", "empty", collected_at=datetime.now().astimezone())
    return ValueResult(rows, record, list(result.errors))
```

- [ ] **Step 3: 更新基准测试并运行**

把 `tests/backtest/test_data.py` 中所有：

```python
mocker.patch("backtest.data._ak_csindex", return_value=raw)
```

替换为：

```python
mocker.patch(
    "backtest.data.fetch_index_history_result",
    return_value=ValueResult(raw, make_record("中证指数官网", "complete")),
)
```

把 `test_benchmark_empty_data_raises` 中的 patch 改为：

```python
mocker.patch(
    "backtest.data.fetch_index_history_result",
    return_value=ValueResult(None, make_record("中证指数官网", "empty")),
)
```

在文件顶部加入：

```python
from data.collection_state import ValueResult, make_record
```

追加测试：

```python
def test_benchmark_result_reports_record(mocker):
    raw = _benchmark_rows(START, END)
    mocker.patch(
        "backtest.data.fetch_index_history_result",
        return_value=ValueResult(raw, make_record("中证指数官网", "complete")),
    )
    provider = HistoricalPriceProvider(mocker.Mock())

    result = provider.fetch_benchmark_result(MONEY_FUND, START, END)

    assert result.record.source == "中证指数官网"
    assert result.record.result_state == "complete"
    assert result.value is not None
    assert result.value.index[0] == date(2022, 1, 3)
```

- [ ] **Step 4: 运行测试确认失败**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/backtest/test_data.py::test_benchmark_result_reports_record -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: FAIL，`fetch_benchmark_result` 不存在。

- [ ] **Step 5: 实现基准结果入口**

把 `HistoricalPriceProvider.fetch_benchmark` 替换为：

```python
def fetch_benchmark(self, benchmark: BenchmarkSpec, start: date, end: date) -> pd.Series:
    """兼容包装：返回基准收盘序列。"""
    result = self.fetch_benchmark_result(benchmark, start, end)
    assert result.value is not None
    return result.value

def fetch_benchmark_result(self, benchmark: BenchmarkSpec, start: date, end: date) -> ValueResult[pd.Series]:
    """获取基准收盘点位序列并返回采集记录。"""
    if start > end:
        raise BacktestDataError(f"日期区间无效: start={start} 晚于 end={end}")
    result = fetch_index_history_result(
        benchmark.symbol,
        start.strftime("%Y%m%d"),
        end.strftime("%Y%m%d"),
    )
    if result.record.result_state == "failed":
        raise BacktestDataError(
            f"基准 {benchmark.id} 行情请求失败：{result.record.error_message}"
        )
    if result.value is None:
        raise BacktestDataError(f"基准 {benchmark.id} 在 {start} 至 {end} 内无数据")
    df = result.value
    # 防御性兼容中文（日期/收盘）与英文（date/close）两种列名
    date_col = "date" if "date" in df.columns else "日期"
    close_col = "close" if "close" in df.columns else "收盘"
    frame = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(df[date_col], errors="coerce"),
            "close": pd.to_numeric(df[close_col], errors="coerce"),
        }
    )
    dropped = int(frame.isna().any(axis=1).sum())
    if dropped:
        logger.warning(
            "基准 %s 原始数据含 %d 行无法解析的日期或收盘值，已丢弃",
            benchmark.id,
            dropped,
        )
    frame = frame.dropna()
    dates: list[date] = [ts.date() for ts in frame["trade_date"]]
    closes = frame["close"].to_numpy()
    rows = [(d, float(c)) for d, c in zip(dates, closes, strict=True)]
    rows = [(d, c) for d, c in rows if start <= d <= end]
    rows = sorted(dict(rows).items())
    if not rows:
        raise BacktestDataError(f"基准 {benchmark.id} 在 {start} 至 {end} 内无数据")
    first_day, last_day = rows[0][0], rows[-1][0]
    if first_day - start > timedelta(days=BOUNDARY_TOLERANCE_DAYS):
        raise BacktestDataError(
            f"基准 {benchmark.id} 缺少 {start} 至 {first_day} 前的行情"
            f"（序列始于 {first_day}，晚于回测起点 {BOUNDARY_TOLERANCE_DAYS} 天）"
        )
    if end - last_day > timedelta(days=BOUNDARY_TOLERANCE_DAYS):
        raise BacktestDataError(
            f"基准 {benchmark.id} 缺少 {last_day} 后至 {end} 的行情"
            f"（序列止于 {last_day}，早于回测终点 {BOUNDARY_TOLERANCE_DAYS} 天）"
        )
    trade_dates = [d for d, _ in rows]
    for i in range(1, len(trade_dates)):
        prev, cur = trade_dates[i - 1], trade_dates[i]
        gap = (cur - prev).days
        if gap > MAX_GAP_DAYS:
            raise BacktestDataError(
                f"基准 {benchmark.id} 在 {prev} 至 {cur} 间存在缺口"
                f"（间隔 {gap} 天），无法对齐股票净值日计算连续净值"
            )
    series = pd.Series(
        [close for _, close in rows],
        index=pd.Index(trade_dates, name="trade_date"),
        name=benchmark.name,
    )
    return ValueResult(series, result.record)
```

- [ ] **Step 6: 运行回测数据测试确认通过**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/backtest/test_data.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: PASS。

- [ ] **Step 7: 写失败测试 — runner 收集 records**

在 `tests/backtest/test_runner.py` 的 `_FixedProvider` 中加入：

```python
def fetch_stock_result(self, symbol: str, start: date, end: date):
    rows = self.fetch_stock(symbol, start, end)
    return ValueResult(rows, make_record("测试行情", "complete" if rows else "empty"))

def fetch_benchmark_result(self, benchmark, start: date, end: date):
    return ValueResult(self.fetch_benchmark(benchmark, start, end), make_record("测试基准", "complete"))
```

在文件顶部加入：

```python
from data.collection_state import ValueResult, make_record
```

追加测试：

```python
def test_runner_exposes_collection_records(tmp_path):
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    (strategies_dir / "test_strategy.yaml").write_text(_strategy_yaml(), encoding="utf-8")
    runner = BacktestRunner(
        provider=_FixedProvider(_price_series()),
        strategies=StrategyRepository(strategies_dir),
        config=Config(),
    )
    result = runner.run(_request())
    assert set(result.collection_records) == {"price", "benchmark"}
    assert result.collection_records["price"].source == "测试行情"
    assert result.collection_records["benchmark"].source == "测试基准"
```

在 `tests/backtest/test_backtest_integration.py` 的 `_FixedProvider` 中同样加入上面的两个结果方法和 `ValueResult`/`make_record` 导入。

```powershell
.\.venv\Scripts\python.exe -m pytest tests/backtest/test_runner.py::test_runner_exposes_collection_records -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: FAIL，`BacktestResult` 没有 `collection_records` 且 runner 调用旧方法。

- [ ] **Step 8: 实现 runner、模型与 manifest**

`src/backtest/models.py` 顶部：

```python
from dataclasses import dataclass, field
```

并加入：

```python
from data.schemas import DataCollectionRecord
```

在 `BacktestResult` 末尾加入：

```python
collection_records: dict[str, DataCollectionRecord] = field(default_factory=dict)
```

`src/backtest/runner.py` 的 `run` 中把：

```python
prices = self._provider.fetch_stock(request.symbol, fetch_start, request.end_date)
validate_price_history(prices, fetch_start, request.end_date)
```

改为：

```python
stock_result = self._provider.fetch_stock_result(request.symbol, fetch_start, request.end_date)
prices = stock_result.value or []
if not prices and stock_result.record.error_message:
    raise BacktestDataError(stock_result.record.error_message)
validate_price_history(prices, fetch_start, request.end_date)
```

把：

```python
bench_net = self._align_benchmark(benchmark, idx, data_start, data_end)
```

改为：

```python
bench_net, benchmark_record = self._align_benchmark(benchmark, idx, data_start, data_end)
```

在 `BacktestResult(` 构造中增加：

```python
collection_records={"price": stock_result.record, "benchmark": benchmark_record},
```

把 `_align_benchmark` 改为：

```python
def _align_benchmark(
    self, benchmark: BenchmarkSpec, idx: pd.DatetimeIndex, data_start: date, data_end: date
) -> tuple[pd.Series, Any]:
    """基准收盘序列前向对齐到策略净值日，统一从 1.0 起算。"""
    result = self._provider.fetch_benchmark_result(benchmark, data_start, data_end)
    raw = result.value
    assert raw is not None
    bench = pd.Series(raw.to_numpy(), index=pd.DatetimeIndex(raw.index))
    aligned = bench.reindex(idx).ffill()
    na_mask = aligned.isna()
    if na_mask.any():
        missing = aligned.index.to_numpy()[na_mask.to_numpy()]
        raise BacktestDataError(
            f"基准 {benchmark.id} 在 {pd.Timestamp(missing[0]).date()} 至 "
            f"{pd.Timestamp(missing[-1]).date()} 无数据，无法对齐净值日"
        )
    return aligned / aligned.iloc[0], result.record
```

`src/backtest/artifacts.py` 的 `_write_manifest` 增加：

```python
"collection_records": {
    key: record.model_dump(mode="json")
    for key, record in result.collection_records.items()
},
```

放在 `"warnings"` 之后、`"version"` 之前。

- [ ] **Step 9: 更新产物测试**

在 `tests/backtest/test_artifacts.py` 的 `_build_result` 返回的 `BacktestResult(...)` 中加入：

```python
collection_records={
    "price": make_record("AkShare", "complete"),
    "benchmark": make_record("中证指数官网", "complete"),
},
```

并在文件顶部加入：

```python
from data.collection_state import make_record
```

在 `test_manifest_snapshot_captures_run_context` 末尾加入：

```python
assert manifest["collection_records"]["price"]["source"] == "AkShare"
assert manifest["collection_records"]["benchmark"]["result_state"] == "complete"
```

- [ ] **Step 10: 运行回测相关测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/backtest -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: PASS。

- [ ] **Step 11: 提交**

```powershell
git add src/backtest/data.py src/backtest/runner.py src/backtest/models.py src/backtest/artifacts.py tests/backtest
git diff --cached --check
git commit -m "feat(回测数据): 记录回测行情与基准取数结果"
```

---

## Task 7: 移除最后一个遗留取数入口

**Files:**
- Modify: `src/core/collection_contract.py:37-57`
- Test: `tests/core/test_collection_contract.py`
- Test: `tests/test_data_entrypoints.py`

- [ ] **Step 1: 写失败测试 — 清单只剩兼容桥**

把 `tests/core/test_collection_contract.py` 的 `test_legacy_fetch_sites_declare_current_migration_ledger` 替换为：

```python
def test_legacy_fetch_sites_contain_only_compatibility_bridge():
    assert {site.path for site in LEGACY_FETCH_SITES} == {"src/data/base.py"}
    assert all(site.phase for site in LEGACY_FETCH_SITES)
```

在 `test_dedicated_capabilities_and_entrypoint_ledger` 的断言中加入：

```python
assert ("src/backtest/data.py", "HistoricalPriceProvider.fetch_stock_result") in ALLOWED_FETCH_RESULT_CALLS
```

```powershell
$run = "tmp/pytest/phase3-task7-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/core/test_collection_contract.py::test_legacy_fetch_sites_contain_only_compatibility_bridge tests/core/test_collection_contract.py::test_dedicated_capabilities_and_entrypoint_ledger -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: FAIL。

- [ ] **Step 2: 更新门禁清单**

把 `src/core/collection_contract.py` 的 `LEGACY_FETCH_SITES` 改为：

```python
LEGACY_FETCH_SITES: tuple[FetchSite, ...] = (
    FetchSite(
        "src/data/base.py", "DataSource.fetch_result", (None,),
        "遗留适配器兼容桥；五组能力必须显式覆盖", "随来源迁移逐步淘汰",
    ),
)
```

在 `ALLOWED_FETCH_RESULT_CALLS` 中加入：

```python
("src/backtest/data.py", "HistoricalPriceProvider.fetch_stock_result"),
```

- [ ] **Step 3: 运行门禁测试**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/core/test_collection_contract.py tests/test_data_entrypoints.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
.\scripts\check-data-entrypoints.py
```

Expected: PASS，脚本输出“数据取数入口检查通过”。

- [ ] **Step 4: 提交**

```powershell
git add src/core/collection_contract.py tests/core/test_collection_contract.py
git diff --cached --check
git commit -m "chore(门禁): 移除回测行情遗留入口"
```

---

## Task 8: 回归、真实取数验收与文档

**Files:**
- Modify: `docs/superpowers/specs/2026-10/2026-10-26-unified-data-collection-design.md:3`、第 17 节状态
- Modify: `docs/superpowers/plans/2026-10/2026-10-26-unified-data-collection-plan.md`（勾选完成项、记录结果）

- [ ] **Step 1: 运行受影响测试集**

```powershell
$run = "tmp/pytest/phase3-accept-$PID-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $run -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest tests/radar tests/backtest tests/data tests/core tests/api tests/test_cli.py -q "--basetemp=$run/basetemp" -o "cache_dir=$run/cache"
```

Expected: PASS。记录 pass/fail/skip 数量；任何既有失败必须单独说明，不能归因于 Phase 3。

- [ ] **Step 2: 运行 Changed/Staged 质量门禁**

```powershell
.\scripts\verify.ps1 -Files src/data/collection_state.py
.\scripts\verify.ps1 -Files src/data/akshare.py
.\scripts\verify.ps1 -Files src/radar/data.py
.\scripts\verify.ps1 -Files src/radar/history.py
.\scripts\verify.ps1 -Files src/radar/refresh.py
.\scripts\verify.ps1 -Files src/radar/overseas.py
.\scripts\verify.ps1 -Files src/radar/benchmarks.py
.\scripts\verify.ps1 -Files src/backtest/data.py
.\scripts\verify.ps1 -Files src/backtest/runner.py
.\scripts\verify.ps1 -Files src/backtest/models.py
.\scripts\verify.ps1 -Files src/backtest/artifacts.py
.\scripts\verify.ps1 -Files src/core/collection_contract.py
```

逐项记录结果；静态检查失败必须修复，不由测试替代。

- [ ] **Step 3: 回测真实取数 CLI 验收**

```powershell
mkdir tmp\acceptance\unified-data-collection-phase3 -Force
.\.venv\Scripts\stock-robot.exe backtest 600519 --start 2025-01-01 --end 2025-12-31 2>&1 | Tee-Object -FilePath tmp\acceptance\unified-data-collection-phase3\backtest-cli.log
```

检查：

- 命令成功并输出指标表。
- `reports/backtests/` 下最新运行目录包含五类产物。
- 最新 `manifest.json` 的 `collection_records` 同时包含 `price` 与 `benchmark`，`source`、`result_state`、`collected_at` 可见。
- 若上游失败，`price`/`benchmark` 记录为 `failed` 且错误信息不含原始异常细节；不得把失败验收记录为通过。

- [ ] **Step 4: 雷达真实刷新验收**

```powershell
.\.venv\Scripts\stock-robot.exe radar refresh --universe cn_hk_etf_v1 2>&1 | Tee-Object -FilePath tmp\acceptance\unified-data-collection-phase3\radar-refresh.log
.\.venv\Scripts\stock-robot.exe radar status --universe cn_hk_etf_v1 2>&1 | Tee-Object -FilePath tmp\acceptance\unified-data-collection-phase3\radar-status.log
```

检查：

- 刷新成功并生成快照；`radar status` 输出状态汇总。
- 快照标的的 `data_source` 为实际成功来源，失败标的保留 `stale/failed` 与安全诊断。
- 失败时旧快照仍可读、`HistoryStore` 未被失败结果污染。
- 不启动常驻队列、不触发浏览器验收。

- [ ] **Step 5: 更新设计状态与计划勾选**

把设计文档顶部状态和第 17 节状态的“待实现”改为“已于 2026-10-28 实现并完成范围验收”，并在计划文档勾选实际完成的步骤、补充验收记录（命令、报告路径、测试数量、既有失败）。

- [ ] **Step 6: 提交文档与验收记录**

```powershell
git add docs/superpowers/specs/2026-10/2026-10-26-unified-data-collection-design.md docs/superpowers/plans/2026-10/2026-10-26-unified-data-collection-plan.md
git diff --cached --check
git commit -m "docs(数据采集): 记录 Phase 3 实施与验收"
```

- [ ] **Step 7: 合并/发布前 Full**

只有用户明确要求合并或发布时才运行：

```powershell
.\scripts\verify.ps1 -Scope Full
```

既有前端静态失败必须单独说明；不能将 Phase 3 表述为 Full 全绿。

---

## Phase 3 自审清单

- [ ] `ValueResult.errors` 默认空列表，Phase 2 单值入口行为不变。
- [ ] ETF 各源 `fetch_daily_result` 覆盖 complete/empty/failed，旧 `fetch_daily` 仍抛原异常。
- [ ] Fallback 保留顺序、熔断、目标日/缺口切源，并区分 complete/partial/empty/failed。
- [ ] `HistorySynchronizer.prepare_result` 返回来源与记录；缓存复用标记 `cache_status="cache"`。
- [ ] 刷新快照 `data_source` 来自 record，失败仍写现有 `stale/failed/error_summary`；无新表列。
- [ ] 海外 `_fetch_result` 只暴露安全诊断，`overseas_cache` 与 `view()` 展示不变。
- [ ] 雷达基准改用 `fetch_index_history_result`，本地 CSV 优先语义不变。
- [ ] 回测 `fetch_stock_result`/`fetch_benchmark_result` 保留区间、连续性、边界容差和错误终止。
- [ ] `BacktestResult.collection_records` 有默认值；manifest 增加记录，其他产物不变。
- [ ] `LEGACY_FETCH_SITES` 只剩 `DataSource.fetch_result` 兼容桥，静态检查真实仓库通过。
- [ ] 未新增雷达持久化表/列、数据源、依赖、评分公式或报告文案。
- [ ] 真实回测与雷达刷新验收完成；失败路径不冒充通过；跳过浏览器。
- [ ] Phase 3 设计/计划仍为同一主题的一份设计＋一份计划。
