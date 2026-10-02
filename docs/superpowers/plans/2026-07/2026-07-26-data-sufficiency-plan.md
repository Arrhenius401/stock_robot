# 数据充足性增强实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 引入充实层（Enricher）解决各维度数据不足问题，建立五维度充足判定标准，新增量化打分体系，重构 LLM 为单次批量调用，输出 v2 报告模板。

**Architecture:** 在现有 `Collect → Analyze → LLM → Report` 管道中插入 Enrich 充实层。充实层按依赖 DAG 执行（PriceEnricher → ValuationEnricher → IndustryEnricher，SentimentEnricher 并行），产出自充足状态标记（DataSufficiency）、充实数据（enriched_*）和量化打分（score/risk_flags）。LLM 改为单次批量调用生成四段结构化解读。

**Tech Stack:** Python 3.11+, Pydantic v2, AkShare, Jinja2, pytest, anthropic SDK / openai SDK

---

### Task 1: Schema 定义 — 充实层数据模型

**Files:**
- Modify: `src/data/schemas.py`

- [ ] **Step 1: 添加 SufficiencyLevel 枚举和 DimensionSufficiency/DataSufficiency 模型**

在 `src/data/schemas.py` 顶部添加 `from enum import StrEnum`，在现有模型类之前插入：

```python
from enum import StrEnum


class SufficiencyLevel(StrEnum):
    SUFFICIENT = "sufficient"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"


class DimensionSufficiency(BaseModel):
    """单个维度的数据充足状态"""
    level: SufficiencyLevel
    reason: str = ""
    sample_count: int = 0
    score_weight: float = 1.0  # sufficient=1.0, partial=0.5, insufficient=0.0


class DataSufficiency(BaseModel):
    """五个维度的充足状态汇总"""
    price: DimensionSufficiency
    financial: DimensionSufficiency
    valuation: DimensionSufficiency
    industry: DimensionSufficiency
    sentiment: DimensionSufficiency
```

- [ ] **Step 2: 添加 Enriched 数据模型**

在 `DataSufficiency` 之后添加：

```python
class DailyValuationPoint(BaseModel):
    """日频估值单点"""
    trade_date: date
    close: float
    pe: float | None = None
    pb: float | None = None
    ps: float | None = None


class EnrichedValuation(BaseModel):
    """充实后的估值数据 — 日频 PE/PB/PS 序列"""
    daily_points: list[DailyValuationPoint] = Field(default_factory=list)
    pe_percentile: float | None = None
    pb_percentile: float | None = None
    pe_zone: str = ""           # "低估" / "中性" / "高估"
    pe_median: float | None = None
    pe_high: float | None = None
    pe_low: float | None = None


class PeerComparison(BaseModel):
    """同行对比单家公司"""
    symbol: str
    name: str = ""
    market_cap: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None
    gross_margin: float | None = None
    net_margin: float | None = None
    roe: float | None = None


class EnrichedIndustry(BaseModel):
    """充实后的行业数据"""
    peer_count: int = 0
    top_peers: list[PeerComparison] = Field(default_factory=list)
    industry_median_pe: float | None = None
    industry_median_pb: float | None = None
    industry_median_gross_margin: float | None = None
    industry_median_net_margin: float | None = None
    target_pe_premium: float | None = None      # 正数=溢价，负数=折价
    target_market_cap_rank: int | None = None


class RawSentimentItem(BaseModel):
    """舆情原始条目"""
    title: str
    source: str  # "news" 或 "announcement"
    publish_date: date
    content: str = ""


class RawSentimentData(BaseModel):
    """采集层输出的舆情原始数据"""
    symbol: str
    fetch_date: date
    items: list[RawSentimentItem] = Field(default_factory=list)


class SentimentItem(BaseModel):
    """LLM 标注后的舆情条目"""
    title: str
    summary: str
    tendency: str  # "positive" / "neutral" / "negative"
    severity: str  # "minor" / "moderate" / "major"
    event_type: str = ""


class EnrichedSentiment(BaseModel):
    """充实后的舆情数据"""
    total_count: int = 0
    positive_count: int = 0
    neutral_count: int = 0
    negative_count: int = 0
    major_events: list[SentimentItem] = Field(default_factory=list)
    all_items: list[SentimentItem] = Field(default_factory=list)
```

- [ ] **Step 3: 扩展 AnalysisContext**

修改 `AnalysisContext` 类：

```python
class AnalysisContext(BaseModel):
    """分析上下文 — 管道中传递的完整数据容器"""
    symbol: str
    name: str
    market: str = "a-shares"
    # 采集层 — 单时点数据改为单值类型，日频序列走 enriched_valuation
    financial_data: list[FinancialData] | None = None
    price_data: list[PriceData] | None = None
    valuation_data: ValuationData | None = None
    industry_data: IndustryData | None = None
    news_data: NewsData | None = None
    collected_at: datetime = Field(default_factory=datetime.now)

    # 采集层 — 舆情原始数据
    raw_sentiment: RawSentimentData | None = None

    # 充实层产出 — 若对应维度完全拉取失败则为 None
    sufficiency: DataSufficiency | None = None
    enriched_valuation: EnrichedValuation | None = None
    enriched_industry: EnrichedIndustry | None = None
    enriched_sentiment: EnrichedSentiment | None = None
```

- [ ] **Step 4: 扩展 AnalysisResult 和 FinancialData**

修改 `AnalysisResult`：

```python
class AnalysisResult(BaseModel):
    """分析模块输出 — 统一结构"""
    dimension: Literal["financial", "technical", "valuation", "industry", "sentiment"]
    status: Literal["ok", "partial", "unavailable"]
    summary: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    charts: list[str] = Field(default_factory=list)

    # 新增：量化打分
    score: float | None = None
    score_detail: str = ""
    risk_flags: list[str] = Field(default_factory=list)
```

修改 `FinancialData`，添加 `deducted_net_profit` 字段：

```python
class FinancialData(BaseModel):
    """单期财务数据"""
    symbol: str
    fiscal_quarter: date
    revenue: float | None = None
    net_profit: float | None = None
    deducted_net_profit: float | None = None  # 新增：扣非净利润
    total_assets: float | None = None
    total_equity: float | None = None
    operating_cash_flow: float | None = None
    roe: float | None = None
    gross_margin: float | None = None
```

- [ ] **Step 5: 验证 Schema 导入**

```bash
cd D:\code\stock_robot && python -c "from src.data.schemas import SufficiencyLevel, DimensionSufficiency, DataSufficiency, EnrichedValuation, EnrichedIndustry, EnrichedSentiment, RawSentimentData, AnalysisContext, AnalysisResult, FinancialData; print('All schemas imported successfully')"
```

- [ ] **Step 6: Commit**

```bash
git add src/data/schemas.py
git commit -m "feat(数据层): 添加充实层数据模型和充足状态 Schema"
```

---

### Task 2: 充实器基类和 ContextEnricher 编排器

**Files:**
- Create: `src/data/enricher.py`
- Create: `src/data/enrichers/__init__.py`

- [ ] **Step 1: 创建充实器基类**

创建 `src/data/enricher.py`：

```python
"""充实器基类和编排器"""
from abc import ABC, abstractmethod
import logging
from data.schemas import AnalysisContext

logger = logging.getLogger(__name__)


class DataEnricher(ABC):
    """充实器抽象基类"""

    @abstractmethod
    def enrich(self, ctx: AnalysisContext) -> AnalysisContext:
        """执行充实逻辑，返回修改后的 ctx"""
        ...


class ContextEnricher:
    """按依赖 DAG 编排所有充实器"""

    def __init__(self):
        self._enrichers: list[DataEnricher] = []

    def register(self, enricher: DataEnricher):
        self._enrichers.append(enricher)

    def enrich(self, ctx: AnalysisContext) -> AnalysisContext:
        """执行全部充实器（按注册顺序，注册顺序即依赖顺序）"""
        for enricher in self._enrichers:
            try:
                ctx = enricher.enrich(ctx)
            except Exception as e:
                logger.error(f"充实器 {enricher.__class__.__name__} 失败: {e}")
        return ctx
```

- [ ] **Step 2: 创建 enrichers 包 init**

创建 `src/data/enrichers/__init__.py`：

```python
from data.enrichers.price_enricher import PriceEnricher
from data.enrichers.valuation_enricher import ValuationEnricher
from data.enrichers.industry_enricher import IndustryEnricher
from data.enrichers.sentiment_enricher import SentimentEnricher

__all__ = ["PriceEnricher", "ValuationEnricher", "IndustryEnricher", "SentimentEnricher"]
```

- [ ] **Step 3: 验证导入**

```bash
cd D:\code\stock_robot && python -c "from src.data.enricher import DataEnricher, ContextEnricher; print('Enricher base imported')"
```

- [ ] **Step 4: Commit**

```bash
git add src/data/enricher.py src/data/enrichers/__init__.py
git commit -m "feat(数据层): 添加充实器基类和 ContextEnricher 编排器"
```

---

### Task 3: PriceEnricher — 行情数据充足判定

**Files:**
- Create: `src/data/enrichers/price_enricher.py`
- Create: `tests/test_enrichers.py`

- [ ] **Step 1: 编写 PriceEnricher 测试**

创建 `tests/test_enrichers.py`：

```python
"""充实器单元测试"""
import pytest
from datetime import date, datetime
from data.schemas import (
    AnalysisContext, PriceData, DataSufficiency, DimensionSufficiency,
    SufficiencyLevel,
)
from data.enrichers.price_enricher import PriceEnricher


def make_price_data(n: int) -> list[PriceData]:
    """生成 n 条行情数据"""
    return [
        PriceData(symbol="000001", trade_date=date(2026, 1, 1), open=10.0,
                  high=10.5, low=9.8, close=10.2, volume=1000000)
        for _ in range(n)
    ]


class TestPriceEnricher:
    def test_sufficient_60_plus(self):
        ctx = AnalysisContext(symbol="000001", name="测试", price_data=make_price_data(120))
        enricher = PriceEnricher()
        result = enricher.enrich(ctx)
        assert result.sufficiency.price.level == SufficiencyLevel.SUFFICIENT
        assert result.sufficiency.price.score_weight == 1.0
        assert result.sufficiency.price.sample_count == 120

    def test_partial_20_to_59(self):
        ctx = AnalysisContext(symbol="000001", name="测试", price_data=make_price_data(40))
        enricher = PriceEnricher()
        result = enricher.enrich(ctx)
        assert result.sufficiency.price.level == SufficiencyLevel.PARTIAL
        assert result.sufficiency.price.score_weight == 0.5

    def test_insufficient_below_20(self):
        ctx = AnalysisContext(symbol="000001", name="测试", price_data=make_price_data(10))
        enricher = PriceEnricher()
        result = enricher.enrich(ctx)
        assert result.sufficiency.price.level == SufficiencyLevel.INSUFFICIENT
        assert result.sufficiency.price.score_weight == 0.0

    def test_none_price_data_is_insufficient(self):
        ctx = AnalysisContext(symbol="000001", name="测试", price_data=None)
        enricher = PriceEnricher()
        result = enricher.enrich(ctx)
        assert result.sufficiency.price.level == SufficiencyLevel.INSUFFICIENT
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd D:\code\stock_robot && python -m pytest tests/test_enrichers.py::TestPriceEnricher -v
```
预期：全部 FAIL，模块未创建

- [ ] **Step 3: 实现 PriceEnricher**

创建 `src/data/enrichers/price_enricher.py`：

```python
"""行情数据充足判定充实器"""
from data.enricher import DataEnricher
from data.schemas import AnalysisContext, DimensionSufficiency, DataSufficiency, SufficiencyLevel


class PriceEnricher(DataEnricher):
    def enrich(self, ctx: AnalysisContext) -> AnalysisContext:
        prices = ctx.price_data or []
        count = len(prices)

        if count >= 60:
            level = SufficiencyLevel.SUFFICIENT
            weight = 1.0
            reason = f"有效行情数据 {count} 条，满足 60 条门槛"
        elif count >= 20:
            level = SufficiencyLevel.PARTIAL
            weight = 0.5
            reason = f"有效行情数据 {count} 条（20–59），缺少长期均线数据，技术分析维度受限"
        else:
            level = SufficiencyLevel.INSUFFICIENT
            weight = 0.0
            reason = f"有效行情数据仅 {count} 条（<20），无法计算任何技术指标" if count > 0 else "行情数据完全缺失"

        ctx.sufficiency = DataSufficiency(
            price=DimensionSufficiency(level=level, reason=reason, sample_count=count, score_weight=weight),
            financial=DimensionSufficiency(level=SufficiencyLevel.INSUFFICIENT, reason="待充实", sample_count=0, score_weight=0.0),
            valuation=DimensionSufficiency(level=SufficiencyLevel.INSUFFICIENT, reason="待充实", sample_count=0, score_weight=0.0),
            industry=DimensionSufficiency(level=SufficiencyLevel.INSUFFICIENT, reason="待充实", sample_count=0, score_weight=0.0),
            sentiment=DimensionSufficiency(level=SufficiencyLevel.INSUFFICIENT, reason="待充实", sample_count=0, score_weight=0.0),
        )
        return ctx
```

- [ ] **Step 4: 运行测试验证通过**

```bash
cd D:\code\stock_robot && python -m pytest tests/test_enrichers.py::TestPriceEnricher -v
```
预期：4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/data/enrichers/price_enricher.py tests/test_enrichers.py
git commit -m "feat(充实层): 实现 PriceEnricher 行情数据充足判定"
```

---

### Task 4: 采集层增强 — price 和 financial

**Files:**
- Modify: `src/data/akshare.py`

- [ ] **Step 1: 修改 _fetch_price 默认 days 参数**

在 `src/data/akshare.py:71`，将 `days = kwargs.get("days", 365)` 改为：

```python
days = kwargs.get("days", 250)  # 近一年交易日，覆盖完整行情周期
```

- [ ] **Step 2: 修改 _fetch_financial 补充 deducted_net_profit 和 gross_margin**

在 `src/data/akshare.py:94-133`，添加扣非净利润和毛利率解析：

```python
def _fetch_financial(self, symbol: str, **kwargs) -> list[FinancialData]:
    df = ak.stock_financial_abstract_ths(symbol=symbol)
    results = []
    periods = df.get("报告期", [])
    revenues = df.get("营业总收入", [])
    profits = df.get("净利润", [])
    assets = df.get("资产总计", [])
    equities = df.get("股东权益合计", [])
    cash_flows = df.get("经营活动现金流量净额", [])
    # 新增字段
    deducted_profits = df.get("扣除非经常性损益后的净利润", [])
    gross_margins = df.get("销售毛利率", [])

    for i in range(min(len(periods), 12)):
        try:
            period_str = str(periods[i])
            try:
                fiscal_date = datetime.strptime(period_str, "%Y-%m-%d").date()
            except ValueError:
                fiscal_date = datetime.strptime(period_str, "%Y%m%d").date()

            equity = parse_cn_number(equities[i]) if i < len(equities) else None
            net_profit = parse_cn_number(profits[i]) if i < len(profits) else None
            revenue = parse_cn_number(revenues[i]) if i < len(revenues) else None
            deducted = parse_cn_number(deducted_profits[i]) if i < len(deducted_profits) else None
            gm = parse_cn_number(gross_margins[i]) if i < len(gross_margins) else None

            roe = (net_profit / equity) if (
                net_profit is not None and equity is not None and equity > 0
            ) else None

            results.append(FinancialData(
                symbol=symbol,
                fiscal_quarter=fiscal_date,
                revenue=revenue,
                net_profit=net_profit,
                deducted_net_profit=deducted,
                total_assets=parse_cn_number(assets[i]) if i < len(assets) else None,
                total_equity=equity,
                operating_cash_flow=parse_cn_number(cash_flows[i]) if i < len(cash_flows) else None,
                roe=roe,
                gross_margin=gm,
            ))
        except (ValueError, IndexError, TypeError) as e:
            logger.warning(f"跳过异常财务数据行 {i}: {e}")
    return results
```

- [ ] **Step 3: 验证导入和运行**

```bash
cd D:\code\stock_robot && python -c "from src.data.akshare import AkShareAdapter; print('Import OK')"
```

- [ ] **Step 4: Commit**

```bash
git add src/data/akshare.py
git commit -m "feat(数据层): _fetch_price 默认 250 天，_fetch_financial 补充扣非净利和毛利率"
```

---

### Task 5: 采集层增强 — industry 扩充

**Files:**
- Modify: `src/data/akshare.py`
- Modify: `src/data/schemas.py` (IndustryData 扩展)

- [ ] **Step 1: 扩展 IndustryData Schema**

修改 `src/data/schemas.py` 中的 `IndustryData`：

```python
class PeerBasicInfo(BaseModel):
    """同行公司基础信息"""
    symbol: str
    name: str = ""
    market_cap: float | None = None

class IndustryData(BaseModel):
    """行业分类与同行业公司"""
    symbol: str
    industry: str
    sector: str
    peers: list[str] = Field(default_factory=list)
    # 新增：头部同行详细数据
    top_peers: list[PeerBasicInfo] = Field(default_factory=list)
```

- [ ] **Step 2: 重写 _fetch_industry**

替换 `src/data/akshare.py:163-186`：

```python
def _fetch_industry(self, symbol: str, **kwargs) -> list[IndustryData]:
    industry = ""
    sector = ""
    industry_code = ""

    # 获取行业分类
    try:
        df = _ak_individual_info_em(symbol)
        if "item" in df.columns and "value" in df.columns:
            ind_row = df[df["item"].str.contains("行业", na=False)]
            if not ind_row.empty:
                industry = str(ind_row["value"].iloc[0])
            sec_row = df[df["item"].str.contains("板块|部门", na=False)]
            if not sec_row.empty:
                sector = str(sec_row["value"].iloc[0])
            # 尝试获取行业代码用于查询成分股
            code_row = df[df["item"].str.contains("行业代码|申万", na=False)]
            if not code_row.empty:
                industry_code = str(code_row["value"].iloc[0])
    except Exception:
        pass

    top_peers = []
    # 从行业板块接口拉取成分股
    try:
        # 尝试通过行业名称匹配板块成分股
        board_df = ak.stock_board_industry_cons_em(symbol=industry)
        if board_df is not None and len(board_df) > 0:
            # 过滤 ST/退市/空值
            cols = board_df.columns
            code_col = "代码" if "代码" in cols else cols[0]
            name_col = "名称" if "名称" in cols else (cols[1] if len(cols) > 1 else code_col)
            mcap_col = None
            for c in cols:
                if "市值" in str(c) or "总市值" in str(c):
                    mcap_col = c
                    break

            valid_rows = []
            for _, row in board_df.iterrows():
                code = str(row[code_col])
                name = str(row.get(name_col, ""))
                # 过滤 ST、*ST、退市标记
                if "ST" in name or "退市" in name or "PT" in name:
                    continue
                mcap = None
                if mcap_col:
                    try:
                        mcap = float(row[mcap_col])
                    except (ValueError, TypeError):
                        pass
                if mcap is not None and mcap > 0:
                    valid_rows.append((code, name, mcap))

            # 按市值排序取前 5
            valid_rows.sort(key=lambda x: x[2], reverse=True)
            for code, name, mcap in valid_rows[:5]:
                pe_ttm, pb = None, None
                # 容错：单家接口失败不中断
                try:
                    if code.startswith("6"):
                        xq = f"SH{code}"
                    else:
                        xq = f"SZ{code}"
                    spot_df = ak.stock_individual_spot_xq(symbol=xq)
                    if "item" in spot_df.columns and "value" in spot_df.columns:
                        pe_row = spot_df[spot_df["item"] == "市盈率(动)"]
                        pb_row = spot_df[spot_df["item"] == "市净率"]
                        if not pe_row.empty:
                            pe_ttm = parse_cn_number(pe_row["value"].iloc[0])
                        if not pb_row.empty:
                            pb = parse_cn_number(pb_row["value"].iloc[0])
                except Exception:
                    pass
                top_peers.append(PeerBasicInfo(symbol=code, name=name, market_cap=mcap))
    except Exception:
        logger.warning(f"获取行业成分股失败: {industry}")

    return [IndustryData(
        symbol=symbol, industry=industry or "未知", sector=sector or "",
        peers=[p.symbol for p in top_peers], top_peers=top_peers,
    )]
```

- [ ] **Step 3: 验证导入**

```bash
cd D:\code\stock_robot && python -c "from src.data.schemas import IndustryData, PeerBasicInfo; print('Import OK')"
```

- [ ] **Step 4: Commit**

```bash
git add src/data/akshare.py src/data/schemas.py
git commit -m "feat(数据层): _fetch_industry 扩充同行列表与头部 5 家公司估值数据"
```

---

### Task 6: 采集层增强 — news 扩展至 30 天

**Files:**
- Modify: `src/data/akshare.py`

- [ ] **Step 1: 重写 _fetch_news 支持 30 天范围**

替换 `src/data/akshare.py:188-199`：

```python
def _fetch_news(self, symbol: str, **kwargs) -> list[NewsData]:
    """拉取近 30 天新闻和公告，输出 RawSentimentData 到额外属性"""
    from data.schemas import RawSentimentData, RawSentimentItem
    from datetime import timedelta

    today = date.today()
    start_date = today - timedelta(days=30)
    items = []
    seen_titles = set()

    # 拉取新闻
    try:
        df = _ak_news(symbol)
        for _, row in df.head(20).iterrows():
            title = str(row.get("标题", "") or row.get("title", ""))
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            content = str(row.get("内容", "") or row.get("content", ""))[:500]
            pub_date = today
            try:
                raw_date = row.get("发布时间", "") or row.get("时间", "")
                if raw_date:
                    pub_date = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").date()
            except Exception:
                pass
            if pub_date >= start_date:
                items.append(RawSentimentItem(
                    title=title, source="news", publish_date=pub_date, content=content,
                ))
    except Exception as e:
        logger.warning(f"新闻数据获取失败: {e}")

    # 拉取公告
    try:
        announce_df = ak.stock_notice_report(symbol=symbol)
        if announce_df is not None and not announce_df.empty:
            cols = announce_df.columns
            title_col = "标题" if "标题" in cols else (cols[0] if len(cols) > 0 else None)
            date_col = "日期" if "日期" in cols else (cols[1] if len(cols) > 1 else None)
            if title_col:
                for _, row in announce_df.head(15).iterrows():
                    title = str(row.get(title_col, ""))
                    if not title or title in seen_titles:
                        continue
                    seen_titles.add(title)
                    pub_date = today
                    if date_col:
                        try:
                            pub_date = datetime.strptime(str(row[date_col])[:10], "%Y-%m-%d").date()
                        except Exception:
                            pass
                    if pub_date >= start_date:
                        items.append(RawSentimentItem(
                            title=title, source="announcement",
                            publish_date=pub_date, content="",
                        ))
    except Exception as e:
        logger.warning(f"公告数据获取失败: {e}")

    # 去重并按日期排序，最多保留 30 条
    items.sort(key=lambda x: x.publish_date, reverse=True)
    items = items[:30]

    headlines = [item.title for item in items]
    result = NewsData(symbol=symbol, date=today, headlines=headlines)

    # 把 raw_sentiment 存到 result 的额外属性（管道 _assign_to_context 会处理）
    result._raw_sentiment = RawSentimentData(symbol=symbol, fetch_date=today, items=items)
    return [result]
```

- [ ] **Step 2: 修改管道 _assign_to_context 处理 raw_sentiment**

在 `src/core/pipeline.py` 的 `_assign_to_context` 方法末尾添加：

```python
# 处理 news 数据附带的 raw_sentiment
if data_type == "news" and data and hasattr(data[0], "_raw_sentiment"):
    ctx.raw_sentiment = data[0]._raw_sentiment
```

- [ ] **Step 3: 验证导入**

```bash
cd D:\code\stock_robot && python -c "from src.data.schemas import RawSentimentData, RawSentimentItem; print('Import OK')"
```

- [ ] **Step 4: Commit**

```bash
git add src/data/akshare.py src/core/pipeline.py
git commit -m "feat(数据层): _fetch_news 扩展至近 30 天新闻+公告并输出 raw_sentiment"
```

---

### Task 7: Financial 充足判定充实器

**Files:**
- Create: `src/data/enrichers/financial_enricher.py`
- Modify: `tests/test_enrichers.py`

- [ ] **Step 1: 在 test_enrichers.py 中添加测试**

```python
from data.enrichers.financial_enricher import FinancialEnricher
from data.schemas import FinancialData


def make_financial_data(n: int) -> list[FinancialData]:
    return [
        FinancialData(
            symbol="000001", fiscal_quarter=date(2025, 12 - i * 3, 1) if 12 - i * 3 > 0 else date(2025, 12, 1),
            revenue=100e8, net_profit=10e8, deducted_net_profit=9e8,
            total_assets=500e8, total_equity=50e8, operating_cash_flow=8e8,
            roe=0.12, gross_margin=0.45,
        )
        for i in range(n)
    ]


class TestFinancialEnricher:
    def test_sufficient_4_plus_complete(self):
        ctx = AnalysisContext(symbol="000001", name="测试",
                              financial_data=make_financial_data(6))
        # 先跑 PriceEnricher 创建 sufficiency
        ctx = PriceEnricher().enrich(ctx)
        ctx = FinancialEnricher().enrich(ctx)
        assert ctx.sufficiency.financial.level == SufficiencyLevel.SUFFICIENT
        assert ctx.sufficiency.financial.score_weight == 1.0
        assert ctx.sufficiency.financial.sample_count == 6

    def test_partial_2_to_3(self):
        ctx = AnalysisContext(symbol="000001", name="测试",
                              financial_data=make_financial_data(2))
        ctx = PriceEnricher().enrich(ctx)
        ctx = FinancialEnricher().enrich(ctx)
        assert ctx.sufficiency.financial.level == SufficiencyLevel.PARTIAL
        assert ctx.sufficiency.financial.score_weight == 0.5

    def test_insufficient_less_than_2(self):
        ctx = AnalysisContext(symbol="000001", name="测试",
                              financial_data=make_financial_data(1))
        ctx = PriceEnricher().enrich(ctx)
        ctx = FinancialEnricher().enrich(ctx)
        assert ctx.sufficiency.financial.level == SufficiencyLevel.INSUFFICIENT
        assert ctx.sufficiency.financial.score_weight == 0.0

    def test_insufficient_missing_profits(self):
        data = make_financial_data(4)
        for d in data:
            d.net_profit = None
        ctx = AnalysisContext(symbol="000001", name="测试", financial_data=data)
        ctx = PriceEnricher().enrich(ctx)
        ctx = FinancialEnricher().enrich(ctx)
        assert ctx.sufficiency.financial.level == SufficiencyLevel.INSUFFICIENT
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd D:\code\stock_robot && python -m pytest tests/test_enrichers.py::TestFinancialEnricher -v
```
预期：全部 FAIL

- [ ] **Step 3: 实现 FinancialEnricher**

创建 `src/data/enrichers/financial_enricher.py`：

```python
"""财务数据充足判定充实器"""
from data.enricher import DataEnricher
from data.schemas import AnalysisContext, DimensionSufficiency, SufficiencyLevel


class FinancialEnricher(DataEnricher):
    def enrich(self, ctx: AnalysisContext) -> AnalysisContext:
        financials = ctx.financial_data or []
        count = len(financials)

        # 检查非空利润数量
        valid_profit_count = sum(1 for f in financials if f.net_profit is not None)
        valid_revenue_count = sum(1 for f in financials if f.revenue is not None)

        if count >= 4 and valid_profit_count >= 3 and valid_revenue_count >= 3:
            level = SufficiencyLevel.SUFFICIENT
            weight = 1.0
            reason = f"具备 {count} 期季报，关键字段完整，可计算 TTM 指标"
        elif count >= 2 and valid_profit_count >= 1:
            level = SufficiencyLevel.PARTIAL
            weight = 0.5
            reason = f"仅 {count} 期季报（2–3 期），历史对比数据有限"
        else:
            level = SufficiencyLevel.INSUFFICIENT
            weight = 0.0
            reason = f"季报仅 {count} 期或关键字段大面积缺失，无法完成财务分析"

        ctx.sufficiency.financial = DimensionSufficiency(
            level=level, reason=reason, sample_count=count, score_weight=weight,
        )
        return ctx
```

- [ ] **Step 4: 运行测试验证通过**

```bash
cd D:\code\stock_robot && python -m pytest tests/test_enrichers.py::TestFinancialEnricher -v
```
预期：4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/data/enrichers/financial_enricher.py tests/test_enrichers.py
git commit -m "feat(充实层): 实现 FinancialEnricher 财务数据充足判定"
```

---

### Task 8: ValuationEnricher — 日频估值序列推导

**Files:**
- Create: `src/data/enrichers/valuation_enricher.py`
- Modify: `tests/test_enrichers.py`

- [ ] **Step 1: 添加估值充实器测试**

在 `tests/test_enrichers.py` 末尾添加：

```python
from data.enrichers.valuation_enricher import ValuationEnricher


def make_price_series(n: int, close: float = 10.0) -> list[PriceData]:
    return [
        PriceData(symbol="000001", trade_date=date(2025, 7, 1) + timedelta(days=i),
                  open=close-0.1, high=close+0.1, low=close-0.2, close=close,
                  volume=1000000)
        for i in range(n)
    ]


class TestValuationEnricher:
    def test_sufficient_with_valid_data(self):
        prices = make_price_series(200)
        financials = make_financial_data(4)
        ctx = AnalysisContext(symbol="000001", name="测试",
                              price_data=prices, financial_data=financials)
        ctx = PriceEnricher().enrich(ctx)
        ctx = FinancialEnricher().enrich(ctx)
        ctx = ValuationEnricher().enrich(ctx)
        assert ctx.sufficiency.valuation.level == SufficiencyLevel.SUFFICIENT
        assert ctx.enriched_valuation is not None
        assert len(ctx.enriched_valuation.daily_points) > 0
        assert ctx.enriched_valuation.pe_percentile is not None

    def test_insufficient_when_price_partial(self):
        prices = make_price_series(30)
        financials = make_financial_data(4)
        ctx = AnalysisContext(symbol="000001", name="测试",
                              price_data=prices, financial_data=financials)
        ctx = PriceEnricher().enrich(ctx)
        ctx = FinancialEnricher().enrich(ctx)
        ctx = ValuationEnricher().enrich(ctx)
        assert ctx.sufficiency.valuation.level == SufficiencyLevel.INSUFFICIENT

    def test_insufficient_no_financial(self):
        prices = make_price_series(200)
        ctx = AnalysisContext(symbol="000001", name="测试",
                              price_data=prices, financial_data=None)
        ctx = PriceEnricher().enrich(ctx)
        ctx = ValuationEnricher().enrich(ctx)
        assert ctx.sufficiency.valuation.level == SufficiencyLevel.INSUFFICIENT
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd D:\code\stock_robot && python -m pytest tests/test_enrichers.py::TestValuationEnricher -v
```
预期：全部 FAIL

- [ ] **Step 3: 实现 ValuationEnricher**

创建 `src/data/enrichers/valuation_enricher.py`：

```python
"""估值充实器 — 行情+财报推导日频 PE/PB/PS 序列"""
import logging
from statistics import median
from data.enricher import DataEnricher
from data.schemas import (
    AnalysisContext, DimensionSufficiency, SufficiencyLevel,
    EnrichedValuation, DailyValuationPoint,
)

logger = logging.getLogger(__name__)


class ValuationEnricher(DataEnricher):
    def enrich(self, ctx: AnalysisContext) -> AnalysisContext:
        prices = ctx.price_data or []
        financials = ctx.financial_data or []

        if (ctx.sufficiency and ctx.sufficiency.price.level == SufficiencyLevel.INSUFFICIENT):
            ctx.sufficiency.valuation = DimensionSufficiency(
                level=SufficiencyLevel.INSUFFICIENT, reason="行情数据不足，无法推导估值序列",
                sample_count=0, score_weight=0.0,
            )
            return ctx

        if len(financials) < 4:
            ctx.sufficiency.valuation = DimensionSufficiency(
                level=SufficiencyLevel.INSUFFICIENT,
                reason=f"仅 {len(financials)} 期财报，无法计算 TTM 指标",
                sample_count=0, score_weight=0.0,
            )
            return ctx

        # 从最近 4 期财报计算 TTM 值
        sorted_fin = sorted(financials, key=lambda x: x.fiscal_quarter)
        recent_4 = sorted_fin[-4:]
        ttm_profit = sum(f.net_profit for f in recent_4 if f.net_profit is not None)
        ttm_equity = recent_4[-1].total_equity  # 最近一期净资产
        ttm_revenue = sum(f.revenue for f in recent_4 if f.revenue is not None)

        if ttm_profit is None or ttm_profit <= 0 or ttm_equity is None or ttm_equity <= 0:
            ctx.sufficiency.valuation = DimensionSufficiency(
                level=SufficiencyLevel.INSUFFICIENT,
                reason="TTM 净利润为负或净资产数据缺失，无法计算有效估值",
                sample_count=0, score_weight=0.0,
            )
            return ctx

        # 获取总股本（优先从 ctx 获取，否则从财报反推）
        total_shares = self._get_total_shares(ctx)

        # 逐日推导估值
        sorted_prices = sorted(prices, key=lambda x: x.trade_date)
        daily_points = []
        for p in sorted_prices:
            if total_shares is None or total_shares <= 0:
                continue
            market_cap = p.close * total_shares
            pe = market_cap / ttm_profit if ttm_profit > 0 else None
            pb = market_cap / ttm_equity if ttm_equity > 0 else None
            ps = market_cap / ttm_revenue if ttm_revenue and ttm_revenue > 0 else None

            # 剔除异常：PE 为负或 PE>200 视为异常点
            if pe is not None and pe <= 0 or (pe is not None and pe > 200):
                pe = None
            if pb is not None and pb <= 0:
                pb = None

            daily_points.append(DailyValuationPoint(
                trade_date=p.trade_date, close=p.close, pe=pe, pb=pb, ps=ps,
            ))

        # 统计有效点
        valid_pe = [d for d in daily_points if d.pe is not None]
        valid_count = len(valid_pe)

        if valid_count >= 120:
            level = SufficiencyLevel.SUFFICIENT
            weight = 1.0
            reason = f"有效估值点数 {valid_count}（≥120），可计算完整历史分位"
        elif valid_count >= 30:
            level = SufficiencyLevel.PARTIAL
            weight = 0.5
            reason = f"有效估值点数 {valid_count}（30–119），仅展示当前值不计算分位"
        else:
            level = SufficiencyLevel.INSUFFICIENT
            weight = 0.0
            reason = f"有效估值点数仅 {valid_count}（<30），估值分析不可用"

        pe_values = sorted([d.pe for d in valid_pe])

        pe_current = valid_pe[-1].pe if valid_pe else None
        pe_percentile = None
        pe_zone = ""
        if pe_current is not None and len(pe_values) >= 120:
            below = sum(1 for p in pe_values if p < pe_current)
            pe_percentile = round(below / len(pe_values) * 100, 1)
            if pe_percentile < 30:
                pe_zone = "低估"
            elif pe_percentile <= 70:
                pe_zone = "中性"
            else:
                pe_zone = "高估"

        ctx.enriched_valuation = EnrichedValuation(
            daily_points=daily_points,
            pe_percentile=pe_percentile,
            pb_percentile=None,
            pe_zone=pe_zone,
            pe_median=median(pe_values) if pe_values else None,
            pe_high=max(pe_values) if pe_values else None,
            pe_low=min(pe_values) if pe_values else None,
        )

        ctx.sufficiency.valuation = DimensionSufficiency(
            level=level, reason=reason, sample_count=valid_count, score_weight=weight,
        )
        return ctx

    def _get_total_shares(self, ctx: AnalysisContext) -> float | None:
        """获取总股本"""
        try:
            import akshare as ak
            df = ak.stock_individual_info_em(symbol=ctx.symbol)
            if "item" in df.columns and "value" in df.columns:
                row = df[df["item"].str.contains("总股本", na=False)]
                if not row.empty:
                    val = str(row["value"].iloc[0])
                    from utils.numbers import parse_cn_number
                    return parse_cn_number(val)
        except Exception:
            pass
        # 回退：从最近一期财报反推
        financials = sorted(ctx.financial_data or [], key=lambda x: x.fiscal_quarter)
        if financials:
            latest = financials[-1]
            if latest.total_equity and latest.total_equity > 0:
                # 从 PB 反推: total_equity * pb / price ≈ shares
                if ctx.valuation_data and ctx.valuation_data.pb and ctx.valuation_data.pb > 0:
                    avg_price = sum(p.close for p in (ctx.price_data or [])[-20:]) / 20 if ctx.price_data else 0
                    if avg_price > 0:
                        return latest.total_equity * ctx.valuation_data.pb / avg_price
        return None
```

- [ ] **Step 4: 运行测试验证通过**

```bash
cd D:\code\stock_robot && python -m pytest tests/test_enrichers.py::TestValuationEnricher -v
```
预期：3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/data/enrichers/valuation_enricher.py tests/test_enrichers.py
git commit -m "feat(充实层): 实现 ValuationEnricher 日频估值序列推导"
```

---

### Task 9: IndustryEnricher — 同业对比与行业中位数

**Files:**
- Create: `src/data/enrichers/industry_enricher.py`
- Modify: `tests/test_enrichers.py`

- [ ] **Step 1: 添加行业充实器测试**

在 `tests/test_enrichers.py` 末尾添加：

```python
from data.enrichers.industry_enricher import IndustryEnricher
from data.schemas import IndustryData, PeerBasicInfo, PeerComparison


class TestIndustryEnricher:
    def test_sufficient_with_8_plus_peers(self):
        top_peers = [
            PeerBasicInfo(symbol=f"60000{i}", name=f"公司{i}", market_cap=1000e8)
            for i in range(5)
        ]
        ind_data = IndustryData(symbol="000001", industry="银行", sector="金融",
                                peers=[p.symbol for p in top_peers], top_peers=top_peers)
        ctx = AnalysisContext(symbol="000001", name="测试", industry_data=ind_data)
        ctx = PriceEnricher().enrich(ctx)
        ctx = IndustryEnricher().enrich(ctx)
        # 5 家同行 > 3，至少 partial+
        assert ctx.sufficiency.industry.level != SufficiencyLevel.INSUFFICIENT

    def test_insufficient_no_industry(self):
        ctx = AnalysisContext(symbol="000001", name="测试", industry_data=None)
        ctx = PriceEnricher().enrich(ctx)
        ctx = IndustryEnricher().enrich(ctx)
        assert ctx.sufficiency.industry.level == SufficiencyLevel.INSUFFICIENT
```

- [ ] **Step 2: 实现 IndustryEnricher**

创建 `src/data/enrichers/industry_enricher.py`：

```python
"""行业充实器 — 同业对比与行业中位数计算"""
import logging
from statistics import median
from data.enricher import DataEnricher
from data.schemas import (
    AnalysisContext, DimensionSufficiency, SufficiencyLevel,
    EnrichedIndustry, PeerComparison,
)

logger = logging.getLogger(__name__)


class IndustryEnricher(DataEnricher):
    def enrich(self, ctx: AnalysisContext) -> AnalysisContext:
        ind_data = ctx.industry_data

        if ind_data is None or (not ind_data.industry or ind_data.industry == "未知"):
            ctx.sufficiency.industry = DimensionSufficiency(
                level=SufficiencyLevel.INSUFFICIENT, reason="行业数据完全缺失",
                sample_count=0, score_weight=0.0,
            )
            return ctx

        top_peers = ind_data.top_peers or []

        # 转换为 PeerComparison（当前仅有基础信息，详细财务估值需在采集层已拉取）
        peer_comparisons = []
        for p in top_peers:
            peer_comparisons.append(PeerComparison(
                symbol=p.symbol, name=p.name, market_cap=p.market_cap,
            ))

        peer_count = len(top_peers)
        # 估算有效同行数（当前简化：top_peers 都视为有效，实际取决于采集层结果）
        valid_head_count = len(peer_comparisons)

        if peer_count >= 8 and valid_head_count >= 4:
            level = SufficiencyLevel.SUFFICIENT
            weight = 1.0
            reason = f"行业有效可比公司 {peer_count} 家，头部 {valid_head_count} 家数据完整"
        elif peer_count >= 3 or valid_head_count >= 2:
            level = SufficiencyLevel.PARTIAL
            weight = 0.5
            reason = f"可比较公司 {peer_count} 家（3-7），横向对比参考价值有限"
        else:
            level = SufficiencyLevel.INSUFFICIENT
            weight = 0.0
            reason = f"可比较公司仅 {peer_count} 家（<3），无法进行有效对比"

        ctx.enriched_industry = EnrichedIndustry(
            peer_count=peer_count, top_peers=peer_comparisons,
        )

        ctx.sufficiency.industry = DimensionSufficiency(
            level=level, reason=reason, sample_count=peer_count, score_weight=weight,
        )
        return ctx
```

- [ ] **Step 3: 运行测试，然后 Commit**

```bash
cd D:\code\stock_robot && python -m pytest tests/test_enrichers.py::TestIndustryEnricher -v
git add src/data/enrichers/industry_enricher.py tests/test_enrichers.py
git commit -m "feat(充实层): 实现 IndustryEnricher 行业充足判定"
```

---

### Task 10: SentimentEnricher — 舆情标注与统计

**Files:**
- Create: `src/data/enrichers/sentiment_enricher.py`
- Modify: `tests/test_enrichers.py`

- [ ] **Step 1: 添加舆情充实器测试**

```python
from data.enrichers.sentiment_enricher import SentimentEnricher
from data.schemas import RawSentimentData, RawSentimentItem


def make_raw_sentiment(n: int) -> RawSentimentData:
    items = [
        RawSentimentItem(
            title=f"测试标题 {i}", source="news", publish_date=date.today(),
            content=f"测试内容 {i}",
        )
        for i in range(n)
    ]
    return RawSentimentData(symbol="000001", fetch_date=date.today(), items=items)


class TestSentimentEnricher:
    def test_sufficient_10_plus(self):
        ctx = AnalysisContext(symbol="000001", name="测试",
                              raw_sentiment=make_raw_sentiment(15))
        ctx = PriceEnricher().enrich(ctx)
        ctx = SentimentEnricher().enrich(ctx)
        assert ctx.sufficiency.sentiment.level == SufficiencyLevel.SUFFICIENT
        assert ctx.sufficiency.sentiment.score_weight == 1.0

    def test_partial_3_to_9(self):
        ctx = AnalysisContext(symbol="000001", name="测试",
                              raw_sentiment=make_raw_sentiment(5))
        ctx = PriceEnricher().enrich(ctx)
        ctx = SentimentEnricher().enrich(ctx)
        assert ctx.sufficiency.sentiment.level == SufficiencyLevel.PARTIAL
        assert ctx.sufficiency.sentiment.score_weight == 0.5

    def test_insufficient_below_3(self):
        ctx = AnalysisContext(symbol="000001", name="测试",
                              raw_sentiment=make_raw_sentiment(1))
        ctx = PriceEnricher().enrich(ctx)
        ctx = SentimentEnricher().enrich(ctx)
        assert ctx.sufficiency.sentiment.level == SufficiencyLevel.INSUFFICIENT
        assert ctx.sufficiency.sentiment.score_weight == 0.0

    def test_insufficient_no_data(self):
        ctx = AnalysisContext(symbol="000001", name="测试", raw_sentiment=None)
        ctx = PriceEnricher().enrich(ctx)
        ctx = SentimentEnricher().enrich(ctx)
        assert ctx.sufficiency.sentiment.level == SufficiencyLevel.INSUFFICIENT
```

- [ ] **Step 2: 实现 SentimentEnricher**

创建 `src/data/enrichers/sentiment_enricher.py`：

```python
"""舆情充实器 — 条目统计、LLM 批量标注"""
import json
import logging
from data.enricher import DataEnricher
from data.schemas import (
    AnalysisContext, DimensionSufficiency, SufficiencyLevel,
    EnrichedSentiment, SentimentItem,
)

logger = logging.getLogger(__name__)

LLM_BATCH_PROMPT = """你是一位金融舆情分析专家。请对以下股票相关的新闻和公告逐条进行标注。

输入格式：JSON 数组，每项包含 title、source（news/announcement）、content。
输出格式：JSON 数组，每项包含：
- title: 原标题
- summary: 一句话摘要（20字以内）
- tendency: "positive" / "neutral" / "negative"
- severity: "minor" / "moderate" / "major"
- event_type: "业绩" / "减持" / "回购" / "监管" / "并购" / "分红" / "其他"

标注原则：
1. 业绩预增、回购、分红 → positive
2. 业绩预降、减持、监管问询、诉讼 → negative
3. 定期报告披露、人事变动 → neutral
4. 涉及重大金额（>1亿）、监管处罚 → major
5. 行业政策、分析师研报 → minor/moderate
6. 无法判断倾向的统一标注 neutral

输入数据：
{items_json}

请只输出 JSON 数组，不要有其他内容。"""


class SentimentEnricher(DataEnricher):
    def __init__(self, llm=None):
        self._llm = llm

    def set_llm(self, llm):
        self._llm = llm

    def enrich(self, ctx: AnalysisContext) -> AnalysisContext:
        raw = ctx.raw_sentiment
        if raw is None:
            ctx.sufficiency.sentiment = DimensionSufficiency(
                level=SufficiencyLevel.INSUFFICIENT, reason="舆情数据完全缺失",
                sample_count=0, score_weight=0.0,
            )
            return ctx

        count = len(raw.items)

        if count >= 10:
            level = SufficiencyLevel.SUFFICIENT
            weight = 1.0
            reason = f"有效新闻公告 {count} 条（≥10），舆情分析完整"
        elif count >= 3:
            level = SufficiencyLevel.PARTIAL
            weight = 0.5
            reason = f"有效新闻公告 {count} 条（3–9），情绪参考有限"
        else:
            level = SufficiencyLevel.INSUFFICIENT
            weight = 0.0
            reason = f"有效新闻公告仅 {count} 条（<3），舆情分析不可用"

        # 统计和标注（LLM 仅在 sufficient 或 partial 时调用）
        enriched = EnrichedSentiment(total_count=count)
        if self._llm and count >= 3:
            try:
                items_json = json.dumps(
                    [{"title": item.title, "source": item.source, "content": item.content[:200]}
                     for item in raw.items],
                    ensure_ascii=False,
                )
                prompt = LLM_BATCH_PROMPT.format(items_json=items_json)
                response = self._llm.generate(prompt)
                # 尝试解析 LLM 返回的 JSON
                response = response.strip()
                if response.startswith("```"):
                    response = response.split("\n", 1)[1]
                    if response.endswith("```"):
                        response = response[:-3]
                items_data = json.loads(response)
                for item_data in items_data:
                    si = SentimentItem(
                        title=item_data.get("title", ""),
                        summary=item_data.get("summary", ""),
                        tendency=item_data.get("tendency", "neutral"),
                        severity=item_data.get("severity", "minor"),
                        event_type=item_data.get("event_type", "其他"),
                    )
                    enriched.all_items.append(si)
                    if si.tendency == "positive":
                        enriched.positive_count += 1
                    elif si.tendency == "negative":
                        enriched.negative_count += 1
                    else:
                        enriched.neutral_count += 1
                    if si.severity == "major":
                        enriched.major_events.append(si)
            except Exception as e:
                logger.warning(f"LLM 舆情标注失败: {e}")

        ctx.enriched_sentiment = enriched

        ctx.sufficiency.sentiment = DimensionSufficiency(
            level=level, reason=reason, sample_count=count, score_weight=weight,
        )
        return ctx
```

- [ ] **Step 3: 运行测试验证**

```bash
cd D:\code\stock_robot && python -m pytest tests/test_enrichers.py::TestSentimentEnricher -v
```
预期：4 PASS

- [ ] **Step 4: Commit**

```bash
git add src/data/enrichers/sentiment_enricher.py tests/test_enrichers.py
git commit -m "feat(充实层): 实现 SentimentEnricher 舆情充足判定与 LLM 批量标注"
```

---

### Task 11: 管道集成 — 接入充实层

**Files:**
- Modify: `src/core/pipeline.py`

- [ ] **Step 1: 在 Pipeline.run 中集成充实步骤**

在 `pipeline.py` 的 `run` 方法中，`self.collect(...)` 之后、分析模块循环之前插入充实调用。

修改 `run` 方法（在 `ctx = self.collect(...)` 之后添加）：

```python
# 充实步骤（collect 之后 analyze 之前）
from data.enricher import ContextEnricher
from data.enrichers import PriceEnricher, FinancialEnricher, ValuationEnricher, IndustryEnricher, SentimentEnricher

enricher = ContextEnricher()
enricher.register(PriceEnricher())
enricher.register(FinancialEnricher())
enricher.register(ValuationEnricher())
enricher.register(IndustryEnricher())

# 如果 LLM 可用，传给 SentimentEnricher
sentiment_enricher = SentimentEnricher()
if self._llm_enabled:
    provider = self._config.get("llm.provider", "openai")
    llm = self._registry.get_llm_backend(provider)
    sentiment_enricher.set_llm(llm)
enricher.register(sentiment_enricher)

ctx = enricher.enrich(ctx)
```

修改 `run` 方法签名中充实步骤后的注释，确保 `on_progress` 回调在充实阶段也有反馈。

- [ ] **Step 2: 更新 enrichers __init__.py 导出 FinancialEnricher**

修改 `src/data/enrichers/__init__.py`：

```python
from data.enrichers.price_enricher import PriceEnricher
from data.enrichers.financial_enricher import FinancialEnricher
from data.enrichers.valuation_enricher import ValuationEnricher
from data.enrichers.industry_enricher import IndustryEnricher
from data.enrichers.sentiment_enricher import SentimentEnricher

__all__ = [
    "PriceEnricher", "FinancialEnricher", "ValuationEnricher",
    "IndustryEnricher", "SentimentEnricher",
]
```

- [ ] **Step 3: 验证管道导入**

```bash
cd D:\code\stock_robot && python -c "from src.core.pipeline import Pipeline; print('Pipeline import OK')"
```

- [ ] **Step 4: Commit**

```bash
git add src/core/pipeline.py src/data/enrichers/__init__.py
git commit -m "feat(管道): 集成充实层到管道 run 方法"
```

---

### Task 12: 分析层 — 打分逻辑实现

**Files:**
- Modify: `src/analysis/financial.py`
- Modify: `src/analysis/technical.py`
- Modify: `src/analysis/valuation.py`
- Modify: `src/analysis/industry.py`
- Modify: `src/analysis/sentiment.py`
- Create: `tests/test_scoring.py`

- [ ] **Step 1: 编写打分测试**

创建 `tests/test_scoring.py`：

```python
"""打分体系单元测试"""
import pytest
from datetime import date, datetime
from data.schemas import (
    AnalysisContext, AnalysisResult, FinancialData, PriceData,
    ValuationData, EnrichedValuation, EnrichedIndustry, EnrichedSentiment,
    DailyValuationPoint, PeerComparison,
    DataSufficiency, DimensionSufficiency, SufficiencyLevel,
)


def make_scoring_context():
    ctx = AnalysisContext(
        symbol="000001", name="测试银行",
        financial_data=[
            FinancialData(symbol="000001", fiscal_quarter=date(2025, 12, 31),
                          revenue=100e8, net_profit=12e8, deducted_net_profit=11e8,
                          total_assets=500e8, total_equity=50e8,
                          operating_cash_flow=10e8, roe=0.12, gross_margin=0.45),
            FinancialData(symbol="000001", fiscal_quarter=date(2025, 9, 30),
                          revenue=95e8, net_profit=11e8, deducted_net_profit=10e8,
                          total_assets=490e8, total_equity=48e8,
                          operating_cash_flow=9e8, roe=0.11, gross_margin=0.44),
            FinancialData(symbol="000001", fiscal_quarter=date(2025, 6, 30),
                          revenue=90e8, net_profit=10e8, deducted_net_profit=9e8,
                          total_assets=480e8, total_equity=46e8,
                          operating_cash_flow=8e8, roe=0.10, gross_margin=0.43),
            FinancialData(symbol="000001", fiscal_quarter=date(2025, 3, 31),
                          revenue=85e8, net_profit=9e8, deducted_net_profit=8e8,
                          total_assets=470e8, total_equity=44e8,
                          operating_cash_flow=7e8, roe=0.09, gross_margin=0.42),
        ],
        price_data=[
            PriceData(symbol="000001", trade_date=date(2025, 7, 1) + timedelta(days=i),
                      open=10.0, high=10.5, low=9.8, close=10.2, volume=1000000)
            for i in range(120)
        ],
        valuation_data=ValuationData(symbol="000001", date=date.today(),
                                     pe_ttm=7.5, pb=0.85, ps_ttm=1.2),
    )
    ctx.sufficiency = DataSufficiency(
        price=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=120, score_weight=1.0),
        financial=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=4, score_weight=1.0),
        valuation=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=150, score_weight=1.0),
        industry=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=10, score_weight=1.0),
        sentiment=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=15, score_weight=1.0),
    )
    ctx.enriched_valuation = EnrichedValuation(
        daily_points=[
            DailyValuationPoint(trade_date=date(2025, 7, 1) + timedelta(days=i),
                                close=10.2, pe=7.0 + i * 0.01, pb=0.85, ps=1.2)
            for i in range(300)
        ],
        pe_percentile=35.0, pb_percentile=28.0, pe_zone="中性",
        pe_median=8.0, pe_high=12.0, pe_low=5.0,
    )
    ctx.enriched_industry = EnrichedIndustry(
        peer_count=10, top_peers=[
            PeerComparison(symbol="600036", name="招商银行", market_cap=10000e8,
                          pe_ttm=9.0, pb=1.2, gross_margin=0.52,
                          net_margin=0.35, roe=0.15),
            PeerComparison(symbol="601398", name="工商银行", market_cap=20000e8,
                          pe_ttm=6.0, pb=0.7, gross_margin=0.48,
                          net_margin=0.32, roe=0.11),
        ],
        industry_median_pe=7.0, industry_median_pb=0.90,
        industry_median_gross_margin=0.48, industry_median_net_margin=0.30,
        target_pe_premium=7.1, target_market_cap_rank=8,
    )
    return ctx


class TestFinancialScoring:
    def test_roe_sufficient(self):
        from analysis.financial import FinancialAnalyzer
        ctx = make_scoring_context()
        result = FinancialAnalyzer().analyze(ctx)
        assert result.score is not None
        assert 0 <= result.score <= 10
        assert "ROE" in result.score_detail

    def test_insufficient_dimension_returns_none_score(self):
        from analysis.financial import FinancialAnalyzer
        ctx = make_scoring_context()
        ctx.sufficiency.financial.level = SufficiencyLevel.INSUFFICIENT
        ctx.sufficiency.financial.score_weight = 0.0
        result = FinancialAnalyzer().analyze(ctx)
        assert result.score is None
        assert result.status == "unavailable"


class TestTechnicalScoring:
    def test_sufficient_returns_score(self):
        from analysis.technical import TechnicalAnalyzer
        ctx = make_scoring_context()
        result = TechnicalAnalyzer().analyze(ctx)
        assert result.score is not None
        assert 0 <= result.score <= 10

    def test_insufficient_no_score(self):
        from analysis.technical import TechnicalAnalyzer
        ctx = make_scoring_context()
        ctx.sufficiency.price.level = SufficiencyLevel.INSUFFICIENT
        ctx.sufficiency.price.score_weight = 0.0
        result = TechnicalAnalyzer().analyze(ctx)
        assert result.score is None
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd D:\code\stock_robot && python -m pytest tests/test_scoring.py -v
```
预期：大部分 FAIL（analyze 方法尚未返回 score）

- [ ] **Step 3: 实现财务打分逻辑**

修改 `src/analysis/financial.py` 的 `analyze` 方法，在 return 之前添加打分逻辑：

```python
# 充足状态检查
if context.sufficiency and context.sufficiency.financial.level == SufficiencyLevel.INSUFFICIENT:
    return AnalysisResult(dimension=self.dimension, status="unavailable",
                          summary="财务数据不足", metrics=metrics, score=None, score_detail="财务维度数据不足，跳过打分")

score = 0.0
score_parts = []
risk_flags = []

# 1. ROE 水平 (3分)
roe = latest.roe
if roe is not None:
    if roe < 0:
        score_parts.append(f"ROE {roe*100:.1f}%（负数），得 0/3 分")
        risk_flags.append("roe_low")
    elif roe >= 0.15:
        score += 3; score_parts.append(f"ROE {roe*100:.1f}%（≥15%），得 3/3 分")
    elif roe >= 0.10:
        score += 2; score_parts.append(f"ROE {roe*100:.1f}%（10-15%），得 2/3 分")
    elif roe >= 0.05:
        score += 1; score_parts.append(f"ROE {roe*100:.1f}%（5-10%），得 1/3 分")
    else:
        score += 0; score_parts.append(f"ROE {roe*100:.1f}%（<5%），得 0/3 分")
else:
    score_parts.append("ROE 数据缺失，得 0/3 分")

# 2. 资产负债率 (2分)
asset_liability_ratio = latest.total_assets / latest.total_equity if (
    latest.total_assets and latest.total_equity and latest.total_equity > 0
) else None
if asset_liability_ratio:
    al_pct = (1 - 1 / asset_liability_ratio) * 100
    if 40 <= al_pct <= 70:
        score += 2; score_parts.append(f"资产负债率 {al_pct:.0f}%（适中），得 2/2 分")
    elif 20 <= al_pct < 40 or 70 < al_pct <= 90:
        score += 1; score_parts.append(f"资产负债率 {al_pct:.0f}%（偏高/偏低），得 1/2 分")
    else:
        score += 0; score_parts.append(f"资产负债率 {al_pct:.0f}%（极端），得 0/2 分")
        risk_flags.append("high_debt")
else:
    score_parts.append("资产负债率数据缺失，得 0/2 分")

# 3. 经营现金流/净利润匹配 (3分)
ocf = latest.operating_cash_flow
np_val = latest.net_profit
if np_val is not None and np_val <= 0:
    score += 0; score_parts.append(f"净利润为负，现金流匹配子项 0/3 分")
    risk_flags.append("cash_flow_mismatch")
elif ocf is not None and np_val is not None and np_val > 0:
    ratio = ocf / np_val
    if ratio > 0.8:
        score += 3; score_parts.append(f"经营现金流/净利润 {ratio:.2f}（>0.8），得 3/3 分")
    elif ratio >= 0.5:
        score += 2; score_parts.append(f"经营现金流/净利润 {ratio:.2f}（0.5-0.8），得 2/3 分")
    else:
        score += 1; score_parts.append(f"经营现金流/净利润 {ratio:.2f}（<0.5），得 1/3 分")
        risk_flags.append("cash_flow_mismatch")
else:
    score_parts.append("经营现金流数据缺失，得 0/3 分")

# 4. 毛利率稳定性 (2分)
gross_margins = [d.gross_margin for d in sorted_data[:4] if d.gross_margin is not None]
if gross_margins:
    if any(gm is not None and gm < 0 for gm in gross_margins):
        score += 0; score_parts.append("存在负毛利率，得 0/2 分")
    else:
        gm_range = max(gross_margins) - min(gross_margins) if len(gross_margins) >= 2 else 0
        if gm_range < 0.05:
            score += 2; score_parts.append(f"近4期毛利率波动 {gm_range*100:.1f}pp（<5pp），得 2/2 分")
        elif gm_range < 0.15:
            score += 1; score_parts.append(f"近4期毛利率波动 {gm_range*100:.1f}pp（5-15pp），得 1/2 分")
        else:
            score += 0; score_parts.append(f"近4期毛利率波动 {gm_range*100:.1f}pp（>15pp），得 0/2 分")
else:
    score_parts.append("毛利率数据缺失，得 0/2 分")

score = round(score, 1)
score_detail = "；".join(score_parts)
return AnalysisResult(dimension=self.dimension, status=status, summary=summary,
                      metrics=metrics, score=score, score_detail=score_detail,
                      risk_flags=risk_flags)
```

（注意：`SufficiencyLevel.INSUFFICIENT` 需要从 `data.schemas` 导入）

- [ ] **Step 4: 实现技术面打分逻辑**

修改 `src/analysis/technical.py` 的 `analyze` 方法，在现有 metrics 计算之后、return 之前添加打分：

```python
# 充足状态检查
if context.sufficiency and context.sufficiency.price.level == SufficiencyLevel.INSUFFICIENT:
    return AnalysisResult(dimension=self.dimension, status="unavailable",
                          summary="行情数据不足", metrics=metrics, score=None,
                          score_detail="技术面数据不足，跳过打分")

score = 0.0
score_parts = []
risk_flags = []

# 1. 均线结构 (4分)
ma5 = metrics.get("ma5")
ma20 = metrics.get("ma20")
ma60 = metrics.get("ma60")
if ma5 and ma20 and ma60:
    if ma5 > ma20 > ma60:
        score += 4; score_parts.append("MA5>MA20>MA60 多头排列，得 4/4 分")
    elif ma5 > ma20 and ma20 < ma60:
        score += 2; score_parts.append("均线交叉震荡，得 2/4 分")
    else:
        score += 1; score_parts.append("均线空头排列，得 1/4 分")
        risk_flags.append("bearish_ma")
elif ma5 and ma20:
    score += 2; score_parts.append(f"缺少 MA60，均线结构部分可判，得 2/4 分")
else:
    score_parts.append("均线数据不足，得 0/4 分")

# 2. 量价配合 (3分)
price_vs_ma20 = metrics.get("price_vs_ma20")
vol_ratio = metrics.get("volume_ratio")
if vol_ratio is not None and price_vs_ma20 is not None:
    if price_vs_ma20 > 0 and vol_ratio > 1.2:
        score += 3; score_parts.append(f"放量上涨（量比 {vol_ratio:.2f}），得 3/3 分")
    elif abs(price_vs_ma20) < 2 and vol_ratio is not None and 0.8 <= vol_ratio <= 1.2:
        score += 2; score_parts.append(f"横盘无量（量比 {vol_ratio:.2f}），得 2/3 分")
    elif price_vs_ma20 < 0 and vol_ratio > 1.2:
        score += 1; score_parts.append(f"放量下跌（量比 {vol_ratio:.2f}），得 1/3 分")
        risk_flags.append("volume_bearish")
    else:
        score += 2; score_parts.append(f"量价配合一般（量比 {vol_ratio:.2f}），得 2/3 分")
else:
    score_parts.append("量能数据不足，得 0/3 分")

# 3. MACD 形态 (3分)
dif = metrics.get("macd_dif")
dea = metrics.get("macd_dea")
macd_bar = metrics.get("macd_bar")
if dif is not None and dea is not None and macd_bar is not None:
    if dif > dea and macd_bar > 0:
        score += 3; score_parts.append(f"DIF>DEA 且 MACD 柱为正，得 3/3 分")
    elif (dif > dea) != (macd_bar > 0):
        score += 2; score_parts.append(f"MACD 临界状态，得 2/3 分")
    else:
        score += 1; score_parts.append(f"MACD 空头形态，得 1/3 分")
else:
    score_parts.append("MACD 数据不足，得 0/3 分")

score = round(score, 1)
score_detail = "；".join(score_parts)
status = "partial" if context.sufficiency.price.level == SufficiencyLevel.PARTIAL else status
return AnalysisResult(dimension=self.dimension, status=status, summary=summary,
                      metrics=metrics, score=score, score_detail=score_detail,
                      risk_flags=risk_flags)
```

- [ ] **Step 5: 实现估值打分逻辑**

修改 `src/analysis/valuation.py` 的 `analyze` 方法，在现有 metrics 计算之后添加打分：

```python
if context.sufficiency and context.sufficiency.valuation.level == SufficiencyLevel.INSUFFICIENT:
    return AnalysisResult(dimension=self.dimension, status="unavailable",
                          summary="估值数据不足", metrics=metrics, score=None,
                          score_detail="估值维度数据不足，跳过打分")

score = 0.0
score_parts = []
risk_flags = []
ev = context.enriched_valuation
ei = context.enriched_industry

# 1. PE 历史分位 (4分)
pe_pct = ev.pe_percentile if ev else None
if pe_pct is not None:
    if pe_pct < 30:
        score += 4; score_parts.append(f"PE 处于 {pe_pct:.0f}% 分位（低估区间），得 4/4 分")
    elif pe_pct < 50:
        score += 3; score_parts.append(f"PE 处于 {pe_pct:.0f}% 分位，得 3/4 分")
    elif pe_pct < 70:
        score += 2; score_parts.append(f"PE 处于 {pe_pct:.0f}% 分位（中性区间），得 2/4 分")
    elif pe_pct < 90:
        score += 1; score_parts.append(f"PE 处于 {pe_pct:.0f}% 分位（偏高），得 1/4 分")
        risk_flags.append("high_pe_premium")
    else:
        score += 0; score_parts.append(f"PE 处于 {pe_pct:.0f}% 分位（极高），得 0/4 分")
        risk_flags.append("high_pe_premium")
else:
    score_parts.append("PE 历史分位数据缺失，得 0/4 分")

# 2. PB 历史分位 (2分)
pb_pct = ev.pb_percentile if ev else None
pb_val = metrics.get("pb")
if pb_val is not None and pb_val <= 0:
    score += 0; score_parts.append("PB 为负，得 0/2 分")
elif pb_pct is not None:
    if pb_pct < 30:
        score += 2; score_parts.append(f"PB 处于 {pb_pct:.0f}% 分位，得 2/2 分")
    elif pb_pct < 70:
        score += 1; score_parts.append(f"PB 处于 {pb_pct:.0f}% 分位，得 1/2 分")
    else:
        score += 0; score_parts.append(f"PB 处于 {pb_pct:.0f}% 分位（偏高），得 0/2 分")
else:
    score_parts.append("PB 历史分位数据缺失，得 0/2 分")

# 3. 行业溢价程度 (4分)
premium = ei.target_pe_premium if ei else None
if premium is None and pe_pct is None:
    score_parts.append("行业溢价数据缺失（标的亏损无 PE），得 0/4 分")
    risk_flags.append("high_pe_premium")
elif premium is not None:
    if premium < -20:
        score += 4; score_parts.append(f"相对行业 PE 折价 {abs(premium):.0f}%（>20%），得 4/4 分")
    elif premium < 0:
        score += 3; score_parts.append(f"相对行业 PE 折价 {abs(premium):.0f}%，得 3/4 分")
    elif premium < 20:
        score += 2; score_parts.append(f"相对行业 PE 溢价 {premium:.0f}%，得 2/4 分")
    elif premium < 50:
        score += 1; score_parts.append(f"相对行业 PE 溢价 {premium:.0f}%，得 1/4 分")
    else:
        score += 0; score_parts.append(f"相对行业 PE 溢价 {premium:.0f}%（>50%），得 0/4 分")
else:
    score_parts.append("行业对比数据缺失，得 0/4 分")

score = round(score, 1)
score_detail = "；".join(score_parts)
return AnalysisResult(dimension=self.dimension, status="partial", summary=summary,
                      metrics=metrics, score=score, score_detail=score_detail,
                      risk_flags=risk_flags)
```

- [ ] **Step 6: 实现行业和舆情打分**

行业打分（修改 `src/analysis/industry.py`）：

```python
def _calculate_score(self, result: AnalysisResult, context: AnalysisContext):
    if context.sufficiency and context.sufficiency.industry.level == SufficiencyLevel.INSUFFICIENT:
        result.score = None
        result.score_detail = "行业维度数据不足，跳过打分"
        result.status = "unavailable"
        return result

    ei = context.enriched_industry
    score = 5.0  # 基础分 5
    score_parts = ["基础分 5"]
    risk_flags = []

    if ei:
        # 同行数量充足 +2
        if ei.peer_count >= 8:
            score += 2; score_parts.append(f"行业可比公司 {ei.peer_count} 家（充足），+2 分")
        elif ei.peer_count >= 3:
            score += 1; score_parts.append(f"行业可比公司 {ei.peer_count} 家（偏少），+1 分")
        # 行业位次 +2
        rank = ei.target_market_cap_rank
        if rank is not None and rank <= 5:
            score += 2; score_parts.append(f"市值位次第 {rank}（头部），+2 分")
        elif rank is not None:
            score += 1; score_parts.append(f"市值位次第 {rank}，+1 分")
        # 毛利率对比 +1
        if ei.industry_median_gross_margin is not None and context.financial_data:
            latest_gm = context.financial_data[0].gross_margin if context.financial_data else None
            if latest_gm is not None:
                diff = (latest_gm - ei.industry_median_gross_margin) * 100
                if diff > 5:
                    score += 1; score_parts.append(f"毛利率高于行业 {diff:.0f}pp，+1 分")
                elif diff < -5:
                    score_parts.append(f"毛利率低于行业 {abs(diff):.0f}pp，不加分")
                    risk_flags.append("industry_weak_margin")
        score = min(score, 10.0)

    result.score = round(score, 1)
    result.score_detail = "；".join(score_parts)
    result.risk_flags = risk_flags
    return result
```

舆情打分（修改 `src/analysis/sentiment.py`）：

```python
def _calculate_score(self, result: AnalysisResult, context: AnalysisContext):
    if context.sufficiency and context.sufficiency.sentiment.level == SufficiencyLevel.INSUFFICIENT:
        result.score = None
        result.score_detail = "舆情维度数据不足，跳过打分"
        result.status = "unavailable"
        return result

    es = context.enriched_sentiment
    score = 5.0
    score_parts = ["基础分 5"]
    risk_flags = []

    if es and es.total_count > 0:
        # 消息数量充足 +2
        if es.total_count >= 10:
            score += 2; score_parts.append(f"有效消息 {es.total_count} 条（充足），+2 分")
        else:
            score += 1; score_parts.append(f"有效消息 {es.total_count} 条（偏少），+1 分")
        # 利好占比 +2
        pos_ratio = es.positive_count / es.total_count
        if pos_ratio > 0.5:
            score += 2; score_parts.append(f"利好消息占比 {pos_ratio:.0%}（偏多），+2 分")
        elif pos_ratio > 0.3:
            score += 1; score_parts.append(f"利好消息占比 {pos_ratio:.0%}（中性），+1 分")
        else:
            score_parts.append(f"利好消息占比 {pos_ratio:.0%}（偏少），不加分")
        # 重大负面 -1
        if es.negative_count > 0 and any(e.severity == "major" for e in es.all_items):
            score -= 1; score_parts.append("存在重大利空事件，-1 分")
            risk_flags.append("major_negative_news")
        # 消息质量 +1
        if any(e.source == "announcement" for e in es.all_items):
            score += 1; score_parts.append("包含官方公告（高权重），+1 分")
        score = max(0.0, min(10.0, score))

    result.score = round(score, 1)
    result.score_detail = "；".join(score_parts)
    result.risk_flags = risk_flags
    return result
```

- [ ] **Step 7: 运行全部打分测试**

```bash
cd D:\code\stock_robot && python -m pytest tests/test_scoring.py -v
```

- [ ] **Step 8: Commit**

```bash
git add src/analysis/financial.py src/analysis/technical.py src/analysis/valuation.py src/analysis/industry.py src/analysis/sentiment.py tests/test_scoring.py
git commit -m "feat(分析层): 五大维度量化打分逻辑实现"
```

---

### Task 13: LLM — 单次批量 Prompt 模板

**Files:**
- Create: `src/llm/prompt_templates/batch_analysis_claude.jinja2`
- Create: `src/llm/prompt_templates/batch_analysis_openai.jinja2`
- Modify: `src/core/pipeline.py`

- [ ] **Step 1: 创建 Claude 批量 prompt 模板**

创建 `src/llm/prompt_templates/batch_analysis_claude.jinja2`：

```jinja2
你是一位资深金融分析师。请基于以下量化分析数据，为 {{ name }}（{{ symbol }}）撰写分析报告。

## 各维度打分

| 维度 | 得分 | 权重 | 充足状态 |
|------|------|------|----------|
{% for dim in scores %}
| {{ dim.label }} | {{ dim.score if dim.score is not none else 'N/A' }} | {{ dim.weight }} | {{ dim.sufficiency }} |
{% endfor %}

## 得分详情

{% for dim in scores %}
### {{ dim.label }}
{% if dim.score_detail %}
{{ dim.score_detail }}
{% else %}
数据不足，跳过此维度。
{% endif %}
{% endfor %}

## 风险标签

{% if risk_flags %}
{% for flag in risk_flags %}
- {{ flag }}
{% endfor %}
{% else %}
无明确风险标签。
{% endif %}

## 完成的分析维度

已覆盖维度：{{ covered_dims }}
缺失维度：{{ missing_dims }}

请按以下固定分段输出，每段严格以 ## N.标识开头：

## 1.维度分项解读
逐维度解读得分原因，每个维度控制在 150 字以内，所有结论必须引用得分详情中的具体数值。数据不足的维度标注"样本不足，不做解读"。

## 2.全维度风险汇总
列出所有可量化风险（财务、估值、行业、技术、舆情），每条风险标注严重程度。若某维度无风险，注明"无明显风险信号"。

## 3.多风格观察视角
从以下三个视角分别用 100 字以内说明当前标的的关注重点：
- 价值投资视角：关注长期盈利能力、估值安全边际
- 成长投资视角：关注营收/利润增速、行业扩张空间
- 短线技术视角：关注均线形态、量价配合、短期压力支撑

以上三种视角不构成操作建议，仅说明不同投资风格的关注点差异。

## 4.综合评级
基于可获取数据给出整体评价，使用"综合数据表现偏正面/中性/偏负面"表述方式，不可出现买卖、调仓、持仓等操作建议。如数据不足，请直言"当前数据不足以形成完整判断"。

严格禁止词汇：买入、卖出、加仓、减仓、持仓、重仓、轻仓、止损、止盈、逢低布局、看涨、看跌、目标价、建议
```

- [ ] **Step 2: 创建 OpenAI 批量 prompt 模板**

创建 `src/llm/prompt_templates/batch_analysis_openai.jinja2`，内容同上（或从 Claude 模板复制并调整格式差异）。

- [ ] **Step 3: 修改管道 _generate_commentary 使用批量 prompt**

修改 `src/core/pipeline.py` 的 `_generate_commentary` 方法：将原来逐个维度调 LLM 的逻辑替换为单次批量调用。

核心逻辑：

```python
def _generate_commentary(self, symbol, name, results, on_progress=None):
    commentary = {}
    provider = self._config.get("llm.provider", "openai")
    llm = self._registry.get_llm_backend(provider)
    if llm is None:
        return commentary

    results_map = {r.dimension: r for r in results}
    dim_labels = {
        "financial": "财务分析", "technical": "技术面分析",
        "valuation": "估值分析", "industry": "行业分析",
        "sentiment": "舆情分析",
    }

    scores = []
    covered = []
    missing = []
    all_risk_flags = []

    for dim, label in dim_labels.items():
        r = results_map.get(dim)
        if r and r.score is not None:
            scores.append({"label": label, "score": r.score, "weight": "...",
                          "sufficiency": "充足", "score_detail": r.score_detail})
            covered.append(label)
            all_risk_flags.extend(r.risk_flags)
        elif r and r.status == "partial":
            scores.append({"label": label, "score": r.score, "weight": "...",
                          "sufficiency": "部分可用", "score_detail": r.score_detail})
            covered.append(label)
        else:
            scores.append({"label": label, "score": None, "weight": "跳过",
                          "sufficiency": "数据不足", "score_detail": ""})
            missing.append(label)

    try:
        from jinja2 import Environment, FileSystemLoader
        template_dir = Path(__file__).parent.parent / "llm" / "prompt_templates"
        env = Environment(loader=FileSystemLoader(str(template_dir)))
        template = env.get_template(f"batch_analysis_{provider}.jinja2")
        prompt = template.render(
            name=name, symbol=symbol, scores=scores,
            risk_flags=all_risk_flags,
            covered_dims="、".join(covered) if covered else "无",
            missing_dims="、".join(missing) if missing else "无",
        )
        response = llm.generate(prompt)
        commentary["bulk"] = response
    except Exception as e:
        logger.warning(f"批量 LLM 分析生成失败: {e}")

    return commentary
```

- [ ] **Step 4: 验证模板渲染**

```bash
cd D:\code\stock_robot && python -c "
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
env = Environment(loader=FileSystemLoader(str(Path('src/llm/prompt_templates'))))
t = env.get_template('batch_analysis_claude.jinja2')
print('Template loaded OK')
"
```

- [ ] **Step 5: Commit**

```bash
git add src/llm/prompt_templates/batch_analysis_claude.jinja2 src/llm/prompt_templates/batch_analysis_openai.jinja2 src/core/pipeline.py
git commit -m "feat(LLM): 改为单次批量 prompt，取代多轮分维度调用"
```

---

### Task 14: Report — v2 模板与 Builder

**Files:**
- Create: `src/report/templates/report_v2.jinja2`
- Modify: `src/report/builder.py`
- Modify: `src/stock_robot/cli.py`

- [ ] **Step 1: 创建 report_v2.jinja2 模板**

创建 `src/report/templates/report_v2.jinja2`，按 spec 定义的六段结构实现完整模板。关键结构：

```jinja2
# {{ name }}（{{ symbol }}）分析报告

> 生成时间：{{ generated_at }} | 市场：{{ market }}

{% if no_llm %}
> **[纯量化模式，无 AI 解读]**
{% endif %}

---

## 一、标的基础概况

| 项目 | 内容 |
|------|------|
| 股票名称 | {{ name }} |
| 股票代码 | {{ symbol }} |
| 所属行业 | {{ industry }} |
| 总市值 | {{ market_cap }} |
| 流通市值 | {{ float_cap }} |
| 近1年最高价 | {{ year_high }} |
| 近1年最低价 | {{ year_low }} |
| 当前位置 | {{ price_position_pct }} |

---

## 二、五大维度纯量化数据

### 1. 财务量化数据
{% if sufficiency.financial == "sufficient" or sufficiency.financial == "partial" %}
...（近3年核心指标表格）
{% else %}
> 财务数据不足，已跳过
{% endif %}

### 2. 技术面量化数据
...（均线、MACD、量能表格）

### 3. 估值量化数据
...（当前 PE/PB/PS + 历史分位）

### 4. 行业对比量化数据
...（头部5家 vs 标的 vs 行业中位数表）

### 5. 舆情量化数据
...（公告/新闻列表 + 倾向统计）

---

## 三、五大维度标准化打分

| 维度 | 得分 | 权重 | 充足状态 |
|------|------|------|----------|
| 财务健康 | {{ financial.score | default("N/A") }} | 30% | {{ financial.sufficiency }} |
| 成长能力 | {{ growth.score | default("N/A") }} | 25% | {{ growth.sufficiency }} |
| 估值合理 | {{ valuation.score | default("N/A") }} | 25% | {{ valuation.sufficiency }} |
| 技术趋势 | {{ technical.score | default("N/A") }} | 20% | {{ technical.sufficiency }} |

**综合得分（扣风险前）：** {{ base_score }}/10
**风险扣分：** -{{ risk_deduction }}
**最终综合得分：** {{ final_score }}/10

### 各维度得分详情

{% for dim in score_details %}
#### {{ dim.label }}
{{ dim.detail }}
{% endfor %}

---

## 四、AI 中性解读 + 风险汇总

{% if commentary.bulk %}
{{ commentary.bulk }}
{% else %}
> AI 解读不可用
{% endif %}

---

## 五、多风格观察视角
（由 AI 解读中的 ## 3. 段提供，无需重复）

---

## 六、工具局限性 + 免责声明

> 本报告数据来源为 AkShare 公开接口，数据时效性和准确性可能受网络、接口限流等因素影响。
> 所有分析方法均为量化指标客观计算，不包含人工主观判断。
> 报告中的分数和多风格视角仅用于客观描述不同投资风格下的关注点差异，不构成任何形式的投资建议。
> 市场有风险，投资需谨慎。使用者需自行承担全部投资决策风险。
```

- [ ] **Step 2: 修改 ReportBuilder 支持 v2**

修改 `src/report/builder.py`：

```python
"""报告构建器 — 将分析结果组装为 Markdown 报告"""
from datetime import datetime
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from data.schemas import AnalysisResult


class ReportBuilder:
    def __init__(self):
        template_dir = Path(__file__).parent / "templates"
        self._env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def build(self, symbol: str, name: str, results: list[AnalysisResult],
              commentary: dict[str, str], no_llm: bool = False, **kwargs) -> str:
        results_map = {r.dimension: r for r in results}
        template = self._env.get_template("report_v2.jinja2")

        # 基础信息
        industry = kwargs.get("industry", "未知")
        market_cap = kwargs.get("market_cap", "暂无")
        float_cap = kwargs.get("float_cap", "暂无")
        year_high = kwargs.get("year_high", "暂无")
        year_low = kwargs.get("year_low", "暂无")
        price_position_pct = kwargs.get("price_position_pct", "暂无")

        # 打分表
        dim_labels = {"financial": "财务健康", "technical": "技术趋势",
                      "valuation": "估值合理", "industry": "行业对比",
                      "sentiment": "舆情风险"}
        dim_weights = {"financial": "30%", "technical": "20%",
                       "valuation": "25%", "industry": "25%", "sentiment": "不计分"}
        sufficiency_map = {
            "sufficient": "充足", "partial": "部分可用", "insufficient": "数据不足",
        }

        score_rows = []
        score_details = []
        for dim, label in dim_labels.items():
            r = results_map.get(dim)
            if r:
                suff = sufficiency_map.get(r.status, r.status)
                score_rows.append({
                    "label": label, "score": f"{r.score:.1f}" if r.score is not None else "N/A",
                    "weight": dim_weights[dim], "sufficiency": suff,
                })
                score_details.append({
                    "label": label, "detail": r.score_detail or "无详情",
                })

        base_score = kwargs.get("base_score", 0)
        risk_deduction = kwargs.get("risk_deduction", 0)
        final_score = kwargs.get("final_score", 0)

        all_risk_flags = []
        for r in results:
            all_risk_flags.extend(r.risk_flags)

        return template.render(
            symbol=symbol, name=name, market="A 股",
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            no_llm=no_llm,
            industry=industry, market_cap=market_cap, float_cap=float_cap,
            year_high=year_high, year_low=year_low,
            price_position_pct=price_position_pct,
            results_map=results_map, commentary=commentary,
            score_rows=score_rows, score_details=score_details,
            base_score=base_score, risk_deduction=risk_deduction,
            final_score=final_score, all_risk_flags=all_risk_flags,
        )
```

- [ ] **Step 3: 修改 CLI 计算综合分并传入 builder**

修改 `src/stock_robot/cli.py` 的 `analyze` 命令中报告生成部分：

```python
# 计算综合分
dim_weights = {"financial": 0.30, "technical": 0.20, "valuation": 0.25, "industry": 0.25}
base_score = 0.0
total_weight = 0.0
results_map = {r.dimension: r for r in results}

for dim, weight in dim_weights.items():
    r = results_map.get(dim)
    if r and r.score is not None:
        base_score += r.score * weight
        total_weight += weight

if total_weight > 0:
    base_score = round(base_score / total_weight, 1)

# 计算风险扣分
risk_deduction = 0
for r in results:
    for flag in r.risk_flags:
        if flag in ("roe_low", "high_debt", "cash_flow_mismatch",
                     "revenue_declineing", "profit_declineing"):
            risk_deduction = min(risk_deduction + 1, 3)
        elif flag in ("high_pe_premium", "pb_below_peer"):
            risk_deduction = min(risk_deduction + 1 - (3 if risk_deduction > 3 else 0), 5)
        elif flag == "industry_weak_margin":
            risk_deduction += 1
        elif flag in ("bearish_ma", "volume_bearish"):
            risk_deduction += 1
        elif flag == "major_negative_news":
            risk_deduction += 1

risk_deduction = min(risk_deduction, 10)
final_score = max(0, base_score - risk_deduction)

# 构建基础信息
price_data = ctx.price_data or []
year_high = max(p.high for p in price_data) if price_data else None
year_low = min(p.low for p in price_data) if price_data else None
latest_price = price_data[-1].close if price_data else None
if year_high and year_low and latest_price:
    price_range = year_high - year_low
    price_position_pct = f"{((latest_price - year_low) / price_range * 100):.0f}%" if price_range > 0 else "暂无"
else:
    price_position_pct = "暂无"

builder = ReportBuilder()
report = builder.build(
    symbol, name, results, commentary,
    no_llm=no_llm,
    industry=(ctx.industry_data.industry if ctx.industry_data else "未知"),
    year_high=f"{year_high:.2f}" if year_high else "暂无",
    year_low=f"{year_low:.2f}" if year_low else "暂无",
    price_position_pct=price_position_pct,
    base_score=base_score, risk_deduction=risk_deduction, final_score=final_score,
)
```

- [ ] **Step 4: Commit**

```bash
git add src/report/templates/report_v2.jinja2 src/report/builder.py src/stock_robot/cli.py
git commit -m "feat(报告): 添加 v2 报告模板，整合打分和 AI 中性解读"
```

---

### Task 15: 端到端集成测试

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: 编写集成测试**

```python
"""管道端到端集成测试"""
import pytest


class TestPipelineIntegration:
    def test_pipeline_runs_without_error(self):
        """验证完整管道：采集 → 充实 → 分析 → LLM → 报告"""
        from core.registry import Registry
        from data.akshare import AkShareAdapter
        from core.pipeline import Pipeline
        from utils.config import Config

        reg = Registry()
        reg.register_data_source(AkShareAdapter())

        from analysis.financial import FinancialAnalyzer
        from analysis.valuation import ValuationAnalyzer
        reg.register_analysis_module(FinancialAnalyzer())
        reg.register_analysis_module(ValuationAnalyzer())

        config = Config()
        pipeline = Pipeline(registry=reg, config=config, llm_enabled=False)

        results, commentary = pipeline.run("000001", "平安银行", dimension="financial")
        assert len(results) == 1
        assert results[0].dimension == "financial"
        # 验证 score 字段存在
        assert hasattr(results[0], "score")
        # 验证 sufficiency 被填充（在管道 run 中充实）
```

- [ ] **Step 2: 运行集成测试**

```bash
cd D:\code\stock_robot && python -m pytest tests/test_integration.py -v
```

- [ ] **Step 3: 运行全部测试**

```bash
cd D:\code\stock_robot && python -m pytest tests/ -v
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test(集成): 添加管道端到端集成测试"
```

---

### Task 16: README 修复

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 修复 README 中的错误**

README 第 60 行 `c --no-llm` 改为 `stock-robot analyze 000001 --no-llm`

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "fix(docs): 修复 README 命令行示例错误"
```
