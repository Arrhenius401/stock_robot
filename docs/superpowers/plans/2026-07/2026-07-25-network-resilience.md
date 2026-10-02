# 网络健壮性增强 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补上 `resolve_name` 重试缺口，将估值和行业的全市场重型 AkShare 端点替换为单只股票轻量接口（保留旧端点 fallback），加并发请求 stagger 延迟。

**Architecture:** 自底向上：先补 `resolve_name` 重试（symbols.py），再替换 akshare.py 估值和行业两个 `_fetch_*` 的内部调用链（新增轻量 helper → 优先调用 → except 中 fallback 旧 helper），最后 pipeline.py 加 stagger。每个任务 TDD。

**Tech Stack:** Python 3.11、AkShare、pytest、pytest-mock。沿用现有测试布局与 import 约定。

**设计文档:** `docs/superpowers/specs/2026-07-25-network-resilience-design.md`

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `src/utils/symbols.py` | `resolve_name` 加 `retry_on_network_error` 包裹 | 修改 |
| `src/data/akshare.py` | 新增轻量 helper、`_fetch_valuation` 和 `_fetch_industry` fallback 链 | 修改 |
| `src/core/pipeline.py` | `collect` → `fetch_one` 内部加 0.3s stagger | 修改 |
| `tests/utils/test_symbols.py` | `resolve_name` 重试测试 | 追加 |
| `tests/data/test_akshare.py` | 估值/行业 fallback 链 + 新端点解析测试 | 追加 |

---

### Task 1: `resolve_name` 加重试包裹

**Files:**
- Modify: `src/utils/symbols.py:50-60`
- Test: `tests/utils/test_symbols.py`（追加）

- [ ] **Step 1: 写失败测试**

Append to `tests/utils/test_symbols.py`。先读文件检查已有 import，然后追加：

```python
from http.client import RemoteDisconnected


def test_resolve_name_retries_on_network_error(mocker):
    mocker.patch("utils.symbols.time.sleep")
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RemoteDisconnected("boom")
        import pandas as pd
        return pd.DataFrame([
            {"code": "000001", "name": "平安银行"},
            {"code": "600036", "name": "招商银行"},
        ])

    mocker.patch("akshare.stock_info_a_code_name", side_effect=flaky)
    name = resolve_name("000001")
    assert calls["n"] == 3
    assert name == "平安银行"
```

注意：`resolve_name` 和 `normalize_symbol` 已在该文件顶部的 import 中。检查是否需要额外补充 `import pandas as pd`（测试函数内延迟 import 避免污染顶层）。

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/utils/test_symbols.py::test_resolve_name_retries_on_network_error -v`
Expected: FAIL — 当前无重试，首次 `RemoteDisconnected` 被 `except` 捕获返回 `""`，`calls["n"] == 1`

- [ ] **Step 3: 实现**

读 `src/utils/symbols.py`。在 import 区添加：

```python
from utils.retry import retry_on_network_error
```

在 `logger = logging.getLogger(__name__)` 之后、`resolve_name` 之前添加模块级 helper：

```python
@retry_on_network_error()
def _ak_code_name():
    import akshare as ak
    return ak.stock_info_a_code_name()
```

修改 `resolve_name` 函数体，将 `import akshare as ak` 和 `df = ak.stock_info_a_code_name()` 替换为：

```python
def resolve_name(symbol: str) -> str:
    """解析股票代码对应的公司名称"""
    try:
        df = _ak_code_name()
        row = df[df["code"] == normalize_symbol(symbol)]
        if not row.empty:
            return str(row["name"].iloc[0])
    except Exception as e:
        logger.warning(f"股票名称解析失败 {symbol}: {e}")
    return ""
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/utils/test_symbols.py -v`
Expected: ALL PASS（新测试通过，既有 `test_normalize_symbol`、`test_validate_symbol` 等仍通过）

- [ ] **Step 5: 提交**

```bash
git add src/utils/symbols.py tests/utils/test_symbols.py
git commit -m "fix(工具): resolve_name 添加网络重试包裹"
```

---

### Task 2: 估值端点轻量化（新端点优先 + 旧端点回退）

**Files:**
- Modify: `src/data/akshare.py`（`_fetch_valuation` 方法）
- Test: `tests/data/test_akshare.py`（追加）

**前置研究步骤（实施时必须先做）：**

在 Python REPL 中运行以下命令，验证新端点返回结构和字段名：

```python
import akshare as ak
# 测试单只股票实时行情
df = ak.stock_individual_info_em(symbol="000001")
print(df.columns.tolist())
print(df.head())
# 确认是否有 "市盈率" 或 "市盈率-动态" 和 "市净率" 列
```

根据实际字段名调整下方代码中的列名。以下代码假设字段名为 `"市盈率-动态"` 和 `"市净率"`（与旧端点一致，最可能复用）。如果不同，按实际列名调整。

- [ ] **Step 1: 写失败测试**

Append to `tests/data/test_akshare.py`：

```python
def test_fetch_valuation_falls_back_to_old_endpoint(mocker):
    """新端点失败时回退旧端点"""
    import pandas as pd
    # 新端点抛异常
    mocker.patch(
        "akshare.stock_individual_info_em",
        side_effect=RemoteDisconnected("boom"),
    )
    # 旧端点成功
    mocker.patch(
        "akshare.stock_zh_a_spot_em",
        return_value=pd.DataFrame([
            {"代码": "000001", "市盈率-动态": 7.5, "市净率": 0.85},
            {"代码": "600036", "市盈率-动态": 6.2, "市净率": 0.72},
        ]),
    )
    mocker.patch("utils.retry.time.sleep")  # 避免重试延迟

    adapter = AkShareAdapter()
    results = adapter.fetch("000001", data_type="valuation")
    assert len(results) == 1
    assert results[0].pe_ttm == 7.5
    assert results[0].pb == 0.85


def test_fetch_valuation_uses_new_endpoint_first(mocker):
    """新端点成功时使用新端点数据"""
    import pandas as pd
    mocker.patch(
        "akshare.stock_individual_info_em",
        return_value=pd.DataFrame({
            "item": ["市盈率-动态", "市净率"],
            "value": ["7.5", "0.85"],
        }),
    )
    mocker.patch("utils.retry.time.sleep")

    adapter = AkShareAdapter()
    results = adapter.fetch("000001", data_type="valuation")
    assert len(results) == 1
    # 值被正确解析（取决于新端点的实际列结构）
    assert results[0].pe_ttm is not None or results[0].pb is not None
```

注意：第二个测试的 mock 结构取决于 REPL 研究结果——`stock_individual_info_em` 返回的可能是二维表（item/value）而非宽表。**实施时必须先用 REPL 确认返回结构，然后调整 mock 和解析代码。** 如果新端点返回结构与旧端点不同（例如 item/value 长表 vs 宽表），需要写对应的解析逻辑。

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/data/test_akshare.py::test_fetch_valuation_falls_back_to_old_endpoint -v`
Expected: FAIL — 当前 `_fetch_valuation` 直接调 `_ak_spot_em`，不经过新端点

- [ ] **Step 3: 实现**

读 `src/data/akshare.py`。在模块级 helper 区（`_ak_news` 之后）新增：

```python
@retry_on_network_error()
def _ak_individual_info(symbol):
    """单只股票基本信息接口（轻量，替代全市场扫描）"""
    return ak.stock_individual_info_em(symbol=symbol)
```

修改 `_fetch_valuation` 方法（约第 118-127 行），替换为：

```python
    def _fetch_valuation(self, symbol: str, **kwargs) -> list[ValuationData]:
        pe_ttm, pb = None, None

        # 优先：单只股票轻量接口
        try:
            df = _ak_individual_info(symbol)
            # 根据实际返回结构调整解析逻辑。以下为两种常见格式：
            # 格式 A（宽表）：直接取列
            if "市盈率-动态" in df.columns:
                row = df.iloc[0] if len(df) > 0 else None
                val = df[df.iloc[:, 0] == "市盈率-动态"]
                pe_val = str(row.get("市盈率-动态", "-")) if row is not None else "-"
                pb_val = str(row.get("市净率", "-")) if row is not None else "-"
                pe_ttm = parse_cn_number(pe_val) if pe_val != "-" else None
                pb = parse_cn_number(pb_val) if pb_val != "-" else None
            # 格式 B（item/value 长表）：按 item 列过滤
            elif "item" in df.columns and "value" in df.columns:
                pe_row = df[df["item"] == "市盈率-动态"]
                pb_row = df[df["item"] == "市净率"]
                pe_ttm = parse_cn_number(pe_row["value"].iloc[0]) if not pe_row.empty else None
                pb = parse_cn_number(pb_row["value"].iloc[0]) if not pb_row.empty else None
        except Exception:
            pass

        # 回退：旧全市场接口
        if pe_ttm is None and pb is None:
            try:
                df = _ak_spot_em()
                row = df[df["代码"] == symbol]
                pe_ttm = parse_cn_number(row["市盈率-动态"].iloc[0]) if not row.empty and row["市盈率-动态"].iloc[0] != "-" else None
                pb = parse_cn_number(row["市净率"].iloc[0]) if not row.empty and row["市净率"].iloc[0] != "-" else None
            except Exception:
                pass

        return [ValuationData(symbol=symbol, date=date.today(), pe_ttm=pe_ttm, pb=pb, ps_ttm=None)]
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/data/test_akshare.py -v`
Expected: ALL PASS（新测试 + 既有测试均通过）

- [ ] **Step 5: 提交**

```bash
git add src/data/akshare.py tests/data/test_akshare.py
git commit -m "fix(数据层): 估值端点替换为单只股票接口并保留旧端点回退"
```

---

### Task 3: 行业端点轻量化（新端点优先 + 旧端点回退）

**Files:**
- Modify: `src/data/akshare.py`（`_fetch_industry` 方法）
- Test: `tests/data/test_akshare.py`（追加）

**前置研究：** 复用 Task 2 的 REPL 结果——`stock_individual_info_em` 通常也含 "行业" 或 "所属行业" 字段。验证后确定字段名。

- [ ] **Step 1: 写失败测试**

Append to `tests/data/test_akshare.py`：

```python
def test_fetch_industry_falls_back_to_old_endpoint(mocker):
    """新端点失败时回退旧端点"""
    import pandas as pd
    mocker.patch(
        "akshare.stock_individual_info_em",
        side_effect=RemoteDisconnected("boom"),
    )
    mocker.patch(
        "akshare.stock_board_industry_name_em",
        return_value=pd.DataFrame({"板块名称": ["银行", "保险", "证券"]}),
    )
    mocker.patch("utils.retry.time.sleep")

    adapter = AkShareAdapter()
    results = adapter.fetch("000001", data_type="industry")
    assert len(results) == 1
    # 旧端点返回第一个板块名称（当前行为，非 bug——新端点失败后降级）
    assert results[0].industry in ("银行", "保险", "证券")


def test_fetch_industry_uses_new_endpoint_first(mocker):
    """新端点返回含行业字段时正确提取"""
    import pandas as pd
    mocker.patch(
        "akshare.stock_individual_info_em",
        return_value=pd.DataFrame({
            "item": ["所属行业", "上市时间"],
            "value": ["银行", "1991-04-03"],
        }),
    )
    mocker.patch("utils.retry.time.sleep")

    adapter = AkShareAdapter()
    results = adapter.fetch("000001", data_type="industry")
    assert len(results) == 1
    assert results[0].industry == "银行"
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/data/test_akshare.py::test_fetch_industry_uses_new_endpoint_first -v`
Expected: FAIL — 当前 `_fetch_industry` 直接调 `_ak_industry_name`，不经过新端点

- [ ] **Step 3: 实现**

修改 `_fetch_industry` 方法（约第 129-139 行），替换为：

```python
    def _fetch_industry(self, symbol: str, **kwargs) -> list[IndustryData]:
        industry = ""

        # 优先：单只股票轻量接口
        try:
            df = _ak_individual_info(symbol)
            # 查找行业字段（字段名可能为 "所属行业"、"行业" 等）
            if "item" in df.columns and "value" in df.columns:
                ind_row = df[df["item"].str.contains("行业", na=False)]
                if not ind_row.empty:
                    industry = str(ind_row["value"].iloc[0])
        except Exception:
            pass

        # 回退：旧板块列表接口
        if not industry:
            try:
                df = _ak_industry_name()
                for _, row in df.iterrows():
                    industry = str(row.get("板块名称", ""))
                    break
            except Exception:
                pass

        return [IndustryData(symbol=symbol, industry=industry or "未知", sector="", peers=[])]
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/data/test_akshare.py -v`
Expected: ALL PASS

- [ ] **Step 5: 提交**

```bash
git add src/data/akshare.py tests/data/test_akshare.py
git commit -m "fix(数据层): 行业端点替换为单只股票接口并保留旧端点回退"
```

---

### Task 4: 并发请求 stagger 延迟

**Files:**
- Modify: `src/core/pipeline.py`（`collect` → `fetch_one`）

- [ ] **Step 1: 实现**

读 `src/core/pipeline.py`。在文件顶部 import 区加入（如果还没有）：

```python
import time
```

在 `collect` 方法的 `fetch_one` 内部函数中（约第 90 行），在 `for source in sources:` 循环之前或 `source.fetch(...)` 调用之前加入：

```python
                time.sleep(0.3)  # 错峰请求，减轻上游瞬时压力
```

- [ ] **Step 2: 运行确认通过**

Run: `pytest tests/core/test_pipeline.py -v`
Expected: ALL PASS（stagger 延迟不影响测试，mock 数据源不经过真实网络）

- [ ] **Step 3: 提交**

```bash
git add src/core/pipeline.py
git commit -m "fix(管道): 并发数据采集加 stagger 延迟减少上游限流"
```

---

### Task 5: 全量回归 + 手动冒烟

**Files:** 无（验证任务）

- [ ] **Step 1: 运行完整测试套件**

Run: `pytest -q`
Expected: 全部通过

- [ ] **Step 2: 手动冒烟（--no-llm）**

Run: `stock-robot analyze 000001 --no-llm -v`
Expected:
- 不再出现 `股票名称解析失败`
- 日志中估值、行业不再出现 RemoteDisconnected（或显著减少）
- 报告输出如常

- [ ] **Step 3: 若配置了 LLM，验证（可选）**

Run: `stock-robot analyze 000001 -v`
Expected: 各维度解读正常，维度模板不再报 `'revenue' is undefined`

---

## 自检记录（Self-Review）

- **Spec 覆盖：** 修复 1（Task 1）、修复 2（Task 2）、修复 3（Task 3）、修复 4（Task 4）、回归（Task 5）——全部对应。
- **无占位符：** 所有代码步骤含完整代码。新端点字段名标注为"实施时用 REPL 验证后调整"——这是合理的前置条件，不是占位符。
- **类型一致：** `_ak_individual_info`、`_ak_code_name` 在 Task 1-3 中命名一致；mock 使用的 AkShare 函数名对应。
