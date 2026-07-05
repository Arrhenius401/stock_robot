# 股票分析 AI Agent — 设计规格说明

## 概述

一款 AI 驱动的智能研报助手。输入股票代码，输出多维度分析报告，包含数据驱动的量化指标和 LLM 生成的解读文字。

**初始范围**：A 股（中国股票）通过 CLI 使用。架构预留多市场和 Web 界面扩展能力。

## 研报维度

1. **财务分析** — 营收、利润、现金流、ROE 等财报指标的趋势解读
2. **技术面分析** — 价格走势、均线系统、成交量、MACD、支撑/阻力位
3. **估值分析** — PE/PB/PS 当前值 vs 历史分位数 vs 行业均值
4. **行业/竞争分析** — 行业地位、同业对比
5. **舆情分析** — 近期新闻、公告、市场情绪总结

## 架构：模块化管道

```
CLI → 数据层 → 分析层 → LLM 层 → 报告构建 → 输出
```

数据采集和指标计算全部由确定性代码完成。LLM 仅在最后一步被调用：接收结构化指标，为每个维度生成自然语言解读，并汇总给出综合结论。

### 核心设计原则

- **容错性**：单个数据源或分析模块失败不阻断整个报告
- **成本可控**：每个维度一次 LLM 调用（可并行），token 消耗最小化
- **可测试**：每一层有清晰接口，可独立测试
- **可扩展**：新数据源、分析模块、LLM 后端均通过定义好的接口接入

## 项目结构

```
stock_robot/
├── cli.py                     # CLI 入口 (click + rich)
├── src/
│   ├── core/
│   │   ├── pipeline.py        # 管道调度器
│   │   └── registry.py        # 模块注册机制
│   ├── data/
│   │   ├── base.py            # 数据源抽象接口
│   │   ├── schemas.py         # Pydantic 数据校验模型
│   │   ├── akshare.py         # AkShare 适配器（A 股）
│   │   └── cache.py           # SQLite 缓存层
│   ├── analysis/
│   │   ├── base.py            # 分析模块抽象接口
│   │   ├── financial.py       # 财务分析
│   │   ├── technical.py       # 技术面分析
│   │   ├── valuation.py       # 估值分析
│   │   ├── industry.py        # 行业分析
│   │   └── sentiment.py       # 舆情分析
│   ├── llm/
│   │   ├── base.py            # LLM 后端抽象接口
│   │   ├── openai.py
│   │   ├── claude.py
│   │   └── prompt_templates/  # {维度}_{模型}.jinja2
│   ├── report/
│   │   ├── builder.py         # 报告组装器
│   │   ├── templates/         # Jinja2 Markdown 模板
│   │   └── formatter.py       # 输出格式化
│   └── utils/
│       ├── symbols.py         # 股票代码解析/校验
│       └── config.py          # YAML 配置管理
├── tests/
├── pyproject.toml
└── README.md
```

## 缓存策略

**存储介质**：SQLite 数据库，路径 `~/.stock_robot/cache.db`。

**缓存粒度**：按 `数据类型 + 股票代码 + 时间维度` 拆分：
- 财务数据 → 按季度缓存（键：股票代码 + 财务季度）
- 行情/技术数据 → 按交易日缓存
- 估值数据 → 按交易日缓存
- 行业数据 → 按季度缓存（行业分类变动不频繁）
- 新闻/舆情 → 按日缓存

**TTL 默认值**：
- 日频数据（行情、估值）：1 天
- 季频数据（财务、行业）：1 周
- 新闻：6 小时

**强制刷新**：
```bash
stock-robot analyze 000001 --refresh-cache        # 忽略全部缓存
stock-robot analyze 000001 --refresh financial    # 刷新指定维度
```

**缓存失效**：读取时基于 TTL 驱逐。手动清理：`stock-robot cache clear`。

## 数据校验

所有从外部数据源获取的数据必须经过 `data/schemas.py` 中定义的 Pydantic v2 模型校验后，才能进入分析管道。这隔离了 AkShare 等社区数据源的不稳定性对分析层的影响。

```python
# 概念性的 Schema 结构
class FinancialData(BaseModel):
    symbol: str
    fiscal_quarter: date
    revenue: float
    net_profit: float
    total_assets: float
    roe: float | None          # 可计算得出，原始数据可能缺失
    gross_margin: float | None
    # ...

class PriceData(BaseModel):
    symbol: str
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int

class ValuationData(BaseModel):
    symbol: str
    date: date
    pe_ttm: float | None
    pb: float | None
    ps_ttm: float | None
    # ...
```

每个数据适配器必须返回通过 Pydantic 校验的模型。校验失败则丢弃数据并标记为"不可用"——绝不向下游传递脏数据。

**多数据源容错**：`base.py` 定义抽象 DataSource 接口，包含 `supports(market, data_type) -> bool` 和 `fetch(symbol, **kwargs) -> BaseModel`。当一个数据源失败时，调度器可尝试同一市场/数据类型下的下一个已注册数据源。

## AnalysisResult 标准化格式

每个分析模块返回统一的 `AnalysisResult`：

```python
class AnalysisResult(BaseModel):
    dimension: str                     # "financial" | "technical" | "valuation" | "industry" | "sentiment"
    status: Literal["ok", "partial", "unavailable"]
    summary: str                       # 一句话摘要，方便快速浏览
    metrics: dict[str, Any]            # 结构化指标键值对
    charts: list[str]                  # 预留：图表图片路径（v2）
```

好处：
- LLM 层接收到的数据结构始终一致，不因分析模块而异
- 报告构建器可以优雅地处理部分数据缺失
- 新增分析维度只需实现这个契约

## LLM 成本管控与提示词管理

**成本管控**：
- `llm.enabled` 配置开关：设为 `false` 时，报告仅含数据/指标，无 LLM 解读
- 数据精简：财务指标只传最近 12 个季度，估值传当前值 + 5 年历史分位数，不传全量原始数据
- Token 用量记录到 `~/.stock_robot/usage.log`，每条记录含：模型、prompt tokens、completion tokens、费用估算

**提示词模板组织**：`prompt_templates/` 按 `{维度}_{模型}.jinja2` 命名：

```
prompt_templates/
├── financial_openai.jinja2
├── financial_claude.jinja2
├── technical_openai.jinja2
├── technical_claude.jinja2
├── valuation_openai.jinja2
├── valuation_claude.jinja2
├── industry_openai.jinja2
├── industry_claude.jinja2
├── sentiment_openai.jinja2
├── sentiment_claude.jinja2
├── summary_openai.jinja2
└── summary_claude.jinja2
```

Claude 模板偏重详细上下文和长文本推理；OpenAI 模板偏重简洁指令和结构化输出标记。LLM 层在运行时根据已配置的 provider 选择对应的模板。

## 合规声明

工具在 README 中展示免责声明，并在首次运行时提示用户：

> 本工具仅用于个人学习和研究目的。所有分析数据和观点不构成任何投资建议。股票投资有风险，入市需谨慎。数据来源的准确性和时效性无法保证，使用者需自行判断。

配置文件包含 `data.disclaimer_accepted: false` 标记——首次使用时需用户确认接受。

## 技术栈

| 领域 | 选型 |
|------|------|
| 语言 | Python 3.11+ |
| 数据获取 | AkShare + pandas |
| CLI 框架 | click（命令解析）+ rich（终端美观输出） |
| LLM SDK | openai SDK + anthropic SDK（不使用 LangChain） |
| 报告模板 | Jinja2 → Markdown |
| 配置管理 | YAML（~/.stock_robot/config.yaml） |
| 数据校验 | Pydantic v2 |
| 缓存存储 | SQLite |
| 测试 | pytest |

**v1 阶段明确不使用 LangChain。**LLM 的角色是每个维度的单次调用——无需 Agent 编排或工具链式调用。直接使用 SDK 更简洁、调试更快、且完全满足需求。后续构建对话式投资顾问功能时再评估引入 LangChain。

## 数据流

```
1. 输入解析      → 标准化股票代码，解析公司名称
2. 缓存检查      → SQLite 按 (数据类型, 股票代码, 日期) 查询；缓存新鲜则跳过采集
3. 数据采集      → 5 个采集任务并行调用 AkShare（财务、行情、估值、行业、新闻）
                   每个返回结果经 Pydantic Schema 校验；失败时尝试备选数据源
                   全部结果存入 AnalysisContext
4. 指标计算      → 5 个分析模块并行计算，每个输出 AnalysisResult
5. LLM 解读      → 逐维度：提示词模板 ({维度}_{模型}.jinja2) + AnalysisResult → 分析文字
                   精简后的数据 + 结构化指标控制 token 消耗
                   最后：汇总提示词综合所有维度 → 总结论
6. 报告组装      → Jinja2 填充 Markdown 模板
7. 输出          → 终端 rich 渲染 + 保存至 reports/{代码}_{日期}.md
```

## 错误处理

| 层级 | 策略 |
|------|------|
| CLI | 输入校验，友好的错误提示 |
| 数据层 | Pydantic 校验失败 → 丢弃，尝试下一数据源或标记"不可用" |
| 数据层 | 单源失败 → 回退到同一市场/数据类型的下一个已注册源 |
| 数据层 | 频率限制 → 指数退避重试 + 缓存兜底 |
| 分析层 | 数据不足 → 输出 AnalysisResult(status="partial" 或 "unavailable") |
| LLM 层 | `llm.enabled: false` → 跳过解读，输出纯数据报告 |
| LLM 层 | 超时重试（最多 2 次），API 异常 → 降级为纯数据报告 |
| 管道层 | 全局异常捕获，失败时输出已完成的部分 |

## 测试策略

| 层级 | 类型 | 关注点 |
|------|------|--------|
| 数据适配器 | 单元（mock AkShare） | 数据解析正确性 |
| 分析模块 | 单元（固定数据集） | 指标计算正确性 |
| LLM 调用 | 单元（mock LLM） | Prompt 构建、响应解析 |
| 报告生成 | 单元（固定上下文） | 模板渲染、格式正确 |
| 管道 | 集成 | 端到端流程 |
| CLI | E2E | 用户命令 → 输出 |

## CLI 使用体验

```bash
stock-robot analyze 000001                        # 完整研报
stock-robot analyze 000001 --dimension financial  # 单维度分析
stock-robot analyze 000001 --refresh-cache        # 强制刷新数据
stock-robot analyze 000001 --verbose              # 显示采集和计算过程
stock-robot analyze 000001 --no-llm               # 仅数据，跳过 LLM 解读

stock-robot config set llm.model claude-opus-4-7
stock-robot config set llm.provider claude
stock-robot config set data.cache_ttl 86400
stock-robot config set llm.enabled false          # 全局关闭 LLM

stock-robot list --market a-shares                # 搜索股票
stock-robot cache clear                           # 清空所有缓存
stock-robot cache status                          # 查看缓存大小和命中率
```

报告通过 rich 格式化的 Markdown 在终端中展示，同时自动保存到 `reports/{代码}_{日期}.md`。

## 未来扩展（v1 范围外）

- Web 界面 / REST API
- 港股和美股市场
- 对话式投资顾问（届时可能引入 LangChain）
- 股票推荐/筛选引擎
- PDF 报告输出
- 付费/专业数据源接入
