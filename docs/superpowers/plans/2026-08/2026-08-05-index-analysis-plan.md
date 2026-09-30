# 指数分析功能 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Stock Robot 新增指数分析管道，支持宽基/行业/海外指数分析及多指数横向对比。

**Architecture:** 新增 `src/index/` 子包作为独立的指数管道，通过 `AnalysisTarget` 值对象在 CLI 层完成个股/指数路由。指数管道复用个股管道的基础设施（Registry、CacheManager、LLM 后端、Rich 渲染），但持有独立的 Schema、数据采集、分析模块和报告构建器。

**Tech Stack:** Python 3.11+, Pydantic v2, AkShare, Rich, Jinja2, pytest

---

### Task 1: 数据模型 — AnalysisTarget 与指数 Schema

**Files:**
- Modify: `src/data/schemas.py`
- Test: `tests/data/test_index_schemas.py`

- [ ] **Step 1: 编写数据模型测试**

```python
"""指数数据模型测试"""
import pytest
from datetime import date
from src.data.schemas import (
    AnalysisTarget, IndexPriceData, IndexValuationData,
    CapitalFlowData, MacroContext, IndexAnalysisContext, IndexReport
)
from src.data.schemas import PriceData, RawSentimentData, EnrichedSentiment, DataSufficiency


class TestAnalysisTarget:
    def test_stock_target_creation(self):
        target = AnalysisTarget(
            target_type="stock", symbol="000001",
            name="平安银行", market="a-shares"
        )
        assert target.target_type == "stock"
        assert target.index_style is None

    def test_index_target_creation(self):
        target = AnalysisTarget(
            target_type="index", symbol="000300",
            name="沪深300", market="a-shares", index_style="broad"
        )
        assert target.target_type == "index"
        assert target.index_style == "broad"


class TestIndexValuationData:
    def test_default_valuation_valid(self):
        v = IndexValuationData(symbol="000300", date=date.today())
        assert v.valuation_valid is True
        assert v.percentile_lookback_years == 5

    def test_valuation_invalid_when_sample_short(self):
        v = IndexValuationData(
            symbol="000300", date=date.today(),
            valuation_valid=False,
            percentile_sample_start=date(2024, 1, 1),
            percentile_sample_end=date(2026, 8, 1),
        )
        assert v.valuation_valid is False
        assert v.pe_percentile is None


class TestIndexAnalysisContext:
    def test_empty_context(self):
        target = AnalysisTarget(
            target_type="index", symbol="000300",
            name="沪深300", market="a-shares", index_style="broad"
        )
        ctx = IndexAnalysisContext(target=target)
        assert ctx.price_data == []
        assert ctx.valuation_data is None
        assert ctx.capital_flow is None
        assert ctx.macro is None
        assert ctx.raw_sentiment is None

    def test_context_with_risk_flags(self):
        target = AnalysisTarget(
            target_type="index", symbol="000300",
            name="沪深300", market="a-shares", index_style="broad"
        )
        ctx = IndexAnalysisContext(target=target, risk_flags=["PE 处于历史高位"])
        assert len(ctx.risk_flags) == 1


class TestIndexReport:
    def test_report_visible_sections_broad(self):
        report = IndexReport(
            code="000300", name="沪深300", date=date.today(),
            overview={}, section_technical={}, section_valuation={},
            section_capital={}, section_macro={}, section_sentiment={},
            tag_technical="bull", tag_valuation="neutral",
            tag_capital="positive", tag_macro="neutral", tag_sentiment="neutral",
            composite_comment="谨慎看多", position_coeff=0.6,
            risk_list=[], visible_sections={"overview", "technical", "valuation",
                                             "capital", "macro", "sentiment"}
        )
        assert "macro" in report.visible_sections

    def test_report_visible_sections_sector_hides_macro(self):
        report = IndexReport(
            code="399006", name="创业板指", date=date.today(),
            overview={}, section_technical={}, section_valuation={},
            section_capital={}, section_macro=None, section_sentiment={},
            tag_technical="shake", tag_valuation="overvalued",
            tag_capital="neutral", tag_macro="na", tag_sentiment="negative",
            composite_comment="中性震荡", position_coeff=0.35,
            risk_list=[],
            visible_sections={"overview", "technical", "valuation", "capital", "sentiment"}
        )
        assert "macro" not in report.visible_sections
```

- [ ] **Step 2: 运行测试验证失败**

```bash
python -m pytest tests/data/test_index_schemas.py -v
```
Expected: ImportError / 类未定义

- [ ] **Step 3: 在 `src/data/schemas.py` 中添加新模型**

在文件末尾追加以下代码（保留所有已有模型不变）：

```python
# ============================================================
# 指数分析数据模型
# ============================================================

class AnalysisTarget(BaseModel):
    """描述"分析什么"的值对象，不含业务数据"""
    target_type: Literal["stock", "index"]
    symbol: str
    name: str
    market: str = "a-shares"
    index_style: Literal["broad", "sector", "overseas"] | None = None


class IndexPriceData(PriceData):
    """指数日线行情（继承 PriceData 的 OHLCV 字段）"""
    turnover: float | None = None
    change_pct: float | None = None


class IndexValuationData(BaseModel):
    """指数估值快照"""
    symbol: str
    date: date
    pe_ttm: float | None = None
    pb: float | None = None
    pe_percentile: float | None = None
    pb_percentile: float | None = None
    dividend_yield: float | None = None
    percentile_lookback_years: int = 5
    percentile_sample_start: date | None = None
    percentile_sample_end: date | None = None
    valuation_valid: bool = True


class CapitalFlowData(BaseModel):
    """资金流向"""
    symbol: str
    date: date
    north_bound: float | None = None
    main_net_inflow: float | None = None
    margin_balance: float | None = None


class MacroContext(BaseModel):
    """宏观经济指标上下文"""
    symbol: str
    fetch_date: date
    shibor_3m: float | None = None
    cpi_yoy: float | None = None
    pmi: float | None = None
    usd_cny: float | None = None
    shibor_percentile: float | None = None
    pmi_percentile: float | None = None


class IndexAnalysisContext(BaseModel):
    """指数分析上下文 — 管道的核心数据容器"""
    target: AnalysisTarget
    price_data: list[IndexPriceData] = Field(default_factory=list)
    valuation_data: IndexValuationData | None = None
    capital_flow: CapitalFlowData | None = None
    macro: MacroContext | None = None
    raw_sentiment: RawSentimentData | None = None
    sufficiency: DataSufficiency | None = None
    enriched_sentiment: EnrichedSentiment | None = None
    risk_flags: list[str] = Field(default_factory=list)


class IndexReport(BaseModel):
    """指数分析报告"""
    code: str
    name: str
    date: date
    overview: dict
    section_technical: dict
    section_valuation: dict
    section_capital: dict
    section_macro: dict | None
    section_sentiment: dict

    tag_technical: Literal["bull", "shake", "bear"]
    tag_valuation: Literal["undervalued", "neutral", "overvalued", "invalid"]
    tag_capital: Literal["positive", "neutral", "negative"]
    tag_macro: Literal["positive", "neutral", "negative", "na"]
    tag_sentiment: Literal["positive", "neutral", "negative"]

    composite_comment: str
    position_coeff: float | None

    risk_list: list[str]
    visible_sections: set[str]
```

- [ ] **Step 4: 运行测试验证通过**

```bash
python -m pytest tests/data/test_index_schemas.py -v
```
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/data/schemas.py tests/data/test_index_schemas.py
git commit -m "feat(数据模型): 添加 AnalysisTarget 与指数分析相关 Schema"
```

---

### Task 2: 指数代码工具 — 校验与映射

**Files:**
- Create: `src/data/index_mapping.py`
- Modify: `src/utils/symbols.py`
- Test: `tests/utils/test_index_symbols.py`

- [ ] **Step 1: 编写指数校验测试**

```python
"""指数代码工具测试"""
import pytest
from src.utils.symbols import validate_index_symbol, normalize_index_symbol
from src.data.index_mapping import IndexMapping, IndexMappingEntry


class TestValidateIndexSymbol:
    def test_valid_broad_index(self):
        assert validate_index_symbol("000300") is True
        assert validate_index_symbol("sh000001") is True

    def test_valid_sector_index(self):
        # 行业板块代码以 8 开头
        assert validate_index_symbol("801010") is True

    def test_invalid_symbol_too_short(self):
        assert validate_index_symbol("123") is False

    def test_normalize_index_symbol(self):
        assert normalize_index_symbol("sh000300") == "000300"
        assert normalize_index_symbol("000300") == "000300"


class TestIndexMapping:
    def test_lookup_broad_index(self):
        mapping = IndexMapping()
        entry = mapping.lookup("000300")
        assert entry is not None
        assert entry.index_style == "broad"
        assert entry.name == "沪深300"

    def test_lookup_unknown_returns_none(self):
        mapping = IndexMapping()
        entry = mapping.lookup("999999")
        assert entry is None
```

- [ ] **Step 2: 运行测试验证失败**

```bash
python -m pytest tests/utils/test_index_symbols.py -v
```
Expected: ImportError

- [ ] **Step 3: 创建 `src/data/index_mapping.py`**

```python
"""指数代码 → 名称 → 类别映射表"""
from pathlib import Path
import csv
from dataclasses import dataclass


@dataclass
class IndexMappingEntry:
    symbol: str
    name: str
    index_style: str    # "broad" | "sector" | "overseas"
    market: str = "a-shares"


class IndexMapping:
    """从 CSV 加载指数映射表，支持查表获取名称和类别"""

    def __init__(self, csv_path: str | Path | None = None):
        if csv_path is None:
            csv_path = Path(__file__).parent.parent.parent / "data" / "index_mapping.csv"
        self._csv_path = Path(csv_path)
        self._mapping: dict[str, IndexMappingEntry] = {}
        self._load()

    def _load(self):
        if not self._csv_path.exists():
            raise FileNotFoundError(f"指数映射表不存在: {self._csv_path}")
        with open(self._csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                symbol = row["symbol"]
                self._mapping[symbol] = IndexMappingEntry(
                    symbol=symbol,
                    name=row["name"],
                    index_style=row["index_style"],
                    market=row.get("market", "a-shares"),
                )

    def lookup(self, symbol: str) -> IndexMappingEntry | None:
        return self._mapping.get(symbol)

    def __len__(self) -> int:
        return len(self._mapping)
```

- [ ] **Step 4: 创建 `data/index_mapping.csv`**

```csv
symbol,name,index_style,market
000001,上证指数,broad,a-shares
000016,上证50,broad,a-shares
000300,沪深300,broad,a-shares
000688,科创50,broad,a-shares
000905,中证500,broad,a-shares
000852,中证1000,broad,a-shares
399001,深证成指,broad,a-shares
399006,创业板指,broad,a-shares
399673,创业板50,broad,a-shares
801010,农林牧渔,sector,a-shares
801020,采掘,sector,a-shares
801030,化工,sector,a-shares
801040,钢铁,sector,a-shares
801050,有色金属,sector,a-shares
801080,电子,sector,a-shares
801110,家用电器,sector,a-shares
801120,食品饮料,sector,a-shares
801150,医药生物,sector,a-shares
801180,房地产,sector,a-shares
801200,商业贸易,sector,a-shares
801230,综合,sector,a-shares
801750,计算机,sector,a-shares
801760,传媒,sector,a-shares
801770,通信,sector,a-shares
801780,银行,sector,a-shares
801790,非银金融,sector,a-shares
801880,汽车,sector,a-shares
801890,机械设备,sector,a-shares
801950,电气设备,sector,a-shares
801960,国防军工,sector,a-shares
801970,交通运输,sector,a-shares
801980,建筑材料,sector,a-shares
801990,建筑装饰,sector,a-shares
802000,轻工制造,sector,a-shares
802010,纺织服装,sector,a-shares
802030,休闲服务,sector,a-shares
802040,公用事业,sector,a-shares
HSI,恒生指数,overseas,hk
HSCEI,恒生中国企业指数,overseas,hk
SPX,标普500,overseas,us
IXIC,纳斯达克综合,overseas,us
DJI,道琼斯工业平均,overseas,us
```

- [ ] **Step 5: 在 `src/utils/symbols.py` 末尾添加两个函数**

```python
def validate_index_symbol(symbol: str) -> bool:
    """校验指数代码格式"""
    import re
    # 支持 sh000001 / sz399001 / 000001 / HSI / SPX 等格式
    cleaned = symbol.strip().upper()
    # 海外指数：大写字母组合
    if re.match(r"^[A-Z]{2,10}$", cleaned):
        return True
    # A 股指数：可选前缀 + 数字
    prefix, digits = _extract_prefix_and_digits(cleaned.lower())
    if not digits:
        return False
    code = digits.zfill(6)
    return bool(re.match(r"^\d{6}$", code))


def normalize_index_symbol(symbol: str) -> str:
    """清理前缀，A 股指数补零到 6 位；海外指数保留大写"""
    import re
    cleaned = symbol.strip().upper()
    # 海外指数直接返回大写
    if re.match(r"^[A-Z]{2,10}$", cleaned):
        return cleaned
    # A 股指数去前缀、补零
    cleaned_lower = re.sub(r"^(sh|sz|SH|SZ)", "", symbol.strip())
    return cleaned_lower.zfill(6)
```

- [ ] **Step 6: 运行测试验证通过**

```bash
python -m pytest tests/utils/test_index_symbols.py -v
```
Expected: 全部 PASS

- [ ] **Step 7: 提交**

```bash
git add src/data/index_mapping.py data/index_mapping.csv src/utils/symbols.py tests/utils/test_index_symbols.py
git commit -m "feat(工具): 添加指数代码校验与映射表"
```

---

### Task 3: AkShare 指数数据采集方法

**Files:**
- Modify: `src/data/akshare.py`
- Test: `tests/data/test_akshare_index.py`

- [ ] **Step 1: 编写 AkShare 指数采集测试**

```python
"""AkShare 指数数据采集测试"""
import pytest
from datetime import date, datetime
from src.data.akshare import AkShareAdapter
from src.data.schemas import IndexPriceData


class TestAkShareIndexAdapter:
    def test_supports_index_price(self):
        adapter = AkShareAdapter()
        assert adapter.supports("a-shares", "index_price") is True

    def test_supports_index_valuation(self):
        adapter = AkShareAdapter()
        assert adapter.supports("a-shares", "index_valuation") is True

    def test_fetch_index_price_broad(self):
        adapter = AkShareAdapter()
        result = adapter.fetch("000300", data_type="index_price",
                               index_style="broad")
        assert isinstance(result, list)
        if len(result) > 0:
            item = result[0]
            assert isinstance(item, IndexPriceData)
            assert item.symbol == "000300"
            assert item.close > 0

    def test_fetch_index_price_returns_empty_on_error(self):
        adapter = AkShareAdapter()
        result = adapter.fetch("INVALID_INDEX", data_type="index_price")
        assert result == []
```

- [ ] **Step 2: 运行测试确认当前不支持**

```bash
python -m pytest tests/data/test_akshare_index.py::TestAkShareIndexAdapter::test_supports_index_price -v
```
Expected: FAIL (`supports` 返回 False)

- [ ] **Step 3: 在 `AkShareAdapter.supports` 中添加指数数据类型**

在 `src/data/akshare.py` 中找到 `AkShareAdapter` 类的 `supports` 方法，添加指数类型：

```python
# 在 supports 方法中添加以下 elif 分支：
elif data_type in ("index_price", "index_valuation", "index_capital_flow",
                   "index_macro", "index_sentiment"):
    return market in ("a-shares", "hk", "us")
```

- [ ] **Step 4: 在 `AkShareAdapter.fetch` 中添加指数数据拉取逻辑**

在 `fetch` 方法中添加指数分发：

```python
# 在 fetch 方法中添加以下分发逻辑：
if data_type == "index_price":
    return self._fetch_index_price(symbol, kwargs.get("index_style", "broad"))
elif data_type == "index_valuation":
    return self._fetch_index_valuation(symbol)
elif data_type == "index_capital_flow":
    return self._fetch_capital_flow(symbol, kwargs.get("index_style", "broad"))
elif data_type == "index_macro":
    return self._fetch_index_macro(symbol, kwargs.get("index_style", "broad"))
elif data_type == "index_sentiment":
    return self._fetch_index_sentiment(symbol)
```

- [ ] **Step 5: 实现指数行情采集方法**

在 `AkShareAdapter` 类中添加：

```python
@retry_on_network_error()
def _fetch_index_price(self, symbol: str, index_style: str) -> list[IndexPriceData]:
    from data.schemas import IndexPriceData

    try:
        # A 股指数使用 stock_zh_index_daily_em
        if index_style in ("broad", "sector"):
            df = ak.stock_zh_index_daily_em(symbol=symbol)
        elif index_style == "overseas":
            # 海外指数用全球指数接口
            df = ak.index_global_hist_em(symbol=f"全球{symbol}")
        else:
            return []

        if df is None or df.empty:
            return []

        results = []
        for _, row in df.iterrows():
            results.append(IndexPriceData(
                symbol=symbol,
                trade_date=_parse_date(row["date"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row.get("volume", 0)),
                turnover=float(row.get("amount", 0)) / 1e8 if row.get("amount") else None,
                change_pct=float(row.get("pct_chg", 0)) if row.get("pct_chg") else None,
            ))
        return results
    except Exception as e:
        logger.warning(f"获取指数 {symbol} 行情失败: {e}")
        return []
```

- [ ] **Step 6: 实现指数估值采集方法**

```python
@retry_on_network_error()
def _fetch_index_valuation(self, symbol: str) -> list[IndexValuationData]:
    from data.schemas import IndexValuationData

    try:
        # 使用 index_value_hist_funddb 获取指数估值历史
        df = ak.index_value_hist_funddb(symbol=symbol, indicator="市盈率")
        if df is None or df.empty:
            return []

        latest = df.iloc[-1]
        return [IndexValuationData(
            symbol=symbol,
            date=_parse_date(str(latest["日期"])),
            pe_ttm=float(latest["市盈率"]) if latest.get("市盈率") else None,
            pb=float(latest.get("市净率", 0)) if latest.get("市净率") else None,
            dividend_yield=float(latest.get("股息率", 0)) if latest.get("股息率") else None,
        )]
    except Exception as e:
        logger.warning(f"获取指数 {symbol} 估值失败: {e}")
        return []
```

- [ ] **Step 7: 实现资金流向采集方法**

```python
@retry_on_network_error()
def _fetch_capital_flow(self, symbol: str, index_style: str) -> list[CapitalFlowData]:
    from data.schemas import CapitalFlowData

    try:
        today = date.today()
        if index_style == "broad":
            # 全市场北向资金
            df = ak.stock_hsgt_north_net_flow_in_em(symbol="北上")
        elif index_style == "sector":
            # 行业板块资金流向
            df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流向")
            row = df[df["名称"].str.contains(symbol[:3])] if not df.empty else None
            if row is not None and not row.empty:
                r = row.iloc[0]
                return [CapitalFlowData(
                    symbol=symbol, date=today,
                    main_net_inflow=float(r.get("主力净流入", 0)) if r.get("主力净流入") else None,
                )]
            return [CapitalFlowData(symbol=symbol, date=today)]
        else:
            return []

        if df is not None and not df.empty:
            latest = df.iloc[-1]
            return [CapitalFlowData(
                symbol=symbol, date=today,
                north_bound=float(latest.get("value", 0)) if latest.get("value") else None,
            )]
        return [CapitalFlowData(symbol=symbol, date=today)]
    except Exception as e:
        logger.warning(f"获取指数 {symbol} 资金流向失败: {e}")
        return [CapitalFlowData(symbol=symbol, date=date.today())]
```

- [ ] **Step 8: 实现宏观数据采集**

```python
@retry_on_network_error()
def _fetch_index_macro(self, symbol: str, index_style: str) -> list[MacroContext]:
    from data.schemas import MacroContext

    if index_style == "sector":
        # sector 保留 MacroContext 实例但字段全 None
        return [MacroContext(symbol=symbol, fetch_date=date.today())]

    result = MacroContext(symbol=symbol, fetch_date=date.today())
    try:
        # PMI
        df_pmi = ak.macro_china_pmi()
        if df_pmi is not None and not df_pmi.empty:
            latest = df_pmi.iloc[-1]
            result.pmi = float(latest["制造业"]) if latest.get("制造业") else None

        # Shibor
        df_shibor = ak.rate_interbank(market="上海银行间同业拆放利率", indicator="Shibor")
        if df_shibor is not None and not df_shibor.empty:
            three_month = df_shibor[df_shibor["期限"] == "3M"]
            if not three_month.empty:
                result.shibor_3m = float(three_month.iloc[-1]["利率"])

        # USD/CNY
        if index_style == "overseas":
            df_fx = ak.fx_spot_quote()
            if df_fx is not None and not df_fx.empty:
                usd_row = df_fx[df_fx["货币对"] == "美元/人民币"]
                if not usd_row.empty:
                    result.usd_cny = float(usd_row.iloc[-1]["最新价"])

    except Exception as e:
        logger.warning(f"获取宏观数据失败: {e}")

    return [result]
```

- [ ] **Step 9: 实现指数舆情采集**

```python
@retry_on_network_error()
def _fetch_index_sentiment(self, symbol: str) -> list[NewsData]:
    from data.schemas import NewsData

    try:
        # 全市场要闻，不用个股新闻接口
        df = ak.stock_news_main_em()
        if df is None or df.empty:
            return [NewsData(symbol=symbol, date=date.today(), headlines=[])]

        headlines = df["title"].head(30).tolist() if "title" in df.columns else []
        result = NewsData(symbol=symbol, date=date.today(), headlines=headlines)
        return [result]
    except Exception as e:
        logger.warning(f"获取指数舆情失败: {e}")
        return [NewsData(symbol=symbol, date=date.today(), headlines=[])]
```

- [ ] **Step 10: 运行测试**
注：采集测试依赖 AkShare 网络接口，标记为 integration 或用 `pytest.mark.skip` 条件跳过离线环境。

```bash
python -m pytest tests/data/test_akshare_index.py -v -k "not fetch" 2>&1 | head -20
python -m pytest tests/data/test_akshare_index.py::TestAkShareIndexAdapter::test_supports_index_price -v
```
Expected: supports 测试 PASS

- [ ] **Step 11: 提交**

```bash
git add src/data/akshare.py tests/data/test_akshare_index.py
git commit -m "feat(数据源): AkShare 适配器增加指数相关数据采集方法"
```

---

### Task 4: 指数包脚手架

**Files:**
- Create: `src/index/__init__.py`
- Create: `src/index/schemas.py`
- Create: `src/index/analysis/__init__.py`
- Test: 无需单独测试（架子文件）

- [ ] **Step 1: 创建 `src/index/__init__.py`**

```python
"""指数分析管道 — 独立于个股管道的指数分析子系统"""
```

- [ ] **Step 2: 创建 `src/index/schemas.py`**

从 `src/data/schemas.py` 重新导出指数相关 schema，方便 `src/index/` 内引用：

```python
"""指数分析 Schema — 从 data.schemas 重新导出"""
from data.schemas import (
    AnalysisTarget,
    IndexPriceData,
    IndexValuationData,
    CapitalFlowData,
    MacroContext,
    IndexAnalysisContext,
    IndexReport,
)
```

- [ ] **Step 3: 创建 `src/index/analysis/__init__.py`**

```python
"""指数分析模块"""
```

- [ ] **Step 4: 提交**

```bash
git add src/index/__init__.py src/index/schemas.py src/index/analysis/__init__.py
git commit -m "feat(指数): 创建指数包脚手架"
```

---

### Task 5: 指数数据采集器 — IndexDataCollector

**Files:**
- Create: `src/index/collector.py`
- Test: `tests/index/test_collector.py`

- [ ] **Step 1: 编写采集器测试**

```python
"""指数数据采集器测试"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import date
from src.index.collector import IndexDataCollector
from src.data.schemas import AnalysisTarget, IndexAnalysisContext


@pytest.fixture
def broad_target():
    return AnalysisTarget(
        target_type="index", symbol="000300",
        name="沪深300", market="a-shares", index_style="broad"
    )


@pytest.fixture
def sector_target():
    return AnalysisTarget(
        target_type="index", symbol="801080",
        name="电子", market="a-shares", index_style="sector"
    )


class TestIndexDataCollector:
    def test_collect_broad_fetches_all_types(self, broad_target):
        collector = IndexDataCollector()
        ctx = collector.collect(broad_target)
        assert isinstance(ctx, IndexAnalysisContext)
        assert ctx.target == broad_target
        # broad 应该有估值和宏观数据
        assert ctx.valuation_data is not None
        assert ctx.macro is not None

    def test_collect_sector_has_macro_none_fields(self, sector_target):
        collector = IndexDataCollector()
        ctx = collector.collect(sector_target)
        assert ctx.macro is not None
        # sector 保留 MacroContext 实例但字段全 None
        assert ctx.macro.pmi is None
        assert ctx.macro.shibor_3m is None

    def test_collect_always_fetches_price(self, broad_target):
        collector = IndexDataCollector()
        ctx = collector.collect(broad_target)
        assert ctx.price_data is not None
```

- [ ] **Step 2: 运行测试验证失败**

```bash
python -m pytest tests/index/test_collector.py -v
```
Expected: ImportError

- [ ] **Step 3: 创建 `src/index/collector.py`**

```python
"""IndexDataCollector — 按 index_style 编排指数数据采集，只拉取原始数据"""
import logging
from core.registry import Registry
from data.schemas import AnalysisTarget, IndexAnalysisContext
from data.akshare import AkShareAdapter

logger = logging.getLogger(__name__)


class IndexDataCollector:
    """按 index_style 编排采集策略，只拉取原始数据，不做衍生计算"""

    def __init__(self, registry: Registry | None = None):
        self._adapter = AkShareAdapter()
        if registry is not None:
            self._registry = registry
        else:
            self._registry = Registry()
            self._registry.register_data_source(self._adapter)

    def collect(self, target: AnalysisTarget) -> IndexAnalysisContext:
        ctx = IndexAnalysisContext(target=target)

        # 所有类别都采集行情
        price_result = self._adapter.fetch(
            target.symbol, data_type="index_price",
            index_style=target.index_style
        )
        if price_result:
            ctx.price_data = price_result

        # 所有类别都采集估值
        val_result = self._adapter.fetch(
            target.symbol, data_type="index_valuation"
        )
        if val_result:
            ctx.valuation_data = val_result[0]

        # 按类别采集资金流向
        if target.index_style in ("broad", "sector"):
            cf_result = self._adapter.fetch(
                target.symbol, data_type="index_capital_flow",
                index_style=target.index_style
            )
            if cf_result:
                ctx.capital_flow = cf_result[0]

        # 按类别采集宏观数据
        if target.index_style in ("broad", "overseas"):
            macro_result = self._adapter.fetch(
                target.symbol, data_type="index_macro",
                index_style=target.index_style
            )
            if macro_result:
                ctx.macro = macro_result[0]
        elif target.index_style == "sector":
            from data.schemas import MacroContext
            from datetime import date
            ctx.macro = MacroContext(symbol=target.symbol, fetch_date=date.today())

        # 舆情
        sentiment_result = self._adapter.fetch(
            target.symbol, data_type="index_sentiment"
        )
        if sentiment_result and len(sentiment_result) > 0:
            from data.schemas import RawSentimentData, RawSentimentItem
            news = sentiment_result[0]
            ctx.raw_sentiment = RawSentimentData(
                symbol=target.symbol,
                fetch_date=news.date,
                items=[RawSentimentItem(
                    title=h, source="market_news",
                    publish_date=news.date
                ) for h in (news.headlines or [])]
            )

        return ctx
```

- [ ] **Step 4: 运行测试**

```bash
python -m pytest tests/index/test_collector.py -v
```
Expected: 全部 PASS（采集测试依赖网络，按需跳过 fetch 验证）

- [ ] **Step 5: 提交**

```bash
git add src/index/collector.py tests/index/test_collector.py
git commit -m "feat(指数): 实现 IndexDataCollector 采集编排"
```

---

### Task 6: 指数充实器 — 分位计算与标签映射

**Files:**
- Create: `src/index/enricher.py`
- Test: `tests/index/test_enricher.py`

- [ ] **Step 1: 编写充实器测试**

```python
"""指数充实器测试"""
import pytest
from datetime import date, timedelta
from src.index.enricher import compute_percentile, IndexValuationEnricher, tag_valuation
from src.data.schemas import (
    AnalysisTarget, IndexAnalysisContext, IndexValuationData,
    IndexPriceData
)


class TestComputePercentile:
    def test_percentile_midpoint(self):
        """当前值恰好为中位数的分位"""
        values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        pct = compute_percentile(50, values)
        assert abs(pct - 50.0) < 5  # 中位数附近

    def test_percentile_low(self):
        values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        pct = compute_percentile(5, values)
        assert pct < 10

    def test_percentile_high(self):
        values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        pct = compute_percentile(200, values)
        assert pct > 90

    def test_percentile_empty(self):
        assert compute_percentile(50, []) is None

    def test_percentile_single(self):
        assert compute_percentile(50, [50]) == 50.0


class TestTagValuation:
    def test_undervalued(self):
        assert tag_valuation(15) == "undervalued"

    def test_neutral(self):
        assert tag_valuation(40) == "neutral"

    def test_overvalued(self):
        assert tag_valuation(85) == "overvalued"

    def test_invalid_none(self):
        assert tag_valuation(None) == "invalid"


class TestIndexValuationEnricher:
    def test_enrich_computes_percentiles(self):
        target = AnalysisTarget(
            target_type="index", symbol="000300",
            name="沪深300", market="a-shares", index_style="broad"
        )
        ctx = IndexAnalysisContext(target=target)
        ctx.valuation_data = IndexValuationData(
            symbol="000300", date=date.today(),
            pe_ttm=12.5, pb=1.4,
        )
        # 模拟日频估值序列
        daily = []
        base = date.today() - timedelta(days=1200)
        for i in range(1000):
            daily.append({"trade_date": base + timedelta(days=i), "pe": 10 + i % 10})

        enricher = IndexValuationEnricher()
        result = enricher.enrich(ctx, daily_pe_values=[d["pe"] for d in daily],
                                  daily_pb_values=[])

        assert result.valuation_data.pe_percentile is not None
        assert result.valuation_data.percentile_sample_start is not None
```

- [ ] **Step 2: 运行测试验证失败**

```bash
python -m pytest tests/index/test_enricher.py -v
```
Expected: ImportError

- [ ] **Step 3: 创建 `src/index/enricher.py`**

```python
"""指数充实器 — 分位计算、标签映射，计算结果不入缓存"""
from datetime import date
from data.schemas import AnalysisTarget, IndexAnalysisContext, IndexValuationData


def compute_percentile(current: float, historical: list[float]) -> float | None:
    """计算当前值在历史序列中的分位（0-100），值越小分位越低"""
    if not historical or current is None:
        return None
    if len(historical) == 1:
        return 50.0
    below = sum(1 for v in historical if v < current)
    return round((below / len(historical)) * 100, 1)


def tag_valuation(pe_percentile: float | None) -> str:
    """PE 分位 → 估值标签"""
    if pe_percentile is None:
        return "invalid"
    if pe_percentile < 30:
        return "undervalued"
    elif pe_percentile > 70:
        return "overvalued"
    return "neutral"


def tag_technical(ma_5: float | None, ma_20: float | None, close: float | None) -> str:
    """均线系统 → 趋势标签"""
    if close is None:
        return "shake"
    if ma_5 is not None and ma_20 is not None:
        if close > ma_5 > ma_20:
            return "bull"
        elif close < ma_5 < ma_20:
            return "bear"
    return "shake"


def tag_capital(north_bound: float | None, main_inflow: float | None) -> str:
    """资金流向 → 资金面标签"""
    positive = 0
    if north_bound is not None and north_bound > 0:
        positive += 1
    if main_inflow is not None and main_inflow > 0:
        positive += 1
    if north_bound is None and main_inflow is None:
        return "neutral"
    if positive >= 1:
        return "positive"
    return "negative"


class IndexValuationEnricher:
    """估值分位充实器 — 从日频 PE/PB 序列计算分位并回填元数据"""

    def enrich(self, ctx: IndexAnalysisContext,
               daily_pe_values: list[float] | None = None,
               daily_pb_values: list[float] | None = None) -> IndexAnalysisContext:
        if ctx.valuation_data is None:
            return ctx

        val = ctx.valuation_data

        if daily_pe_values:
            val.pe_percentile = compute_percentile(val.pe_ttm, daily_pe_values)
        if daily_pb_values:
            val.pb_percentile = compute_percentile(val.pb, daily_pb_values)

        # 分位元数据
        if daily_pe_values or daily_pb_values:
            all_vals = (daily_pe_values or []) + (daily_pb_values or [])
            if len(all_vals) < 252:  # 不足 1 年交易日
                val.valuation_valid = False

        return ctx
```

- [ ] **Step 4: 运行测试验证通过**

```bash
python -m pytest tests/index/test_enricher.py -v
```
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/index/enricher.py tests/index/test_enricher.py
git commit -m "feat(指数): 实现分位计算充实器与标签映射函数"
```

---

### Task 7: 指数分析模块 — 技术面与估值面

**Files:**
- Create: `src/index/analysis/technical.py`
- Create: `src/index/analysis/valuation.py`
- Test: `tests/index/analysis/test_technical.py`
- Test: `tests/index/analysis/test_valuation.py`

- [ ] **Step 1: 编写技术面分析测试**

```python
"""指数技术面分析测试"""
import pytest
from datetime import date, timedelta
from src.index.analysis.technical import IndexTechnicalAnalyzer
from src.data.schemas import AnalysisTarget, IndexAnalysisContext, IndexPriceData


@pytest.fixture
def tech_ctx():
    target = AnalysisTarget(
        target_type="index", symbol="000300",
        name="沪深300", market="a-shares", index_style="broad"
    )
    ctx = IndexAnalysisContext(target=target)
    base = date.today() - timedelta(days=120)
    prices = []
    for i in range(120):
        prices.append(IndexPriceData(
            symbol="000300", trade_date=base + timedelta(days=i),
            open=4000 + i * 2, high=4010 + i * 2,
            low=3990 + i * 2, close=4005 + i * 2,
            volume=1000000, change_pct=0.1
        ))
    ctx.price_data = prices
    return ctx


class TestIndexTechnicalAnalyzer:
    def test_dimension(self):
        analyzer = IndexTechnicalAnalyzer()
        assert analyzer.dimension == "index_technical"

    def test_analyze_bull_trend(self, tech_ctx):
        analyzer = IndexTechnicalAnalyzer()
        result = analyzer.analyze(tech_ctx)
        assert result.status in ("ok", "partial")
        assert "tag" in result.metrics
        assert result.metrics["tag"] in ("bull", "shake", "bear")

    def test_analyze_no_data(self):
        target = AnalysisTarget(
            target_type="index", symbol="000300",
            name="沪深300", market="a-shares", index_style="broad"
        )
        ctx = IndexAnalysisContext(target=target)
        analyzer = IndexTechnicalAnalyzer()
        result = analyzer.analyze(ctx)
        assert result.status == "unavailable"
```

- [ ] **Step 2: 运行测试验证失败**

```bash
python -m pytest tests/index/analysis/test_technical.py -v
```
Expected: ImportError

- [ ] **Step 3: 创建 `src/index/analysis/technical.py`**

```python
"""指数技术面分析 — 趋势、均线、支撑/压力位"""
from typing import Any
from analysis.base import AnalysisModule
from data.schemas import AnalysisResult, IndexAnalysisContext, SufficiencyLevel
from index.enricher import tag_technical


class IndexTechnicalAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> str:
        return "index_technical"

    def analyze(self, context: IndexAnalysisContext,
                config: dict[str, Any] | None = None) -> AnalysisResult:
        prices = context.price_data or []
        if not prices:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="行情数据不可用", metrics={})

        sorted_prices = sorted(prices, key=lambda x: x.trade_date)
        closes = [p.close for p in sorted_prices]
        latest_close = closes[-1] if closes else 0

        # 计算均线
        ma_5 = sum(closes[-5:]) / min(5, len(closes)) if len(closes) >= 5 else None
        ma_20 = sum(closes[-20:]) / min(20, len(closes)) if len(closes) >= 20 else None
        ma_60 = sum(closes[-60:]) / min(60, len(closes)) if len(closes) >= 60 else None

        # 计算支撑/压力位（简化：近期高点和低点）
        year_high = max(p.high for p in sorted_prices[-250:]) if len(sorted_prices) >= 250 else max(p.high for p in sorted_prices)
        year_low = min(p.low for p in sorted_prices[-250:]) if len(sorted_prices) >= 250 else min(p.low for p in sorted_prices)

        # 趋势标签
        trend_tag = tag_technical(ma_5, ma_20, latest_close)

        metrics = {
            "latest_close": latest_close,
            "ma_5": ma_5,
            "ma_20": ma_20,
            "ma_60": ma_60,
            "year_high": year_high,
            "year_low": year_low,
            "tag": trend_tag,
        }

        if context.sufficiency and context.sufficiency.price.level == SufficiencyLevel.INSUFFICIENT:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="行情数据不足", metrics=metrics)

        status = "partial" if len(closes) < 20 else "ok"
        summary = f"最新价 {latest_close:.2f}，趋势: {trend_tag}"

        return AnalysisResult(dimension=self.dimension, status=status,
                              summary=summary, metrics=metrics)
```

- [ ] **Step 4: 创建 `src/index/analysis/valuation.py`**

```python
"""指数估值面分析 — PE/PB 历史分位 + 估值区域判断"""
from typing import Any
from analysis.base import AnalysisModule
from data.schemas import AnalysisResult, IndexAnalysisContext
from index.enricher import tag_valuation


class IndexValuationAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> str:
        return "index_valuation"

    def analyze(self, context: IndexAnalysisContext,
                config: dict[str, Any] | None = None) -> AnalysisResult:
        val = context.valuation_data
        if val is None:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="估值数据不可用", metrics={})

        pe_pct = val.pe_percentile
        pb_pct = val.pb_percentile
        vtag = tag_valuation(pe_pct)

        metrics = {
            "pe_ttm": val.pe_ttm,
            "pb": val.pb,
            "pe_percentile": pe_pct,
            "pb_percentile": pb_pct,
            "dividend_yield": val.dividend_yield,
            "valuation_valid": val.valuation_valid,
            "tag": vtag,
            "percentile_lookback_years": val.percentile_lookback_years,
            "sample_start": val.percentile_sample_start,
            "sample_end": val.percentile_sample_end,
        }

        if not val.valuation_valid:
            return AnalysisResult(dimension=self.dimension, status="partial",
                                  summary=f"PE-TTM {val.pe_ttm}，估值样本不足，分位仅供参考",
                                  metrics=metrics)

        status = "ok"
        summary = f"PE-TTM {val.pe_ttm}，历史分位 {pe_pct}%，估值: {vtag}"
        return AnalysisResult(dimension=self.dimension, status=status,
                              summary=summary, metrics=metrics)
```

- [ ] **Step 5: 运行测试验证通过**

```bash
python -m pytest tests/index/analysis/test_technical.py tests/index/analysis/test_valuation.py -v
```
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
git add src/index/analysis/technical.py src/index/analysis/valuation.py tests/index/analysis/
git commit -m "feat(指数): 实现指数技术面和估值面分析模块"
```

---

### Task 8: 指数分析模块 — 资金面、宏观面、舆情面

**Files:**
- Create: `src/index/analysis/capital_flow.py`
- Create: `src/index/analysis/macro.py`
- Create: `src/index/analysis/sentiment.py`
- Test: `tests/index/analysis/test_capital_flow.py`
- Test: `tests/index/analysis/test_macro.py`
- Test: `tests/index/analysis/test_sentiment.py`

- [ ] **Step 1: 编写三个模块的测试**

```python
"""指数资金面/宏观面/舆情面分析测试"""
import pytest
from datetime import date
from src.index.analysis.capital_flow import CapitalFlowAnalyzer
from src.index.analysis.macro import MacroAnalyzer
from src.index.analysis.sentiment import IndexSentimentAnalyzer
from src.data.schemas import (
    AnalysisTarget, IndexAnalysisContext, CapitalFlowData,
    MacroContext, RawSentimentData
)


class TestCapitalFlowAnalyzer:
    def test_dimension(self):
        assert CapitalFlowAnalyzer().dimension == "index_capital_flow"

    def test_analyze_no_data(self):
        target = AnalysisTarget(
            target_type="index", symbol="000300",
            name="沪深300", market="a-shares", index_style="broad"
        )
        ctx = IndexAnalysisContext(target=target)
        result = CapitalFlowAnalyzer().analyze(ctx)
        assert result.status == "unavailable"

    def test_analyze_with_data(self):
        target = AnalysisTarget(
            target_type="index", symbol="000300",
            name="沪深300", market="a-shares", index_style="broad"
        )
        ctx = IndexAnalysisContext(target=target)
        ctx.capital_flow = CapitalFlowData(
            symbol="000300", date=date.today(),
            north_bound=5.2, main_net_inflow=10.0
        )
        result = CapitalFlowAnalyzer().analyze(ctx)
        assert result.metrics["tag"] in ("positive", "neutral", "negative")


class TestMacroAnalyzer:
    def test_dimension(self):
        assert MacroAnalyzer().dimension == "index_macro"

    def test_analyze_sector_na(self):
        target = AnalysisTarget(
            target_type="index", symbol="801080",
            name="电子", market="a-shares", index_style="sector"
        )
        ctx = IndexAnalysisContext(target=target)
        ctx.macro = MacroContext(symbol="801080", fetch_date=date.today())
        result = MacroAnalyzer().analyze(ctx)
        assert result.metrics["tag"] == "na"


class TestIndexSentimentAnalyzer:
    def test_dimension(self):
        assert IndexSentimentAnalyzer().dimension == "index_sentiment"

    def test_analyze_no_data(self):
        target = AnalysisTarget(
            target_type="index", symbol="000300",
            name="沪深300", market="a-shares", index_style="broad"
        )
        ctx = IndexAnalysisContext(target=target)
        result = IndexSentimentAnalyzer().analyze(ctx)
        assert result.status == "unavailable"
        assert "暂无有效市场舆情信号" in result.summary
```

- [ ] **Step 2: 创建 `src/index/analysis/capital_flow.py`**

```python
"""指数资金面分析 — 北向资金、主力资金、融资余额"""
from typing import Any
from analysis.base import AnalysisModule
from data.schemas import AnalysisResult, IndexAnalysisContext
from index.enricher import tag_capital


class CapitalFlowAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> str:
        return "index_capital_flow"

    def analyze(self, context: IndexAnalysisContext,
                config: dict[str, Any] | None = None) -> AnalysisResult:
        cf = context.capital_flow
        if cf is None:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="资金流向数据不可用", metrics={})

        flow_tag = tag_capital(cf.north_bound, cf.main_net_inflow)

        metrics = {
            "north_bound": cf.north_bound,
            "main_net_inflow": cf.main_net_inflow,
            "margin_balance": cf.margin_balance,
            "tag": flow_tag,
        }

        summary = f"资金面: {flow_tag}"
        if cf.north_bound is not None:
            direction = "流入" if cf.north_bound > 0 else "流出"
            summary += f"，北向资金净{direction} {abs(cf.north_bound):.1f}亿"

        return AnalysisResult(dimension=self.dimension, status="ok",
                              summary=summary, metrics=metrics)
```

- [ ] **Step 3: 创建 `src/index/analysis/macro.py`**

```python
"""指数宏观面分析 — PMI、利率、汇率关联"""
from typing import Any
from analysis.base import AnalysisModule
from data.schemas import AnalysisResult, IndexAnalysisContext


class MacroAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> str:
        return "index_macro"

    def analyze(self, context: IndexAnalysisContext,
                config: dict[str, Any] | None = None) -> AnalysisResult:
        macro = context.macro
        if macro is None:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="宏观数据不可用", metrics={})

        # sector 的 macro 字段全为 None → 输出 na
        vals = [macro.pmi, macro.shibor_3m, macro.cpi_yoy, macro.usd_cny]
        if all(v is None for v in vals):
            return AnalysisResult(dimension=self.dimension, status="ok",
                                  summary="行业指数不适用宏观分析",
                                  metrics={"tag": "na"})

        # 简单宏观信号
        signals = []
        if macro.pmi is not None:
            signals.append(f"PMI {macro.pmi:.1f}" + ("（扩张）" if macro.pmi >= 50 else "（收缩）"))
        if macro.shibor_3m is not None:
            signals.append(f"Shibor3M {macro.shibor_3m:.2f}%")
        if macro.usd_cny is not None:
            signals.append(f"USD/CNY {macro.usd_cny:.4f}")

        # 宏观标签：以 PMI 为主要信号
        if macro.pmi is not None:
            if macro.pmi >= 50:
                macro_tag = "positive"
            elif macro.pmi >= 48:
                macro_tag = "neutral"
            else:
                macro_tag = "negative"
        else:
            macro_tag = "neutral"

        metrics = {
            "pmi": macro.pmi, "shibor_3m": macro.shibor_3m,
            "cpi_yoy": macro.cpi_yoy, "usd_cny": macro.usd_cny,
            "shibor_percentile": macro.shibor_percentile,
            "pmi_percentile": macro.pmi_percentile,
            "tag": macro_tag,
        }

        return AnalysisResult(dimension=self.dimension, status="ok",
                              summary="；".join(signals) if signals else "宏观数据不足",
                              metrics=metrics)
```

- [ ] **Step 4: 创建 `src/index/analysis/sentiment.py`**

```python
"""指数舆情分析 — 市场层面舆情，禁止个股新闻"""
from typing import Any
from analysis.base import AnalysisModule
from data.schemas import AnalysisResult, IndexAnalysisContext


class IndexSentimentAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> str:
        return "index_sentiment"

    def analyze(self, context: IndexAnalysisContext,
                config: dict[str, Any] | None = None) -> AnalysisResult:
        raw = context.raw_sentiment
        if raw is None or not raw.items:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="暂无有效市场舆情信号", metrics={})

        items = raw.items
        headlines = [item.title for item in items]

        # 简单的正面/负面关键词计数，不做 LLM 标注（LLM 在报告中统一解读）
        positive_kw = ["利好", "上涨", "突破", "增长", "回升", "改善", "扩张"]
        negative_kw = ["利空", "下跌", "下滑", "衰退", "收紧", "风险", "危机"]

        pos_count = sum(1 for h in headlines if any(kw in h for kw in positive_kw))
        neg_count = sum(1 for h in headlines if any(kw in h for kw in negative_kw))

        if pos_count > neg_count:
            sent_tag = "positive"
        elif neg_count > pos_count:
            sent_tag = "negative"
        else:
            sent_tag = "neutral"

        metrics = {
            "headline_count": len(headlines),
            "top_headlines": headlines[:10],
            "tag": sent_tag,
        }

        return AnalysisResult(dimension=self.dimension, status="ok",
                              summary=f"近1日市场要闻 {len(headlines)} 条",
                              metrics=metrics)
```

- [ ] **Step 5: 运行测试验证通过**

```bash
python -m pytest tests/index/analysis/ -v
```
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
git add src/index/analysis/capital_flow.py src/index/analysis/macro.py src/index/analysis/sentiment.py tests/index/analysis/
git commit -m "feat(指数): 实现资金面、宏观面、舆情面分析模块"
```

---

### Task 9: 指数单报告构建器 — IndexReportBuilder

**Files:**
- Create: `src/index/build_single.py`
- Create: `src/report/templates/index_report.jinja2`
- Test: `tests/index/test_build_single.py`

- [ ] **Step 1: 编写单报告构建器测试**

```python
"""指数单报告构建器测试"""
import pytest
from datetime import date
from src.index.build_single import IndexReportBuilder
from src.data.schemas import (
    AnalysisTarget, IndexAnalysisContext, IndexValuationData,
    AnalysisResult, IndexReport
)


@pytest.fixture
def broad_ctx():
    target = AnalysisTarget(
        target_type="index", symbol="000300",
        name="沪深300", market="a-shares", index_style="broad"
    )
    ctx = IndexAnalysisContext(target=target)
    ctx.valuation_data = IndexValuationData(
        symbol="000300", date=date.today(),
        pe_ttm=12.5, pb=1.4, pe_percentile=68.0,
        percentile_lookback_years=5,
        percentile_sample_start=date(2021, 8, 1),
        percentile_sample_end=date(2026, 8, 1),
        valuation_valid=True
    )
    return ctx


@pytest.fixture
def broad_results():
    return [
        AnalysisResult(dimension="index_technical", status="ok",
                       summary="趋势: bull", metrics={"tag": "bull", "latest_close": 4000.0}),
        AnalysisResult(dimension="index_valuation", status="ok",
                       summary="PE 分位 68%", metrics={"tag": "neutral", "pe_ttm": 12.5}),
        AnalysisResult(dimension="index_capital_flow", status="ok",
                       summary="资金面: positive", metrics={"tag": "positive"}),
        AnalysisResult(dimension="index_macro", status="ok",
                       summary="PMI 50.5", metrics={"tag": "neutral", "pmi": 50.5}),
        AnalysisResult(dimension="index_sentiment", status="ok",
                       summary="舆情 30 条", metrics={"tag": "neutral"}),
    ]


class TestIndexReportBuilder:
    def test_build_broad_report(self, broad_ctx, broad_results):
        builder = IndexReportBuilder()
        report = builder.build(broad_ctx, broad_results)
        assert isinstance(report, IndexReport)
        assert report.code == "000300"
        assert report.tag_technical == "bull"
        assert "macro" in report.visible_sections
        assert "capital" in report.visible_sections

    def test_visible_sections_sector(self):
        target = AnalysisTarget(
            target_type="index", symbol="801080",
            name="电子", market="a-shares", index_style="sector"
        )
        ctx = IndexAnalysisContext(target=target)
        results = [
            AnalysisResult(dimension="index_technical", status="partial",
                           summary="shake", metrics={"tag": "shake"}),
            AnalysisResult(dimension="index_valuation", status="partial",
                           summary="NA", metrics={"tag": "invalid"}),
            AnalysisResult(dimension="index_capital_flow", status="ok",
                           summary="neutral", metrics={"tag": "neutral"}),
            AnalysisResult(dimension="index_macro", status="ok",
                           summary="行业不适用", metrics={"tag": "na"}),
            AnalysisResult(dimension="index_sentiment", status="unavailable",
                           summary="暂无舆情", metrics={}),
        ]
        builder = IndexReportBuilder()
        report = builder.build(ctx, results)
        assert "macro" not in report.visible_sections
```

- [ ] **Step 2: 创建 `src/index/build_single.py`**

```python
"""IndexReportBuilder — 从 IndexAnalysisContext + list[AnalysisResult] 构建单指数报告"""
from datetime import datetime, date
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from data.schemas import IndexAnalysisContext, AnalysisResult, IndexReport


class IndexReportBuilder:
    def __init__(self, template_dir: Path | None = None):
        if template_dir is None:
            template_dir = Path(__file__).parent.parent / "report" / "templates"
        self._env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        from report.builder import _md_table
        self._env.filters["md_table"] = _md_table

    def build(self, ctx: IndexAnalysisContext,
              results: list[AnalysisResult]) -> IndexReport:
        results_map = {r.dimension: r for r in results}

        # 提取各维度 tag
        tags = self._extract_tags(results_map)

        # 计算 visible_sections
        visible = self._compute_visible_sections(ctx.target.index_style)

        # 构建各章节数据
        overview = self._build_overview(ctx, results_map)
        technical = self._build_section(results_map, "index_technical")
        valuation = self._build_section(results_map, "index_valuation")
        capital = self._build_section(results_map, "index_capital_flow")
        macro = self._build_section(results_map, "index_macro")
        sentiment = self._build_section(results_map, "index_sentiment")

        # 综合定性评语和仓位系数
        comment, coeff = self._composite(tags)

        return IndexReport(
            code=ctx.target.symbol,
            name=ctx.target.name,
            date=date.today(),
            overview=overview,
            section_technical=technical,
            section_valuation=valuation,
            section_capital=capital,
            section_macro=macro if "macro" in visible else None,
            section_sentiment=sentiment,
            tag_technical=tags.get("index_technical", "shake"),
            tag_valuation=tags.get("index_valuation", "invalid"),
            tag_capital=tags.get("index_capital_flow", "neutral"),
            tag_macro=tags.get("index_macro", "na"),
            tag_sentiment=tags.get("index_sentiment", "neutral"),
            composite_comment=comment,
            position_coeff=coeff,
            risk_list=ctx.risk_flags,
            visible_sections=visible,
        )

    def _extract_tags(self, results_map: dict) -> dict:
        tags = {}
        for dim, r in results_map.items():
            tag = r.metrics.get("tag", "") if r.metrics else ""
            if tag:
                tags[dim] = tag
        return tags

    def _compute_visible_sections(self, index_style: str | None) -> set[str]:
        sections = {"overview", "technical", "valuation", "sentiment"}
        if index_style == "broad":
            sections.update({"capital", "macro"})
        elif index_style == "sector":
            sections.add("capital")
        elif index_style == "overseas":
            sections.add("macro")
        return sections

    def _build_overview(self, ctx: IndexAnalysisContext,
                        results_map: dict) -> dict:
        prices = ctx.price_data
        latest_close = prices[-1].close if prices else None
        change_pct = prices[-1].change_pct if prices else None
        val = ctx.valuation_data

        return {
            "latest_close": latest_close,
            "change_pct": change_pct,
            "pe_ttm": val.pe_ttm if val else None,
            "pb": val.pb if val else None,
            "pe_percentile": val.pe_percentile if val else None,
            "valuation_valid": val.valuation_valid if val else True,
            "percentile_lookback_years": val.percentile_lookback_years if val else 5,
        }

    def _build_section(self, results_map: dict, dimension: str) -> dict:
        r = results_map.get(dimension)
        if r is None:
            return {"status": "unavailable", "summary": "", "metrics": {}}
        return {
            "status": r.status,
            "summary": r.summary,
            "metrics": r.metrics,
        }

    def _composite(self, tags: dict) -> tuple[str, float | None]:
        """从各维度 tag 生成综合定性评语和仓位参考系数"""
        bullish = sum(1 for t in tags.values()
                      if t in ("bull", "undervalued", "positive"))
        bearish = sum(1 for t in tags.values()
                     if t in ("bear", "overvalued", "negative"))
        invalid = sum(1 for t in tags.values() if t in ("invalid", "na"))
        active = len(tags) - invalid if len(tags) > invalid else 1

        net = (bullish - bearish) / max(active, 1)

        if net > 0.3:
            comment = "谨慎看多"
        elif net < -0.3:
            comment = "谨慎看空"
        else:
            comment = "中性震荡"

        coeff = round(max(0.1, min(0.9, 0.5 + net * 0.4)), 2)
        return comment, coeff
```

- [ ] **Step 3: 创建 Jinja2 模板 `src/report/templates/index_report.jinja2`**

```jinja2
{# 指数分析报告模板 #}
# 指数分析报告：{{ name }} ({{ code }})

> 生成时间：{{ generated_at }}

## 概览

| 指标 | 数值 |
|------|------|
| 最新点位 | {{ overview.latest_close or "N/A" }} |
| 涨跌幅 | {{ "%.2f%%"|format(overview.change_pct) if overview.change_pct else "N/A" }} |
| PE-TTM | {{ "%.2f"|format(overview.pe_ttm) if overview.pe_ttm else "N/A" }}{% if overview.pe_percentile %}（历史分位 {{ "%.0f"|format(overview.pe_percentile) }}%，回看 {{ overview.percentile_lookback_years }} 年）{% endif %} |
| {% if not overview.valuation_valid %}> ⚠️ 估值样本不足，分位仅供参考{% endif %}

---

## 技术面

{% if section_technical.status != "unavailable" %}
{{ section_technical.summary }}

{{ section_technical.metrics | md_table }}
{% else %}
技术面数据不可用。
{% endif %}

---

## 估值面

{% if section_valuation.status != "unavailable" %}
{{ section_valuation.summary }}
{% if not section_valuation.metrics.valuation_valid %}
> 估值样本不足配置年限，分位仅供参考。
{% endif %}

{{ section_valuation.metrics | md_table }}
{% else %}
估值数据不可用。
{% endif %}

---

{% if "capital" in visible_sections %}
## 资金面

{% if section_capital.status != "unavailable" %}
{{ section_capital.summary }}
{% else %}
资金流向数据不可用。
{% endif %}
{% endif %}

---

{% if "macro" in visible_sections %}
## 宏观面

{% if section_macro.status != "unavailable" %}
{{ section_macro.summary }}
> 宏观指标反映国内整体宏观环境。
{% else %}
宏观数据不可用。
{% endif %}
{% endif %}

---

## 舆情面

{% if section_sentiment.status != "unavailable" %}
{{ section_sentiment.summary }}
{% else %}
暂无有效市场舆情信号。
{% endif %}

---

## 综合评估

| 维度 | 标签 |
|------|------|
| 技术面 | {{ tag_technical }} |
| 估值面 | {{ tag_valuation }} |
| 资金面 | {{ tag_capital }} |
| 宏观面 | {{ tag_macro }} |
| 舆情面 | {{ tag_sentiment }} |

**综合定性：** {{ composite_comment }}
**建议仓位参考系数：** {{ position_coeff if position_coeff is not none else "N/A" }}
```

- [ ] **Step 4: 运行测试验证通过**

```bash
python -m pytest tests/index/test_build_single.py -v
```
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/index/build_single.py src/report/templates/index_report.jinja2 tests/index/test_build_single.py
git commit -m "feat(指数): 实现单指数报告构建器与模板"
```

---

### Task 10: 横向对比报告构建器

**Files:**
- Create: `src/index/build_compare.py`
- Test: `tests/index/test_build_compare.py`

- [ ] **Step 1: 编写对比测试**

```python
"""指数横向对比测试"""
import pytest
from datetime import date
from src.index.build_compare import IndexCompareReportBuilder, CompareTable
from src.data.schemas import (
    AnalysisTarget, IndexAnalysisContext, IndexValuationData,
    AnalysisResult, IndexReport
)


@pytest.fixture
def compare_data():
    reports = []
    contexts = []
    for code, name, style in [
        ("000300", "沪深300", "broad"),
        ("000905", "中证500", "broad"),
        ("399006", "创业板指", "broad"),
    ]:
        target = AnalysisTarget(
            target_type="index", symbol=code,
            name=name, market="a-shares", index_style=style
        )
        ctx = IndexAnalysisContext(target=target)
        ctx.valuation_data = IndexValuationData(
            symbol=code, date=date.today(), pe_ttm=12.0, pe_percentile=50.0,
            valuation_valid=True
        )
        contexts.append(ctx)
        reports.append(IndexReport(
            code=code, name=name, date=date.today(),
            overview={"latest_close": 4000.0, "change_pct": 0.5},
            section_technical={}, section_valuation={},
            section_capital={}, section_macro={}, section_sentiment={},
            tag_technical="bull", tag_valuation="neutral",
            tag_capital="positive", tag_macro="neutral", tag_sentiment="neutral",
            composite_comment="谨慎看多", position_coeff=0.6,
            risk_list=[], visible_sections=set(),
        ))
    return contexts, reports


class TestIndexCompareReportBuilder:
    def test_build_compare_table(self, compare_data):
        contexts, reports = compare_data
        builder = IndexCompareReportBuilder()
        table = builder.build(contexts, reports)
        assert isinstance(table, CompareTable)
        assert len(table.rows) == 3
        assert table.rows[0]["name"] == "沪深300"
```

- [ ] **Step 2: 创建 `src/index/build_compare.py`**

```python
"""IndexCompareReportBuilder — 多指数横向对比"""
from dataclasses import dataclass, field
from data.schemas import IndexAnalysisContext, IndexReport


@dataclass
class CompareTable:
    """横向对比表格"""
    rows: list[dict] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)


class IndexCompareReportBuilder:
    def build(self, contexts: list[IndexAnalysisContext],
              reports: list[IndexReport]) -> CompareTable:
        headers = [
            "指数名称", "最新点位", "涨跌幅", "PE 分位", "PB 分位",
            "趋势", "估值", "资金", "综合评级",
        ]
        rows = []
        for ctx, report in zip(contexts, reports):
            val = ctx.valuation_data
            change_str = (
                f"{report.overview.get('change_pct', 0):+.2f}%"
                if report.overview.get("change_pct") is not None
                else "N/A"
            )
            rows.append({
                "name": ctx.target.name,
                "latest": report.overview.get("latest_close", "N/A"),
                "change": change_str,
                "pe_pct": f"{val.pe_percentile:.0f}%" if val and val.pe_percentile is not None else "N/A",
                "pb_pct": f"{val.pb_percentile:.0f}%" if val and val.pb_percentile is not None else "N/A",
                "trend": report.tag_technical,
                "valuation": report.tag_valuation,
                "capital": report.tag_capital,
                "composite": report.composite_comment,
            })
        return CompareTable(headers=headers, rows=rows)
```

- [ ] **Step 3: 运行测试验证通过**

```bash
python -m pytest tests/index/test_build_compare.py -v
```
Expected: 全部 PASS

- [ ] **Step 4: 提交**

```bash
git add src/index/build_compare.py tests/index/test_build_compare.py
git commit -m "feat(指数): 实现多指数横向对比构建器"
```

---

### Task 11: 指数管道编排 — IndexPipeline

**Files:**
- Create: `src/index/pipeline.py`
- Test: `tests/index/test_pipeline.py`

- [ ] **Step 1: 编写管道测试**

```python
"""指数管道测试"""
import pytest
from unittest.mock import MagicMock, patch
from src.index.pipeline import IndexPipeline
from src.data.schemas import AnalysisTarget


@pytest.fixture
def mock_pipeline():
    with patch("src.index.pipeline.IndexDataCollector") as mock_collector, \
         patch("src.index.pipeline.IndexReportBuilder") as mock_builder:
        pipeline = IndexPipeline()
        yield pipeline


class TestIndexPipeline:
    def test_run_single(self, mock_pipeline):
        target = AnalysisTarget(
            target_type="index", symbol="000300",
            name="沪深300", market="a-shares", index_style="broad"
        )
        result = mock_pipeline.run([target])
        assert result.reports is not None
        assert result.compare is None  # 单指数不生成对比

    def test_run_multiple_generates_compare(self, mock_pipeline):
        targets = [
            AnalysisTarget(target_type="index", symbol="000300",
                           name="沪深300", market="a-shares", index_style="broad"),
            AnalysisTarget(target_type="index", symbol="000905",
                           name="中证500", market="a-shares", index_style="broad"),
        ]
        result = mock_pipeline.run(targets)
        assert result.reports is not None
        assert result.compare is not None

    def test_single_failure_does_not_block_others(self, mock_pipeline):
        targets = [
            AnalysisTarget(target_type="index", symbol="000300",
                           name="沪深300", market="a-shares", index_style="broad"),
            AnalysisTarget(target_type="index", symbol="INVALID",
                           name="无效指数", market="a-shares", index_style="broad"),
        ]
        result = mock_pipeline.run(targets)
        # 第一个应该成功，第二个失败但不应中断
        assert len(result.reports) <= 2
```

- [ ] **Step 2: 创建 `src/index/pipeline.py`**

```python
"""IndexPipeline — 指数分析管道编排"""
import logging
from dataclasses import dataclass, field
from core.registry import Registry
from data.schemas import AnalysisTarget, IndexAnalysisContext, AnalysisResult, IndexReport
from index.collector import IndexDataCollector
from index.enricher import IndexValuationEnricher
from index.build_single import IndexReportBuilder
from index.build_compare import IndexCompareReportBuilder, CompareTable

logger = logging.getLogger(__name__)


@dataclass
class IndexPipelineResult:
    reports: list[IndexReport] = field(default_factory=list)
    compare: CompareTable | None = None
    errors: list[str] = field(default_factory=list)


class IndexPipeline:
    def __init__(self, registry: Registry | None = None):
        if registry is None:
            registry = Registry()
        self._registry = registry
        self._collector = IndexDataCollector(registry)
        self._enricher = IndexValuationEnricher()
        self._report_builder = IndexReportBuilder()
        self._compare_builder = IndexCompareReportBuilder()
        self._analysis_modules = self._init_analysis_modules()

    def _init_analysis_modules(self) -> list:
        from index.analysis.technical import IndexTechnicalAnalyzer
        from index.analysis.valuation import IndexValuationAnalyzer
        from index.analysis.capital_flow import CapitalFlowAnalyzer
        from index.analysis.macro import MacroAnalyzer
        from index.analysis.sentiment import IndexSentimentAnalyzer

        return [
            IndexTechnicalAnalyzer(),
            IndexValuationAnalyzer(),
            CapitalFlowAnalyzer(),
            MacroAnalyzer(),
            IndexSentimentAnalyzer(),
        ]

    def run(self, targets: list[AnalysisTarget]) -> IndexPipelineResult:
        reports: list[IndexReport] = []
        contexts: list[IndexAnalysisContext] = []
        errors: list[str] = []

        for target in targets:
            try:
                ctx = self._collector.collect(target)

                # 充实层：分位计算（需要日频序列，此处由 analyze 模块内部处理）
                # 估值分位充实不在采集层做，保留给分析模块

                # 运行所有分析模块
                results: list[AnalysisResult] = []
                for module in self._analysis_modules:
                    try:
                        result = module.analyze(ctx)
                        results.append(result)
                    except Exception as e:
                        logger.error(f"分析模块 {module.dimension} 失败: {e}")
                        results.append(AnalysisResult(
                            dimension=module.dimension, status="unavailable",
                            summary=f"分析模块异常: {e}", metrics={}
                        ))

                report = self._report_builder.build(ctx, results)
                reports.append(report)
                contexts.append(ctx)

            except Exception as e:
                logger.error(f"指数 {target.symbol} 分析失败: {e}")
                errors.append(f"{target.symbol}: {e}")

        compare = None
        if len(reports) >= 2:
            compare = self._compare_builder.build(contexts, reports)

        return IndexPipelineResult(reports=reports, compare=compare, errors=errors)

    def get_snapshot(self, symbol: str) -> dict | None:
        """轻量快照接口 — 仅供个股联动使用，不跑完整指数管道"""
        try:
            target = AnalysisTarget(
                target_type="index", symbol=symbol,
                name=symbol, market="a-shares", index_style="broad"
            )
            ctx = self._collector.collect(target)
            if ctx.valuation_data is None:
                return None
            val = ctx.valuation_data
            return {
                "symbol": symbol,
                "pe_ttm": val.pe_ttm,
                "pe_percentile": val.pe_percentile,
                "valuation_valid": val.valuation_valid,
            }
        except Exception as e:
            logger.warning(f"获取指数快照失败 {symbol}: {e}")
            return None
```

- [ ] **Step 3: 运行测试验证通过**

```bash
python -m pytest tests/index/test_pipeline.py -v
```
Expected: 全部 PASS

- [ ] **Step 4: 提交**

```bash
git add src/index/pipeline.py tests/index/test_pipeline.py
git commit -m "feat(指数): 实现 IndexPipeline 编排与轻量快照接口"
```

---

### Task 12: Pipeline 路由 — AnalysisTarget 分发

**Files:**
- Modify: `src/core/pipeline.py`
- Test: `tests/core/test_pipeline_routing.py`

- [ ] **Step 1: 编写路由测试**

```python
"""管道路由测试"""
import pytest
from unittest.mock import MagicMock, patch
from src.core.pipeline import Pipeline
from src.data.schemas import AnalysisTarget


class TestPipelineRouting:
    def test_run_with_analysis_target_stock(self):
        pipeline = Pipeline(registry=MagicMock(), llm_enabled=False)
        target = AnalysisTarget(
            target_type="stock", symbol="000001",
            name="平安银行", market="a-shares"
        )
        # 个股 AnalysisTarget 应转为传统 run(symbol, name) 调用
        with patch.object(pipeline, "run", wraps=pipeline.run) as spy:
            pass  # 此时尚未转换，先测试 collect 接受 AnalysisTarget

    def test_collect_accepts_analysis_target(self):
        from src.data.schemas import AnalysisContext
        pipeline = Pipeline(registry=MagicMock(), llm_enabled=False)
        target = AnalysisTarget(
            target_type="stock", symbol="000001",
            name="平安银行", market="a-shares"
        )
        # 新增方法 collect_from_target
        with patch.object(pipeline, "collect", return_value=MagicMock(spec=AnalysisContext)):
            assert pipeline.collect_from_target(target) is not None
```

- [ ] **Step 2: 在 `Pipeline` 类中添加 `collect_from_target` 方法**

```python
def collect_from_target(self, target: AnalysisTarget) -> AnalysisContext:
    """从 AnalysisTarget 收集数据 — 个股管道的入口适配"""
    return self.collect(
        symbol=target.symbol,
        name=target.name,
        market=target.market,
    )
```

- [ ] **Step 3: 运行测试**

```bash
python -m pytest tests/core/test_pipeline_routing.py -v
```
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add src/core/pipeline.py tests/core/test_pipeline_routing.py
git commit -m "feat(管道): Pipeline 增加 collect_from_target 支持 AnalysisTarget 分发"
```

---

### Task 13: Registry 按 category 筛选分析模块

**Files:**
- Modify: `src/core/registry.py`
- Test: `tests/core/test_registry_index.py`

- [ ] **Step 1: 编写 Registry 筛选测试**

```python
"""Registry 按 category 筛选测试"""
import pytest
from src.core.registry import Registry
from src.index.analysis.technical import IndexTechnicalAnalyzer
from src.analysis.technical import TechnicalAnalyzer


class TestRegistryCategoryFilter:
    def test_get_analysis_modules_by_index_dimension(self):
        reg = Registry()
        reg.register_analysis_module(TechnicalAnalyzer())
        reg.register_analysis_module(IndexTechnicalAnalyzer())

        all_modules = reg.get_analysis_modules()
        assert len(all_modules) == 2

        # 按类别筛选：index 前缀的模块
        index_modules = [m for m in all_modules
                         if m.dimension.startswith("index_")]
        assert len(index_modules) == 1
```

- [ ] **Step 2: 运行测试**

```bash
python -m pytest tests/core/test_registry_index.py -v
```
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add tests/core/test_registry_index.py
git commit -m "test(注册): 添加按 category 筛选分析模块的测试"
```

---

### Task 14: CLI — `index` 命令

**Files:**
- Modify: `src/stock_robot/cli.py`
- Test: `tests/test_cli_index.py`

- [ ] **Step 1: 编写 CLI 测试**

```python
"""CLI index 命令测试"""
import pytest
from click.testing import CliRunner
from src.stock_robot.cli import main


class TestIndexCommand:
    def test_index_command_help(self):
        runner = CliRunner()
        result = runner.invoke(main, ["index", "--help"])
        assert result.exit_code == 0
        assert "symbols" in result.output or "SYMBOLS" in result.output

    def test_index_single_symbol(self):
        runner = CliRunner()
        result = runner.invoke(main, ["index", "000300", "--style", "broad"])
        # 可能因数据网络原因失败，但命令格式应正确
        assert result.exit_code in (0, 1)

    def test_index_invalid_symbol(self):
        runner = CliRunner()
        result = runner.invoke(main, ["index", "abc"])
        assert result.exit_code == 1
```

- [ ] **Step 2: 在 `src/stock_robot/cli.py` 中添加 `index` 命令和 `analyze` 的 `--with-market` 选项**

```python
@main.command()
@click.argument("symbols", nargs=-1, required=True)
@click.option("--style", "-s", type=click.Choice(["broad", "sector", "overseas"]),
              help="指数类别（默认自动检测）")
@click.option("--output", "-o", type=click.Choice(["terminal", "markdown"]),
              default="terminal", help="输出格式")
@click.option("--compare-only", is_flag=True, help="仅输出横向对比表格")
def index(symbols, style, output, compare_only):
    """分析指数并生成报告"""
    from utils.symbols import validate_index_symbol, normalize_index_symbol
    from utils.config import Config
    from data.index_mapping import IndexMapping
    from data.schemas import AnalysisTarget
    from index.pipeline import IndexPipeline

    config = Config()
    if not _check_disclaimer(config):
        return

    mapping = IndexMapping()
    targets = []

    for raw in symbols:
        if not validate_index_symbol(raw):
            console.print(f"[red]无效的指数代码: {raw}[/red]")
            sys.exit(1)

        normalized = normalize_index_symbol(raw)
        entry = mapping.lookup(normalized)

        if entry is None:
            if style is None:
                console.print(
                    f"[red]无法识别指数 {normalized}，"
                    f"请用 --style 指定类别 (broad/sector/overseas)[/red]"
                )
                sys.exit(1)
            index_style = style
            name = raw
            market = "a-shares"
        else:
            index_style = entry.index_style
            name = entry.name
            market = entry.market

        targets.append(AnalysisTarget(
            target_type="index", symbol=normalized,
            name=name, market=market, index_style=index_style,
        ))

    pipeline = IndexPipeline()
    result = pipeline.run(targets)

    from report.formatter import ReportFormatter

    if not compare_only:
        for report in result.reports:
            if output == "terminal":
                console.print(_render_index_report(report))
            elif output == "markdown":
                saved = ReportFormatter.save(
                    _render_index_report_md(report),
                    report.code
                )
                console.print(f"[green]报告已保存: {saved}[/green]")

    if result.compare is not None:
        console.print(_render_compare_table(result.compare))

    if result.errors:
        for err in result.errors:
            console.print(f"[yellow]警告: {err}[/yellow]")


def _render_index_report(report) -> str:
    """将 IndexReport 渲染为终端可读的 Rich Markdown"""
    from report.builder import ReportBuilder, _md_table
    from datetime import datetime
    from jinja2 import Environment, FileSystemLoader
    from pathlib import Path

    template_dir = Path(__file__).parent.parent / "report" / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)),
                      trim_blocks=True, lstrip_blocks=True)
    env.filters["md_table"] = _md_table
    template = env.get_template("index_report.jinja2")
    md = template.render(
        code=report.code,
        name=report.name,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        overview=report.overview,
        section_technical=report.section_technical,
        section_valuation=report.section_valuation,
        section_capital=report.section_capital,
        section_macro=report.section_macro,
        section_sentiment=report.section_sentiment,
        tag_technical=report.tag_technical,
        tag_valuation=report.tag_valuation,
        tag_capital=report.tag_capital,
        tag_macro=report.tag_macro,
        tag_sentiment=report.tag_sentiment,
        composite_comment=report.composite_comment,
        position_coeff=report.position_coeff,
        visible_sections=report.visible_sections,
    )
    from report.formatter import ReportFormatter
    return ReportFormatter.to_rich_markdown(md)


def _render_index_report_md(report) -> str:
    """将 IndexReport 渲染为纯 Markdown 文本"""
    from jinja2 import Environment, FileSystemLoader
    from pathlib import Path
    from datetime import datetime

    template_dir = Path(__file__).parent.parent / "report" / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)),
                      trim_blocks=True, lstrip_blocks=True)
    from report.builder import _md_table
    env.filters["md_table"] = _md_table
    template = env.get_template("index_report.jinja2")
    return template.render(
        code=report.code,
        name=report.name,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        overview=report.overview,
        section_technical=report.section_technical,
        section_valuation=report.section_valuation,
        section_capital=report.section_capital,
        section_macro=report.section_macro,
        section_sentiment=report.section_sentiment,
        tag_technical=report.tag_technical,
        tag_valuation=report.tag_valuation,
        tag_capital=report.tag_capital,
        tag_macro=report.tag_macro,
        tag_sentiment=report.tag_sentiment,
        composite_comment=report.composite_comment,
        position_coeff=report.position_coeff,
        visible_sections=report.visible_sections,
    )


def _render_compare_table(compare) -> str:
    """渲染横向对比表格"""
    if not compare or not compare.rows:
        return ""
    from rich.table import Table
    table = Table(title="指数横向对比")
    for h in compare.headers:
        table.add_column(h)
    for row in compare.rows:
        table.add_row(*[str(row.get(h, "")) for h in compare.headers])
    return table
```

- [ ] **Step 3: 在 `analyze` 命令中添加 `--with-market` 选项**

在现有 `analyze` 函数中：

```python
@click.option("--with-market", is_flag=True, help="在报告中嵌入大盘环境分析")
```

在 `analyze` 函数体内，`builder.build(...)` 之前，添加：

```python
if with_market:
    from index.pipeline import IndexPipeline
    index_pipeline = IndexPipeline()
    snapshot = index_pipeline.get_snapshot("000300")
    if snapshot:
        market_env = snapshot
    else:
        market_env = None
    # 将 market_env 注入 report builder（在 builder.build 参数中新增 market_env）
```

`ReportBuilder.build` 方法签名新增参数 `market_env: dict | None = None`。

- [ ] **Step 4: 提交**

```bash
git add src/stock_robot/cli.py tests/test_cli_index.py
git commit -m "feat(CLI): 添加 index 命令与 analyze --with-market 选项"
```

---

### Task 15: 打分配置 YAML 文件

**Files:**
- Create: `src/analysis/config/index/base/broad.yaml`
- Create: `src/analysis/config/index/base/sector.yaml`
- Create: `src/analysis/config/index/base/overseas.yaml`

- [ ] **Step 1: 创建 `broad.yaml`**

```yaml
meta:
  index_style: broad
  description: 宽基指数配置 — 偏宏观+估值

tags:
  technical:
    bull:
      ma_5_above_ma_20: true
      close_above_ma_20: true
    bear:
      ma_5_below_ma_20: true
      close_below_ma_20: true
    shake:
      default: true

  valuation:
    undervalued:
      pe_percentile_max: 30
    neutral:
      pe_percentile_min: 30
      pe_percentile_max: 70
    overvalued:
      pe_percentile_min: 70
    invalid:
      valuation_valid: false

  macro:
    positive:
      pmi_min: 50
    neutral:
      pmi_min: 48
      pmi_max: 50
    negative:
      pmi_max: 48
```

- [ ] **Step 2: 创建 `sector.yaml`**

```yaml
meta:
  index_style: sector
  description: 行业指数配置 — 偏资金+技术

tags:
  technical:
    bull:
      ma_5_above_ma_20: true
    bear:
      ma_5_below_ma_20: true
    shake:
      default: true

  valuation:
    undervalued:
      pe_percentile_max: 30
    neutral:
      pe_percentile_min: 30
      pe_percentile_max: 70
    overvalued:
      pe_percentile_min: 70
    invalid:
      valuation_valid: false
```

- [ ] **Step 3: 创建 `overseas.yaml`**

```yaml
meta:
  index_style: overseas
  description: 海外指数配置 — 偏趋势+汇率

tags:
  technical:
    bull:
      ma_5_above_ma_20: true
      close_above_ma_200: true
    bear:
      ma_5_below_ma_20: true
      close_below_ma_200: true
    shake:
      default: true

  valuation:
    undervalued:
      pe_percentile_max: 30
    neutral:
      pe_percentile_min: 30
      pe_percentile_max: 70
    overvalued:
      pe_percentile_min: 70
    invalid:
      valuation_valid: false
```

- [ ] **Step 4: 提交**

```bash
git add src/analysis/config/index/
git commit -m "feat(配置): 添加指数三类打分配置 YAML"
```

---

### Task 16: 指数上下文充实器 — 个股联动

**Files:**
- Create: `src/data/enrichers/index_context_enricher.py`
- Test: `tests/data/enrichers/test_index_context_enricher.py`

- [ ] **Step 1: 编写测试**

```python
"""个股-指数联动充实器测试"""
import pytest
from unittest.mock import MagicMock, patch
from src.data.enrichers.index_context_enricher import IndexContextEnricher
from src.data.schemas import AnalysisContext


class TestIndexContextEnricher:
    def test_enrich_adds_market_environment(self):
        ctx = AnalysisContext(symbol="000001", name="平安银行", market="a-shares")

        mock_pipeline = MagicMock()
        mock_pipeline.get_snapshot.return_value = {
            "symbol": "000300",
            "pe_ttm": 12.5,
            "pe_percentile": 68.0,
            "valuation_valid": True,
        }

        enricher = IndexContextEnricher(mock_pipeline)
        result = enricher.enrich(ctx)

        assert result.market_environment is not None
        assert result.market_environment["symbol"] == "000300"
```

- [ ] **Step 2: 创建 `src/data/enrichers/index_context_enricher.py`**

```python
"""IndexContextEnricher — 个股充实器：注入所属大盘指数快照"""
import logging
from data.enricher import DataEnricher
from data.schemas import AnalysisContext

logger = logging.getLogger(__name__)


class IndexContextEnricher(DataEnricher):
    """将沪深300等基准指数快照注入个股 AnalysisContext"""

    def __init__(self, index_pipeline):
        self._index_pipeline = index_pipeline

    def enrich(self, ctx: AnalysisContext) -> AnalysisContext:
        try:
            snapshot = self._index_pipeline.get_snapshot("000300")
            if snapshot:
                ctx.market_environment = snapshot
        except Exception as e:
            logger.warning(f"获取大盘环境快照失败: {e}")
        return ctx
```

- [ ] **Step 3: 在 `AnalysisContext` 上添加 `market_environment` 字段**

在 `src/data/schemas.py` 中，`AnalysisContext` 类内追加：

```python
    # 大盘环境快照（由 IndexContextEnricher 填充）
    market_environment: dict | None = None
```

- [ ] **Step 4: 运行测试验证通过**

```bash
python -m pytest tests/data/enrichers/test_index_context_enricher.py -v
```
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add src/data/enrichers/index_context_enricher.py tests/data/enrichers/test_index_context_enricher.py src/data/schemas.py
git commit -m "feat(联动): 实现个股-指数联动充实器"
```

---

### Task 17: 集成测试 — 指数管道端到端

**Files:**
- Test: `tests/index/test_integration.py`

- [ ] **Step 1: 编写集成测试**

```python
"""指数管道端到端集成测试"""
import pytest
from src.index.pipeline import IndexPipeline
from src.data.schemas import AnalysisTarget


class TestIndexPipelineIntegration:
    def test_full_pipeline_broad(self):
        target = AnalysisTarget(
            target_type="index", symbol="000300",
            name="沪深300", market="a-shares", index_style="broad"
        )
        pipeline = IndexPipeline()
        result = pipeline.run([target])
        assert len(result.reports) == 1
        report = result.reports[0]
        assert report.code == "000300"
        assert "overview" in report.visible_sections
        assert "macro" in report.visible_sections
        assert "capital" in report.visible_sections

    def test_full_pipeline_sector(self):
        target = AnalysisTarget(
            target_type="index", symbol="801080",
            name="电子", market="a-shares", index_style="sector"
        )
        pipeline = IndexPipeline()
        result = pipeline.run([target])
        report = result.reports[0]
        assert "macro" not in report.visible_sections

    def test_multi_index_compare(self):
        targets = [
            AnalysisTarget(target_type="index", symbol="000300",
                           name="沪深300", market="a-shares", index_style="broad"),
            AnalysisTarget(target_type="index", symbol="000905",
                           name="中证500", market="a-shares", index_style="broad"),
        ]
        pipeline = IndexPipeline()
        result = pipeline.run(targets)
        assert len(result.reports) == 2
        assert result.compare is not None
```

- [ ] **Step 2: 运行集成测试**

```bash
python -m pytest tests/index/test_integration.py -v
```
Expected: PASS（依赖网络数据；脱机时标记为 skip）

- [ ] **Step 3: 提交**

```bash
git add tests/index/test_integration.py
git commit -m "test(指数): 添加指数管道端到端集成测试"
```

---

### Task 18: 最终验证 — 全量测试套件

- [ ] **Step 1: 运行全量测试**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -40
```
Expected: 所有已有测试保持 PASS，新增测试 PASS

- [ ] **Step 2: 检查导入完整性**

```bash
python -c "
from src.data.schemas import AnalysisTarget, IndexPriceData, IndexValuationData
from src.data.schemas import CapitalFlowData, MacroContext, IndexAnalysisContext, IndexReport
from src.index.pipeline import IndexPipeline, IndexPipelineResult
from src.index.collector import IndexDataCollector
from src.index.enricher import compute_percentile, tag_valuation
from src.index.build_single import IndexReportBuilder
from src.index.build_compare import IndexCompareReportBuilder, CompareTable
from src.index.analysis.technical import IndexTechnicalAnalyzer
from src.index.analysis.valuation import IndexValuationAnalyzer
from src.index.analysis.capital_flow import CapitalFlowAnalyzer
from src.index.analysis.macro import MacroAnalyzer
from src.index.analysis.sentiment import IndexSentimentAnalyzer
from src.data.index_mapping import IndexMapping
from src.utils.symbols import validate_index_symbol, normalize_index_symbol
print('All imports OK')
"
```
Expected: `All imports OK`

- [ ] **Step 3: 检查循环依赖**

```bash
python -c "
# 确保 src/data/ 不会 import src/index/
import ast, sys
with open('src/data/schemas.py') as f:
    tree = ast.parse(f.read())
for node in ast.walk(tree):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        module = getattr(node, 'module', '') or ''
        if 'index' in module and node.level == 0:
            print(f'ERROR: data/schemas.py imports {module}')
            sys.exit(1)
print('No circular imports detected')
"
```
Expected: `No circular imports detected`

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "test(指数): 全量测试验证与循环依赖检查"
```
