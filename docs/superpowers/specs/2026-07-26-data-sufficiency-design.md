# 数据充足性增强设计文档

> 状态: 设计完成 / 日期: 2026-07-26 / 关联: 报告模板 v2 改造的前置阶段

## 概述

解决当前报告各维度数据严重不足的问题（行情仅 1 条、财务仅单期、估值无历史分位、行业无同行对比、舆情仅 1 条新闻）。引入充实层（DataEnricher）在采集和分析之间补充数据推导与下游拉取，建立五维度数据充足判定标准，新增纯量化打分体系。

## 架构变更

在现有 `Collect → Analyze → LLM → Report` 管道中插入充实层：

```
Collect → Enrich → Analyze → LLM → Report
 (并行)   (依赖DAG)  (并行)    (单次)  (输出)
```

### 新增组件

| 组件 | 位置 | 职责 |
|------|------|------|
| `DataEnricher` 基类 | `src/data/enricher.py` | 充实器抽象接口 |
| `PriceEnricher` | `src/data/enrichers/` | 验证行情数据量，标定充足状态 |
| `ValuationEnricher` | `src/data/enrichers/` | 行情+财报 → 日频 PE/PB/PS 序列 |
| `IndustryEnricher` | `src/data/enrichers/` | 同行列表 → 头部5家财务估值 → 行业中位数 |
| `SentimentEnricher` | `src/data/enrichers/` | 统计条目 → LLM 批量标注 → 舆情结构化 |
| `ContextEnricher` 编排器 | `src/data/enricher.py` | 按依赖 DAG 调度各充实器 |

### 充实器执行顺序

```
PriceEnricher ─────────────────────┐
  (无依赖)                         │
                                   ▼
                    ValuationEnricher ───┐
                      (依赖: price+fin)  │
                                         ▼
Financial 数据 ──────► IndustryEnricher ──► SentimentEnricher
                        (依赖: industry)    (无依赖，可并行)
```

## 一、Schema 扩展

### AnalysisContext 新增字段

```python
class AnalysisContext(BaseModel):
    # === 现有字段不变 ===
    symbol: str
    name: str
    market: str = "a-shares"
    financial_data: list[FinancialData] | None = None
    price_data: list[PriceData] | None = None
    valuation_data: ValuationData | None = None  # 只存当日单时点，日频序列走 enriched_valuation
    industry_data: IndustryData | None = None
    news_data: NewsData | None = None
    collected_at: datetime = Field(default_factory=datetime.now)

    # === 新增：采集层原始数据 ===
    raw_sentiment: RawSentimentData | None = None  # 30天新闻+公告原文

    # === 新增：充实层产出 ===
    sufficiency: DataSufficiency | None = None
    enriched_valuation: EnrichedValuation | None = None
    enriched_industry: EnrichedIndustry | None = None
    enriched_sentiment: EnrichedSentiment | None = None
```

所有 Optional 字段若对应维度数据完全拉取失败，值为 None；分析层读取前需先判空。

### DataSufficiency / SufficiencyLevel

```python
from enum import StrEnum

class SufficiencyLevel(StrEnum):
    SUFFICIENT = "sufficient"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"

class DimensionSufficiency(BaseModel):
    level: SufficiencyLevel
    reason: str = ""           # "有效行情数据 245 条，满足 60 条门槛"
    sample_count: int = 0
    score_weight: float = 1.0  # sufficient=1.0, partial=0.5, insufficient=0.0

class DataSufficiency(BaseModel):
    price: DimensionSufficiency
    financial: DimensionSufficiency
    valuation: DimensionSufficiency
    industry: DimensionSufficiency
    sentiment: DimensionSufficiency
```

### 各维度充足判定阈值

| 维度 | 充足 (sufficient) | 部分 (partial) | 不足 (insufficient) |
|------|-------------------|----------------|---------------------|
| price | ≥60 条 OHLCV | 20–59 条 | <20 条 |
| financial | ≥4 期季报且关键字段不缺失 | 2–3 期或字段部分缺失 | <2 期或大面积缺失 |
| valuation | 有效点数 ≥120 | 30–119 | <30 |
| industry | 同行≥8 且头部≥4/5 有效 | 同行3–7 或头部2–3 | 同行<3 |
| sentiment | ≥10 条 | 3–9 条 | <3 条 |

补充规则：
- 有效同行 = 剔除 ST、退市、市值为空标的
- 头部有效 = 该龙头财务+估值无大面积缺失
- financial 仅期数达标但 4 期中有 3 期利润为空，仍降级为 insufficient

### Enriched 数据模型

```python
class DailyValuationPoint(BaseModel):
    trade_date: date
    close: float
    pe: float | None
    pb: float | None
    ps: float | None

class EnrichedValuation(BaseModel):
    daily_pe: list[DailyValuationPoint]
    daily_pb: list[DailyValuationPoint]
    daily_ps: list[DailyValuationPoint]
    pe_percentile: float | None
    pb_percentile: float | None
    pe_zone: str           # "低估"(<30%) / "中性"(30-70%) / "高估"(>70%)
    pe_median: float | None
    pe_high: float | None
    pe_low: float | None

class PeerComparison(BaseModel):
    symbol: str
    name: str
    market_cap: float | None
    pe_ttm: float | None
    pb: float | None
    gross_margin: float | None
    net_margin: float | None
    roe: float | None

class EnrichedIndustry(BaseModel):
    peer_count: int
    top_peers: list[PeerComparison]
    industry_median_pe: float | None
    industry_median_pb: float | None
    industry_median_gross_margin: float | None
    industry_median_net_margin: float | None
    target_pe_premium: float | None      # 相对行业 PE 溢价/折价%
    target_market_cap_rank: int | None

class RawSentimentItem(BaseModel):
    title: str
    source: Literal["news", "announcement"]
    publish_date: date
    content: str = ""

class RawSentimentData(BaseModel):
    symbol: str
    fetch_date: date
    items: list[RawSentimentItem] = Field(default_factory=list)

class SentimentItem(BaseModel):
    title: str
    summary: str                           # LLM 生成的一句话摘要
    tendency: Literal["positive", "neutral", "negative"]
    severity: Literal["minor", "moderate", "major"]
    event_type: str                        # 业绩/减持/回购/监管/并购/其他

class EnrichedSentiment(BaseModel):
    total_count: int
    positive_count: int
    neutral_count: int
    negative_count: int
    major_events: list[SentimentItem]
    all_items: list[SentimentItem]
```

## 二、采集层变更

### `_fetch_price`
- 默认 `days` 参数从 365 改为 250
- 拉取 `ak.stock_zh_a_hist()` 近一年日线 OHLCV

### `_fetch_financial`
- 补充毛利率字段：优先解析 `stock_financial_abstract_ths` 中的毛利率，不可得时从 `(营收-成本)/营收` 推算
- 补充扣非净利润字段：从同花顺财报摘要中解析"扣除非经常性损益后的净利润"，用于成长能力分的连续增长判定
- FinancialData 新增 `deducted_net_profit: float | None` 字段

### `_fetch_valuation`
- 保持不变，仅返回当日单时点 ValuationData

### `_fetch_industry`（最大变更）

流程：
```
1. 拉行业分类 → 获取该行业全部成分股列表
2. 过滤：剔除 ST、*ST、退市、市值为空标的
3. 按总市值排序取前 5
4. 循环拉取头部 5 家估值 + 最新一期财务指标
   - 单家接口失败 → 容错跳过，不中断整体采集，标记该龙头缺失
5. 输出包含同行列表 + 头部 5 家财务估值的 IndustryData
```

### `_fetch_news`
- 时间窗口扩展至近 30 天
- 两路数据合并：交易所公告 + 财经新闻
- 去重过滤：剔除重复推送、无关简讯
- 上限 30 条，写入 `ctx.raw_sentiment`

## 三、充实层实现

### PriceEnricher
- 输入：`ctx.price_data`
- 统计有效 OHLCV 行数，按阈值标定 sufficient/partial/insufficient
- 输出：`ctx.sufficiency.price`

### ValuationEnricher
1. 总股本来源：优先从 `stock_individual_info_em` 获取最新总股本；若不可得，用 `总资产/每股净资产` 反推近似值
2. 逐日取收盘价 × 总股本 → 日市值
3. 从最近 4 期财报（按财报截止日往前取 4 个季报期）计算 TTM 净利润/TTM 净资产/TTM 营收
4. 每日 PE = 日市值 / TTM净利润，PB/PS 同理
5. 清洗：剔除 EPS≤0 的异常点
6. 标定充足状态
- 输出：`ctx.enriched_valuation` + `ctx.sufficiency.valuation`

### IndustryEnricher
1. 过滤同行列表（ST/退市/空值）
2. 统计有效同行数、头部完整数
3. 计算行业中位数
4. 计算标的与行业均值偏离度
5. 标定充足状态
- 输出：`ctx.enriched_industry` + `ctx.sufficiency.industry`

### SentimentEnricher
1. 统计有效条目数 → 标定充足状态
2. 若 sufficient 或 partial：调用 LLM 批量标注（摘要+倾向+影响等级）
3. 汇总利好/中性/利空统计，提取重大事件
- 输出：`ctx.enriched_sentiment` + `ctx.sufficiency.sentiment`

## 四、量化打分体系

### AnalysisResult 扩展

```python
class AnalysisResult(BaseModel):
    # 现有字段不变
    dimension: Literal["financial", "technical", "valuation", "industry", "sentiment"]
    status: Literal["ok", "partial", "unavailable"]
    summary: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    charts: list[str] = Field(default_factory=list)

    # 新增
    score: float | None = None
    score_detail: str = ""
    risk_flags: list[str] = Field(default_factory=list)
```

### 打分规则

**财务健康分（权重 30%）**

| 子项 | 满分 | 规则 |
|------|------|------|
| ROE 水平 | 3 | ≥15%=3, 10-15%=2, 5-10%=1, <5%=0, ROE<0 直接 0 |
| 资产负债率 | 2 | 40-70%=2, 偏高偏低=1, <20%或>90%=0 |
| 经营现金流/净利润匹配 | 3 | >0.8=3, 0.5-0.8=2, 0-0.5=1, 净利润为负直接 0 |
| 毛利率稳定性 | 2 | 近4期波动<5pp=2, 5-15pp=1, >15pp=0, 任意一期毛利率为负=0 |

**成长能力分（权重 25%）**

| 子项 | 满分 | 规则 |
|------|------|------|
| 营收增速 | 4 | ≥20%=4, 10-20%=3, 0-10%=2, -20%至0=1, ≤-20%=0 |
| 净利润增速 | 4 | 同上 |
| 连续增长性 | 2 | 近3期扣非净利同比全正=2, 2期=1, 否则=0 |

**估值合理分（权重 25%）**

| 子项 | 满分 | 规则 |
|------|------|------|
| PE 历史分位 | 4 | <30%=4, 30-50%=3, 50-70%=2, 70-90%=1, >90%=0 |
| PB 历史分位 | 2 | 同上，PB为负直接 0 |
| 行业溢价程度 | 4 | 折价>20%=4, 折价0-20%=3, 溢价0-20%=2, 溢价20-50%=1, >50%=0。标的亏损无PE时跳过并标记风险 |

**技术趋势分（权重 20%）**

| 子项 | 满分 | 规则 |
|------|------|------|
| 均线结构 | 4 | MA5>MA20>MA60=4, 交叉震荡=2, 空头=1, insufficient=无分 |
| 量价配合 | 3 | 放量上涨=3, 缩量调整=2, 放量下跌=1, 横盘无量=2 |
| MACD 形态 | 3 | DIF>DEA 且 MACD柱正=3, 临界=2, 空头=1 |

**综合风险分（倒扣制，保底 0）**

基础分 = 四大正向维度加权平均分（0–10），再叠加风险倒扣：

| 风险项 | 扣分 | 触发条件 |
|--------|------|----------|
| 财务风险 | 最高 -3 | 每条 risk_flag 扣 1 |
| 估值风险 | 最高 -2 | PE>90%分位扣 1，PB<行业中位数50%扣 1 |
| 行业竞争风险 | 最高 -2 | 毛利率低于行业均值超 10pp 扣 1，市值位次<行业50%扣 1 |
| 技术形态风险 | 最高 -2 | 空头排列扣 1，放量下跌扣 1 |
| 舆情风险 | -1 | 近30天重大利空事件扣 1 |

最终得分截断至 [0, 10]。

### risk_flags 标准化枚举

```
roe_low, high_debt, cash_flow_mismatch, revenue_declineing,
profit_declineing, high_pe_premium, pb_below_peer,
industry_weak_margin, bearish_ma, volume_bearish,
major_negative_news
```

### 充足状态与分数的联动

- `insufficient`：score=None, status=unavailable，不参与综合分
- `partial`：原始得分 × 0.5 后参与加权
- `score_detail` 强制每条子项带具体数值（例："ROE 12%，区间 10-15%，得 2 分"）

## 五、LLM 层变更

### 从多轮调用改为单次批量调用

- Before: 5 维度各调一次 + 1 次总结 = 6 次 LLM 调用
- After: 1 次批量调用，四段结构化输出

### 输入截断策略

- 仅传输结构化摘要：score、score_detail、risk_flags、核心指标数值、sufficiency 状态
- 不传入完整 K 线数据、全量新闻原文
- 舆情仅传 enriched_sentiment 精简条目

### System Prompt 约束

- 固定分段标识：`## 1.维度分项解读` `## 2.全维度风险汇总` `## 3.多风格观察视角` `## 4.综合评级`
- 禁止词汇列表：买入、卖出、加仓、减仓、持仓、重仓、轻仓、止损、止盈、逢低布局、看涨、看跌、目标价、建议
- 缺失维度标注"数据不足，跳过"
- 所有解读必须引用 score_detail 中的具体数值

### --no-llm 模式

- 报告顶部标 `[纯量化模式，无 AI 解读]`
- 仅输出量化数据 + 打分 + 风险清单
- 跳过第四、五部分

## 六、报告模板

新模板 `report_v2.jinja2` 结构：

```
一、标的基础概况
  - 名称、代码、行业、总市值、流通市值
  - 近1年最高/最低价、当前价格位置百分比

二、五大维度纯量化数据
  - 财务：近3年核心指标表格
  - 技术面：均线、MACD、量能表格
  - 估值：PE/PB/PS 当前值 + 历史分位
  - 行业：头部5家 vs 标的 vs 行业中位数对比表
  - 舆情：公告/新闻列表 + 倾向统计

三、五大维度标准化打分
  - 打分汇总表（维度 | 得分 | 权重 | 充足状态）
  - 每维度附 score_detail 逐项说明

四、AI 中性解读 + 风险汇总
  - LLM 四段输出（分段标识切割后渲染）
  - risk_flags 清单列表

五、多风格观察视角
  - 价值投资 / 成长投资 / 短线技术

六、工具局限性 + 免责声明
```

### 维度缺失渲染规则

- `insufficient`：该章节显示 `> 该维度数据不足，已跳过`
- `partial`：正常渲染但标注 `[数据有限，仅供参考]`

## 七、实现顺序

```
Phase 1: Schema 定义
  DataSufficiency / SufficiencyLevel / Enriched* 模型
  RawSentimentData / RawSentimentItem
  AnalysisResult 扩展 + AnalysisContext 扩展

Phase 2: 采集层增强
  _fetch_price: 250天默认
  _fetch_financial: 毛利率补充
  _fetch_industry: 同行列表 + 头部5家
  _fetch_news: 30天新闻+公告

Phase 3: 充实层实现
  PriceEnricher → ValuationEnricher → IndustryEnricher → SentimentEnricher
  ContextEnricher 编排器 + 管道集成

Phase 4: 打分体系
  五维度打分 + risk_flags + 综合分计算

Phase 5: LLM + 报告
  单次批量 prompt 模板 + report_v2.jinja2 + --no-llm 模式

Phase 6: 测试
  Enricher 单元测试、打分边界测试、充足状态切换、集成测试
```

## 八、测试关注点

- **Enricher 单元测试**：固定行情+财报输入，验证估值序列推导数值正确
- **打分边界测试**：ROE 负值、PE>90%分位、净利润≤-20% 等边界 case
- **充足状态切换**：模拟 19/59/60 条行情数据验证 partial/sufficient 判定
- **风险扣分下限**：极端多风险 flag 情况验证总分截断至 0
- **行业容错**：模拟头部公司接口失败，验证不中断整体采集
