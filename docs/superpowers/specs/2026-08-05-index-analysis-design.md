# 指数分析功能设计

## 概述

为 Stock Robot 增加指数分析能力，覆盖 A 股宽基、行业板块、海外指数，支持单指数报告和多指数横向对比。与现有个股分析联动，个股报告中可嵌入大盘环境摘要。

## 需求摘要

| 维度 | 决策 |
|------|------|
| 指数覆盖 | 宽基 + 行业板块 + 海外（全部经由 AkShare） |
| 分析侧重 | 宽基→估值+宏观，行业→轮动+相对强弱，海外→趋势+汇率 |
| 与个股联动 | 独立指数分析 + 个股报告中嵌入大盘环境 |
| 分析维度 | 技术面、估值（改造）、舆情（市场层面）、资金面（新）、宏观面（新） |
| 批量能力 | 多指数批量 + 横向对比 |
| 打分模式 | 各维度独立标签 + 综合定性评语 + 仓位参考系数，不做加权合成总分 |

---

## 第 1 节：核心抽象 — AnalysisTarget

当前管道的入口是股票代码字符串，经过 `validate_symbol` → `AnalysisContext`。引入指数后，需要一个更高层级的入口概念：

```python
class AnalysisTarget(BaseModel):
    """描述"分析什么"的值对象，不含数据"""
    target_type: Literal["stock", "index"]
    symbol: str                      # 原始输入，如 "000001" 或 "sh000001"
    name: str                        # 解析后的名称
    market: str                      # "a-shares" / "hk" / "us"

    # 仅指数使用
    index_style: Literal["broad", "sector", "overseas"] | None = None
```

`AnalysisTarget` 由 CLI 解析输入后创建，作为整个管道的第一参数传递。管道路由：

```python
if target.target_type == "stock":
    return self._run_stock_pipeline(target)
else:
    return self._run_index_pipeline(target)
```

---

## 第 2 节：指数数据 Schema

指数数据模型与个股 `AnalysisContext` 平级，互不继承、互不污染。

### IndexPriceData

```python
class IndexPriceData(PriceData):
    turnover: float | None = None      # 成交额（亿元）
    change_pct: float | None = None    # 涨跌幅 %
```

### IndexValuationData

```python
class IndexValuationData(BaseModel):
    symbol: str
    date: date
    pe_ttm: float | None = None
    pb: float | None = None
    pe_percentile: float | None = None
    pb_percentile: float | None = None
    dividend_yield: float | None = None

    # 分位元数据（回测刚需）
    percentile_lookback_years: int = 5
    percentile_sample_start: date | None = None
    percentile_sample_end: date | None = None
    valuation_valid: bool = True   # 样本不足时置 False
```

### CapitalFlowData

```python
class CapitalFlowData(BaseModel):
    symbol: str
    date: date
    north_bound: float | None = None       # 北向资金净流入（亿元）
    main_net_inflow: float | None = None   # 主力净流入（亿元）
    margin_balance: float | None = None    # 融资余额（亿元）
```

### MacroContext

```python
class MacroContext(BaseModel):
    symbol: str
    fetch_date: date
    shibor_3m: float | None = None
    cpi_yoy: float | None = None
    pmi: float | None = None
    usd_cny: float | None = None
    shibor_percentile: float | None = None
    pmi_percentile: float | None = None
```

sector 指数保留 `MacroContext` 实例但字段全 `None`，由渲染层 `visible_sections` 控制隐藏。

### IndexAnalysisContext

```python
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
```

### 舆情过滤规则

指数舆情采集禁止成分股个股新闻，仅市场层面舆情：
- 不使用 `stock_news_em(symbol)`
- 改用 `stock_news_main_em()` 全市场要闻，或 `news_economic_telegraph()` 宏观电讯
- 若 `raw_sentiment` 为空，渲染层输出"暂无有效市场舆情信号"，不抛异常

---

## 第 3 节：分析维度与模块

### 五个指数分析模块

| 维度 | 模块名 | 输入 | 核心逻辑 |
|------|--------|------|----------|
| 技术面 | `IndexTechnicalAnalyzer` | `price_data` | 趋势、均线、量价，指数特有的支撑/压力位 |
| 估值面 | `IndexValuationAnalyzer` | `valuation_data` | PE/PB 历史分位 + 估值区域判断 + `valuation_valid` 开关 |
| 舆情面 | `IndexSentimentAnalyzer` | `raw_sentiment` | 市场层面舆情，禁止个股新闻，关注政策信号、宏观叙事 |
| 资金面 | `CapitalFlowAnalyzer` | `capital_flow` | 北向资金趋势、主力资金方向、融资余额变化 |
| 宏观面 | `MacroAnalyzer` | `macro` | 利率/PMI/汇率与指数的历史相关性，宏观周期定位 |

每个模块实现 `AnalysisModule` 接口，输出 `AnalysisResult`。

### 打分配置

不再基于申万行业分类，改用 `index_style` 路由：

```
config/index/
├── base/
│   ├── broad.yaml           # 偏宏观+估值
│   ├── sector.yaml          # 偏资金+技术
│   └── overseas.yaml        # 偏趋势+汇率
└── override/                # 具体指数覆写（按需）
    └── 000300.yaml
```

配置内容为各维度独立阈值+标签映射规则，不做加权总分计算。

---

## 第 4 节：指数报告构建

### 输出模式

单指数报告 → `IndexReport`，多指数 → 外加对比表格（`CompareTable`），两者职责分离：
- `IndexReportBuilder`：只处理单个 `IndexAnalysisContext`，输出单份报告
- `IndexCompareReportBuilder`：接收 `list[IndexAnalysisContext]`，输出横向对比表格

### IndexReport 模型

```python
class IndexReport(BaseModel):
    code: str
    name: str
    date: date
    overview: dict
    section_technical: dict
    section_valuation: dict
    section_capital: dict
    section_macro: dict | None
    section_sentiment: dict

    # 各维度独立标签（无总分数）
    tag_technical: Literal["bull", "shake", "bear"]
    tag_valuation: Literal["undervalued", "neutral", "overvalued", "invalid"]
    tag_capital: Literal["positive", "neutral", "negative"]
    tag_macro: Literal["positive", "neutral", "negative", "na"]
    tag_sentiment: Literal["positive", "neutral", "negative"]

    composite_comment: str          # 综合定性文本，如"谨慎看多"
    position_coeff: float | None    # 建议仓位系数 0~1，仅供策略参考

    risk_list: list[str]
    visible_sections: set[str]      # 控制哪些章节渲染输出
```

### 报告章节

```
指数分析报告：沪深300 (sh000300)
================================

1. 概览
   - 最新点位、涨跌幅
   - PE-TTM 12.3（历史分位 68%，回看 5 年）
   - 若 valuation_valid=False，概览区醒目提示
   
2. 技术面
   - 趋势标签（bull/shake/bear）、均线系统、关键支撑/压力位

3. 估值面
   - PE/PB 历史分位、估值区域（低估/中性/高估）
   - 附带元信息：实际样本时长、是否缩尾
   - "估值样本仅 3.2 年，不足配置 5 年，分位仅供参考"

4. 资金面
   - 宽基：全市场北向、全市场融资余额
   - 行业：本行业北向增持、板块主力资金
   - 渲染层按 category 区分口径

5. 宏观面
   - broad：PMI/利率/汇率与指数关联分析
   - sector：直接隐藏，不输出 N/A 占位
   - overseas：仅汇率部分
   - 注释：宏观指标反映国内整体宏观，不直接代表本行业

6. 舆情面
   - 市场政策信号、宏观叙事摘要
   - 绝对不能出现成分股个股新闻
   - 无数据时输出"暂无有效市场舆情信号"

7. 雷达图
   - 将 5 个 tag 映射为 0~1 数值用于可视化
   - 仅可视化用途，禁止依赖雷达图数值计算仓位
```

### 章节显示控制

根据 `index_style` 动态控制章节：

| 章节 | broad | sector | overseas |
|------|-------|--------|----------|
| 概览 | ✅ | ✅ | ✅ |
| 技术面 | ✅ | ✅ | ✅ |
| 估值面 | ✅ | ✅ | ✅ |
| 资金面 | ✅（全市场） | ✅（板块） | ❌ |
| 宏观面 | ✅ | ❌ | ✅（仅汇率） |
| 舆情面 | ✅ | ✅ | ✅ |

### 复用边界

- ✅ 可复用：Rich 表格/Markdown 输出工具、LLM 调用流程、通用异常/空值渲染逻辑
- ❌ 不可复用：个股报告的打分加权逻辑、财务表格渲染组件

---

## 第 5 节：管道编排

### 整体流程

```
CLI ("stock-robot index <symbols...>")
  │
  ├─ 解析 + 校验 → list[AnalysisTarget]
  ├─ 判断 target_type == "index"
  │
  ▼
IndexPipeline.run(targets)
  │
  ├─ for each target（单条异常隔离，不中断其他条目）:
  │     IndexDataCollector.collect(target) → IndexAnalysisContext
  │     ContextEnricher.enrich(ctx)         → 分位计算、标签映射
  │     for each module in index_modules:
  │       module.analyze(ctx) → AnalysisResult
  │     IndexReportBuilder.build(ctx, results) → IndexReport
  │
  ├─ if len(targets) >= 2:
  │     IndexCompareReportBuilder.build(contexts, reports) → CompareTable
  │
  └─ 返回 IndexPipelineResult(reports, compare)
```

### IndexDataCollector

按 `index_style` 编排采集策略，只拉取原始数据，不做衍生计算：

| 数据 | broad | sector | overseas |
|------|-------|--------|----------|
| 行情 | ✅ | ✅ | ✅ |
| 估值（原始） | ✅ | ✅ | ✅ |
| 宏观原始数据 | ✅ | 实例保留 None | ✅（仅汇率） |
| 资金流向 | ✅（全市场） | ✅（板块） | ❌ |
| 市场舆情 | ✅ | ✅ | ✅ |

### ContextEnricher（充实层）

分位计算、标签映射等衍生逻辑全部在此执行。
计算结果不入缓存（避免未来函数），每次新鲜计算。

### 个股联动

个股管道中通过 `IndexContextEnricher` 注入大盘环境：

```python
class IndexContextEnricher(DataEnricher):
    """个股充实器：注入所属大盘指数快照"""

    def enrich(self, ctx: AnalysisContext) -> AnalysisContext:
        # 轻量快照接口，直接读缓存，不跑完整指数管道
        benchmark = self.index_snapshot("000300")
        ctx.market_environment = benchmark
        return ctx
```

`get_snapshot()` 为轻量查询接口：直接从缓存读取估值快照+趋势标签，TTL 缓存。禁止触发完整指数流水线。

### 缓存策略

- 原始数据（行情、估值原始值、宏观原始值）：按现有 TTL 缓存
- 分位计算结果、标签映射等衍生数据：不入缓存，每次新鲜计算

### 异常隔离

批量循环中单条 `try/except`，失败条目置标记后 `continue`，不中断其他条目。

---

## 第 6 节：CLI 接口设计

```bash
# 单指数
stock-robot index 000300

# 多指数（独立报告 + 横向对比）
stock-robot index 000300 000905 399001

# 指定类别（默认自动检测）
stock-robot index 000300 --style broad

# 输出格式
stock-robot index 000300 --output terminal   # 默认
stock-robot index 000300 --output markdown   # 保存 .md 文件

# 仅对比模式（不做单报告详情）
stock-robot index 000300 000905 399001 --compare-only

# 个股 + 大盘环境
stock-robot analyze 000001 --with-market
```

指数代码支持带前缀（`sh000001` / `sz399001` / `000001`）。
自动映射 `index_style`：`000xxx`（上证）→ broad，`399xxx`（深证）→ broad，行业板块代码 → sector，`HSI` / `SPX` 等 → overseas。
无法识别时必须显式传 `--style`。

---

## 第 7 节：文件组织结构

```
src/
├── index/                          # ★ 新增
│   ├── __init__.py
│   ├── pipeline.py                 # IndexPipeline 编排
│   ├── collector.py                # IndexDataCollector
│   ├── enricher.py                 # 估值分位充实器 + 标签映射
│   ├── schemas.py                  # IndexAnalysisContext, IndexReport 等
│   ├── build_single.py             # IndexReportBuilder
│   ├── build_compare.py            # IndexCompareReportBuilder
│   └── analysis/
│       ├── __init__.py
│       ├── technical.py            # IndexTechnicalAnalyzer
│       ├── valuation.py            # IndexValuationAnalyzer
│       ├── capital_flow.py         # CapitalFlowAnalyzer
│       ├── macro.py                # MacroAnalyzer
│       └── sentiment.py            # IndexSentimentAnalyzer
│
├── data/
│   ├── schemas.py                  # ★ 新增：AnalysisTarget, IndexValuationData 等
│   ├── akshare.py                  # ★ 新增：指数相关 fetch 方法
│   └── index_mapping.py            # ★ 新增：指数代码→名称→类别映射表
│
├── analysis/
│   └── config/
│       └── index/                  # ★ 新增
│           ├── base/
│           │   ├── broad.yaml
│           │   ├── sector.yaml
│           │   └── overseas.yaml
│           └── override/
│
├── core/
│   ├── pipeline.py                 # ★ 修改：增加 AnalysisTarget 路由
│   └── registry.py                 # ★ 修改：支持按 category 筛选模块
│
├── report/
│   ├── builder.py                  # 不变
│   └── formatter.py                # 不变，共享渲染工具
│
└── utils/
    └── symbols.py                  # ★ 修改：增加 validate_index_symbol
```

### 模块边界规则

- `src/index/` 可以 import 任意 `src/data/`、`src/core/`、`src/llm/`、`src/report/`、`src/utils/` 中的公共组件
- `src/data/`、`src/analysis/`、`src/report/` 的子包不能反过来 import `src/index/`
- 个股联动走 `src/core/pipeline.py` 中的顶层编排，不形成模块级循环依赖
