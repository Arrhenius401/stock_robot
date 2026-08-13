# Agent 架构设计

## 目标

将 Stock Robot 从固定管道分析工具升级为**规划-执行式 AI Agent**，具备自主拆解复杂投研任务、RAG 知识增强分析、MCP 工具生态接入三大核心能力。存量 CLI 一次性分析命令完全兼容不废弃。

## 总体架构

```
┌─────────────────────────────────────────────────────────┐
│                    接口层 (Interface)                     │
│   CLI 对话 (click)  │  HTTP API (FastAPI)  │  Web UI    │
│   ✅ 存量命令兼容    │   中期开放            │  远期配套   │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│                    Agent 核心 (新增)                      │
│                                                         │
│  ┌─────────────┐   ┌─────────────┐   ┌──────────────┐  │
│  │   Planner   │   │  Executor   │   │    Memory    │  │
│  │ 任务拆解     │◄─►│ 逐步执行     │   │ 对话+计划+结果│  │
│  └──────┬──────┘   └──────┬──────┘   └──────────────┘  │
│         │                 │                              │
│  ┌──────▼─────────────────▼──────────────────────────┐  │
│  │                  Tool Registry                     │  │
│  │  (仅做工具映射，业务实现全部下沉引擎层)              │  │
│  │   pipeline_tools  │  rag_tools  │  mcp_tools      │  │
│  └──────────────────────────┬────────────────────────┘  │
└─────────────────────────────┼───────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────┐
│                   引擎层 (Engine)                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │   Pipeline   │  │     RAG      │  │  MCP Gateway │  │
│  │  (存量复用)   │  │  (新增)       │  │  (新增)       │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### 关键设计原则

- **分层隔离** — Agent 核心是新增层，不侵入现有管道代码
- **存量兼容** — `analyze`、`index` 命令不变，Agent 对话通过新增 `chat` 命令进入
- **工具统一抽象** — 所有工具（管道/RAG/MCP）实现统一的 `ToolProtocol`，Agent 不感知工具来源
- **接口层双路径分流** — 存量命令直通 Pipeline，对话输入走 Agent 核心

### MCP Gateway 注释说明

MCP Gateway 兼顾两种角色：
- **内部 MCP Server**：将本地分析器、数据源、指标计算等能力标准化封装为 MCP 工具，可被外部 MCP 客户端（如 Claude Desktop、VS Code 插件）调用
- **外部 MCP Client**：接入第三方 MCP Server（新闻搜索、交易下单、财经数据等），中长期逐步扩展

### RAG 引擎本地报告同步

RAG 引擎内置 `LocalReportSync` 模块，监听 `reports/` 目录变更，自动对新生成的报告执行 chunk → embed → upsert，并注入元数据（股票代码、日期、分析维度、标签），确保 Agent 可检索历史分析成果。

## 场景与触发规则

### 目标场景

| 场景 | 典型输入 | Agent 拆解行为 |
|------|---------|---------------|
| 多资产对比分析 | "帮我找 3 只被低估的新能源龙头，对比基本面和风险" | 筛选标的 → 采集财务/估值 → RAG 检索研报观点 → 综合对比评分 |
| 择时研判 | "大盘现在适合入场吗？给我一份完整的策略建议" | 指数分析 → 宏观分析 → 板块轮动分析 → RAG 检索政策/研报 → 综合建议 |
| 事件驱动分析 | "美联储刚降息了，评估对我的持仓的影响" | 理解事件 → 识别受影响标的 → 采集数据 → RAG 检索历史类似事件 → 评估冲击 |

### RAG 调用触发规则

| 场景 | 触发条件 | 检索目标 Collection |
|------|---------|-------------------|
| 多资产对比 | 分析模块完成后，需要外部观点佐证 | 研报库 + 历史报告 |
| 择时研判 | 宏观/指数分析完成后，需要政策背景 | 政策/宏观库 + 研报库 |
| 事件驱动 | 事件理解后，需要历史类似事件参考 | 研报库 + 学术文献 |
| 行业深挖 | 行业分析时，需要财报细节 | 财报/公告库 |
| 工具使用 | Agent 不清楚某个指标含义或工具用法 | 系统规则库 |

## Agent 核心设计

### Planner — 只做拆解，不执行

```
输入: 用户消息 + Memory.facts（用户偏好）+ Memory.messages（上下文窗口）+ 工具列表摘要
输出: Plan（有序 TaskStep 列表）

流程:
1. 复杂度判断 —— 简单问题 → 单步 Plan，复杂问题 → 多步 Plan
2. 生成步骤列表，标注依赖关系（如"筛选标的"必须在"采集财务数据"之前）
3. 返回 Plan 给 Executor
```

**复杂度判断**：Planner 的 system prompt 中指示 LLM 自主判断任务复杂度。同时 CLI 入口增加一层**硬编码前缀拦截**作为兜底——简单查询（如"什么是PE"、"最近茅台涨了多少"）直接拦截为单步，不走 LLM 规划。

**Planner 不做工具选择**：只描述"做什么"（如"筛选新能源龙头股"），不决定"用哪个工具"。工具绑定在 Executor 执行时动态匹配，让 Planner 输出稳定、工具变更不影响计划逻辑。

**工具列表联动**：Planner 初始化时调用 `ToolRegistry.list_all()` 获取所有工具的名称、描述和语义标签，注入 system prompt 作为计划参考。Planner 不直接绑定工具，但需要知道可用能力范围。

### Executor — 逐步执行，动态调整

```
对每个 TaskStep:
1. 从 Tool Registry 匹配最合适的工具（语义标签粗筛 + LLM 二次校验）
2. 调用工具，收集 ToolResult
3. 结果写入 Memory.messages
4. 判断: 成功 → 下一步 / 失败 → 重试或请求 Planner 重新规划剩余步骤
5. 所有步骤完成 → 汇总输出
```

关键行为：
- **失败隔离**：单步失败标记 `failed`，依赖它的后续步骤标记 `skipped`，其余独立步骤继续执行
- **动态重规划**：当某步结果与预期严重不符时，Executor 可请求 Planner 重新规划剩余步骤
- **进度回调**：复用 `src/core/pipeline.py` 中的 `ProgressCallback` 协议，每步执行前后触发通知

### Memory — 双向数据流

```
Memory ──→ Planner:  facts（用户偏好）+ 最近 N 轮对话（上下文窗口）
Planner ──→ Memory:  新生成的 plan_history

Memory ──→ Executor:  plan_history（当前计划）+ facts（用户偏好）
Executor ──→ Memory:  每步工具结果追加到 messages，更新 step status
```

三层存储：
- `messages`：当前会话上下文窗口，进程内列表
- `plan_history`：本次会话的计划记录，进程内列表
- `facts`：跨会话持久化（用户偏好、常用标的等），JSON 文件存储，预留向量长期记忆升级路径

### 核心数据结构

```python
@dataclass
class TaskStep:
    id: str                          # 如 "step-1"
    description: str                 # 如 "筛选新能源龙头股"
    tool_name: str | None            # 计划阶段可为空，执行时由 Executor 匹配填充
    tool_args: dict | None
    depends_on: list[str]            # 依赖的 step id
    status: TaskStatus               # pending | running | done | failed | skipped

@dataclass
class Plan:
    goal: str                        # 原始用户意图
    steps: list[TaskStep]
    context_summary: str             # 从 Memory 提取的相关历史摘要

@dataclass
class Memory:
    messages: list[dict]             # 当前会话上下文窗口
    plan_history: list[Plan]         # 本次会话的计划记录
    facts: dict[str, Any]            # 跨会话持久化
```

## 工具系统

### ToolProtocol 统一协议

```python
@dataclass
class ToolResult:
    status: Literal["success", "error", "partial"]
    data: Any | None = None
    error: str | None = None
    metadata: dict | None = None     # 含 execution_time_ms, source, tags 等

class ToolProtocol(Protocol):
    name: str
    description: str                 # 给 LLM 阅读的语义描述，含使用场景和参数说明
    parameters: dict                 # JSON Schema 格式的参数定义
    tags: list[str]                  # 语义标签 ["pipeline", "stock", "screening"]
    source: Literal["pipeline", "rag", "mcp_internal", "mcp_external"]

    async def execute(self, **kwargs) -> ToolResult: ...
```

### ToolRegistry — 仅做映射，业务全部下沉引擎层

```
ToolRegistry:
  _tools:  dict[name → ToolProtocol]
  _index:  dict[tag → list[name]]          # 语义标签倒排索引

  register(tool: ToolProtocol)
  match(description: str, tags: list[str] | None = None) → list[ToolProtocol]
  list_all() → list[ToolProtocol]
  get(name: str) → ToolProtocol | None
```

**匹配流程**：`Planner 步骤描述 → tags 参数标签粗筛 → 语义相似度排序 → LLM 二次校验 → 返回最佳工具`

### pipeline_tools（包装存量，不改内部代码）

| 工具名 | 包装的存量能力 | 用途 |
|--------|--------------|------|
| `analyze_stock` | `Pipeline.run()` | 单股全维度分析 |
| `analyze_index` | `IndexPipeline.run()` | 指数分析 |
| `screen_stocks` | 新增：行业分类器 + 财务过滤器 | 按条件筛选标的 |
| `get_snapshot` | `IndexPipeline.get_snapshot()` | 轻量估值快照 |

### rag_tools

| 工具名 | 功能 |
|--------|------|
| `rag_search` | 语义检索知识库 |
| `rag_list_sources` | 列出当前索引的知识源清单 |

### mcp_tools（MCP Gateway 管理）

| 工具类别 | source 标签 | 说明 |
|---------|------------|------|
| 内部 MCP 工具 | `mcp_internal` | 本地分析器、数据源、指标计算标准化暴露 |
| 外部 MCP 工具 | `mcp_external` | 第三方 MCP Server 接入（新闻、交易等），中长期扩展 |

### 异常处理

所有工具的 `execute()` 内部统一 try/catch，异常转为 `ToolResult(status="error", error=msg)` 而非向上抛出。Executor 只需检查 `result.status` 便可统一处理。

## RAG 引擎

### 架构

```
┌──────────────────────────────────────────────┐
│                 RAG Engine                    │
│                                               │
│  ┌──────────────┐   ┌──────────────────────┐ │
│  │  Ingestion    │   │  Retrieval           │ │
│  │  文档摄入流水线 │   │  检索-重排-生成      │ │
│  └──────┬───────┘   └──────────┬───────────┘ │
│         │                       │             │
│  ┌──────▼───────────────────────▼───────────┐ │
│  │            Vector Store                   │ │
│  │   ChromaDB，按知识源 Collection 分区      │ │
│  └──────────────────────────────────────────┘ │
│                                               │
│  ┌──────────────────────────────────────────┐ │
│  │         Local Report Sync                 │ │
│  │  reports/ 目录监听 → 自动 chunk → upsert │ │
│  └──────────────────────────────────────────┘ │
└──────────────────────────────────────────────┘
```

### Collection 分区策略

| Collection 名称 | 知识类型 | Chunk 策略 |
|----------------|---------|-----------|
| `research_reports` | 券商研报 PDF | 按章节标题切分，保留层级上下文 |
| `financial_filings` | 财报/公告 PDF/TXT | 按财报科目切分（营收段、利润段、现金流段）|
| `policy_macro` | 政策/宏观 PDF/网页 | 按政策要点段落切分 |
| `academic` | 学术文献 PDF | 按摘要/方法/结论三段切分 |
| `history_reports` | 项目自身历史报告 MD | 按分析维度切分，注入元数据 |
| `system_rules` | 系统规则 YAML/MD | 按工具/指标条目切分 |

### 文档哈希去重

Ingestion 管道入口计算文档 SHA256 哈希，与已入库文档哈希比对，重复则跳过。哈希索引持久化在 ChromaDB 的 metadata 层。

### 标准化元数据

每个 chunk 附带统一元数据结构：

```python
@dataclass
class ChunkMetadata:
    source_type: str         # Collection 类型
    source_path: str         # 原始文件路径
    source_hash: str         # 文档 SHA256
    title: str               # 文档标题
    date: str | None         # 文档日期
    symbols: list[str]       # 关联股票代码（可多只）
    tags: list[str]          # 内容标签
    chunk_index: int         # 在文档中的序号
    ingested_at: str         # 摄入时间戳
```

### 检索流程

```
用户查询 → embedding → 向量检索(top_k=20, 可选按 metadata 过滤)
                      → Cross-Encoder 重排(top_k=5)
                      → 拼接上下文注入 LLM
```

**检索参数透传**：`rag_search` 工具的 `execute()` 接受可选的 `filters`（限定 symbol/date_range/source_type）、`top_k`、`rerank` 参数，上层 Agent 可精细控制检索行为。

### 选型

| 组件 | 开发阶段 | 线上可升级 |
|------|---------|-----------|
| Embedding | `bge-small-zh`（本地） | OpenAI `text-embedding-3-small` |
| 向量库 | ChromaDB（本地） | Milvus Lite 或云服务 |
| 重排器 | `bge-reranker-base`（本地） | Cohere Rerank API |

**本地模型缓存降级**：首次启动时下载本地模型到 `~/.cache/stock_robot/models/`，后续启动直接加载。若本地模型不可用，降级为纯关键词检索（BM25），并在日志中告警。Embedding 服务进程内常驻，不依赖外部 HTTP 服务。

### 知识库清理工具

提供 `stock-robot rag clean` 子命令：

```bash
stock-robot rag clean --source research_reports  # 清除指定 Collection
stock-robot rag clean --before 2025-01-01        # 清除指定日期前的文档
stock-robot rag clean --symbol 000001            # 清除指定股票关联的文档
stock-robot rag clean --dry-run                   # 预览，不实际删除
```

### 本地报告自动同步

`LocalReportSync` 通过文件系统事件监听 `reports/` 目录：
- **新报告** → 按分析维度 chunk → embed → upsert 到 `history_reports` Collection
- **报告更新** → 哈希比对 → 增量更新受影响 chunks
- **报告删除** → 按 source_path 清除对应 chunks

## MCP Gateway

### 双角色定位

```
┌─────────────────────────────────────────────┐
│              MCP Gateway                     │
│                                              │
│  ┌─────────────────┐  ┌───────────────────┐ │
│  │  内部 MCP Server  │  │  外部 MCP Client   │ │
│  │  本地能力标准化    │  │  第三方工具接入    │ │
│  └────────┬────────┘  └────────┬──────────┘ │
│           │                    │             │
│  ┌────────▼────────────────────▼──────────┐  │
│  │         MCP Tool Adapter               │  │
│  │   将 MCP 工具统一适配为 ToolProtocol    │  │
│  └────────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

### 内部 MCP Server（初期暴露的工具）

| 类别 | MCP 工具名 | 对应本地能力 |
|------|-----------|------------|
| 数据 | `fetch_stock_data` | `AkShareAdapter` |
| 数据 | `fetch_index_data` | `IndexDataCollector` |
| 分析 | `analyze_financial` | `FinancialAnalyzer` |
| 分析 | `analyze_technical` | `TechnicalAnalyzer` |
| 分析 | `analyze_valuation` | `ValuationAnalyzer` |
| 分析 | `analyze_industry` | `IndustryAnalyzer` |
| 分析 | `analyze_sentiment` | `SentimentAnalyzer` |
| 分析 | `analyze_index_*` | 5 个指数分析器 |
| 计算 | `calc_indicator` | `numbers.py` 指标计算 |
| 报告 | `generate_report` | 报告构建器 |

### 外部 MCP Client（中长期）

外部 MCP Server 通过 MCP 协议连接后，`MCPAdapter` 自动将其工具列表转为 `ToolProtocol` 并注册到 `ToolRegistry`，Agent 无差别调用。区分通过 `source` 标签（`mcp_internal` vs `mcp_external`）。

### MCP ↔ ToolProtocol 适配流程

```
MCP 工具定义 → MCPAdapter.translate() → ToolProtocol 实例
                                              │
                                              ▼
                                       ToolRegistry.register()
```

## 接口层

### 双路径执行分流

```
                    ┌─────────────┐
                    │  用户输入    │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │  路由判断    │
                    │  analyze /  │  存量命令？
                    │  index 命   │
                    │  令 + 选项？ │
                    └──┬──────┬──┘
                       │ YES  │ NO
              ┌────────▼┐  ┌─▼──────────┐
              │ 存量直通 │  │ Agent 对话  │
              │ Pipeline │  │ Planner →  │
              │ 直接执行  │  │ Executor   │
              └────────┬┘  └─┬──────────┘
                       │     │
              ┌────────▼─────▼──────────┐
              │       统一输出          │
              │  Rich 渲染 / JSON / HTML│
              └────────────────────────┘
```

### 全局参数统一透传

`chat`、`analyze`、`index` 命令共享的全局参数（`--verbose`、`--config`、`--output` 等）通过 `Context` 对象在各层间透传，不依赖全局变量。

### CLI 设计

```bash
# 进入交互式对话
stock-robot chat

# 单次对话（非交互式）
stock-robot chat --ask "帮我找 3 只被低估的新能源龙头"
stock-robot chat --ask "大盘现在适合入场吗" --verbose

# 存量命令完全兼容
stock-robot analyze 000001
stock-robot index 000300 --with-market
```

### CLI 内置会话快捷指令

在交互式对话模式下，支持以下快捷指令（以 `/` 开头）：

| 指令 | 功能 |
|------|------|
| `/help` | 显示 Agent 可用命令和工具摘要 |
| `/tools` | 列出当前注册的所有工具 |
| `/plan` | 显示当前/最近一次执行计划 |
| `/clear` | 清空当前会话上下文 |
| `/save <名称>` | 将当前会话保存为命名快照 |
| `/load <名称>` | 恢复之前保存的会话 |
| `/exit` | 退出对话模式 |
| `/verbose` | 切换详细输出模式（显示工具调用细节）|

### HTTP API（中期）

```python
# FastAPI 路由
POST /api/v1/chat              # Agent 对话接口
POST /api/v1/chat/stream       # Agent 对话接口（SSE 流式）
GET  /api/v1/tools             # 列出可用工具
POST /api/v1/analyze           # 存量分析能力开放
POST /api/v1/index             # 存量指数能力开放
```

**会话隔离与鉴权**：
- 每个 API 请求携带 `X-Session-Id` header，服务端按 session 隔离 Memory
- 可选 `X-API-Key` header 鉴权，通过配置文件管理密钥
- 无鉴权模式下仅监听 `127.0.0.1`

### 多端渲染抽象

输出渲染通过 `OutputRenderer` 协议抽象，根据上下文自动选择：

```python
class OutputRenderer(Protocol):
    def render_report(self, report) -> str: ...      # Rich / JSON / HTML
    def render_plan(self, plan) -> str: ...          # Rich Table / JSON
    def render_progress(self, step: TaskStep) -> str: ...  # Rich Progress / SSE
    def render_error(self, error: str) -> str: ...   # Rich Panel / JSON error
```

CLI 模式使用 Rich 渲染，API 模式使用 JSON，Web UI 使用 HTML 片段。

## 测试策略

| 层级 | 方式 | 说明 |
|------|------|------|
| Tool 单测 | 标准 pytest | 每个工具的 execute 输入输出断言，mock 外部依赖 |
| Executor 单测 | 给定 Plan + mock 工具 | 验证执行顺序、依赖编排、失败隔离 |
| Planner 单测 | 给定 prompt → 断言计划结构 | 验证步骤数、依赖关系、复杂度判断，使用 mock LLM |
| RAG 检索测试 | 固定知识库 + 标准查询集 | 验证召回率和重排效果 |
| RAG 摄 入测试 | 固定文档 → 断言 chunk 数量和元数据 | 验证去重、分区、元数据提取 |
| MCP 适配器测试 | mock MCP 工具定义 → 断言 ToolProtocol | 验证字段映射和异常处理 |
| 集成测试 | 真实 LLM + 真实工具 | 端到端对话场景，验证工具选择准确率 |
| 存量回归 | 运行全部现有测试 | 确保 Pipeline 功能零影响 |

关键原则：**Planner 和 Executor 的单元测试不依赖真实 LLM**，用可控的 mock LLM 返回固定计划/工具选择，保证 CI 稳定可重复。

## 分阶段落地

```
Phase 1 · Agent 核心 + 工具系统      Phase 2 · RAG 引擎        Phase 3 · MCP + 多端
─────────────────────────────      ─────────────────────      ──────────────────────
· chat 命令                         · ChromaDB 集成            · 内部 MCP Server
· Planner + Executor                · Ingestion 管道           · 外部 MCP Client 接入
· ToolRegistry                      · rag_search 工具          · FastAPI HTTP API
· pipeline_tools（包装存量）         · 本地报告自动同步          · Web UI 原型
                                    · 知识库清理工具
─────────────────────────────      ─────────────────────      ──────────────────────
预计: 2-3 周                        预计: 1-2 周               预计: 2-3 周
交付: 可对话的 Agent                交付: 有知识增强的分析     交付: 多端可用 + 外部扩展
```

## 影响范围

### 新增文件

```
stock_robot/
├── src/
│   ├── agent/                # Agent 核心
│   │   ├── __init__.py
│   │   ├── planner.py        # Planner 实现
│   │   ├── executor.py       # Executor 实现
│   │   ├── memory.py         # Memory 数据结构与持久化
│   │   └── tools.py          # ToolProtocol + ToolRegistry
│   ├── rag/                  # RAG 引擎
│   │   ├── __init__.py
│   │   ├── engine.py         # RAG 引擎入口
│   │   ├── ingestion.py      # 文档摄入流水线
│   │   ├── retrieval.py      # 检索-重排
│   │   └── sync.py           # 本地报告自动同步
│   ├── mcp/                  # MCP Gateway
│   │   ├── __init__.py
│   │   ├── gateway.py        # Gateway 入口
│   │   ├── server.py         # 内部 MCP Server
│   │   └── adapter.py        # MCP ↔ ToolProtocol 适配器
│   ├── api/                  # HTTP API（Phase 3）
│   │   ├── __init__.py
│   │   └── app.py
│   └── output/               # 输出渲染
│       ├── __init__.py
│       └── renderer.py       # OutputRenderer 协议与各端实现
├── data/
│   └── knowledge/            # 知识库文件存储
├── tests/
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── test_planner.py
│   │   ├── test_executor.py
│   │   ├── test_memory.py
│   │   └── test_tools.py
│   ├── rag/
│   │   └── test_retrieval.py
│   └── mcp/
│       └── test_adapter.py
```

### 修改文件

| 文件 | 改动 |
|------|------|
| `src/stock_robot/cli.py` | 新增 `chat` 命令；存量命令不变 |
| `pyproject.toml` | 新增依赖：chromadb, pdfplumber, sentence-transformers, fastapi（Phase 3）|

### 不修改

- 现有分析模块、数据源、报告构建器、评分器 — 不被侵入
- `Pipeline.run()` / `IndexPipeline.run()` — 接口不变，仅通过 tool wrapper 间接调用
- 现有测试 — 全部保留，新增测试并行存放

## 关键风险与缓解

| 风险 | 缓解措施 |
|------|---------|
| Planner 对复杂任务拆解不准确 | 硬编码前缀拦截兜底；Executor 支持动态重规划 |
| 工具匹配错误 | 语义标签粗筛 + LLM 二次校验两级匹配 |
| RAG 检索噪音干扰 LLM 判断 | Cross-Encoder 重排 + Agent 自主判断是否采纳检索结果 |
| LLM token 消耗过大 | 单步上下文隔离；简单任务单步降级；对话窗口限制 |
| 存量功能回归 | 新增代码独立模块；存量测试全部保留 |
