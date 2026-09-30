# 股票分析 AI Agent 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个 CLI 工具，输入 A 股代码，输出包含财务、技术面、估值、行业、舆情五个维度的智能分析报告

**Architecture:** 模块化管道架构。数据采集和分析计算全部由确定性代码完成，LLM 仅在最后一步接收结构化指标生成自然语言解读。各层通过抽象基类解耦，支持新增数据源、分析模块和 LLM 后端。

**Tech Stack:** Python 3.11+, Pydantic v2, click + rich, AkShare + pandas, openai SDK + anthropic SDK, Jinja2, SQLite, pytest

**Spec:** `docs/superpowers/specs/2026-07-02-stock-analysis-agent-design.md`

---

### Task 1: 项目脚手架和配置管理

**Files:**
- Create: `pyproject.toml`
- Create: `src/__init__.py`
- Create: `src/core/__init__.py`
- Create: `src/data/__init__.py`
- Create: `src/analysis/__init__.py`
- Create: `src/llm/__init__.py`
- Create: `src/report/__init__.py`
- Create: `src/utils/__init__.py`
- Create: `src/utils/config.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/utils/__init__.py`
- Create: `tests/utils/test_config.py`
- Create: `README.md`

- [ ] **Step 1: 编写配置管理的失败测试**

```python
# tests/utils/test_config.py
import tempfile
from pathlib import Path
from src.utils.config import Config, DEFAULT_CONFIG


class TestConfig:
    def test_default_config_has_required_sections(self):
        cfg = Config(config_dir=Path("/nonexistent"))
        assert "llm" in cfg.data
        assert "data" in cfg.data
        assert cfg.data["llm"]["enabled"] is True
        assert cfg.data["llm"]["provider"] == "openai"

    def test_load_from_yaml_file(self, tmp_path):
        yaml_content = """
llm:
  provider: claude
  model: claude-opus-4-7
  enabled: false
data:
  cache_ttl: 3600
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_content)
        cfg = Config(config_dir=tmp_path)
        assert cfg.data["llm"]["provider"] == "claude"
        assert cfg.data["llm"]["model"] == "claude-opus-4-7"
        assert cfg.data["data"]["cache_ttl"] == 3600

    def test_get_returns_nested_value(self):
        cfg = Config(config_dir=Path("/nonexistent"))
        assert cfg.get("llm.provider") == "openai"
        assert cfg.get("nonexistent.key", "fallback") == "fallback"

    def test_set_updates_value_and_persists(self, tmp_path):
        cfg = Config(config_dir=tmp_path)
        cfg.set("llm.provider", "claude")
        assert cfg.get("llm.provider") == "claude"
        # Reload from disk
        cfg2 = Config(config_dir=tmp_path)
        assert cfg2.get("llm.provider") == "claude"

    def test_first_run_creates_default_config(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        assert not config_file.exists()
        Config(config_dir=tmp_path)
        assert config_file.exists()

    def test_disclaimer_flag_defaults_to_false(self):
        cfg = Config(config_dir=Path("/nonexistent"))
        assert cfg.data["data"]["disclaimer_accepted"] is False
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd D:/code/stock_robot && python -m pytest tests/utils/test_config.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'src.utils.config'`

- [ ] **Step 3: 创建 pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "stock-robot"
version = "0.1.0"
description = "AI-powered stock analysis report assistant"
requires-python = ">=3.11"
dependencies = [
    "click>=8.1",
    "rich>=13.0",
    "pydantic>=2.0",
    "pyyaml>=6.0",
    "akshare>=1.14",
    "pandas>=2.0",
    "jinja2>=3.1",
    "openai>=1.0",
    "anthropic>=0.30",
]

[project.scripts]
stock-robot = "cli:main"

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-mock>=3.12",
]
```

- [ ] **Step 4: 创建所有 `__init__.py` 文件（均为空文件）**

- [ ] **Step 5: 实现 Config 类**

```python
# src/utils/config.py
from pathlib import Path
import shutil
import yaml


DEFAULT_CONFIG = {
    "llm": {
        "provider": "openai",
        "model": "gpt-4o",
        "enabled": True,
        "api_key": "",
        "temperature": 0.3,
        "max_tokens": 2000,
    },
    "data": {
        "cache_ttl": {
            "daily": 86400,
            "quarterly": 604800,
            "news": 21600,
        },
        "disclaimer_accepted": False,
    },
}


class Config:
    def __init__(self, config_dir: Path | None = None):
        if config_dir is None:
            config_dir = Path.home() / ".stock_robot"
        self._config_dir = Path(config_dir)
        self._config_dir.mkdir(parents=True, exist_ok=True)
        self._config_path = self._config_dir / "config.yaml"
        self.data = self._load()

    def _load(self) -> dict:
        if not self._config_path.exists():
            self._write_default()
            return self._deep_copy(DEFAULT_CONFIG)
        with open(self._config_path, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}
        merged = self._deep_copy(DEFAULT_CONFIG)
        self._merge(merged, user_config)
        return merged

    def _write_default(self):
        with open(self._config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(DEFAULT_CONFIG, f, allow_unicode=True, default_flow_style=False)

    def _persist(self):
        with open(self._config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.data, f, allow_unicode=True, default_flow_style=False)

    def get(self, key: str, default=None):
        keys = key.split(".")
        node = self.data
        for k in keys:
            if isinstance(node, dict) and k in node:
                node = node[k]
            else:
                return default
        return node

    def set(self, key: str, value):
        keys = key.split(".")
        node = self.data
        for k in keys[:-1]:
            if k not in node:
                node[k] = {}
            node = node[k]
        node[keys[-1]] = value
        self._persist()

    def get_llm_config(self) -> dict:
        return {k: v for k, v in self.data["llm"].items() if k != "api_key"}

    @property
    def config_dir(self) -> Path:
        return self._config_dir

    @staticmethod
    def _deep_copy(d: dict) -> dict:
        import copy
        return copy.deepcopy(d)

    @staticmethod
    def _merge(base: dict, override: dict):
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                Config._merge(base[key], value)
            else:
                base[key] = value
```

- [ ] **Step 6: 运行测试确认通过**

```bash
cd D:/code/stock_robot && python -m pytest tests/utils/test_config.py -v
```
Expected: PASS (6 tests)

- [ ] **Step 7: 创建 README.md**

```markdown
# Stock Robot

AI-powered stock research report assistant.

## 免责声明

本工具仅用于个人学习和研究目的。所有分析数据和观点不构成任何投资建议。股票投资有风险，入市需谨慎。数据来源的准确性和时效性无法保证，使用者需自行判断。

## 安装

pip install -e ".[dev]"

## 使用

stock-robot analyze 000001
```

- [ ] **Step 8: 安装项目和开发依赖**

```bash
cd D:/code/stock_robot && pip install -e ".[dev]"
```
Expected: 无错误安装成功

- [ ] **Step 9: 提交**

```bash
git add -A
git commit -m "feat: project scaffold with config management"
```

---

### Task 2: 数据 Schema 定义

**Files:**
- Create: `src/data/schemas.py`
- Create: `tests/data/__init__.py`
- Create: `tests/data/test_schemas.py`

- [ ] **Step 1: 编写 Schema 测试**

```python
# tests/data/test_schemas.py
from datetime import date
import pytest
from pydantic import ValidationError
from src.data.schemas import (
    FinancialData,
    PriceData,
    ValuationData,
    IndustryData,
    NewsData,
    AnalysisContext,
    AnalysisResult,
)


class TestFinancialData:
    def test_valid_financial_data(self):
        d = FinancialData(
            symbol="000001",
            fiscal_quarter=date(2025, 12, 31),
            revenue=45_000_000_000.0,
            net_profit=8_500_000_000.0,
            total_assets=500_000_000_000.0,
            total_equity=45_000_000_000.0,
            operating_cash_flow=12_000_000_000.0,
            roe=0.12,
            gross_margin=0.32,
        )
        assert d.symbol == "000001"
        assert d.roe == 0.12

    def test_optional_fields_accept_none(self):
        d = FinancialData(
            symbol="000001",
            fiscal_quarter=date(2025, 12, 31),
            revenue=45_000_000_000.0,
            net_profit=8_500_000_000.0,
            total_assets=500_000_000_000.0,
            total_equity=45_000_000_000.0,
            operating_cash_flow=12_000_000_000.0,
            roe=None,
            gross_margin=None,
        )
        assert d.roe is None

    def test_missing_required_fields_raises_error(self):
        with pytest.raises(ValidationError):
            FinancialData(symbol="000001")


class TestPriceData:
    def test_valid_price_data(self):
        d = PriceData(
            symbol="000001",
            trade_date=date(2026, 7, 1),
            open=12.50,
            high=12.80,
            low=12.30,
            close=12.65,
            volume=50_000_000,
        )
        assert d.close == 12.65

    def test_negative_price_raises_error(self):
        with pytest.raises(ValidationError):
            PriceData(
                symbol="000001",
                trade_date=date(2026, 7, 1),
                open=-12.50,
                high=12.80,
                low=12.30,
                close=12.65,
                volume=50_000_000,
            )


class TestValuationData:
    def test_valid_valuation_data(self):
        d = ValuationData(
            symbol="000001",
            date=date(2026, 7, 1),
            pe_ttm=7.5,
            pb=0.85,
            ps_ttm=1.2,
        )
        assert d.pe_ttm == 7.5

    def test_optional_valuation_metrics(self):
        d = ValuationData(
            symbol="000001",
            date=date(2026, 7, 1),
            pe_ttm=None,
            pb=None,
            ps_ttm=None,
        )
        assert d.pe_ttm is None


class TestIndustryData:
    def test_valid_industry_data(self):
        d = IndustryData(
            symbol="000001",
            industry="银行",
            sector="金融",
            peers=["600036", "601398", "601939"],
        )
        assert d.industry == "银行"
        assert len(d.peers) == 3

    def test_empty_peers_is_valid(self):
        d = IndustryData(
            symbol="000001",
            industry="综合",
            sector="其他",
            peers=[],
        )
        assert d.peers == []


class TestNewsData:
    def test_valid_news_data(self):
        d = NewsData(
            symbol="000001",
            date=date(2026, 7, 1),
            headlines=["平安银行发布2025年度报告", "平安银行获批设立理财子公司"],
        )
        assert len(d.headlines) == 2

    def test_empty_headlines_is_valid(self):
        d = NewsData(
            symbol="000001",
            date=date(2026, 7, 1),
            headlines=[],
        )
        assert d.headlines == []


class TestAnalysisResult:
    def test_ok_status(self):
        r = AnalysisResult(
            dimension="financial",
            status="ok",
            summary="营收同比增长15%，ROE维持高位",
            metrics={"revenue_growth": 0.15, "roe": 0.12},
        )
        assert r.status == "ok"

    def test_partial_status(self):
        r = AnalysisResult(
            dimension="technical",
            status="partial",
            summary="部分技术指标可用",
            metrics={"ma5": 12.5},
        )
        assert r.status == "partial"

    def test_unavailable_status(self):
        r = AnalysisResult(
            dimension="sentiment",
            status="unavailable",
            summary="舆情数据暂时不可用",
            metrics={},
        )
        assert r.status == "unavailable"

    def test_charts_defaults_to_empty_list(self):
        r = AnalysisResult(
            dimension="financial",
            status="ok",
            summary="test",
            metrics={},
        )
        assert r.charts == []


class TestAnalysisContext:
    def test_empty_context(self):
        ctx = AnalysisContext(symbol="000001", name="平安银行")
        assert ctx.symbol == "000001"
        assert ctx.financial_data is None
        assert ctx.price_data is None

    def test_populated_context(self):
        ctx = AnalysisContext(
            symbol="000001",
            name="平安银行",
            financial_data=[
                FinancialData(
                    symbol="000001",
                    fiscal_quarter=date(2025, 12, 31),
                    revenue=45_000_000_000.0,
                    net_profit=8_500_000_000.0,
                    total_assets=500_000_000_000.0,
                    total_equity=45_000_000_000.0,
                    operating_cash_flow=12_000_000_000.0,
                    roe=0.12,
                    gross_margin=0.32,
                )
            ],
        )
        assert len(ctx.financial_data) == 1

    def test_to_dict_serializes_correctly(self):
        ctx = AnalysisContext(symbol="000001", name="平安银行")
        d = ctx.model_dump()
        assert d["symbol"] == "000001"
        assert d["financial_data"] is None
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd D:/code/stock_robot && python -m pytest tests/data/test_schemas.py -v
```
Expected: FAIL

- [ ] **Step 3: 实现 Schema**

```python
# src/data/schemas.py
from datetime import date, datetime
from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator


class FinancialData(BaseModel):
    symbol: str
    fiscal_quarter: date
    revenue: float
    net_profit: float
    total_assets: float
    total_equity: float
    operating_cash_flow: float
    roe: float | None = None
    gross_margin: float | None = None


class PriceData(BaseModel):
    symbol: str
    trade_date: date
    open: float = Field(ge=0)
    high: float = Field(ge=0)
    low: float = Field(ge=0)
    close: float = Field(ge=0)
    volume: int = Field(ge=0)


class ValuationData(BaseModel):
    symbol: str
    date: date
    pe_ttm: float | None = None
    pb: float | None = None
    ps_ttm: float | None = None


class IndustryData(BaseModel):
    symbol: str
    industry: str
    sector: str
    peers: list[str] = Field(default_factory=list)


class NewsData(BaseModel):
    symbol: str
    date: date
    headlines: list[str] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    dimension: Literal["financial", "technical", "valuation", "industry", "sentiment"]
    status: Literal["ok", "partial", "unavailable"]
    summary: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    charts: list[str] = Field(default_factory=list)


class AnalysisContext(BaseModel):
    symbol: str
    name: str
    market: str = "a-shares"
    financial_data: list[FinancialData] | None = None
    price_data: list[PriceData] | None = None
    valuation_data: ValuationData | list[ValuationData] | None = None
    industry_data: IndustryData | None = None
    news_data: NewsData | None = None
    collected_at: datetime = Field(default_factory=datetime.now)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd D:/code/stock_robot && python -m pytest tests/data/test_schemas.py -v
```
Expected: PASS (12 tests)

- [ ] **Step 5: 提交**

```bash
git add tests/data/ src/data/schemas.py
git commit -m "feat: add Pydantic data schemas and AnalysisResult/Context models"
```

---

### Task 3: 抽象基类接口

**Files:**
- Create: `src/data/base.py`
- Create: `src/analysis/base.py`
- Create: `src/llm/base.py`
- Create: `tests/data/test_base.py`
- Create: `tests/analysis/__init__.py`
- Create: `tests/analysis/test_base.py`
- Create: `tests/llm/__init__.py`
- Create: `tests/llm/test_base.py`

- [ ] **Step 1: 编写数据源接口测试**

```python
# tests/data/test_base.py
import pytest
from datetime import date
from src.data.base import DataSource
from src.data.schemas import PriceData


class FakeSource(DataSource):
    def supports(self, market: str, data_type: str) -> bool:
        return market == "a-shares" and data_type == "price"

    def fetch(self, symbol: str, **kwargs) -> list:
        return [
            PriceData(
                symbol=symbol,
                trade_date=date(2026, 7, 1),
                open=10.0,
                high=11.0,
                low=9.5,
                close=10.5,
                volume=1000000,
            )
        ]


class TestDataSource:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            DataSource()

    def test_concrete_implementation_works(self):
        source = FakeSource()
        assert source.supports("a-shares", "price") is True
        assert source.supports("us", "price") is False
        result = source.fetch("000001")
        assert len(result) == 1
        assert result[0].close == 10.5
```

- [ ] **Step 2: 编写分析模块接口测试**

```python
# tests/analysis/test_base.py
import pytest
from src.analysis.base import AnalysisModule
from src.data.schemas import AnalysisContext, AnalysisResult


class FakeModule(AnalysisModule):
    @property
    def dimension(self) -> str:
        return "financial"

    def analyze(self, context: AnalysisContext) -> AnalysisResult:
        return AnalysisResult(
            dimension="financial",
            status="ok",
            summary="test summary",
            metrics={"revenue_growth": 0.15},
        )


class TestAnalysisModule:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            AnalysisModule()

    def test_concrete_implementation_works(self):
        mod = FakeModule()
        assert mod.dimension == "financial"
        ctx = AnalysisContext(symbol="000001", name="测试")
        result = mod.analyze(ctx)
        assert result.status == "ok"
        assert result.dimension == "financial"
```

- [ ] **Step 3: 编写 LLM 后端接口测试**

```python
# tests/llm/test_base.py
import pytest
from src.llm.base import LLMBackend


class FakeLLM(LLMBackend):
    @property
    def model_name(self) -> str:
        return "fake-model"

    def generate(self, prompt: str, **kwargs) -> str:
        return f"Response to: {prompt[:20]}..."


class TestLLMBackend:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            LLMBackend()

    def test_concrete_implementation_works(self):
        llm = FakeLLM()
        assert llm.model_name == "fake-model"
        result = llm.generate("Analyze this stock")
        assert result.startswith("Response to:")
```

- [ ] **Step 4: 运行测试确认失败**

```bash
cd D:/code/stock_robot && python -m pytest tests/data/test_base.py tests/analysis/test_base.py tests/llm/test_base.py -v
```
Expected: FAIL (import errors)

- [ ] **Step 5: 实现三个抽象基类**

```python
# src/data/base.py
from abc import ABC, abstractmethod


class DataSource(ABC):
    @abstractmethod
    def supports(self, market: str, data_type: str) -> bool: ...

    @abstractmethod
    def fetch(self, symbol: str, **kwargs) -> list: ...
```

```python
# src/analysis/base.py
from abc import ABC, abstractmethod
from src.data.schemas import AnalysisContext, AnalysisResult


class AnalysisModule(ABC):
    @property
    @abstractmethod
    def dimension(self) -> str: ...

    @abstractmethod
    def analyze(self, context: AnalysisContext) -> AnalysisResult: ...
```

```python
# src/llm/base.py
from abc import ABC, abstractmethod


class LLMBackend(ABC):
    @property
    @abstractmethod
    def model_name(self) -> str: ...

    @abstractmethod
    def generate(self, prompt: str, **kwargs) -> str: ...
```

- [ ] **Step 6: 运行测试确认通过**

```bash
cd D:/code/stock_robot && python -m pytest tests/data/test_base.py tests/analysis/test_base.py tests/llm/test_base.py -v
```
Expected: PASS (6 tests)

- [ ] **Step 7: 提交**

```bash
git add src/data/base.py src/analysis/base.py src/llm/base.py tests/data/test_base.py tests/analysis/ tests/llm/
git commit -m "feat: add abstract base classes for DataSource, AnalysisModule, LLMBackend"
```

---

### Task 4: SQLite 缓存层

**Files:**
- Create: `src/data/cache.py`
- Create: `tests/data/test_cache.py`

- [ ] **Step 1: 编写缓存层测试**

```python
# tests/data/test_cache.py
import json
import time
from datetime import date, timedelta
from pathlib import Path
from src.data.cache import CacheManager
from src.data.schemas import PriceData


class TestCacheManager:
    def test_put_and_get(self, tmp_path):
        cache = CacheManager(db_path=tmp_path / "cache.db")
        data = PriceData(
            symbol="000001",
            trade_date=date(2026, 7, 1),
            open=10.0, high=11.0, low=9.5, close=10.5, volume=1000000,
        )
        cache.put("price", "000001", "2026-07-01", data.model_dump_json())
        result = cache.get("price", "000001", "2026-07-01")
        assert result is not None
        parsed = json.loads(result)
        assert parsed["close"] == 10.5

    def test_get_nonexistent_key_returns_none(self, tmp_path):
        cache = CacheManager(db_path=tmp_path / "cache.db")
        assert cache.get("price", "000001", "2026-07-01") is None

    def test_get_expired_returns_none(self, tmp_path):
        cache = CacheManager(db_path=tmp_path / "cache.db")
        data = PriceData(
            symbol="000001",
            trade_date=date(2026, 7, 1),
            open=10.0, high=11.0, low=9.5, close=10.5, volume=1000000,
        )
        cache.put("price", "000001", "2026-07-01", data.model_dump_json(), ttl_seconds=0)
        time.sleep(0.1)
        assert cache.get("price", "000001", "2026-07-01") is None

    def test_invalidate_removes_entry(self, tmp_path):
        cache = CacheManager(db_path=tmp_path / "cache.db")
        data = '{"test": true}'
        cache.put("price", "000001", "2026-07-01", data)
        cache.invalidate("price", "000001", "2026-07-01")
        assert cache.get("price", "000001", "2026-07-01") is None

    def test_invalidate_by_symbol(self, tmp_path):
        cache = CacheManager(db_path=tmp_path / "cache.db")
        cache.put("price", "000001", "2026-07-01", '{"a": 1}')
        cache.put("price", "000001", "2026-07-02", '{"a": 2}')
        cache.put("price", "000002", "2026-07-01", '{"a": 3}')
        cache.invalidate_symbol("000001")
        assert cache.get("price", "000001", "2026-07-01") is None
        assert cache.get("price", "000001", "2026-07-02") is None
        assert cache.get("price", "000002", "2026-07-01") is not None

    def test_clear_all(self, tmp_path):
        cache = CacheManager(db_path=tmp_path / "cache.db")
        cache.put("price", "000001", "2026-07-01", '{"a": 1}')
        cache.put("financial", "000001", "2025Q4", '{"a": 2}')
        cache.clear()
        assert cache.get("price", "000001", "2026-07-01") is None
        assert cache.get("financial", "000001", "2025Q4") is None

    def test_stats(self, tmp_path):
        cache = CacheManager(db_path=tmp_path / "cache.db")
        cache.put("price", "000001", "2026-07-01", '{"a": 1}')
        stats = cache.stats()
        assert stats["total_entries"] == 1
        assert stats["db_size_bytes"] > 0

    def test_default_ttl_used_when_not_specified(self, tmp_path):
        cache = CacheManager(db_path=tmp_path / "cache.db", default_ttls={"price": 0})
        cache.put("price", "000001", "2026-07-01", '{"a": 1}')
        time.sleep(0.1)
        assert cache.get("price", "000001", "2026-07-01") is None
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd D:/code/stock_robot && python -m pytest tests/data/test_cache.py -v
```
Expected: FAIL

- [ ] **Step 3: 实现缓存层**

```python
# src/data/cache.py
import json
import sqlite3
import time
from pathlib import Path


class CacheManager:
    def __init__(self, db_path: Path, default_ttls: dict[str, int] | None = None):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._default_ttls = default_ttls or {
            "price": 86400,
            "valuation": 86400,
            "financial": 604800,
            "industry": 604800,
            "news": 21600,
        }
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    data_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    date_key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    ttl_seconds INTEGER NOT NULL,
                    PRIMARY KEY (data_type, symbol, date_key)
                )
            """)

    def get(self, data_type: str, symbol: str, date_key: str) -> str | None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT value, created_at, ttl_seconds FROM cache WHERE data_type=? AND symbol=? AND date_key=?",
                (data_type, symbol, date_key),
            ).fetchone()
            if row is None:
                return None
            value, created_at, ttl_seconds = row
            if ttl_seconds > 0 and (time.time() - created_at) > ttl_seconds:
                self.invalidate(data_type, symbol, date_key)
                return None
            return value

    def put(self, data_type: str, symbol: str, date_key: str, value: str, ttl_seconds: int | None = None):
        if ttl_seconds is None:
            ttl_seconds = self._default_ttls.get(data_type, 86400)
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (data_type, symbol, date_key, value, created_at, ttl_seconds) VALUES (?,?,?,?,?,?)",
                (data_type, symbol, date_key, value, time.time(), ttl_seconds),
            )

    def invalidate(self, data_type: str, symbol: str, date_key: str):
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM cache WHERE data_type=? AND symbol=? AND date_key=?",
                (data_type, symbol, date_key),
            )

    def invalidate_symbol(self, symbol: str):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM cache WHERE symbol=?", (symbol,))

    def clear(self):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM cache")

    def stats(self) -> dict:
        with self._get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
            db_size = self._db_path.stat().st_size if self._db_path.exists() else 0
        return {"total_entries": count, "db_size_bytes": db_size}
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd D:/code/stock_robot && python -m pytest tests/data/test_cache.py -v
```
Expected: PASS (8 tests)

- [ ] **Step 5: 提交**

```bash
git add src/data/cache.py tests/data/test_cache.py
git commit -m "feat: add SQLite cache layer with TTL-based eviction"
```

---

### Task 5: AkShare 数据源适配器

**Files:**
- Create: `src/data/akshare.py`
- Create: `tests/data/test_akshare.py`
- Create: `tests/data/conftest.py`

- [ ] **Step 1: 编写 AkShare 适配器测试**

```python
# tests/data/conftest.py
import pytest
from datetime import date
from src.data.schemas import PriceData, FinancialData, ValuationData, IndustryData, NewsData


@pytest.fixture
def mock_akshare(mocker):
    def _mock_history(symbol, period, start_date, end_date, adjust):
        return [
            {"日期": "2026-07-01", "开盘": "10.0", "最高": "11.0", "最低": "9.5", "收盘": "10.5", "成交量": 1000000},
            {"日期": "2026-06-30", "开盘": "10.2", "最高": "10.8", "最低": "9.8", "收盘": "10.0", "成交量": 800000},
        ]

    def _mock_financial(symbol):
        return {
            "报告期": ["2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31"],
            "营业总收入": [45000000000, 33000000000, 22000000000, 11000000000],
            "净利润": [8500000000, 6200000000, 4100000000, 2000000000],
            "资产总计": [500000000000, 490000000000, 485000000000, 475000000000],
            "股东权益合计": [45000000000, 44000000000, 43500000000, 43000000000],
            "经营活动现金流量净额": [12000000000, 9000000000, 6000000000, 3000000000],
        }

    mocker.patch("akshare.stock_zh_a_hist", side_effect=_mock_history)
    mocker.patch("akshare.stock_financial_abstract_ths", side_effect=_mock_financial)
    return {"history": _mock_history, "financial": _mock_financial}


# tests/data/test_akshare.py
from datetime import date, datetime
import pytest
from src.data.akshare import AkShareAdapter
from src.data.schemas import PriceData, FinancialData


class TestAkShareAdapter:
    def test_supports_a_shares_and_price(self):
        adapter = AkShareAdapter()
        assert adapter.supports("a-shares", "price") is True

    def test_does_not_support_us_market(self):
        adapter = AkShareAdapter()
        assert adapter.supports("us", "price") is False

    def test_fetch_price_data(self, mock_akshare):
        adapter = AkShareAdapter()
        results = adapter.fetch("000001", data_type="price", days=365)
        assert len(results) == 2
        assert isinstance(results[0], PriceData)
        assert results[0].close == 10.5
        assert results[0].symbol == "000001"

    def test_fetch_price_data_without_cache_param(self, mock_akshare):
        adapter = AkShareAdapter()
        results = adapter.fetch("000001", data_type="price")
        assert len(results) == 2

    def test_fetch_financial_data(self, mock_akshare):
        adapter = AkShareAdapter()
        results = adapter.fetch("000001", data_type="financial")
        assert len(results) == 4
        assert isinstance(results[0], FinancialData)
        assert results[0].revenue == 45000000000
        assert results[0].fiscal_quarter == date(2025, 12, 31)

    def test_unsupported_data_type_returns_empty(self):
        adapter = AkShareAdapter()
        results = adapter.fetch("000001", data_type="unknown_type")
        assert results == []

    def test_fetch_with_error_returns_empty(self, mocker):
        mocker.patch("akshare.stock_zh_a_hist", side_effect=Exception("Network error"))
        adapter = AkShareAdapter()
        results = adapter.fetch("000001", data_type="price")
        assert results == []
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd D:/code/stock_robot && python -m pytest tests/data/test_akshare.py -v
```
Expected: FAIL

- [ ] **Step 3: 实现 AkShare 适配器**

```python
# src/data/akshare.py
from datetime import date, datetime, timedelta
import logging
import akshare as ak
from src.data.base import DataSource
from src.data.schemas import PriceData, FinancialData, ValuationData, IndustryData, NewsData

logger = logging.getLogger(__name__)


class AkShareAdapter(DataSource):
    def supports(self, market: str, data_type: str) -> bool:
        return market == "a-shares" and data_type in (
            "price", "financial", "valuation", "industry", "news"
        )

    def fetch(self, symbol: str, **kwargs) -> list:
        data_type = kwargs.get("data_type", "price")
        try:
            method = getattr(self, f"_fetch_{data_type}", None)
            if method is None:
                logger.warning(f"AkShare does not support data_type={data_type}")
                return []
            return method(symbol, **kwargs)
        except Exception as e:
            logger.error(f"AkShare fetch failed for {symbol}/{data_type}: {e}")
            return []

    def _fetch_price(self, symbol: str, **kwargs) -> list[PriceData]:
        days = kwargs.get("days", 365)
        end_date = date.today().strftime("%Y%m%d")
        start_date = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
        results = []
        for _, row in df.iterrows():
            try:
                results.append(PriceData(
                    symbol=symbol,
                    trade_date=datetime.strptime(str(row["日期"]), "%Y-%m-%d").date(),
                    open=float(row["开盘"]),
                    high=float(row["最高"]),
                    low=float(row["最低"]),
                    close=float(row["收盘"]),
                    volume=int(row["成交量"]),
                ))
            except (ValueError, KeyError) as e:
                logger.warning(f"Skip malformed price row: {e}")
        return results

    def _fetch_financial(self, symbol: str, **kwargs) -> list[FinancialData]:
        df = ak.stock_financial_abstract_ths(symbol=symbol)
        results = []
        periods = df.get("报告期", [])
        revenues = df.get("营业总收入", [])
        profits = df.get("净利润", [])
        assets = df.get("资产总计", [])
        equities = df.get("股东权益合计", [])
        cash_flows = df.get("经营活动现金流量净额", [])

        for i in range(min(len(periods), 12)):
            try:
                period_str = str(periods[i])
                try:
                    fiscal_date = datetime.strptime(period_str, "%Y-%m-%d").date()
                except ValueError:
                    fiscal_date = datetime.strptime(period_str, "%Y%m%d").date()

                equity = float(equities[i]) if i < len(equities) and equities[i] is not None else 0.0
                net_profit = float(profits[i]) if i < len(profits) and profits[i] is not None else 0.0
                revenue = float(revenues[i]) if i < len(revenues) and revenues[i] is not None else 0.0

                roe = (net_profit / equity) if equity > 0 else None
                gross_margin = None

                results.append(FinancialData(
                    symbol=symbol,
                    fiscal_quarter=fiscal_date,
                    revenue=revenue,
                    net_profit=net_profit,
                    total_assets=float(assets[i]) if i < len(assets) and assets[i] is not None else 0.0,
                    total_equity=equity,
                    operating_cash_flow=float(cash_flows[i]) if i < len(cash_flows) and cash_flows[i] is not None else 0.0,
                    roe=roe,
                    gross_margin=gross_margin,
                ))
            except (ValueError, IndexError, TypeError) as e:
                logger.warning(f"Skip malformed financial row {i}: {e}")
        return results

    def _fetch_valuation(self, symbol: str, **kwargs) -> list[ValuationData]:
        results = self._fetch_price(symbol, **kwargs)
        try:
            df = ak.stock_zh_a_spot_em()
            row = df[df["代码"] == symbol]
            pe_ttm = float(row["市盈率-动态"].iloc[0]) if not row.empty and row["市盈率-动态"].iloc[0] != "-" else None
            pb = float(row["市净率"].iloc[0]) if not row.empty and row["市净率"].iloc[0] != "-" else None
        except Exception:
            pe_ttm, pb = None, None

        today = date.today()
        return [ValuationData(
            symbol=symbol,
            date=today,
            pe_ttm=pe_ttm,
            pb=pb,
            ps_ttm=None,
        )]

    def _fetch_industry(self, symbol: str, **kwargs) -> list[IndustryData]:
        try:
            df = ak.stock_board_industry_name_em()
            industry = ""
            for _, row in df.iterrows():
                industry = str(row.get("板块名称", ""))
                break
            return [IndustryData(
                symbol=symbol,
                industry=industry or "未知",
                sector="",
                peers=[],
            )]
        except Exception as e:
            logger.warning(f"Industry fetch failed: {e}")
            return [IndustryData(symbol=symbol, industry="未知", sector="", peers=[])]

    def _fetch_news(self, symbol: str, **kwargs) -> list[NewsData]:
        try:
            df = ak.stock_news_em(symbol=symbol)
            headlines = []
            for _, row in df.head(10).iterrows():
                title = str(row.get("标题", "") or row.get("title", ""))
                if title:
                    headlines.append(title)
            return [NewsData(symbol=symbol, date=date.today(), headlines=headlines)]
        except Exception as e:
            logger.warning(f"News fetch failed: {e}")
            return [NewsData(symbol=symbol, date=date.today(), headlines=[])]
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd D:/code/stock_robot && python -m pytest tests/data/test_akshare.py -v
```
Expected: PASS (7 tests)

- [ ] **Step 5: 提交**

```bash
git add src/data/akshare.py tests/data/test_akshare.py tests/data/conftest.py
git commit -m "feat: add AkShare data adapter for A-shares"
```

---

### Task 6: 分析模块 — 财务分析和技术面分析

**Files:**
- Create: `src/analysis/financial.py`
- Create: `src/analysis/technical.py`
- Create: `tests/analysis/test_financial.py`
- Create: `tests/analysis/test_technical.py`

- [ ] **Step 1: 编写财务分析测试**

```python
# tests/analysis/test_financial.py
from datetime import date
from src.analysis.financial import FinancialAnalyzer
from src.data.schemas import AnalysisContext, FinancialData


class TestFinancialAnalyzer:
    def test_dimension_is_financial(self):
        a = FinancialAnalyzer()
        assert a.dimension == "financial"

    def test_full_data_analysis(self):
        financials = [
            FinancialData(symbol="000001", fiscal_quarter=date(2025,12,31), revenue=45e9, net_profit=8.5e9, total_assets=500e9, total_equity=45e9, operating_cash_flow=12e9, roe=0.189, gross_margin=None),
            FinancialData(symbol="000001", fiscal_quarter=date(2025,9,30), revenue=33e9, net_profit=6.2e9, total_assets=490e9, total_equity=44e9, operating_cash_flow=9e9, roe=0.141, gross_margin=None),
            FinancialData(symbol="000001", fiscal_quarter=date(2025,6,30), revenue=22e9, net_profit=4.1e9, total_assets=485e9, total_equity=43.5e9, operating_cash_flow=6e9, roe=0.094, gross_margin=None),
            FinancialData(symbol="000001", fiscal_quarter=date(2025,3,31), revenue=11e9, net_profit=2.0e9, total_assets=475e9, total_equity=43e9, operating_cash_flow=3e9, roe=0.047, gross_margin=None),
            FinancialData(symbol="000001", fiscal_quarter=date(2024,12,31), revenue=42e9, net_profit=7.8e9, total_assets=460e9, total_equity=41e9, operating_cash_flow=11e9, roe=0.190, gross_margin=None),
        ]
        ctx = AnalysisContext(symbol="000001", name="平安银行", financial_data=financials)
        result = FinancialAnalyzer().analyze(ctx)
        assert result.status == "ok"
        assert "revenue_growth_yoy" in result.metrics
        assert "roe_trend" in result.metrics
        assert len(result.metrics["roe_trend"]) > 0

    def test_no_financial_data_returns_unavailable(self):
        ctx = AnalysisContext(symbol="000001", name="测试")
        result = FinancialAnalyzer().analyze(ctx)
        assert result.status == "unavailable"

    def test_single_quarter_returns_partial(self):
        ctx = AnalysisContext(symbol="000001", name="测试", financial_data=[
            FinancialData(symbol="000001", fiscal_quarter=date(2025,12,31), revenue=45e9, net_profit=8.5e9, total_assets=500e9, total_equity=45e9, operating_cash_flow=12e9)
        ])
        result = FinancialAnalyzer().analyze(ctx)
        assert result.status == "partial"
```

- [ ] **Step 2: 编写技术面分析测试**

```python
# tests/analysis/test_technical.py
from datetime import date
from src.analysis.technical import TechnicalAnalyzer
from src.data.schemas import AnalysisContext, PriceData


class TestTechnicalAnalyzer:
    def test_dimension_is_technical(self):
        a = TechnicalAnalyzer()
        assert a.dimension == "technical"

    def test_full_data_analysis(self):
        prices = []
        for i in range(120):
            prices.append(PriceData(
                symbol="000001",
                trade_date=date(2026, 1, 1) + __import__("datetime").timedelta(days=i) if i < 180 else date(2025, 12, 31),
                open=10.0 + i * 0.01,
                high=10.5 + i * 0.01,
                low=9.8 + i * 0.01,
                close=10.3 + i * 0.01,
                volume=5000000 + i * 10000,
            ))
        from datetime import timedelta
        prices = []
        base = date(2026, 1, 5)
        for i in range(120):
            prices.append(PriceData(
                symbol="000001",
                trade_date=base + timedelta(weeks=i//5),
                open=10.0 + i * 0.02,
                high=10.5 + i * 0.02,
                low=9.8 + i * 0.02,
                close=10.3 + i * 0.02,
                volume=5000000 + i * 10000,
            ))
        ctx = AnalysisContext(symbol="000001", name="测试", price_data=prices)
        result = TechnicalAnalyzer().analyze(ctx)
        assert result.status == "ok"
        assert "ma5" in result.metrics
        assert "ma20" in result.metrics
        assert "ma60" in result.metrics
        assert "price_vs_ma20" in result.metrics

    def test_insufficient_price_data_returns_partial(self):
        prices = [PriceData(symbol="000001", trade_date=date(2026,7,1), open=10, high=11, low=9.5, close=10.5, volume=1e6)]
        ctx = AnalysisContext(symbol="000001", name="测试", price_data=prices)
        result = TechnicalAnalyzer().analyze(ctx)
        assert result.status == "partial"

    def test_no_price_data_returns_unavailable(self):
        ctx = AnalysisContext(symbol="000001", name="测试")
        result = TechnicalAnalyzer().analyze(ctx)
        assert result.status == "unavailable"
```

- [ ] **Step 3: 运行测试确认失败**

```bash
cd D:/code/stock_robot && python -m pytest tests/analysis/test_financial.py tests/analysis/test_technical.py -v
```
Expected: FAIL

- [ ] **Step 4: 实现财务分析器**

```python
# src/analysis/financial.py
from src.analysis.base import AnalysisModule
from src.data.schemas import AnalysisContext, AnalysisResult


class FinancialAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> str:
        return "financial"

    def analyze(self, context: AnalysisContext) -> AnalysisResult:
        financials = context.financial_data or []
        if not financials:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="财务数据不可用", metrics={})

        sorted_data = sorted(financials, key=lambda x: x.fiscal_quarter, reverse=True)
        latest = sorted_data[0]

        metrics = {
            "latest_quarter": latest.fiscal_quarter.isoformat(),
            "revenue": latest.revenue,
            "net_profit": latest.net_profit,
            "total_assets": latest.total_assets,
            "total_equity": latest.total_equity,
            "operating_cash_flow": latest.operating_cash_flow,
            "roe": latest.roe,
            "gross_margin": latest.gross_margin,
        }

        if len(sorted_data) >= 2:
            prev_year = sorted_data[-1] if len(sorted_data) >= 5 else sorted_data[1]
            if prev_year.revenue > 0:
                metrics["revenue_growth_yoy"] = round((latest.revenue - prev_year.revenue) / prev_year.revenue, 4)
            if prev_year.net_profit > 0:
                metrics["profit_growth_yoy"] = round((latest.net_profit - prev_year.net_profit) / prev_year.net_profit, 4)

        roe_trend = []
        for d in sorted_data[:8]:
            if d.roe is not None:
                roe_trend.append({"quarter": d.fiscal_quarter.isoformat(), "roe": round(d.roe, 4)})
        metrics["roe_trend"] = roe_trend

        status = "partial" if len(sorted_data) < 3 else "ok"
        summary = self._build_summary(metrics, status)
        return AnalysisResult(dimension=self.dimension, status=status, summary=summary, metrics=metrics)

    def _build_summary(self, metrics: dict, status: str) -> str:
        parts = []
        rev_growth = metrics.get("revenue_growth_yoy")
        if rev_growth is not None:
            direction = "增长" if rev_growth > 0 else "下降"
            parts.append(f"营收同比{direction}{abs(rev_growth)*100:.1f}%")
        profit_growth = metrics.get("profit_growth_yoy")
        if profit_growth is not None:
            direction = "增长" if profit_growth > 0 else "下降"
            parts.append(f"净利润同比{direction}{abs(profit_growth)*100:.1f}%")
        roe = metrics.get("roe")
        if roe is not None:
            parts.append(f"ROE {roe*100:.1f}%")
        return "；".join(parts) if parts else "财务指标数据不足"
```

- [ ] **Step 5: 实现技术面分析器**

```python
# src/analysis/technical.py
from statistics import mean
from src.analysis.base import AnalysisModule
from src.data.schemas import AnalysisContext, AnalysisResult


class TechnicalAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> str:
        return "technical"

    def analyze(self, context: AnalysisContext) -> AnalysisResult:
        prices = context.price_data or []
        if not prices:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="行情数据不可用", metrics={})

        sorted_prices = sorted(prices, key=lambda x: x.trade_date)
        closes = [p.close for p in sorted_prices]

        metrics = {}
        if len(closes) >= 5:
            metrics["ma5"] = round(mean(closes[-5:]), 2)
        if len(closes) >= 10:
            metrics["ma10"] = round(mean(closes[-10:]), 2)
        if len(closes) >= 20:
            metrics["ma20"] = round(mean(closes[-20:]), 2)
        if len(closes) >= 60:
            metrics["ma60"] = round(mean(closes[-60:]), 2)

        latest_close = closes[-1] if closes else 0
        metrics["latest_close"] = latest_close

        if "ma20" in metrics and latest_close > 0:
            metrics["price_vs_ma20"] = round((latest_close - metrics["ma20"]) / metrics["ma20"] * 100, 2)

        if len(closes) >= 5:
            volumes = [p.volume for p in sorted_prices[-5:]]
            metrics["avg_volume_5d"] = int(mean(volumes))
            if len(closes) >= 25:
                prev_volumes = [p.volume for p in sorted_prices[-25:-5]]
                if prev_volumes and mean(prev_volumes) > 0:
                    metrics["volume_ratio"] = round(mean(volumes) / mean(prev_volumes), 2)

        if len(closes) >= 26:
            ema12 = self._ema(closes, 12)
            ema26 = self._ema(closes, 26)
            dif = ema12 - ema26
            dea = self._ema_from_values([dif], 9, dif) if dif else 0
            macd = 2 * (dif - dea)
            metrics["macd_dif"] = round(dif, 4)
            metrics["macd_dea"] = round(dea, 4)
            metrics["macd_bar"] = round(macd, 4)

        status = "partial" if len(closes) < 20 else "ok"
        summary = self._build_summary(metrics, status)
        return AnalysisResult(dimension=self.dimension, status=status, summary=summary, metrics=metrics)

    def _ema(self, data: list[float], period: int) -> float:
        if len(data) < period:
            return data[-1] if data else 0
        multiplier = 2 / (period + 1)
        ema = mean(data[:period])
        for price in data[period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    def _ema_from_values(self, data: list[float], period: int, initial: float) -> float:
        multiplier = 2 / (period + 1)
        ema = initial
        for value in data:
            ema = (value - ema) * multiplier + ema
        return ema

    def _build_summary(self, metrics: dict, status: str) -> str:
        parts = []
        price = metrics.get("latest_close", 0)
        if price:
            parts.append(f"最新价 {price:.2f}")
        vs_ma20 = metrics.get("price_vs_ma20")
        if vs_ma20 is not None:
            position = "上方" if vs_ma20 > 0 else "下方"
            parts.append(f"位于20日均线{position} {abs(vs_ma20):.1f}%")
        return "；".join(parts) if parts else "技术指标数据不足"
```

- [ ] **Step 6: 运行测试确认通过**

```bash
cd D:/code/stock_robot && python -m pytest tests/analysis/test_financial.py tests/analysis/test_technical.py -v
```
Expected: PASS (7 tests)

- [ ] **Step 7: 提交**

```bash
git add src/analysis/financial.py src/analysis/technical.py tests/analysis/test_financial.py tests/analysis/test_technical.py
git commit -m "feat: add financial and technical analysis modules"
```

---

### Task 7: 分析模块 — 估值、行业、舆情分析

**Files:**
- Create: `src/analysis/valuation.py`
- Create: `src/analysis/industry.py`
- Create: `src/analysis/sentiment.py`
- Create: `tests/analysis/test_valuation.py`
- Create: `tests/analysis/test_industry.py`
- Create: `tests/analysis/test_sentiment.py`

- [ ] **Step 1: 编写估值分析测试**

```python
# tests/analysis/test_valuation.py
from datetime import date
from src.analysis.valuation import ValuationAnalyzer
from src.data.schemas import AnalysisContext, ValuationData, PriceData


class TestValuationAnalyzer:
    def test_dimension_is_valuation(self):
        assert ValuationAnalyzer().dimension == "valuation"

    def test_single_valuation(self):
        ctx = AnalysisContext(symbol="000001", name="测试", valuation_data=[
            ValuationData(symbol="000001", date=date(2026,7,1), pe_ttm=7.5, pb=0.85, ps_ttm=1.2)
        ])
        result = ValuationAnalyzer().analyze(ctx)
        assert result.status == "partial"
        assert result.metrics["pe_ttm"] == 7.5
        assert result.metrics["pb"] == 0.85

    def test_no_data_returns_unavailable(self):
        ctx = AnalysisContext(symbol="000001", name="测试")
        result = ValuationAnalyzer().analyze(ctx)
        assert result.status == "unavailable"
```

- [ ] **Step 2: 编写行业分析测试**

```python
# tests/analysis/test_industry.py
from src.analysis.industry import IndustryAnalyzer
from src.data.schemas import AnalysisContext, IndustryData


class TestIndustryAnalyzer:
    def test_dimension_is_industry(self):
        assert IndustryAnalyzer().dimension == "industry"

    def test_with_industry_data(self):
        ctx = AnalysisContext(symbol="000001", name="测试", industry_data=IndustryData(
            symbol="000001", industry="银行", sector="金融", peers=["600036", "601398"]
        ))
        result = IndustryAnalyzer().analyze(ctx)
        assert result.status == "ok"
        assert result.metrics["industry"] == "银行"
        assert len(result.metrics["peers"]) == 2

    def test_no_data_returns_unavailable(self):
        ctx = AnalysisContext(symbol="000001", name="测试")
        result = IndustryAnalyzer().analyze(ctx)
        assert result.status == "unavailable"
```

- [ ] **Step 3: 编写舆情分析测试**

```python
# tests/analysis/test_sentiment.py
from datetime import date
from src.analysis.sentiment import SentimentAnalyzer
from src.data.schemas import AnalysisContext, NewsData


class TestSentimentAnalyzer:
    def test_dimension_is_sentiment(self):
        assert SentimentAnalyzer().dimension == "sentiment"

    def test_with_news_data(self):
        ctx = AnalysisContext(symbol="000001", name="测试", news_data=NewsData(
            symbol="000001", date=date(2026,7,1),
            headlines=["业绩增长超预期", "机构上调目标价", "大股东增持"]
        ))
        result = SentimentAnalyzer().analyze(ctx)
        assert result.status == "ok"
        assert result.metrics["headline_count"] == 3

    def test_empty_headlines_returns_partial(self):
        ctx = AnalysisContext(symbol="000001", name="测试", news_data=NewsData(
            symbol="000001", date=date(2026,7,1), headlines=[]
        ))
        result = SentimentAnalyzer().analyze(ctx)
        assert result.status == "partial"

    def test_no_data_returns_unavailable(self):
        ctx = AnalysisContext(symbol="000001", name="测试")
        result = SentimentAnalyzer().analyze(ctx)
        assert result.status == "unavailable"
```

- [ ] **Step 4: 运行测试确认失败**

```bash
cd D:/code/stock_robot && python -m pytest tests/analysis/test_valuation.py tests/analysis/test_industry.py tests/analysis/test_sentiment.py -v
```
Expected: FAIL

- [ ] **Step 5: 实现三个分析器**

```python
# src/analysis/valuation.py
from src.analysis.base import AnalysisModule
from src.data.schemas import AnalysisContext, AnalysisResult, ValuationData


class ValuationAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> str:
        return "valuation"

    def analyze(self, context: AnalysisContext) -> AnalysisResult:
        val_data = context.valuation_data
        if val_data is None:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="估值数据不可用", metrics={})

        if isinstance(val_data, ValuationData):
            val_list = [val_data]
        else:
            val_list = val_data

        if not val_list:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="估值数据不可用", metrics={})

        latest = val_list[0]
        metrics = {
            "pe_ttm": latest.pe_ttm,
            "pb": latest.pb,
            "ps_ttm": latest.ps_ttm,
        }

        if len(val_list) >= 20:
            pe_history = [v.pe_ttm for v in val_list if v.pe_ttm is not None]
            if pe_history and latest.pe_ttm is not None:
                below = sum(1 for p in pe_history if p < latest.pe_ttm)
                metrics["pe_percentile"] = round(below / len(pe_history) * 100, 1)

        status = "ok" if len(val_list) >= 1 else "partial"
        summary = self._build_summary(metrics)
        return AnalysisResult(dimension=self.dimension, status=status, summary=summary, metrics=metrics)

    def _build_summary(self, metrics: dict) -> str:
        parts = []
        pe = metrics.get("pe_ttm")
        if pe is not None:
            parts.append(f"PE(TTM) {pe:.2f}")
        pb = metrics.get("pb")
        if pb is not None:
            parts.append(f"PB {pb:.2f}")
        pct = metrics.get("pe_percentile")
        if pct is not None:
            parts.append(f"PE处于历史{pct}%分位")
        return "；".join(parts) if parts else "估值数据不足"
```

```python
# src/analysis/industry.py
from src.analysis.base import AnalysisModule
from src.data.schemas import AnalysisContext, AnalysisResult


class IndustryAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> str:
        return "industry"

    def analyze(self, context: AnalysisContext) -> AnalysisResult:
        ind_data = context.industry_data
        if ind_data is None:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="行业数据不可用", metrics={})

        metrics = {
            "industry": ind_data.industry,
            "sector": ind_data.sector,
            "peers": ind_data.peers,
        }
        status = "ok" if ind_data.industry and ind_data.industry != "未知" else "partial"
        summary = f"所属行业: {ind_data.industry}" if status == "ok" else "行业分类数据有限"
        return AnalysisResult(dimension=self.dimension, status=status, summary=summary, metrics=metrics)
```

```python
# src/analysis/sentiment.py
from src.analysis.base import AnalysisModule
from src.data.schemas import AnalysisContext, AnalysisResult


class SentimentAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> str:
        return "sentiment"

    def analyze(self, context: AnalysisContext) -> AnalysisResult:
        news = context.news_data
        if news is None:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="舆情数据不可用", metrics={})

        headlines = news.headlines or []
        metrics = {
            "headline_count": len(headlines),
            "headlines": headlines,
            "date": news.date.isoformat(),
        }

        if len(headlines) == 0:
            status = "partial"
            summary = "近期无相关新闻"
        else:
            status = "ok"
            summary = f"近1日共 {len(headlines)} 条相关新闻"

        return AnalysisResult(dimension=self.dimension, status=status, summary=summary, metrics=metrics)
```

- [ ] **Step 6: 运行测试确认通过**

```bash
cd D:/code/stock_robot && python -m pytest tests/analysis/test_valuation.py tests/analysis/test_industry.py tests/analysis/test_sentiment.py -v
```
Expected: PASS (10 tests)

- [ ] **Step 7: 提交**

```bash
git add src/analysis/valuation.py src/analysis/industry.py src/analysis/sentiment.py tests/analysis/test_valuation.py tests/analysis/test_industry.py tests/analysis/test_sentiment.py
git commit -m "feat: add valuation, industry, and sentiment analysis modules"
```

---

### Task 8: LLM 适配器与提示词模板

**Files:**
- Create: `src/llm/openai.py`
- Create: `src/llm/claude.py`
- Create: `src/llm/usage.py`
- Create: `src/llm/prompt_templates/financial_openai.jinja2`
- Create: `src/llm/prompt_templates/financial_claude.jinja2`
- Create: `src/llm/prompt_templates/technical_openai.jinja2`
- Create: `src/llm/prompt_templates/technical_claude.jinja2`
- Create: `src/llm/prompt_templates/valuation_openai.jinja2`
- Create: `src/llm/prompt_templates/valuation_claude.jinja2`
- Create: `src/llm/prompt_templates/industry_openai.jinja2`
- Create: `src/llm/prompt_templates/industry_claude.jinja2`
- Create: `src/llm/prompt_templates/sentiment_openai.jinja2`
- Create: `src/llm/prompt_templates/sentiment_claude.jinja2`
- Create: `src/llm/prompt_templates/summary_openai.jinja2`
- Create: `src/llm/prompt_templates/summary_claude.jinja2`
- Create: `tests/llm/test_openai.py`
- Create: `tests/llm/test_claude.py`
- Create: `tests/llm/test_usage.py`

- [ ] **Step 1: 编写 LLM 适配器测试**

```python
# tests/llm/test_usage.py
import json
from pathlib import Path
from src.llm.usage import UsageLogger


class TestUsageLogger:
    def test_log_writes_json_line(self, tmp_path):
        log_path = tmp_path / "usage.log"
        logger = UsageLogger(log_path)
        logger.log("gpt-4o", 500, 200, 0.003)
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["model"] == "gpt-4o"
        assert entry["prompt_tokens"] == 500
        assert entry["completion_tokens"] == 200

    def test_get_stats_aggregates_correctly(self, tmp_path):
        log_path = tmp_path / "usage.log"
        logger = UsageLogger(log_path)
        logger.log("gpt-4o", 500, 200, 0.005)
        logger.log("gpt-4o", 300, 100, 0.003)
        stats = logger.get_stats()
        assert stats["total_calls"] == 2
        assert stats["total_prompt_tokens"] == 800
        assert stats["total_completion_tokens"] == 300
```

- [ ] **Step 2: 编写 OpenAI 适配器测试**

```python
# tests/llm/test_openai.py
from unittest.mock import MagicMock
from src.llm.openai import OpenAIAdapter


class TestOpenAIAdapter:
    def test_model_name(self):
        adapter = OpenAIAdapter(api_key="sk-test", model="gpt-4o")
        assert adapter.model_name == "gpt-4o"

    def test_generate_calls_openai(self, mocker):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "分析结果：该股票表现良好"
        mock_response.usage.prompt_tokens = 100
        mock_response.usage.completion_tokens = 50
        mock_client.chat.completions.create.return_value = mock_response
        mocker.patch("src.llm.openai.OpenAI", return_value=mock_client)

        adapter = OpenAIAdapter(api_key="sk-test", model="gpt-4o")
        result = adapter.generate("分析平安银行")

        assert "分析结果" in result
        mock_client.chat.completions.create.assert_called_once()
        call_args = mock_client.chat.completions.create.call_args.kwargs
        assert call_args["model"] == "gpt-4o"
        assert call_args["temperature"] == 0.3

    def test_generate_with_error_returns_data_only_message(self, mocker):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API Error")
        mocker.patch("src.llm.openai.OpenAI", return_value=mock_client)

        adapter = OpenAIAdapter(api_key="sk-test", model="gpt-4o")
        result = adapter.generate("分析")
        assert "LLM 分析暂时不可用" in result
```

- [ ] **Step 3: 编写 Claude 适配器测试**

```python
# tests/llm/test_claude.py
from unittest.mock import MagicMock
from src.llm.claude import ClaudeAdapter


class TestClaudeAdapter:
    def test_model_name(self):
        adapter = ClaudeAdapter(api_key="sk-ant-test", model="claude-sonnet-4-6")
        assert adapter.model_name == "claude-sonnet-4-6"

    def test_generate_calls_anthropic(self, mocker):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = "分析结果：该股票技术面偏多"
        mock_response.usage.input_tokens = 120
        mock_response.usage.output_tokens = 60
        mock_client.messages.create.return_value = mock_response
        mocker.patch("src.llm.claude.Anthropic", return_value=mock_client)

        adapter = ClaudeAdapter(api_key="sk-ant-test", model="claude-sonnet-4-6")
        result = adapter.generate("分析平安银行")

        assert "分析结果" in result
        mock_client.messages.create.assert_called_once()

    def test_generate_with_error_returns_fallback(self, mocker):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API Error")
        mocker.patch("src.llm.claude.Anthropic", return_value=mock_client)

        adapter = ClaudeAdapter(api_key="sk-ant-test")
        result = adapter.generate("分析")
        assert "LLM 分析暂时不可用" in result
```

- [ ] **Step 4: 运行测试确认失败**

```bash
cd D:/code/stock_robot && python -m pytest tests/llm/ -v
```
Expected: FAIL

- [ ] **Step 5: 实现 UsageLogger**

```python
# src/llm/usage.py
import json
import time
from pathlib import Path


class UsageLogger:
    def __init__(self, log_path: Path):
        self._log_path = Path(log_path)
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, model: str, prompt_tokens: int, completion_tokens: int, cost_estimate: float = 0.0):
        entry = {
            "timestamp": time.time(),
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost_estimate": cost_estimate,
        }
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_stats(self) -> dict:
        if not self._log_path.exists():
            return {"total_calls": 0, "total_prompt_tokens": 0, "total_completion_tokens": 0}
        calls = 0
        prompt_total = 0
        completion_total = 0
        with open(self._log_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    entry = json.loads(line)
                    calls += 1
                    prompt_total += entry.get("prompt_tokens", 0)
                    completion_total += entry.get("completion_tokens", 0)
        return {
            "total_calls": calls,
            "total_prompt_tokens": prompt_total,
            "total_completion_tokens": completion_total,
        }
```

- [ ] **Step 6: 实现 OpenAI 适配器**

```python
# src/llm/openai.py
import logging
from openai import OpenAI
from src.llm.base import LLMBackend

logger = logging.getLogger(__name__)


class OpenAIAdapter(LLMBackend):
    def __init__(self, api_key: str, model: str = "gpt-4o", temperature: float = 0.3,
                 max_tokens: int = 2000):
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._client = OpenAI(api_key=api_key)

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, prompt: str, system: str | None = None, **kwargs) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=kwargs.get("temperature", self._temperature),
                max_tokens=kwargs.get("max_tokens", self._max_tokens),
            )
            content = response.choices[0].message.content or ""
            usage = response.usage
            if usage:
                self._log_usage(usage.prompt_tokens, usage.completion_tokens)
            return content
        except Exception as e:
            logger.error(f"OpenAI API call failed: {e}")
            return "（LLM 分析暂时不可用，请检查 API 配置）"

    def _log_usage(self, prompt_tokens: int, completion_tokens: int):
        try:
            cost = self._estimate_cost(prompt_tokens, completion_tokens)
            from src.llm.usage import UsageLogger
            from pathlib import Path
            log_path = Path.home() / ".stock_robot" / "usage.log"
            UsageLogger(log_path).log(self._model, prompt_tokens, completion_tokens, cost)
        except Exception:
            pass

    def _estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        pricing = {"gpt-4o": (2.50 / 1_000_000, 10.00 / 1_000_000),
                    "gpt-4o-mini": (0.15 / 1_000_000, 0.60 / 1_000_000)}
        input_price, output_price = pricing.get(self._model, (0, 0))
        return prompt_tokens * input_price + completion_tokens * output_price
```

- [ ] **Step 7: 实现 Claude 适配器**

```python
# src/llm/claude.py
import logging
from anthropic import Anthropic
from src.llm.base import LLMBackend

logger = logging.getLogger(__name__)


class ClaudeAdapter(LLMBackend):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6",
                 temperature: float = 0.3, max_tokens: int = 2000):
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._client = Anthropic(api_key=api_key)

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, prompt: str, system: str | None = None, **kwargs) -> str:
        try:
            kwargs_dict = {
                "model": self._model,
                "max_tokens": kwargs.get("max_tokens", self._max_tokens),
                "temperature": kwargs.get("temperature", self._temperature),
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                kwargs_dict["system"] = system

            response = self._client.messages.create(**kwargs_dict)
            content = ""
            for block in response.content:
                if hasattr(block, "text"):
                    content += block.text

            usage = response.usage
            if usage:
                self._log_usage(usage.input_tokens, usage.output_tokens)
            return content
        except Exception as e:
            logger.error(f"Claude API call failed: {e}")
            return "（LLM 分析暂时不可用，请检查 API 配置）"

    def _log_usage(self, prompt_tokens: int, completion_tokens: int):
        try:
            cost = self._estimate_cost(prompt_tokens, completion_tokens)
            from src.llm.usage import UsageLogger
            from pathlib import Path
            log_path = Path.home() / ".stock_robot" / "usage.log"
            UsageLogger(log_path).log(self._model, prompt_tokens, completion_tokens, cost)
        except Exception:
            pass

    def _estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        pricing = {
            "claude-opus-4-7": (15.00 / 1_000_000, 75.00 / 1_000_000),
            "claude-sonnet-4-6": (3.00 / 1_000_000, 15.00 / 1_000_000),
            "claude-haiku-4-5-20251001": (0.80 / 1_000_000, 4.00 / 1_000_000),
        }
        input_price, output_price = pricing.get(self._model, (0, 0))
        return prompt_tokens * input_price + completion_tokens * output_price
```

- [ ] **Step 8: 创建提示词模板文件**

每个模板创建为 Jinja2 文件。示例 `financial_openai.jinja2`：

```jinja2
你是一位专业的股票分析师。请基于以下财务数据，对 {{ name }}（{{ symbol }}）进行简洁专业的财务分析。

最新财务数据（{{ latest_quarter }}）：
- 营业收入：{{ "%.2f"|format(revenue/1e8) }} 亿元
- 净利润：{{ "%.2f"|format(net_profit/1e8) }} 亿元
- 总资产：{{ "%.2f"|format(total_assets/1e8) }} 亿元
- ROE：{{ "%.2f"|format(roe*100) if roe else "暂无" }}%
{% if revenue_growth_yoy is not none %}
- 营收同比增长：{{ "%.1f"|format(revenue_growth_yoy*100) }}%
{% endif %}
{% if profit_growth_yoy is not none %}
- 净利润同比增长：{{ "%.1f"|format(profit_growth_yoy*100) }}%
{% endif %}

ROE 趋势（近几个季度）：
{% for item in roe_trend %}
- {{ item.quarter }}: {{ "%.2f"|format(item.roe*100) }}%
{% endfor %}

请分析：
1. 营收和利润的增长趋势
2. ROE 水平和变化趋势
3. 盈利质量（结合经营现金流）
4. 需要关注的风险点

请用中文输出，控制在 300 字以内。
```

类似地创建其余 11 个模板文件，每个按对应维度调整内容。完整模板内容见下方。

- [ ] **Step 9: 运行测试确认通过**

```bash
cd D:/code/stock_robot && python -m pytest tests/llm/ -v
```
Expected: PASS (7 tests)

- [ ] **Step 10: 提交**

```bash
git add src/llm/ tests/llm/
git commit -m "feat: add OpenAI and Claude LLM adapters with prompt templates"
```

---

### Task 9: 报告构建器

**Files:**
- Create: `src/report/templates/report.jinja2`
- Create: `src/report/builder.py`
- Create: `src/report/formatter.py`
- Create: `tests/report/__init__.py`
- Create: `tests/report/test_builder.py`
- Create: `tests/report/test_formatter.py`

- [ ] **Step 1: 编写报告构建器测试**

```python
# tests/report/test_builder.py
from datetime import datetime
from src.report.builder import ReportBuilder
from src.data.schemas import AnalysisResult


class TestReportBuilder:
    def test_build_full_report(self):
        results = [
            AnalysisResult(dimension="financial", status="ok",
                           summary="营收增长15%，ROE稳健",
                           metrics={"revenue_growth_yoy": 0.15, "roe": 0.12}),
            AnalysisResult(dimension="technical", status="ok",
                           summary="价格位于20日均线上方3.2%",
                           metrics={"latest_close": 12.5, "ma20": 12.1}),
            AnalysisResult(dimension="valuation", status="partial",
                           summary="PE(TTM) 7.50",
                           metrics={"pe_ttm": 7.5, "pb": 0.85}),
            AnalysisResult(dimension="industry", status="ok",
                           summary="所属行业: 银行",
                           metrics={"industry": "银行"}),
            AnalysisResult(dimension="sentiment", status="ok",
                           summary="近1日共 3 条相关新闻",
                           metrics={"headline_count": 3}),
        ]
        builder = ReportBuilder()
        report = builder.build("000001", "平安银行", results, commentary={
            "financial": "财务表现稳健。",
            "technical": "技术面偏多。",
            "valuation": "估值合理。",
            "industry": "行业地位稳固。",
            "sentiment": "舆情偏正面。",
            "summary": "综合来看，该公司基本面扎实。",
        })
        assert "# 平安银行（000001）分析报告" in report
        assert "## 财务分析" in report
        assert "## 技术面分析" in report
        assert "## 估值分析" in report
        assert "## 行业分析" in report
        assert "## 舆情分析" in report
        assert "## 综合总结" in report
        assert "免责声明" in report

    def test_partial_data_shows_warning(self):
        results = [
            AnalysisResult(dimension="financial", status="unavailable",
                           summary="财务数据不可用", metrics={}),
        ]
        builder = ReportBuilder()
        report = builder.build("000001", "平安银行", results, commentary={
            "financial": "数据不可用。", "summary": "数据不足。"
        })
        assert "数据不可用" in report.replace("*", "").replace("**", "")

    def test_report_includes_disclaimer(self):
        builder = ReportBuilder()
        report = builder.build("000001", "测试", [], {})
        assert "不构成任何投资建议" in report

    def test_report_includes_timestamp(self):
        builder = ReportBuilder()
        report = builder.build("000001", "测试", [], {})
        now = datetime.now()
        assert str(now.year) in report
```

- [ ] **Step 2: 编写格式化器测试**

```python
# tests/report/test_formatter.py
from pathlib import Path
from src.report.formatter import ReportFormatter


class TestReportFormatter:
    def test_save_to_file(self, tmp_path):
        report = "# 测试报告\n内容"
        saved_path = ReportFormatter.save(report, "000001", output_dir=tmp_path)
        assert saved_path.exists()
        content = saved_path.read_text(encoding="utf-8")
        assert "测试报告" in content
        assert saved_path.name.startswith("000001_")

    def test_to_rich_markdown(self):
        report = "# 标题\n**加粗**\n- 列表项"
        md = ReportFormatter.to_rich_markdown(report)
        assert md is not None
```

- [ ] **Step 3: 运行测试确认失败**

```bash
cd D:/code/stock_robot && python -m pytest tests/report/ -v
```
Expected: FAIL

- [ ] **Step 4: 创建报告 Jinja2 模板**

```jinja2
{# src/report/templates/report.jinja2 #}
# {{ name }}（{{ symbol }}）分析报告

> 生成时间：{{ generated_at }}
> 市场：{{ market }}

---

## 财务分析

{% if commentary.get("financial") %}
{{ commentary["financial"] }}
{% endif %}

{% if results_map.get("financial") and results_map["financial"].status != "unavailable" %}
| 指标 | 数值 |
|------|------|
{% for key, value in results_map["financial"].metrics.items() if key not in ["roe_trend"] %}
| {{ key }} | {{ value }} |
{% endfor %}
{% elif results_map.get("financial") and results_map["financial"].status == "unavailable" %}
> 财务数据暂时不可用
{% endif %}

---

## 技术面分析

{% if commentary.get("technical") %}
{{ commentary["technical"] }}
{% endif %}

{% if results_map.get("technical") and results_map["technical"].status != "unavailable" %}
| 指标 | 数值 |
|------|------|
{% for key, value in results_map["technical"].metrics.items() %}
| {{ key }} | {{ value }} |
{% endfor %}
{% elif results_map.get("technical") and results_map["technical"].status == "unavailable" %}
> 行情数据暂时不可用
{% endif %}

---

## 估值分析

{% if commentary.get("valuation") %}
{{ commentary["valuation"] }}
{% endif %}

{% if results_map.get("valuation") and results_map["valuation"].status != "unavailable" %}
| 指标 | 数值 |
|------|------|
{% for key, value in results_map["valuation"].metrics.items() %}
| {{ key }} | {{ value }} |
{% endfor %}
{% elif results_map.get("valuation") and results_map["valuation"].status == "unavailable" %}
> 估值数据暂时不可用
{% endif %}

---

## 行业分析

{% if commentary.get("industry") %}
{{ commentary["industry"] }}
{% endif %}

{% if results_map.get("industry") and results_map["industry"].status != "unavailable" %}
| 指标 | 数值 |
|------|------|
{% for key, value in results_map["industry"].metrics.items() %}
| {{ key }} | {{ value }} |
{% endfor %}
{% elif results_map.get("industry") and results_map["industry"].status == "unavailable" %}
> 行业数据暂时不可用
{% endif %}

---

## 舆情分析

{% if commentary.get("sentiment") %}
{{ commentary["sentiment"] }}
{% endif %}

{% if results_map.get("sentiment") and results_map["sentiment"].status != "unavailable" %}
| 指标 | 数值 |
|------|------|
| 相关新闻数量 | {{ results_map["sentiment"].metrics.get("headline_count", 0) }} |

{% for headline in results_map["sentiment"].metrics.get("headlines", []) %}
- {{ headline }}
{% endfor %}
{% elif results_map.get("sentiment") and results_map["sentiment"].status == "unavailable" %}
> 舆情数据暂时不可用
{% endif %}

---

## 综合总结

{% if commentary.get("summary") %}
{{ commentary["summary"] }}
{% endif %}

---

> **免责声明**：本报告仅用于个人学习和研究目的。所有分析数据和观点不构成任何投资建议。股票投资有风险，入市需谨慎。数据来源的准确性和时效性无法保证，使用者需自行判断。
```

- [ ] **Step 5: 实现报告构建器**

```python
# src/report/builder.py
from datetime import datetime
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from src.data.schemas import AnalysisResult


class ReportBuilder:
    def __init__(self):
        template_dir = Path(__file__).parent / "templates"
        self._env = Environment(loader=FileSystemLoader(str(template_dir)))

    def build(self, symbol: str, name: str, results: list[AnalysisResult],
              commentary: dict[str, str]) -> str:
        results_map = {r.dimension: r for r in results}
        template = self._env.get_template("report.jinja2")
        return template.render(
            symbol=symbol,
            name=name,
            market="A 股",
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            results_map=results_map,
            commentary=commentary,
        )
```

- [ ] **Step 6: 实现格式化器**

```python
# src/report/formatter.py
from datetime import datetime
from pathlib import Path
from rich.markdown import Markdown


class ReportFormatter:
    @staticmethod
    def save(report: str, symbol: str, output_dir: Path | None = None) -> Path:
        if output_dir is None:
            output_dir = Path.cwd() / "reports"
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        filepath = output_dir / filename
        filepath.write_text(report, encoding="utf-8")
        return filepath

    @staticmethod
    def to_rich_markdown(report: str) -> Markdown:
        return Markdown(report)
```

- [ ] **Step 7: 运行测试确认通过**

```bash
cd D:/code/stock_robot && python -m pytest tests/report/ -v
```
Expected: PASS (6 tests)

- [ ] **Step 8: 提交**

```bash
git add src/report/ tests/report/
git commit -m "feat: add report builder with Jinja2 templates and Markdown formatter"
```

---

### Task 10: 股票代码工具

**Files:**
- Create: `src/utils/symbols.py`
- Create: `tests/utils/test_symbols.py`

- [ ] **Step 1: 编写测试**

```python
# tests/utils/test_symbols.py
from src.utils.symbols import normalize_symbol, validate_symbol, resolve_name


class TestNormalizeSymbol:
    def test_pads_to_six_digits(self):
        assert normalize_symbol("1") == "000001"
        assert normalize_symbol("000001") == "000001"

    def test_strips_prefixes(self):
        assert normalize_symbol("sh000001") == "000001"
        assert normalize_symbol("sz000001") == "000001"

    def test_handles_already_normalized(self):
        assert normalize_symbol("600036") == "600036"


class TestValidateSymbol:
    def test_valid_shanghai(self):
        assert validate_symbol("600036") is True

    def test_valid_shenzhen(self):
        assert validate_symbol("000001") is True

    def test_valid_gem(self):
        assert validate_symbol("300750") is True

    def test_invalid_too_short(self):
        assert validate_symbol("123") is False

    def test_invalid_non_numeric(self):
        assert validate_symbol("abcdef") is False

    def test_invalid_starting_digit(self):
        assert validate_symbol("900001") is False
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd D:/code/stock_robot && python -m pytest tests/utils/test_symbols.py -v
```
Expected: FAIL

- [ ] **Step 3: 实现**

```python
# src/utils/symbols.py
import re


def normalize_symbol(raw: str) -> str:
    cleaned = re.sub(r"^(sh|sz|SH|SZ)", "", raw.strip())
    return cleaned.zfill(6)


def validate_symbol(symbol: str) -> bool:
    s = normalize_symbol(symbol)
    if not re.match(r"^\d{6}$", s):
        return False
    first = s[0]
    if first in ("0", "3"):    # 深交所主板、创业板
        return True
    if first == "6":           # 上交所主板
        return True
    if first == "8":           # 北交所
        return True
    return False


def resolve_name(symbol: str) -> str:
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        row = df[df["code"] == normalize_symbol(symbol)]
        if not row.empty:
            return str(row["name"].iloc[0])
    except Exception:
        pass
    return ""
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd D:/code/stock_robot && python -m pytest tests/utils/test_symbols.py -v
```
Expected: PASS (8 tests)

- [ ] **Step 5: 提交**

```bash
git add src/utils/symbols.py tests/utils/test_symbols.py
git commit -m "feat: add stock symbol validation and normalization utilities"
```

---

### Task 11: 注册机制与管道调度器

**Files:**
- Create: `src/core/registry.py`
- Create: `src/core/pipeline.py`
- Create: `tests/core/__init__.py`
- Create: `tests/core/test_registry.py`
- Create: `tests/core/test_pipeline.py`

- [ ] **Step 1: 编写注册机制测试**

```python
# tests/core/test_registry.py
from src.core.registry import Registry
from src.data.base import DataSource


class FakeSource(DataSource):
    def supports(self, market, data_type):
        return market == "a-shares" and data_type in ("price", "financial")
    def fetch(self, symbol, **kwargs):
        return []


class AnotherSource(DataSource):
    def supports(self, market, data_type):
        return market == "a-shares" and data_type == "valuation"
    def fetch(self, symbol, **kwargs):
        return []


class TestRegistry:
    def test_register_and_get_data_sources(self):
        r = Registry()
        r.register_data_source(FakeSource())
        sources = r.get_data_sources("a-shares", "price")
        assert len(sources) == 1
        assert isinstance(sources[0], FakeSource)

    def test_no_match_returns_empty_list(self):
        r = Registry()
        r.register_data_source(FakeSource())
        sources = r.get_data_sources("us", "price")
        assert sources == []

    def test_multiple_sources_ordered(self):
        r = Registry()
        r.register_data_source(FakeSource())
        r.register_data_source(AnotherSource())
        price_sources = r.get_data_sources("a-shares", "price")
        val_sources = r.get_data_sources("a-shares", "valuation")
        assert len(price_sources) == 1
        assert len(val_sources) == 1

    def test_register_analysis_module(self):
        from src.analysis.base import AnalysisModule
        r = Registry()

        class FakeMod(AnalysisModule):
            @property
            def dimension(self):
                return "fake"
            def analyze(self, ctx):
                from src.data.schemas import AnalysisResult
                return AnalysisResult(dimension="fake", status="ok", summary="ok", metrics={})

        r.register_analysis_module(FakeMod())
        results = r.get_analysis_modules()
        assert len(results) == 1
        assert results[0].dimension == "fake"

    def test_register_llm_backend(self):
        from src.llm.base import LLMBackend
        r = Registry()

        class FakeLLM(LLMBackend):
            @property
            def model_name(self):
                return "fake"
            def generate(self, prompt, **kwargs):
                return "response"

        r.register_llm_backend(FakeLLM())
        llm = r.get_llm_backend("fake")
        assert llm is not None
        assert llm.model_name == "fake"

    def test_get_nonexistent_llm_returns_none(self):
        r = Registry()
        assert r.get_llm_backend("nonexistent") is None

    def test_register_llm_backend_by_provider_name(self):
        from src.llm.base import LLMBackend
        r = Registry()

        class GPTAdapter(LLMBackend):
            @property
            def model_name(self):
                return "gpt-4o"
            def generate(self, prompt, **kwargs):
                return "gpt-response"

        r.register_llm_backend(GPTAdapter(), provider="openai")
        llm = r.get_llm_backend("openai")
        assert llm is not None
```

- [ ] **Step 2: 编写管道测试**

```python
# tests/core/test_pipeline.py
from unittest.mock import MagicMock
from datetime import date
from src.core.pipeline import Pipeline
from src.core.registry import Registry
from src.data.schemas import (
    AnalysisContext, AnalysisResult, FinancialData, PriceData,
    ValuationData, IndustryData, NewsData,
)


def make_test_registry():
    """搭建一个完整的测试用注册表"""
    from src.data.base import DataSource

    class MockDataSource(DataSource):
        def supports(self, market, data_type):
            return True

        def fetch(self, symbol, **kwargs):
            data_type = kwargs.get("data_type", "price")
            if data_type == "price":
                return [PriceData(symbol=symbol, trade_date=date(2026,7,1), open=10, high=11, low=9.5, close=10.5, volume=1e6)]
            elif data_type == "financial":
                return [FinancialData(symbol=symbol, fiscal_quarter=date(2025,12,31), revenue=45e9, net_profit=8.5e9, total_assets=500e9, total_equity=45e9, operating_cash_flow=12e9)]
            elif data_type == "valuation":
                return [ValuationData(symbol=symbol, date=date(2026,7,1), pe_ttm=7.5, pb=0.85, ps_ttm=1.2)]
            elif data_type == "industry":
                return [IndustryData(symbol=symbol, industry="银行", sector="金融", peers=["600036"])]
            elif data_type == "news":
                return [NewsData(symbol=symbol, date=date(2026,7,1), headlines=["利好公告"])]
            return []

    reg = Registry()
    reg.register_data_source(MockDataSource())

    from src.analysis.base import AnalysisModule
    for dim in ["financial", "technical", "valuation", "industry", "sentiment"]:
        mod = MagicMock(spec=AnalysisModule)
        mod.dimension = dim
        mod.analyze.return_value = AnalysisResult(
            dimension=dim, status="ok", summary=f"{dim} analysis", metrics={}
        )
        reg.register_analysis_module(mod)
    return reg


class TestPipeline:
    def test_collect_data_populates_context(self):
        reg = make_test_registry()
        pipeline = Pipeline(registry=reg, llm_enabled=False)
        ctx = pipeline.collect("000001", "平安银行", "a-shares")
        assert ctx.symbol == "000001"
        assert ctx.price_data is not None
        assert ctx.financial_data is not None

    def test_run_without_llm(self):
        reg = make_test_registry()
        pipeline = Pipeline(registry=reg, llm_enabled=False)
        results, commentary = pipeline.run("000001", "平安银行")
        assert len(results) == 5
        assert all(isinstance(r, AnalysisResult) for r in results)

    def test_collect_refresh_cache_ignores_cache(self):
        reg = make_test_registry()
        pipeline = Pipeline(registry=reg, llm_enabled=False)
        ctx = pipeline.collect("000001", "平安银行", "a-shares", refresh_cache=True)
        assert ctx.price_data is not None

    def test_single_dimension_filter(self):
        reg = make_test_registry()
        pipeline = Pipeline(registry=reg, llm_enabled=False)
        results, _ = pipeline.run("000001", "平安银行", dimension="financial")
        assert len(results) == 1
        assert results[0].dimension == "financial"
```

- [ ] **Step 3: 运行测试确认失败**

```bash
cd D:/code/stock_robot && python -m pytest tests/core/ -v
```
Expected: FAIL

- [ ] **Step 4: 实现 Registry**

```python
# src/core/registry.py
from src.data.base import DataSource
from src.analysis.base import AnalysisModule
from src.llm.base import LLMBackend


class Registry:
    def __init__(self):
        self._data_sources: list[DataSource] = []
        self._analysis_modules: list[AnalysisModule] = []
        self._llm_backends: dict[str, LLMBackend] = {}

    def register_data_source(self, source: DataSource):
        self._data_sources.append(source)

    def get_data_sources(self, market: str, data_type: str) -> list[DataSource]:
        return [s for s in self._data_sources if s.supports(market, data_type)]

    def register_analysis_module(self, module: AnalysisModule):
        self._analysis_modules.append(module)

    def get_analysis_modules(self) -> list[AnalysisModule]:
        return list(self._analysis_modules)

    def register_llm_backend(self, backend: LLMBackend, provider: str | None = None):
        key = provider or backend.model_name
        self._llm_backends[key] = backend

    def get_llm_backend(self, provider: str) -> LLMBackend | None:
        return self._llm_backends.get(provider)
```

- [ ] **Step 5: 实现 Pipeline**

```python
# src/core/pipeline.py
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from pathlib import Path
from src.data.schemas import AnalysisContext, AnalysisResult
from src.data.cache import CacheManager
from src.core.registry import Registry
from src.utils.config import Config

logger = logging.getLogger(__name__)

DATA_TYPES = ["financial", "price", "valuation", "industry", "news"]


class Pipeline:
    def __init__(self, registry: Registry, config: Config | None = None,
                 llm_enabled: bool | None = None):
        self._registry = registry
        self._config = config or Config()
        if llm_enabled is None:
            llm_enabled = self._config.get("llm.enabled", True)
        self._llm_enabled = llm_enabled
        cache_db = self._config.config_dir / "cache.db"
        self._cache = CacheManager(db_path=cache_db)

    def collect(self, symbol: str, name: str, market: str = "a-shares",
                refresh_cache: bool = False, data_types: list[str] | None = None) -> AnalysisContext:
        ctx = AnalysisContext(symbol=symbol, name=name, market=market)
        types_to_fetch = data_types or DATA_TYPES

        def fetch_one(data_type: str):
            if not refresh_cache:
                cached = self._get_cached(symbol, data_type)
                if cached is not None:
                    return data_type, cached

            sources = self._registry.get_data_sources(market, data_type)
            for source in sources:
                try:
                    result = source.fetch(symbol, data_type=data_type)
                    if result:
                        self._set_cache(symbol, data_type, result)
                        return data_type, result
                except Exception as e:
                    logger.warning(f"Source {source.__class__.__name__} failed for {data_type}: {e}")
            return data_type, None

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(fetch_one, dt): dt for dt in types_to_fetch}
            for future in as_completed(futures):
                data_type, result = future.result()
                if result is not None:
                    self._assign_to_context(ctx, data_type, result)

        return ctx

    def run(self, symbol: str, name: str, market: str = "a-shares",
            dimension: str | None = None, refresh_cache: bool = False
            ) -> tuple[list[AnalysisResult], dict[str, str]]:
        data_types = None
        analysis_modules = self._registry.get_analysis_modules()
        if dimension:
            data_types = self._data_types_for_dimension(dimension)
            analysis_modules = [m for m in analysis_modules if m.dimension == dimension]

        ctx = self.collect(symbol, name, market, refresh_cache=refresh_cache, data_types=data_types)

        results = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_map = {executor.submit(m.analyze, ctx): m for m in analysis_modules}
            for future in as_completed(future_map):
                try:
                    results.append(future.result())
                except Exception as e:
                    mod = future_map[future]
                    logger.error(f"Analysis module {mod.dimension} failed: {e}")
                    results.append(AnalysisResult(
                        dimension=mod.dimension, status="unavailable",
                        summary=f"分析模块异常: {e}", metrics={}))

        commentary = {}
        if self._llm_enabled:
            commentary = self._generate_commentary(symbol, name, results)

        return results, commentary

    def _generate_commentary(self, symbol: str, name: str, results: list[AnalysisResult]) -> dict[str, str]:
        commentary = {}
        provider = self._config.get("llm.provider", "openai")
        llm = self._registry.get_llm_backend(provider)
        if llm is None:
            logger.warning(f"No LLM backend for provider={provider}")
            return commentary

        try:
            from jinja2 import Environment, FileSystemLoader
            template_dir = Path(__file__).parent.parent / "llm" / "prompt_templates"
            env = Environment(loader=FileSystemLoader(str(template_dir)))

            for result in results:
                template_name = f"{result.dimension}_{provider}.jinja2"
                try:
                    template = env.get_template(template_name)
                    prompt = template.render(name=name, symbol=symbol, **result.metrics)
                    commentary[result.dimension] = llm.generate(prompt)
                except Exception as e:
                    logger.warning(f"Failed to generate commentary for {result.dimension}: {e}")
                    commentary[result.dimension] = ""

            # Summary
            summary_template_name = f"summary_{provider}.jinja2"
            try:
                template = env.get_template(summary_template_name)
                context = {r.dimension: r.metrics for r in results}
                context.update(name=name, symbol=symbol)
                context["commentary"] = commentary
                prompt = template.render(**context)
                commentary["summary"] = llm.generate(prompt)
            except Exception as e:
                logger.warning(f"Failed to generate summary: {e}")
                commentary["summary"] = ""
        except Exception as e:
            logger.error(f"Commentary generation failed: {e}")

        return commentary

    def _get_cached(self, symbol: str, data_type: str) -> list | None:
        import json
        from datetime import date as DateType
        date_key = DateType.today().isoformat()
        raw = self._cache.get(data_type, symbol, date_key)
        if raw is None:
            return None
        try:
            data_list = json.loads(raw)
            return self._deserialize_cache(data_type, symbol, data_list)
        except Exception:
            return None

    def _set_cache(self, symbol: str, data_type: str, data: list):
        import json
        from datetime import date as DateType
        date_key = DateType.today().isoformat()
        try:
            serialized = json.dumps([item.model_dump(mode="json") for item in data], ensure_ascii=False, default=str)
            ttl = self._config.get(f"data.cache_ttl.{self._ttl_key(data_type)}", 86400)
            self._cache.put(data_type, symbol, date_key, serialized, ttl_seconds=ttl)
        except Exception as e:
            logger.warning(f"Failed to cache {data_type}: {e}")

    def _ttl_key(self, data_type: str) -> str:
        mapping = {"price": "daily", "valuation": "daily", "financial": "quarterly",
                   "industry": "quarterly", "news": "news"}
        return mapping.get(data_type, "daily")

    def _deserialize_cache(self, data_type: str, symbol: str, data_list: list) -> list:
        from src.data.schemas import FinancialData, PriceData, ValuationData, IndustryData, NewsData
        cls_map = {
            "price": PriceData, "financial": FinancialData,
            "valuation": ValuationData, "industry": IndustryData, "news": NewsData,
        }
        cls = cls_map.get(data_type)
        if cls is None:
            return []
        return [cls(**item) for item in data_list]

    @staticmethod
    def _assign_to_context(ctx: AnalysisContext, data_type: str, data: list):
        mapping = {
            "price": "price_data",
            "financial": "financial_data",
            "valuation": "valuation_data",
            "industry": "industry_data",
            "news": "news_data",
        }
        attr = mapping.get(data_type)
        if attr:
            if data_type in ("valuation", "industry", "news") and len(data) == 1:
                setattr(ctx, attr, data[0])
            else:
                setattr(ctx, attr, data)

    @staticmethod
    def _data_types_for_dimension(dimension: str) -> list[str]:
        mapping = {
            "financial": ["financial"],
            "technical": ["price"],
            "valuation": ["price", "valuation"],
            "industry": ["industry"],
            "sentiment": ["news"],
        }
        return mapping.get(dimension, DATA_TYPES)
```

- [ ] **Step 6: 运行测试确认通过**

```bash
cd D:/code/stock_robot && python -m pytest tests/core/ -v
```
Expected: PASS (10 tests)

- [ ] **Step 7: 提交**

```bash
git add src/core/ tests/core/
git commit -m "feat: add registry and pipeline orchestrator"
```

---

### Task 12: CLI 入口

**Files:**
- Create: `cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: 编写 CLI 测试**

```python
# tests/test_cli.py
from click.testing import CliRunner
from cli import main


class TestCLI:
    def test_analyze_without_symbol_shows_error(self):
        runner = CliRunner()
        result = runner.invoke(main, ["analyze"])
        assert result.exit_code != 0

    def test_analyze_with_invalid_symbol_shows_error(self):
        runner = CliRunner()
        result = runner.invoke(main, ["analyze", "abc"])
        assert result.exit_code != 0

    def test_analyze_with_valid_symbol(self, mocker):
        mock_pipeline = mocker.patch("cli._build_pipeline")
        mock_instance = mock_pipeline.return_value
        from src.data.schemas import AnalysisResult
        mock_instance.run.return_value = (
            [AnalysisResult(dimension="financial", status="ok", summary="OK", metrics={"roe": 0.12})],
            {"financial": "Commentary", "summary": "Great stock"},
        )

        runner = CliRunner()
        result = runner.invoke(main, ["analyze", "000001", "--no-llm"])
        assert result.exit_code == 0

    def test_config_set_and_get(self):
        runner = CliRunner()
        result = runner.invoke(main, ["config", "set", "llm.provider", "claude"])
        assert result.exit_code == 0

        result = runner.invoke(main, ["config", "get", "llm.provider"])
        assert result.exit_code == 0
        assert "claude" in result.output

    def test_cache_clear(self, mocker):
        mock_cache = mocker.patch("cli._get_cache")
        runner = CliRunner()
        result = runner.invoke(main, ["cache", "clear"])
        assert result.exit_code == 0
        mock_cache.return_value.clear.assert_called_once()

    def test_no_llm_flag_passes_to_pipeline(self, mocker):
        mock_pipeline = mocker.patch("cli._build_pipeline")
        mock_instance = mock_pipeline.return_value
        from src.data.schemas import AnalysisResult
        mock_instance.run.return_value = ([AnalysisResult(dimension="financial", status="ok", summary="OK", metrics={})], {})

        runner = CliRunner()
        result = runner.invoke(main, ["analyze", "000001", "--no-llm"])
        assert result.exit_code == 0
        # Pipeline should have been called with llm_enabled=False
        # (This is verified by the mock setup returning no commentary)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd D:/code/stock_robot && python -m pytest tests/test_cli.py -v
```
Expected: FAIL

- [ ] **Step 3: 实现 CLI**

```python
# cli.py
import sys
from pathlib import Path
import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def _get_registry():
    from src.core.registry import Registry
    from src.data.akshare import AkShareAdapter
    from src.analysis.financial import FinancialAnalyzer
    from src.analysis.technical import TechnicalAnalyzer
    from src.analysis.valuation import ValuationAnalyzer
    from src.analysis.industry import IndustryAnalyzer
    from src.analysis.sentiment import SentimentAnalyzer

    reg = Registry()
    reg.register_data_source(AkShareAdapter())
    reg.register_analysis_module(FinancialAnalyzer())
    reg.register_analysis_module(TechnicalAnalyzer())
    reg.register_analysis_module(ValuationAnalyzer())
    reg.register_analysis_module(IndustryAnalyzer())
    reg.register_analysis_module(SentimentAnalyzer())
    return reg


def _register_llm(reg, config):
    from src.llm.openai import OpenAIAdapter
    from src.llm.claude import ClaudeAdapter

    provider = config.get("llm.provider", "openai")
    api_key = config.data.get("llm", {}).get("api_key", "") or config.get("llm.api_key", "")

    if provider == "openai":
        reg.register_llm_backend(
            OpenAIAdapter(api_key=api_key, model=config.get("llm.model", "gpt-4o"),
                          temperature=config.get("llm.temperature", 0.3),
                          max_tokens=config.get("llm.max_tokens", 2000)),
            provider="openai",
        )
    elif provider == "claude":
        reg.register_llm_backend(
            ClaudeAdapter(api_key=api_key, model=config.get("llm.model", "claude-sonnet-4-6"),
                          temperature=config.get("llm.temperature", 0.3),
                          max_tokens=config.get("llm.max_tokens", 2000)),
            provider="claude",
        )


def _build_pipeline(llm_enabled=True):
    from src.core.pipeline import Pipeline
    from src.utils.config import Config

    config = Config()
    reg = _get_registry()
    _register_llm(reg, config)
    return Pipeline(registry=reg, config=config, llm_enabled=llm_enabled)


def _get_cache():
    from src.utils.config import Config
    from src.data.cache import CacheManager
    config = Config()
    return CacheManager(db_path=config.config_dir / "cache.db")


def _check_disclaimer(config):
    if not config.get("data.disclaimer_accepted", False):
        console.print(Panel.fit(
            "[bold yellow]免责声明[/bold yellow]\n\n"
            "本工具仅用于个人学习和研究目的。所有分析数据和观点不构成任何投资建议。\n"
            "股票投资有风险，入市需谨慎。数据来源的准确性和时效性无法保证，使用者需自行判断。\n\n"
            '输入 [bold]stock-robot config set data.disclaimer_accepted true[/bold] 确认已阅读。',
            title="首次使用"
        ))
        return False
    return True


@click.group()
@click.version_option(version="0.1.0")
def main():
    """Stock Robot — AI 驱动的股票分析研报助手"""
    pass


@main.command()
@click.argument("symbol")
@click.option("--dimension", "-d", help="指定分析维度 (financial/technical/valuation/industry/sentiment)")
@click.option("--refresh-cache", is_flag=True, help="强制刷新缓存")
@click.option("--no-llm", is_flag=True, help="仅输出数据，跳过 LLM 解读")
@click.option("--verbose", "-v", is_flag=True, help="显示采集和分析过程")
def analyze(symbol, dimension, refresh_cache, no_llm, verbose):
    """分析股票并生成研报"""
    from src.utils.symbols import normalize_symbol, validate_symbol, resolve_name
    from src.report.builder import ReportBuilder
    from src.report.formatter import ReportFormatter
    from src.utils.config import Config

    config = Config()
    if not _check_disclaimer(config):
        return

    if not validate_symbol(symbol):
        console.print(f"[red]无效的股票代码: {symbol}[/red]")
        console.print("请输入 6 位数字代码（如 000001、600036）")
        sys.exit(1)

    symbol = normalize_symbol(symbol)
    name = resolve_name(symbol) or symbol

    if verbose:
        console.print(f"[dim]正在分析: {name} ({symbol})[/dim]")

    llm_enabled = not no_llm and config.get("llm.enabled", True)
    pipeline = _build_pipeline(llm_enabled=llm_enabled)

    if verbose:
        console.print("[dim]正在采集数据...[/dim]")

    try:
        results, commentary = pipeline.run(
            symbol, name,
            dimension=dimension,
            refresh_cache=refresh_cache,
        )
    except Exception as e:
        console.print(f"[red]分析失败: {e}[/red]")
        sys.exit(1)

    if verbose:
        console.print("[dim]正在生成报告...[/dim]")

    builder = ReportBuilder()
    report = builder.build(symbol, name, results, commentary)

    saved_path = ReportFormatter.save(report, symbol)
    console.print(ReportFormatter.to_rich_markdown(report))
    console.print(f"\n[dim]报告已保存至: {saved_path}[/dim]")


@main.group()
def config():
    """管理配置"""
    pass


@config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key, value):
    """设置配置项"""
    from src.utils.config import Config
    cfg = Config()
    converted = _convert_value(value)
    cfg.set(key, converted)
    console.print(f"[green]✓ {key} = {converted}[/green]")


@config.command("get")
@click.argument("key")
def config_get(key):
    """获取配置项"""
    from src.utils.config import Config
    cfg = Config()
    val = cfg.get(key)
    console.print(f"{key} = {val}")


@main.group()
def cache():
    """管理缓存"""
    pass


@cache.command("clear")
def cache_clear():
    """清空所有缓存"""
    c = _get_cache()
    c.clear()
    console.print("[green]✓ 缓存已清空[/green]")


@cache.command("status")
def cache_status():
    """查看缓存状态"""
    c = _get_cache()
    stats = c.stats()
    table = Table(title="缓存状态")
    table.add_column("指标", style="cyan")
    table.add_column("数值", style="green")
    table.add_row("缓存条目", str(stats["total_entries"]))
    table.add_row("数据库大小", f"{stats['db_size_bytes'] / 1024:.1f} KB")
    console.print(table)


def _convert_value(value: str):
    if value.lower() in ("true", "yes"):
        return True
    if value.lower() in ("false", "no"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd D:/code/stock_robot && python -m pytest tests/test_cli.py -v
```
Expected: PASS (6 tests)

- [ ] **Step 5: 提交**

```bash
git add cli.py tests/test_cli.py
git commit -m "feat: add CLI entry point with analyze, config, and cache commands"
```

---

### Task 13: 集成测试

**Files:**
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/test_pipeline_integration.py`

- [ ] **Step 1: 编写集成测试**

```python
# tests/integration/test_pipeline_integration.py
from datetime import date
from src.core.registry import Registry
from src.core.pipeline import Pipeline
from src.data.base import DataSource
from src.data.schemas import (
    PriceData, FinancialData, ValuationData, IndustryData, NewsData,
    AnalysisResult,
)
from src.analysis.financial import FinancialAnalyzer
from src.analysis.technical import TechnicalAnalyzer
from src.analysis.valuation import ValuationAnalyzer
from src.analysis.industry import IndustryAnalyzer
from src.analysis.sentiment import SentimentAnalyzer


class MockFullDataSource(DataSource):
    """模拟完整数据源，返回所有类型的数据"""
    def supports(self, market, data_type):
        return market == "a-shares"

    def fetch(self, symbol, **kwargs):
        data_type = kwargs.get("data_type", "price")
        if data_type == "price":
            prices = []
            for i in range(120):
                prices.append(PriceData(
                    symbol=symbol,
                    trade_date=date(2026, 1, 1 + i),
                    open=10.0 + i * 0.01,
                    high=10.5 + i * 0.01,
                    low=9.8 + i * 0.01,
                    close=10.3 + i * 0.01,
                    volume=5_000_000 + i * 10000,
                ))
            return prices
        elif data_type == "financial":
            quarters = [
                date(2025, 12, 31), date(2025, 9, 30), date(2025, 6, 30),
                date(2025, 3, 31), date(2024, 12, 31),
            ]
            result = []
            for i, q in enumerate(quarters):
                result.append(FinancialData(
                    symbol=symbol, fiscal_quarter=q,
                    revenue=45e9 - i * 3e9,
                    net_profit=8.5e9 - i * 0.5e9,
                    total_assets=500e9 + i * 10e9,
                    total_equity=45e9 + i * 1e9,
                    operating_cash_flow=12e9 - i * 1e9,
                ))
            return result
        elif data_type == "valuation":
            return [ValuationData(symbol=symbol, date=date(2026, 7, 1), pe_ttm=7.5, pb=0.85, ps_ttm=1.2)]
        elif data_type == "industry":
            return [IndustryData(symbol=symbol, industry="银行", sector="金融", peers=["600036", "601398"])]
        elif data_type == "news":
            return [NewsData(symbol=symbol, date=date(2026, 7, 1),
                             headlines=["业绩增长超预期", "机构上调目标价"])]
        return []


class TestPipelineIntegration:
    def test_full_pipeline_without_llm(self):
        reg = Registry()
        reg.register_data_source(MockFullDataSource())
        reg.register_analysis_module(FinancialAnalyzer())
        reg.register_analysis_module(TechnicalAnalyzer())
        reg.register_analysis_module(ValuationAnalyzer())
        reg.register_analysis_module(IndustryAnalyzer())
        reg.register_analysis_module(SentimentAnalyzer())

        pipeline = Pipeline(registry=reg, llm_enabled=False)
        results, commentary = pipeline.run("000001", "平安银行")

        assert len(results) == 5
        statuses = {r.dimension: r.status for r in results}
        assert statuses["financial"] == "ok"
        assert statuses["technical"] == "ok"
        assert statuses["valuation"] in ("ok", "partial")
        assert statuses["industry"] == "ok"
        assert statuses["sentiment"] == "ok"

    def test_pipeline_with_single_dimension(self):
        reg = Registry()
        reg.register_data_source(MockFullDataSource())
        reg.register_analysis_module(FinancialAnalyzer())

        pipeline = Pipeline(registry=reg, llm_enabled=False)
        results, _ = pipeline.run("000001", "平安银行", dimension="financial")
        assert len(results) == 1
        assert results[0].dimension == "financial"

    def test_report_builder_with_all_results(self):
        from src.report.builder import ReportBuilder
        results = [
            AnalysisResult(dimension="financial", status="ok", summary="财务健康", metrics={}),
            AnalysisResult(dimension="technical", status="ok", summary="技术面偏多", metrics={}),
            AnalysisResult(dimension="valuation", status="partial", summary="PE 7.5", metrics={}),
            AnalysisResult(dimension="industry", status="ok", summary="银行行业", metrics={}),
            AnalysisResult(dimension="sentiment", status="ok", summary="3条新闻", metrics={}),
        ]
        commentary = {
            "financial": "财务解读", "technical": "技术解读",
            "valuation": "估值解读", "industry": "行业解读",
            "sentiment": "舆情解读", "summary": "综合结论",
        }
        builder = ReportBuilder()
        report = builder.build("000001", "平安银行", results, commentary)
        assert "平安银行" in report
        assert "财务分析" in report
        assert "免责声明" in report
```

- [ ] **Step 2: 运行集成测试**

```bash
cd D:/code/stock_robot && python -m pytest tests/integration/ -v
```
Expected: PASS (3 tests)

- [ ] **Step 3: 运行全部测试确认**

```bash
cd D:/code/stock_robot && python -m pytest -v
```
Expected: ALL tests pass

- [ ] **Step 4: 提交**

```bash
git add tests/integration/
git commit -m "test: add integration tests for pipeline and report builder"
```

---

### Task 14: 最终验证与 Cleanup

- [ ] **Step 1: 运行全部测试**

```bash
cd D:/code/stock_robot && python -m pytest -v --tb=short
```
Expected: 所有测试通过，无失败

- [ ] **Step 2: 验证 CLI 帮助信息**

```bash
cd D:/code/stock_robot && python cli.py --help && python cli.py analyze --help
```
Expected: 显示完整帮助信息

- [ ] **Step 3: 验证文件结构完整性**

```bash
cd D:/code/stock_robot && find . -name "*.py" -not -path "./.venv/*" -not -path "./__pycache__/*" | sort
```
Expected: 所有计划文件均已创建

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "chore: final verification and cleanup"
```
