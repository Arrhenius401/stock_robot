# 研报管道数据缺失与 LLM 幻觉修复 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 `stock-robot analyze` 的财务解析失败、网络掐断、维度模板拷贝错误、缺失数据无护栏、LLM 幻觉五类问题，让报告有数据支撑、无数据时诚实标注。

**Architecture:** 自底向上分层修复：先补两个纯工具（中文数字解析、网络重试装饰器），再改数据层（Schema 可空 + 财务解析 + 重试 + 降并发），再改分析器 None 护栏，再重写 8 个维度提示词模板并加冒烟测试锁死回归，最后加管道缺失数据护栏、总结防幻觉与报告表格渲染修正。

**Tech Stack:** Python 3.11、Pydantic v2、Jinja2、pytest、pytest-mock。测试导入不带 `src.` 前缀（如 `from data.akshare import ...`），沿用现有 `tests/<layer>/test_*.py` 布局与 `mocker` fixture。

**设计文档:** `docs/superpowers/specs/2026-07-08-report-pipeline-fixes-design.md`

---

## 文件结构

| 文件 | 职责 | 动作 |
|---|---|---|
| `src/utils/numbers.py` | 中文单位数字解析 | 新增 |
| `src/utils/retry.py` | 网络异常重试装饰器 | 新增 |
| `src/data/schemas.py` | `FinancialData` 数值字段可空 | 修改 |
| `src/data/akshare.py` | 财务用解析器、roe 护栏、网络重试 | 修改 |
| `src/core/pipeline.py` | 采集降并发、解读缺失护栏、总结跳过 | 修改 |
| `src/analysis/financial.py` | 同比增长 None 护栏 | 修改 |
| `src/llm/prompt_templates/{technical,valuation,industry,sentiment}_{openai,claude}.jinja2` | 各维度真实内容 + 防幻觉 | 重写（8 个） |
| `src/llm/prompt_templates/financial_{openai,claude}.jinja2` | revenue/net_profit 的 None 守卫 | 修改 |
| `src/llm/prompt_templates/summary_{openai,claude}.jinja2` | 覆盖/缺失维度清单 + 防幻觉 | 修改 |
| `src/report/builder.py` | Jinja `trim_blocks/lstrip_blocks` | 修改 |
| `tests/utils/test_numbers.py`、`tests/utils/test_retry.py`、`tests/data/test_schemas.py`、`tests/data/test_akshare.py`、`tests/analysis/test_financial.py`、`tests/llm/test_prompt_templates.py`、`tests/core/test_pipeline.py`、`tests/report/test_builder.py` | 测试 | 新增/追加 |

---

## Task 1: 中文单位数字解析 `parse_cn_number`

**Files:**
- Create: `src/utils/numbers.py`
- Test: `tests/utils/test_numbers.py`

- [ ] **Step 1: 写失败测试**

Create `tests/utils/test_numbers.py`:

```python
import pytest
from utils.numbers import parse_cn_number


class TestParseCnNumber:
    def test_yi_unit(self):
        assert parse_cn_number("3.54亿") == pytest.approx(3.54e8)

    def test_wan_unit(self):
        assert parse_cn_number("7217.13万") == pytest.approx(7.21713e7)

    def test_wanyi_unit(self):
        assert parse_cn_number("1.2万亿") == pytest.approx(1.2e12)

    def test_negative(self):
        assert parse_cn_number("-3.54亿") == pytest.approx(-3.54e8)

    def test_plain_number_string(self):
        assert parse_cn_number("123.45") == pytest.approx(123.45)

    def test_thousands_separator(self):
        assert parse_cn_number("1,234.5") == pytest.approx(1234.5)

    def test_numeric_input_passthrough(self):
        assert parse_cn_number(100) == 100.0
        assert parse_cn_number(4.5e10) == pytest.approx(4.5e10)

    def test_dash_and_empty_and_none_return_none(self):
        assert parse_cn_number("-") is None
        assert parse_cn_number("--") is None
        assert parse_cn_number("") is None
        assert parse_cn_number(None) is None

    def test_garbage_returns_none(self):
        assert parse_cn_number("abc") is None
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/utils/test_numbers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'utils.numbers'`

- [ ] **Step 3: 实现**

Create `src/utils/numbers.py`:

```python
"""中文单位数字解析 — 将 '3.54亿'、'7217.13万' 等转为 float"""

# 注意：'万亿' 必须排在 '亿' 之前，否则 '1.2万亿' 会误配 '亿'
_UNITS = [("万亿", 1e12), ("亿", 1e8), ("万", 1e4)]
_NULL_TOKENS = {"", "-", "--", "None", "none", "nan", "NaN"}


def parse_cn_number(value) -> float | None:
    """解析带中文单位的数值。无法解析或表示缺失时返回 None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).strip().replace(",", "")
    if s in _NULL_TOKENS:
        return None

    for unit, mult in _UNITS:
        if s.endswith(unit):
            num_part = s[: -len(unit)]
            try:
                return float(num_part) * mult
            except ValueError:
                return None

    try:
        return float(s)
    except ValueError:
        return None
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/utils/test_numbers.py -v`
Expected: PASS（全部用例）

- [ ] **Step 5: 提交**

```bash
git add src/utils/numbers.py tests/utils/test_numbers.py
git commit -m "feat(工具): 添加中文单位数字解析 parse_cn_number"
```

---

## Task 2: 网络重试装饰器 `retry_on_network_error`

**Files:**
- Create: `src/utils/retry.py`
- Test: `tests/utils/test_retry.py`

- [ ] **Step 1: 写失败测试**

Create `tests/utils/test_retry.py`:

```python
import pytest
from http.client import RemoteDisconnected
from utils.retry import retry_on_network_error


def test_retries_then_succeeds(mocker):
    mocker.patch("utils.retry.time.sleep")
    calls = {"n": 0}

    @retry_on_network_error(max_attempts=3, base_delay=0.01)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RemoteDisconnected("boom")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3


def test_gives_up_after_max_attempts(mocker):
    mocker.patch("utils.retry.time.sleep")
    calls = {"n": 0}

    @retry_on_network_error(max_attempts=3, base_delay=0.01)
    def always_fail():
        calls["n"] += 1
        raise RemoteDisconnected("boom")

    with pytest.raises(RemoteDisconnected):
        always_fail()
    assert calls["n"] == 3


def test_non_network_error_not_retried(mocker):
    mocker.patch("utils.retry.time.sleep")
    calls = {"n": 0}

    @retry_on_network_error(max_attempts=3, base_delay=0.01)
    def bad():
        calls["n"] += 1
        raise ValueError("logic")

    with pytest.raises(ValueError):
        bad()
    assert calls["n"] == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/utils/test_retry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'utils.retry'`

- [ ] **Step 3: 实现**

Create `src/utils/retry.py`:

```python
"""网络异常重试装饰器 — 对瞬时连接错误指数退避重试"""
import time
from functools import wraps
from http.client import RemoteDisconnected

import requests

# 仅对这些网络类异常重试；业务/逻辑异常立即抛出
NETWORK_ERRORS = (
    ConnectionError,
    RemoteDisconnected,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


def retry_on_network_error(max_attempts: int = 3, base_delay: float = 0.5):
    """重试装饰器：捕获网络异常，指数退避（base_delay, 2x, 4x...），用尽后抛出。"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except NETWORK_ERRORS:
                    attempt += 1
                    if attempt >= max_attempts:
                        raise
                    time.sleep(base_delay * (2 ** (attempt - 1)))
        return wrapper
    return decorator
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/utils/test_retry.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/utils/retry.py tests/utils/test_retry.py
git commit -m "feat(工具): 添加网络异常重试装饰器 retry_on_network_error"
```

---

## Task 3: `FinancialData` 数值字段改为可空

**Files:**
- Modify: `src/data/schemas.py:6-16`
- Test: `tests/data/test_schemas.py`（追加）

- [ ] **Step 1: 写失败测试**

Append to `tests/data/test_schemas.py`（若无 import 则补上文件头 `from datetime import date` 和 `from data.schemas import FinancialData`）:

```python
def test_financial_data_allows_none_numeric_fields():
    fd = FinancialData(
        symbol="600350",
        fiscal_quarter=date(2025, 12, 31),
        revenue=None,
        net_profit=None,
        total_assets=None,
        total_equity=None,
        operating_cash_flow=None,
    )
    assert fd.revenue is None
    assert fd.net_profit is None
    assert fd.total_equity is None
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/data/test_schemas.py::test_financial_data_allows_none_numeric_fields -v`
Expected: FAIL — `pydantic.ValidationError`（当前字段为非空 `float`）

- [ ] **Step 3: 实现**

Modify `src/data/schemas.py`，将 `FinancialData` 改为:

```python
class FinancialData(BaseModel):
    """单期财务数据"""
    symbol: str
    fiscal_quarter: date
    revenue: float | None = None
    net_profit: float | None = None
    total_assets: float | None = None
    total_equity: float | None = None
    operating_cash_flow: float | None = None
    roe: float | None = None
    gross_margin: float | None = None
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/data/test_schemas.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/data/schemas.py tests/data/test_schemas.py
git commit -m "feat(数据层): FinancialData 数值字段改为可空以反映缺失"
```

---

## Task 4: 财务解析使用 `parse_cn_number` + roe None 护栏

**Files:**
- Modify: `src/data/akshare.py:55-92`
- Test: `tests/data/test_akshare.py`（追加）

- [ ] **Step 1: 写失败测试**

Append to `tests/data/test_akshare.py`（文件顶部补 `import pandas as pd`、`import pytest`；`date`、`AkShareAdapter`、`FinancialData` 已 import）:

```python
def test_fetch_financial_parses_chinese_units(mocker):
    def _mock(symbol):
        return pd.DataFrame({
            "报告期": ["2025-12-31", "2025-09-30"],
            "营业总收入": ["3.54亿", "3.76亿"],
            "净利润": ["7217.13万", "2.08亿"],
            "资产总计": ["1.2万亿", "1.1万亿"],
            "股东权益合计": ["45亿", "44亿"],
            "经营活动现金流量净额": ["12亿", "-3.5亿"],
        })
    mocker.patch("akshare.stock_financial_abstract_ths", side_effect=_mock)
    adapter = AkShareAdapter()
    results = adapter.fetch("600350", data_type="financial")
    assert len(results) == 2  # 行不再被跳过
    assert results[0].revenue == pytest.approx(3.54e8)
    assert results[0].net_profit == pytest.approx(7.21713e7)
    assert results[0].total_assets == pytest.approx(1.2e12)
    assert results[1].operating_cash_flow == pytest.approx(-3.5e8)


def test_fetch_financial_unparseable_becomes_none(mocker):
    def _mock(symbol):
        return pd.DataFrame({
            "报告期": ["2025-12-31"],
            "营业总收入": ["--"],
            "净利润": ["8.5亿"],
            "资产总计": ["500亿"],
            "股东权益合计": ["45亿"],
            "经营活动现金流量净额": ["12亿"],
        })
    mocker.patch("akshare.stock_financial_abstract_ths", side_effect=_mock)
    adapter = AkShareAdapter()
    results = adapter.fetch("600350", data_type="financial")
    assert len(results) == 1  # 缺一个字段不再整行丢弃
    assert results[0].revenue is None
    assert results[0].net_profit == pytest.approx(8.5e8)
    assert results[0].roe == pytest.approx(8.5e8 / 45e8)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/data/test_akshare.py::test_fetch_financial_parses_chinese_units -v`
Expected: FAIL — 当前 `float('3.54亿')` 抛错，整行被跳过，`len(results) == 0`

- [ ] **Step 3: 实现**

在 `src/data/akshare.py` 顶部 import 区加入:

```python
from utils.numbers import parse_cn_number
```

将 `_fetch_financial` 的循环体（第 66-89 行）替换为:

```python
            try:
                period_str = str(periods[i])
                try:
                    fiscal_date = datetime.strptime(period_str, "%Y-%m-%d").date()
                except ValueError:
                    fiscal_date = datetime.strptime(period_str, "%Y%m%d").date()

                equity = parse_cn_number(equities[i]) if i < len(equities) else None
                net_profit = parse_cn_number(profits[i]) if i < len(profits) else None
                revenue = parse_cn_number(revenues[i]) if i < len(revenues) else None

                roe = (net_profit / equity) if (
                    net_profit is not None and equity is not None and equity > 0
                ) else None

                results.append(FinancialData(
                    symbol=symbol,
                    fiscal_quarter=fiscal_date,
                    revenue=revenue,
                    net_profit=net_profit,
                    total_assets=parse_cn_number(assets[i]) if i < len(assets) else None,
                    total_equity=equity,
                    operating_cash_flow=parse_cn_number(cash_flows[i]) if i < len(cash_flows) else None,
                    roe=roe,
                    gross_margin=None,
                ))
            except (ValueError, IndexError, TypeError) as e:
                logger.warning(f"跳过异常财务数据行 {i}: {e}")
```

> 说明：`parse_cn_number` 不再抛 `ValueError`，因此仅日期解析异常等才会触发跳过。

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/data/test_akshare.py -v`
Expected: PASS（含既有 `test_fetch_financial_data`，因 `parse_cn_number(45000000000)==4.5e10`）

- [ ] **Step 5: 提交**

```bash
git add src/data/akshare.py tests/data/test_akshare.py
git commit -m "fix(数据层): 财务数值支持中文单位解析，缺失落为 None 不再整行丢弃"
```

---

## Task 5: 网络重试应用到 AkShare 调用 + 采集降并发

**Files:**
- Modify: `src/data/akshare.py`（新增 4 个装饰过的模块级 helper，并在各 `_fetch_*` 中调用）
- Modify: `src/core/pipeline.py:107`
- Test: `tests/data/test_akshare.py`（追加）

- [ ] **Step 1: 写失败测试**

Append to `tests/data/test_akshare.py`（顶部补 `from http.client import RemoteDisconnected`）:

```python
def test_fetch_price_retries_on_network_error(mocker):
    mocker.patch("utils.retry.time.sleep")
    calls = {"n": 0}

    def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RemoteDisconnected("boom")
        return pd.DataFrame([
            {"日期": "2026-07-01", "开盘": "10.0", "最高": "11.0",
             "最低": "9.5", "收盘": "10.5", "成交量": 1000000},
        ])

    mocker.patch("akshare.stock_zh_a_hist", side_effect=flaky)
    adapter = AkShareAdapter()
    results = adapter.fetch("000001", data_type="price")
    assert calls["n"] == 2  # 第一次失败被重试
    assert len(results) == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/data/test_akshare.py::test_fetch_price_retries_on_network_error -v`
Expected: FAIL — 当前无重试，首次 `RemoteDisconnected` 直接被外层 `fetch` 捕获返回 `[]`，`calls["n"] == 1`

- [ ] **Step 3: 实现**

在 `src/data/akshare.py` import 区加入:

```python
from utils.retry import retry_on_network_error
```

在 `logger = logging.getLogger(__name__)` 之后、`class AkShareAdapter` 之前，新增模块级 helper:

```python
@retry_on_network_error()
def _ak_hist(**kwargs):
    return ak.stock_zh_a_hist(**kwargs)


@retry_on_network_error()
def _ak_spot_em():
    return ak.stock_zh_a_spot_em()


@retry_on_network_error()
def _ak_industry_name():
    return ak.stock_board_industry_name_em()


@retry_on_network_error()
def _ak_news(symbol):
    return ak.stock_news_em(symbol=symbol)
```

将各 `_fetch_*` 内对 `ak.*` 的直接调用替换为 helper:

- `_fetch_price`：`df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")` → `df = _ak_hist(symbol=symbol, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")`
- `_fetch_valuation`：`df = ak.stock_zh_a_spot_em()` → `df = _ak_spot_em()`
- `_fetch_industry`：`df = ak.stock_board_industry_name_em()` → `df = _ak_industry_name()`
- `_fetch_news`：`df = ak.stock_news_em(symbol=symbol)` → `df = _ak_news(symbol)`

> 说明：`_fetch_valuation/_industry/_news` 内部已有 `try/except` 兜底，将重试放在内层调用，可先重试再兜底，保留原有降级行为。

- [ ] **Step 4: 降低采集并发**

Modify `src/core/pipeline.py:107`，将 `collect` 中的:

```python
        with ThreadPoolExecutor(max_workers=5) as executor:
```

改为:

```python
        with ThreadPoolExecutor(max_workers=3) as executor:
```

> 只改 `collect` 的采集并发（网络密集）；`run` 中分析阶段的线程池保持不变。

- [ ] **Step 5: 运行确认通过**

Run: `pytest tests/data/test_akshare.py tests/core/test_pipeline.py -v`
Expected: PASS（新重试测试通过；既有采集/进度测试仍通过）

- [ ] **Step 6: 提交**

```bash
git add src/data/akshare.py src/core/pipeline.py tests/data/test_akshare.py
git commit -m "fix(数据层): AkShare 网络调用加重试退避，采集并发降到 3"
```

---

## Task 6: 财务分析器同比增长 None 护栏

**Files:**
- Modify: `src/analysis/financial.py:31-36`
- Test: `tests/analysis/test_financial.py`（追加）

- [ ] **Step 1: 写失败测试**

Append to `tests/analysis/test_financial.py`:

```python
def test_analyze_tolerates_none_revenue():
    financials = [
        FinancialData(symbol="600350", fiscal_quarter=date(2025, 12, 31),
                      revenue=None, net_profit=8.5e8, total_assets=None,
                      total_equity=45e8, operating_cash_flow=None, roe=0.18),
        FinancialData(symbol="600350", fiscal_quarter=date(2024, 12, 31),
                      revenue=None, net_profit=7.0e8, total_assets=None,
                      total_equity=43e8, operating_cash_flow=None, roe=0.16),
    ]
    ctx = AnalysisContext(symbol="600350", name="山东高速", financial_data=financials)
    result = FinancialAnalyzer().analyze(ctx)
    assert result.status in ("ok", "partial")          # 不再抛异常
    assert "revenue_growth_yoy" not in result.metrics   # revenue 为 None 时跳过
    assert "profit_growth_yoy" in result.metrics         # net_profit 可算
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/analysis/test_financial.py::test_analyze_tolerates_none_revenue -v`
Expected: FAIL — `TypeError: '>' not supported between 'NoneType' and 'int'`（`if prev_year.revenue > 0`）

- [ ] **Step 3: 实现**

Modify `src/analysis/financial.py`，将第 31-36 行替换为:

```python
        if len(sorted_data) >= 2:
            prev_year = sorted_data[-1] if len(sorted_data) >= 5 else sorted_data[1]
            if (latest.revenue is not None and prev_year.revenue is not None
                    and prev_year.revenue > 0):
                metrics["revenue_growth_yoy"] = round((latest.revenue - prev_year.revenue) / prev_year.revenue, 4)
            if (latest.net_profit is not None and prev_year.net_profit is not None
                    and prev_year.net_profit > 0):
                metrics["profit_growth_yoy"] = round((latest.net_profit - prev_year.net_profit) / prev_year.net_profit, 4)
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/analysis/test_financial.py -v`
Expected: PASS（含既有 `test_full_data_analysis`）

- [ ] **Step 5: 提交**

```bash
git add src/analysis/financial.py tests/analysis/test_financial.py
git commit -m "fix(分析模块): 财务同比增长计算容忍可空 revenue/net_profit"
```

---

## Task 7: 重写 8 个维度模板 + 财务模板 None 守卫 + 冒烟测试

**Files:**
- Rewrite: `src/llm/prompt_templates/technical_openai.jinja2`、`technical_claude.jinja2`、`valuation_openai.jinja2`、`valuation_claude.jinja2`、`industry_openai.jinja2`、`industry_claude.jinja2`、`sentiment_openai.jinja2`、`sentiment_claude.jinja2`
- Modify: `src/llm/prompt_templates/financial_openai.jinja2`、`financial_claude.jinja2`
- Test: `tests/llm/test_prompt_templates.py`（新增）

- [ ] **Step 1: 写失败测试**

Create `tests/llm/test_prompt_templates.py`:

```python
from pathlib import Path
import pytest
from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src" / "llm" / "prompt_templates"

# 每个维度对应其 analyzer 真实产出的 metrics
METRICS = {
    "financial": {
        "latest_quarter": "2025-12-31", "revenue": 3.54e8, "net_profit": 7.2e7,
        "roe": 0.18, "revenue_growth_yoy": 0.12, "profit_growth_yoy": -0.05,
        "roe_trend": [{"quarter": "2025-12-31", "roe": 0.18}],
    },
    "technical": {
        "ma5": 10.5, "ma10": 10.2, "ma20": 10.0, "ma60": 9.8,
        "latest_close": 10.7, "price_vs_ma20": 7.0, "avg_volume_5d": 1000000,
        "volume_ratio": 1.3, "macd_dif": 0.12, "macd_dea": 0.08, "macd_bar": 0.08,
    },
    "valuation": {"pe_ttm": 7.5, "pb": 0.85, "ps_ttm": 1.2, "pe_percentile": 30.0},
    "industry": {"industry": "高速公路", "sector": "交通运输", "peers": ["600377", "600020"]},
    "sentiment": {"headline_count": 3, "headlines": ["利好A", "中性B", "利空C"], "date": "2026-07-08"},
}


def _env():
    return Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))


@pytest.mark.parametrize("provider", ["openai", "claude"])
@pytest.mark.parametrize("dimension", list(METRICS))
def test_template_renders_with_real_metrics(dimension, provider):
    template = _env().get_template(f"{dimension}_{provider}.jinja2")
    out = template.render(name="山东高速", symbol="600350", **METRICS[dimension])
    assert out.strip()  # 非空、无异常


@pytest.mark.parametrize("provider", ["openai", "claude"])
@pytest.mark.parametrize("dimension", list(METRICS))
def test_template_renders_when_numeric_metrics_none(dimension, provider):
    # 所有标量指标为 None（列表/字符串保留）也不应抛异常
    metrics = {
        k: (None if isinstance(v, (int, float)) else v)
        for k, v in METRICS[dimension].items()
    }
    template = _env().get_template(f"{dimension}_{provider}.jinja2")
    out = template.render(name="山东高速", symbol="600350", **metrics)
    assert out.strip()
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/llm/test_prompt_templates.py -v`
Expected: FAIL — 例如 `technical_openai` 当前是财务模板拷贝，用 technical metrics 渲染触发 `'revenue' is undefined`

- [ ] **Step 3: 重写 technical 模板**

Overwrite `src/llm/prompt_templates/technical_openai.jinja2`:

```
你是专业的股票分析师。请基于以下技术指标，对 {{ name }}（{{ symbol }}）进行简洁的技术面分析。

{% if latest_close is defined and latest_close is not none %}- 最新收盘价：{{ "%.2f"|format(latest_close) }}{% endif %}
{% if ma5 is defined and ma5 is not none %}- MA5：{{ "%.2f"|format(ma5) }}{% endif %}
{% if ma10 is defined and ma10 is not none %}- MA10：{{ "%.2f"|format(ma10) }}{% endif %}
{% if ma20 is defined and ma20 is not none %}- MA20：{{ "%.2f"|format(ma20) }}{% endif %}
{% if ma60 is defined and ma60 is not none %}- MA60：{{ "%.2f"|format(ma60) }}{% endif %}
{% if price_vs_ma20 is defined and price_vs_ma20 is not none %}- 价格相对 MA20：{{ "%.2f"|format(price_vs_ma20) }}%{% endif %}
{% if volume_ratio is defined and volume_ratio is not none %}- 量比（近5日/前20日）：{{ "%.2f"|format(volume_ratio) }}{% endif %}
{% if macd_dif is defined and macd_dif is not none %}- MACD：DIF {{ "%.4f"|format(macd_dif) }}，DEA {{ "%.4f"|format(macd_dea) }}，柱 {{ "%.4f"|format(macd_bar) }}{% endif %}

请分析：均线多空排列、价格与均线关系、量能变化、MACD 信号。仅基于以上给出的数据分析，不得编造未提供的数字；若数据不足以支撑某项判断，请明确指出。用中文输出，300 字以内。
```

Overwrite `src/llm/prompt_templates/technical_claude.jinja2`:

```
你是一位资深股票分析师。请基于以下技术指标，对 {{ name }}（{{ symbol }}）进行技术面分析。

## 技术指标
| 指标 | 数值 |
|------|------|
{% if latest_close is defined and latest_close is not none %}| 最新收盘价 | {{ "%.2f"|format(latest_close) }} |{% endif %}
{% if ma5 is defined and ma5 is not none %}| MA5 | {{ "%.2f"|format(ma5) }} |{% endif %}
{% if ma20 is defined and ma20 is not none %}| MA20 | {{ "%.2f"|format(ma20) }} |{% endif %}
{% if ma60 is defined and ma60 is not none %}| MA60 | {{ "%.2f"|format(ma60) }} |{% endif %}
{% if price_vs_ma20 is defined and price_vs_ma20 is not none %}| 价格相对 MA20 | {{ "%.2f"|format(price_vs_ma20) }}% |{% endif %}
{% if volume_ratio is defined and volume_ratio is not none %}| 量比 | {{ "%.2f"|format(volume_ratio) }} |{% endif %}
{% if macd_bar is defined and macd_bar is not none %}| MACD 柱 | {{ "%.4f"|format(macd_bar) }} |{% endif %}

请从以下角度分析：
1. 均线系统的多空排列与趋势
2. 价格相对关键均线的位置
3. 量能与量比反映的资金动向
4. MACD 金叉/死叉信号

仅基于以上给出的数据分析，不得编造未提供的数字；若数据不足以支撑某项判断，请明确指出。请用中文输出，控制在 400 字以内。
```

- [ ] **Step 4: 重写 valuation 模板**

Overwrite `src/llm/prompt_templates/valuation_openai.jinja2`:

```
你是专业的股票分析师。请基于以下估值指标，对 {{ name }}（{{ symbol }}）进行简洁的估值分析。

- PE(TTM)：{% if pe_ttm is defined and pe_ttm is not none %}{{ "%.2f"|format(pe_ttm) }}{% else %}暂无{% endif %}
- PB：{% if pb is defined and pb is not none %}{{ "%.2f"|format(pb) }}{% else %}暂无{% endif %}
- PS(TTM)：{% if ps_ttm is defined and ps_ttm is not none %}{{ "%.2f"|format(ps_ttm) }}{% else %}暂无{% endif %}
{% if pe_percentile is defined and pe_percentile is not none %}- PE 历史分位：{{ "%.1f"|format(pe_percentile) }}%{% endif %}

请分析：当前估值的绝对水平、相对历史/行业是否偏高或偏低、投资安全边际。仅基于以上给出的数据分析，不得编造未提供的数字；若数据不足以支撑某项判断，请明确指出。用中文输出，300 字以内。
```

Overwrite `src/llm/prompt_templates/valuation_claude.jinja2`:

```
你是一位资深投资分析师。请基于以下估值指标，对 {{ name }}（{{ symbol }}）进行估值分析。

## 估值指标
| 指标 | 数值 |
|------|------|
| PE(TTM) | {% if pe_ttm is defined and pe_ttm is not none %}{{ "%.2f"|format(pe_ttm) }}{% else %}暂无{% endif %} |
| PB | {% if pb is defined and pb is not none %}{{ "%.2f"|format(pb) }}{% else %}暂无{% endif %} |
| PS(TTM) | {% if ps_ttm is defined and ps_ttm is not none %}{{ "%.2f"|format(ps_ttm) }}{% else %}暂无{% endif %} |
{% if pe_percentile is defined and pe_percentile is not none %}| PE 历史分位 | {{ "%.1f"|format(pe_percentile) }}% |{% endif %}

请从以下角度分析：
1. 各估值指标反映的绝对贵贱
2. 结合行业属性判断估值合理性
3. 潜在的估值修复或压缩空间

仅基于以上给出的数据分析，不得编造未提供的数字；若数据不足以支撑某项判断，请明确指出。请用中文输出，控制在 400 字以内。
```

- [ ] **Step 5: 重写 industry 模板**

Overwrite `src/llm/prompt_templates/industry_openai.jinja2`:

```
你是专业的股票分析师。请基于以下行业信息，对 {{ name }}（{{ symbol }}）进行简洁的行业分析。

- 所属行业：{% if industry is defined and industry %}{{ industry }}{% else %}未知{% endif %}
{% if sector is defined and sector %}- 板块：{{ sector }}{% endif %}
{% if peers is defined and peers %}- 同业公司：{{ peers | join("、") }}{% endif %}

请分析：行业景气度、公司在行业中的地位、与同业相比的相对优势与风险。仅基于以上给出的数据分析，不得编造未提供的数字；若数据不足以支撑某项判断，请明确指出。用中文输出，300 字以内。
```

Overwrite `src/llm/prompt_templates/industry_claude.jinja2`:

```
你是一位资深行业研究员。请基于以下信息，对 {{ name }}（{{ symbol }}）进行行业分析。

## 行业信息
- 所属行业：{% if industry is defined and industry %}{{ industry }}{% else %}未知{% endif %}
{% if sector is defined and sector %}- 板块：{{ sector }}{% endif %}
{% if peers is defined and peers %}- 同业公司：{{ peers | join("、") }}{% endif %}

请从以下角度分析：
1. 行业当前所处的景气周期
2. 公司在产业链中的位置与竞争格局
3. 相对同业公司的优势与潜在风险

仅基于以上给出的数据分析，不得编造未提供的数字；若数据不足以支撑某项判断，请明确指出。请用中文输出，控制在 400 字以内。
```

- [ ] **Step 6: 重写 sentiment 模板**

Overwrite `src/llm/prompt_templates/sentiment_openai.jinja2`:

```
你是专业的股票分析师。请基于以下舆情信息，对 {{ name }}（{{ symbol }}）进行简洁的舆情分析。

- 近期相关新闻数量：{% if headline_count is defined and headline_count is not none %}{{ headline_count }}{% else %}0{% endif %} 条
{% if headlines is defined and headlines %}
近期新闻标题：
{% for h in headlines %}- {{ h }}
{% endfor %}{% endif %}

请分析：新闻热度、消息面倾向（利好/利空/中性）、值得关注的事件。仅基于以上给出的数据分析，不得编造未提供的数字；若数据不足以支撑某项判断，请明确指出。用中文输出，300 字以内。
```

Overwrite `src/llm/prompt_templates/sentiment_claude.jinja2`:

```
你是一位资深舆情分析师。请基于以下信息，对 {{ name }}（{{ symbol }}）进行舆情分析。

## 舆情概览
- 近期相关新闻数量：{% if headline_count is defined and headline_count is not none %}{{ headline_count }}{% else %}0{% endif %} 条
{% if headlines is defined and headlines %}
### 近期新闻标题
{% for h in headlines %}- {{ h }}
{% endfor %}{% endif %}

请从以下角度分析：
1. 新闻数量反映的市场关注度
2. 标题整体的消息面倾向（利好/利空/中性）
3. 需要重点跟踪的潜在事件

仅基于以上给出的数据分析，不得编造未提供的数字；若数据不足以支撑某项判断，请明确指出。请用中文输出，控制在 400 字以内。
```

- [ ] **Step 7: 给 financial 模板加 None 守卫**

Overwrite `src/llm/prompt_templates/financial_openai.jinja2`:

```
你是专业的股票分析师。请基于以下财务数据，对 {{ name }}（{{ symbol }}）进行简洁的分析。

最新季度（{{ latest_quarter }}）：
- 营收：{% if revenue is defined and revenue is not none %}{{ "%.2f"|format(revenue/1e8) }} 亿元{% else %}暂无{% endif %}
- 净利润：{% if net_profit is defined and net_profit is not none %}{{ "%.2f"|format(net_profit/1e8) }} 亿元{% else %}暂无{% endif %}
- ROE：{% if roe %}{{ "%.2f"|format(roe*100) }}%{% else %}暂无{% endif %}
{% if revenue_growth_yoy is defined and revenue_growth_yoy is not none %}
- 营收同比增长：{{ "%.1f"|format(revenue_growth_yoy*100) }}%
{% endif %}
{% if profit_growth_yoy is defined and profit_growth_yoy is not none %}
- 净利润同比增长：{{ "%.1f"|format(profit_growth_yoy*100) }}%
{% endif %}

请分析：营收利润趋势、ROE水平、盈利质量。仅基于以上给出的数据分析，不得编造未提供的数字；若数据不足以支撑某项判断，请明确指出。用中文输出，300字以内。
```

Overwrite `src/llm/prompt_templates/financial_claude.jinja2`:

```
你是一位资深股票分析师。请基于以下财务数据，对 {{ name }}（{{ symbol }}）进行深度分析。

## 最新财务数据（{{ latest_quarter }}）

| 指标 | 数值 |
|------|------|
| 营业收入 | {% if revenue is defined and revenue is not none %}{{ "%.2f"|format(revenue/1e8) }} 亿元{% else %}暂无{% endif %} |
| 净利润 | {% if net_profit is defined and net_profit is not none %}{{ "%.2f"|format(net_profit/1e8) }} 亿元{% else %}暂无{% endif %} |
| ROE | {% if roe %}{{ "%.2f"|format(roe*100) }}%{% else %}暂无{% endif %} |
{% if revenue_growth_yoy is defined and revenue_growth_yoy is not none %}| 营收同比 | {{ "%.1f"|format(revenue_growth_yoy*100) }}% |{% endif %}
{% if profit_growth_yoy is defined and profit_growth_yoy is not none %}| 净利润同比 | {{ "%.1f"|format(profit_growth_yoy*100) }}% |{% endif %}

{% if roe_trend is defined and roe_trend %}
## ROE 趋势
{% for item in roe_trend %}
- {{ item.quarter }}: {{ "%.2f"|format(item.roe*100) }}%
{% endfor %}
{% endif %}

请从以下角度分析：
1. 营收和利润的增长趋势及驱动因素推断
2. ROE 水平评估（杜邦分析视角）
3. 盈利质量（经营现金流与净利润匹配度）
4. 需要关注的风险信号

仅基于以上给出的数据分析，不得编造未提供的数字；若数据不足以支撑某项判断，请明确指出。请用中文输出详细分析，控制在 400 字以内。
```

- [ ] **Step 8: 运行确认通过**

Run: `pytest tests/llm/test_prompt_templates.py -v`
Expected: PASS（全部维度 × provider × 真实/None 两组）

- [ ] **Step 9: 提交**

```bash
git add src/llm/prompt_templates/ tests/llm/test_prompt_templates.py
git commit -m "fix(LLM): 重写各维度提示词模板为对应维度内容并加防幻觉约束"
```

---

## Task 8: 管道解读缺失数据护栏 + 总结跳过

**Files:**
- Modify: `src/core/pipeline.py:176-201`
- Test: `tests/core/test_pipeline.py`（追加）

- [ ] **Step 1: 写失败测试**

Append to `tests/core/test_pipeline.py`（顶部补 `from llm.base import LLMBackend`）:

```python
class CountingLLM(LLMBackend):
    def __init__(self):
        self.calls = []

    @property
    def model_name(self):
        return "fake"

    def generate(self, prompt, **kwargs):
        self.calls.append(prompt)
        return "MOCK解读"


class TestGenerateCommentaryGuards:
    def test_skips_unavailable_dimensions(self):
        reg = Registry()
        llm = CountingLLM()
        reg.register_llm_backend(llm, provider="openai")
        pipeline = Pipeline(registry=reg)
        results = [
            AnalysisResult(dimension="valuation", status="partial", summary="",
                           metrics={"pe_ttm": 7.5, "pb": 0.85, "ps_ttm": 1.2}),
            AnalysisResult(dimension="financial", status="unavailable",
                           summary="财务数据不可用", metrics={}),
        ]
        commentary = pipeline._generate_commentary("600350", "山东高速", results)
        assert commentary["financial"] == ""            # 跳过 LLM
        assert commentary["valuation"] == "MOCK解读"
        assert len(llm.calls) == 2                        # 1 维度 + 1 总结

    def test_skips_summary_when_all_unavailable(self):
        reg = Registry()
        llm = CountingLLM()
        reg.register_llm_backend(llm, provider="openai")
        pipeline = Pipeline(registry=reg)
        results = [
            AnalysisResult(dimension="financial", status="unavailable", summary="x", metrics={}),
            AnalysisResult(dimension="technical", status="unavailable", summary="x", metrics={}),
        ]
        commentary = pipeline._generate_commentary("600350", "山东高速", results)
        assert commentary.get("summary", "") == ""
        assert len(llm.calls) == 0                        # 完全不调用 LLM
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/core/test_pipeline.py::TestGenerateCommentaryGuards -v`
Expected: FAIL — 当前对 unavailable 维度仍会渲染财务模板（空 metrics）触发异常并调用/计数不符；`test_skips_summary_when_all_unavailable` 仍会调用 summary，`len(llm.calls) != 0`

- [ ] **Step 3: 实现**

Modify `src/core/pipeline.py`，将 `_generate_commentary` 中的维度循环（第 176-188 行）替换为:

```python
            for result in results:
                if result.status == "unavailable" or not result.metrics:
                    commentary[result.dimension] = ""
                    completed += 1
                    if on_progress:
                        on_progress("llm", completed, total,
                                   DIMENSION_LLM_LABELS.get(result.dimension, result.dimension))
                    continue

                template_name = f"{result.dimension}_{provider}.jinja2"
                try:
                    template = env.get_template(template_name)
                    prompt = template.render(name=name, symbol=symbol, **result.metrics)
                    commentary[result.dimension] = llm.generate(prompt)
                except Exception as e:
                    logger.warning(f"生成 {result.dimension} 解读失败: {e}")
                    commentary[result.dimension] = ""
                completed += 1
                if on_progress:
                    on_progress("llm", completed, total,
                               DIMENSION_LLM_LABELS.get(result.dimension, result.dimension))
```

将其后的综合总结块（第 190-201 行）替换为:

```python
            # 综合总结：仅当至少一个维度有真实解读时才生成
            has_any = any(commentary.get(r.dimension) for r in results)
            if has_any:
                summary_template_name = f"summary_{provider}.jinja2"
                try:
                    template = env.get_template(summary_template_name)
                    prompt = template.render(name=name, symbol=symbol, commentary=commentary)
                    commentary["summary"] = llm.generate(prompt)
                except Exception as e:
                    logger.warning(f"生成综合总结失败: {e}")
                    commentary["summary"] = ""
            else:
                commentary["summary"] = ""
            completed += 1
            if on_progress:
                on_progress("llm", completed, total, "生成综合总结")
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/core/test_pipeline.py -v`
Expected: PASS（含既有管道/进度测试）

- [ ] **Step 5: 提交**

```bash
git add src/core/pipeline.py tests/core/test_pipeline.py
git commit -m "fix(管道): 不可用维度跳过 LLM，全维度缺失时不生成总结"
```

---

## Task 9: 综合总结模板防幻觉

**Files:**
- Modify: `src/llm/prompt_templates/summary_openai.jinja2`、`summary_claude.jinja2`
- Test: `tests/llm/test_prompt_templates.py`（追加）

- [ ] **Step 1: 写失败测试**

Append to `tests/llm/test_prompt_templates.py`:

```python
@pytest.mark.parametrize("provider", ["openai", "claude"])
def test_summary_template_lists_covered_and_missing(provider):
    template = _env().get_template(f"summary_{provider}.jinja2")
    out = template.render(name="山东高速", symbol="600350",
                          commentary={"valuation": "估值解读内容"})
    assert "估值" in out       # 覆盖维度列出
    assert "缺失" in out       # 有缺失维度提示
    assert "估值解读内容" in out
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/llm/test_prompt_templates.py::test_summary_template_lists_covered_and_missing -v`
Expected: FAIL — 当前 summary 模板无“缺失”字样

- [ ] **Step 3: 实现**

Overwrite `src/llm/prompt_templates/summary_openai.jinja2`:

```
你是资深投资分析师。请基于以下各维度分析，为 {{ name }}（{{ symbol }}）撰写综合总结。

{% set dims = [("financial","财务"),("technical","技术面"),("valuation","估值"),("industry","行业"),("sentiment","舆情")] %}
已覆盖维度：{% for k, label in dims %}{% if commentary.get(k) %}{{ label }} {% endif %}{% endfor %}
缺失维度：{% for k, label in dims %}{% if not commentary.get(k) %}{{ label }} {% endif %}{% endfor %}

{% if commentary.get("financial") %}
## 财务分析
{{ commentary["financial"] }}
{% endif %}
{% if commentary.get("technical") %}
## 技术面分析
{{ commentary["technical"] }}
{% endif %}
{% if commentary.get("valuation") %}
## 估值分析
{{ commentary["valuation"] }}
{% endif %}
{% if commentary.get("industry") %}
## 行业分析
{{ commentary["industry"] }}
{% endif %}
{% if commentary.get("sentiment") %}
## 舆情分析
{{ commentary["sentiment"] }}
{% endif %}

请给出：
1. 综合评级（积极/中性/谨慎）
2. 核心逻辑（2-3 句话）
3. 主要风险
4. 需要关注的关键指标

约束：仅基于上方各维度解读撰写，不得引入未提供的数据或杜撰具体数字；对“缺失维度”不做臆测，如整体信息不足请直言“数据不足，结论仅供参考”。用中文输出，300 字以内。
```

Overwrite `src/llm/prompt_templates/summary_claude.jinja2`:

```
你是一位资深投资分析师。请基于以下各维度分析，为 {{ name }}（{{ symbol }}）撰写一份综合投资研判。

{% set dims = [("financial","财务"),("technical","技术面"),("valuation","估值"),("industry","行业"),("sentiment","舆情")] %}
> 已覆盖维度：{% for k, label in dims %}{% if commentary.get(k) %}{{ label }} {% endif %}{% endfor %}
> 缺失维度：{% for k, label in dims %}{% if not commentary.get(k) %}{{ label }} {% endif %}{% endfor %}

{% if commentary.get("financial") %}
## 财务分析
{{ commentary["financial"] }}
{% endif %}
{% if commentary.get("technical") %}
## 技术面分析
{{ commentary["technical"] }}
{% endif %}
{% if commentary.get("valuation") %}
## 估值分析
{{ commentary["valuation"] }}
{% endif %}
{% if commentary.get("industry") %}
## 行业分析
{{ commentary["industry"] }}
{% endif %}
{% if commentary.get("sentiment") %}
## 舆情分析
{{ commentary["sentiment"] }}
{% endif %}

请给出：
1. 综合评级（积极 / 中性 / 谨慎）
2. 核心投资逻辑
3. 主要风险
4. 需要重点跟踪的关键指标

约束：仅基于上方各维度解读撰写，不得引入未提供的数据或杜撰具体数字；对“缺失维度”不做臆测，如整体信息不足请直言“数据不足，结论仅供参考”。请用中文输出，控制在 400 字以内。
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/llm/test_prompt_templates.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/llm/prompt_templates/summary_openai.jinja2 src/llm/prompt_templates/summary_claude.jinja2 tests/llm/test_prompt_templates.py
git commit -m "fix(LLM): 综合总结列出覆盖/缺失维度并加防幻觉约束"
```

---

## Task 10: 报告表格渲染修正（`trim_blocks`）

**Files:**
- Modify: `src/report/builder.py:11`
- Test: `tests/report/test_builder.py`（追加）

- [ ] **Step 1: 写失败测试**

Append to `tests/report/test_builder.py`:

```python
def test_table_header_and_rows_are_contiguous():
    from report.builder import ReportBuilder
    results = [
        AnalysisResult(dimension="valuation", status="partial", summary="",
                       metrics={"pe_ttm": 7.5, "pb": 0.85, "ps_ttm": 1.2}),
    ]
    report = ReportBuilder().build("600350", "山东高速", results, commentary={})
    # 表头、分隔线、首行之间无空行，Markdown 表格才能正确渲染
    assert "| 指标 | 数值 |\n|------|------|\n| pe_ttm | 7.5 |" in report
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/report/test_builder.py::test_table_header_and_rows_are_contiguous -v`
Expected: FAIL — 当前 `{% for %}` 前后残留换行，分隔线与首行间出现空行

- [ ] **Step 3: 实现**

Modify `src/report/builder.py` 第 10-11 行，将:

```python
        template_dir = Path(__file__).parent / "templates"
        self._env = Environment(loader=FileSystemLoader(str(template_dir)))
```

改为:

```python
        template_dir = Path(__file__).parent / "templates"
        self._env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            trim_blocks=True,
            lstrip_blocks=True,
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/report/test_builder.py -v`
Expected: PASS（含既有 4 个 builder 测试）

- [ ] **Step 5: 提交**

```bash
git add src/report/builder.py tests/report/test_builder.py
git commit -m "fix(报告): 开启 Jinja trim_blocks 修正 Markdown 表格折行"
```

---

## Task 11: 全量回归与手动验证

**Files:** 无（验证任务）

- [ ] **Step 1: 运行完整测试套件**

Run: `pytest -q`
Expected: 全部通过，无失败、无新增警告导致的错误

- [ ] **Step 2: 手动冒烟（需网络与已配置 LLM，或用 `--no-llm`）**

Run: `stock-robot analyze sh600350 --no-llm -v`
Expected:
- 财务分析区块出现真实数值（营收/净利润按亿元显示），不再“财务数据暂时不可用”
- 估值/行业表格正常成表，不再折行
- 日志中不再出现 `could not convert string to float`

- [ ] **Step 3: 若配置了 LLM，验证解读与总结**

Run: `stock-robot analyze sh600350 -v`
Expected:
- 五个维度各自的解读针对本维度（技术面讲均线/MACD，估值讲 PE/PB，而非都在讲营收）
- 日志中不再出现 `生成 X 解读失败: 'revenue' is undefined`
- 综合总结引用真实维度内容，缺失维度被诚实标注而非虚构

> 注：若某维度因网络仍失败，报告应显示“XX数据暂时不可用”占位，这是预期的诚实降级。

---

## 自检记录（Self-Review）

- **Spec 覆盖：** 修复 1（Task 1、4）、修复 2（Task 2、5）、修复 3（Task 7）、修复 4（Task 8）、缺陷 5（Task 8、9）、瑕疵 6（Task 10）、Schema 可空（Task 3）、分析器护栏（Task 6）——全部对应任务。
- **无占位符：** 每个代码步骤均含完整代码与确切命令。
- **类型一致：** `parse_cn_number`、`retry_on_network_error`、`_ak_hist/_ak_spot_em/_ak_industry_name/_ak_news`、`CountingLLM`、`_generate_commentary` 在各任务中命名一致；模板变量名与各 analyzer 产出的 metrics 键一致。
