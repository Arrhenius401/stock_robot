# Stock Robot

AI 驱动的股票/指数分析研报助手。支持 A 股 + 指数分析、AI Agent 对话式投研、RAG 知识库增强、MCP 工具生态接入、HTTP API + Web UI。

## 免责声明

本工具仅用于个人学习和研究目的。所有分析数据和观点不构成任何投资建议。股票投资有风险，入市需谨慎。数据来源的准确性和时效性无法保证，使用者需自行判断。

## 安装

```bash
git clone <repo-url> && cd stock_robot
pip install -e ".[dev]"
```

## 首次使用

```bash
stock-robot config set data.disclaimer_accepted true
```

---

## 配置 LLM

### OpenAI

```bash
stock-robot config set llm.api_key "sk-your-openai-api-key"
stock-robot config set llm.model "gpt-4o-mini"    # 可选，默认 gpt-4o
```

### Claude

```bash
stock-robot config set llm.provider "claude"
stock-robot config set llm.api_key "sk-ant-your-claude-api-key"
stock-robot config set llm.model "claude-sonnet-4-6"
```

### 不调用 LLM

```bash
stock-robot analyze 000001 --no-llm               # 单次跳过
stock-robot config set llm.enabled false           # 全局关闭
```

---

## 命令详解

### chat — AI Agent 对话式投研（新增）

进入交互式 AI Agent 对话模式。Agent 会自动拆解复杂投研任务、选择合适的工具、逐步执行并汇总结果。

```bash
# 进入交互式对话
stock-robot chat

# 单次提问（非交互）
stock-robot chat --ask "帮我找 3 只被低估的新能源龙头，对比基本面和风险"
stock-robot chat --ask "大盘现在适合入场吗？" --verbose
```

**交互式对话中的快捷指令（以 `/` 开头）：**

| 指令 | 功能 |
|------|------|
| `/help` | 显示可用命令和工具列表 |
| `/tools` | 列出当前注册的所有工具 |
| `/plan` | 显示当前/最近一次执行计划 |
| `/clear` | 清空当前会话上下文 |
| `/verbose` | 切换详细输出模式（显示工具调用细节） |
| `/exit` | 退出对话 |

**Agent 内置工具：**

| 工具名 | 来源 | 功能 |
|--------|------|------|
| `analyze_stock` | pipeline | 单股全维度分析（财务/技术/估值/行业/舆情） |
| `analyze_index` | pipeline | 指数多维度分析 |
| `screen_stocks` | pipeline | 按行业筛选标的 |
| `get_snapshot` | pipeline | 指数估值快照 |
| `rag_search` | rag | 知识库语义检索 |
| `rag_list_sources` | rag | 列出知识库索引清单 |

**典型场景：**

```bash
# 多资产对比
stock-robot chat --ask "对比分析平安银行、招商银行和兴业银行，谁更有投资价值"

# 择时研判
stock-robot chat --ask "大盘现在适合入场吗？给我一份完整的策略建议"

# 事件驱动
stock-robot chat --ask "美联储刚降息了，评估对银行板块的影响"
```

Agent 使用**规划-执行架构**：Planner 将用户意图拆解为有序 TaskStep，Executor 匹配工具并逐步执行，支持依赖编排和失败隔离。

---

### analyze — 分析股票（存量命令）

```bash
stock-robot analyze <股票代码>
```

| 选项 | 说明 |
|------|------|
| `-d, --dimension` | 指定分析维度：`financial` / `technical` / `valuation` / `industry` / `sentiment` |
| `--with-market` | 注入大盘环境数据（沪深300 技术面/估值/资金面快照） |
| `--refresh-cache` | 强制刷新数据，忽略本地缓存 |
| `--no-llm` | 仅输出数据和指标，不调用 LLM |
| `-v, --verbose` | 显示数据采集和计算的过程日志 |

**示例：**

```bash
stock-robot analyze 000001                          # 完整研报
stock-robot analyze 000001 --dimension financial    # 只看财务分析
stock-robot analyze 600036 -v                       # 详细模式
stock-robot analyze 000001 --with-market            # 嵌入大盘环境
```

股票代码支持简写（`1` → `000001`）、带前缀（`sh000001`、`sz000001`），上交所（6 开头）、深交所主板（0 开头）、创业板（3 开头）、北交所（8 开头）均可识别。

---

### index — 分析指数（存量命令）

```bash
stock-robot index <指数代码> [指数代码...]
```

| 选项 | 说明 |
|------|------|
| `--style, -s` | 手动指定指数类别：`broad` / `sector` / `overseas` |
| `--output, -o` | 输出格式：`terminal`（默认）/ `markdown` |
| `--compare-only` | 仅输出多指数横向对比表格 |

**示例：**

```bash
stock-robot index 000300                             # 单指数分析
stock-robot index 801080 --style sector              # 行业指数
stock-robot index 000300 000905 000016               # 批量对比
stock-robot index 000300 000905 --compare-only       # 仅对比表
stock-robot index HSI SPX NDX                        # 海外指数
```

---

### rag — 知识库管理（新增）

管理 RAG 知识库的文档摄入、清理和统计。知识库包含 6 个分区：券商研报、财报/公告、政策/宏观、学术文献、历史分析报告、系统规则。

#### 摄入文档

```bash
# 摄入单个文件
stock-robot rag ingest /path/to/report.pdf -s research_reports \
    -t "2025年新能源策略报告" -d "2025-01-15" --symbol 600519 --tag 新能源

# 批量摄入目录下所有支持文件
stock-robot rag ingest /data/reports/ -s research_reports --symbol 000001

# 摄入历史分析报告
stock-robot rag ingest reports/000001_20260807_161939.md \
    -s history_reports -t "平安银行分析" --symbol 000001 --tag 历史报告
```

| 参数 | 说明 |
|------|------|
| `-s, --source-type` | **必填**，知识库类型（见下方 6 个分区） |
| `-t, --title` | 文档标题 |
| `-d, --date` | 文档日期 (YYYY-MM-DD) |
| `--symbol` | 关联股票代码（可多次指定） |
| `--tag` | 内容标签（可多次指定） |

**6 个知识库分区：**

| 分区名 | 知识类型 | 分块策略 |
|--------|---------|---------|
| `research_reports` | 券商研报 | 按章节标题切分 |
| `financial_filings` | 财报/公告 | 按财报科目切分 |
| `policy_macro` | 政策/宏观 | 按段落簇切分 |
| `academic` | 学术文献 | 按摘要/方法/结论切分 |
| `history_reports` | 历史分析报告 | 按分析维度切分 |
| `system_rules` | 系统规则 | 按工具/指标条目切分 |

#### 查看统计

```bash
stock-robot rag stats
```

输出各 Collection 的文档数量、嵌入模型名称等汇总信息。

#### 清理知识库

```bash
# 删除指定分区的全部文档
stock-robot rag clean --source research_reports

# 删除指定日期前的文档
stock-robot rag clean --before 2025-01-01

# 删除指定股票关联的所有文档
stock-robot rag clean --symbol 000001

# 预览模式（不实际删除）
stock-robot rag clean --source research_reports --dry-run
```

#### 自动同步

`LocalReportSync` 模块可扫描 `reports/` 目录，自动将新生成的分析报告摄入 `history_reports` 知识库：

```python
from rag.engine import RAGEngine
from rag.sync import LocalReportSync

engine = RAGEngine()
sync = LocalReportSync(reports_dir="reports", engine=engine)
result = sync.sync()
print(f"已处理: {result['processed']}, 跳过: {result['skipped']}, 失败: {result['failed']}")
```

> **注意：** 首次运行时 `sentence-transformers` 和 `chromadb` 的 `hnswlib` 可能需要 VC++ 运行库。如果嵌入模型不可用，系统会自动降级为关键词检索模式。建议使用 Python 官方构建（python.org）。

---

### HTTP API（新增）

启动 HTTP 服务，将 Agent 和工具能力通过 REST API 暴露：

```bash
# 启动服务（默认 127.0.0.1:8000）
PYTHONPATH=src python -m uvicorn api.app:app --host 127.0.0.1 --port 8000

# 开发模式自动重载
PYTHONPATH=src python -m uvicorn api.app:app --reload
```

**API 端点：**

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `GET` | `/api/v1/tools` | 列出可用工具 |
| `POST` | `/api/v1/chat` | Agent 对话（JSON 响应） |
| `POST` | `/api/v1/chat/stream` | Agent 对话（SSE 流式） |
| `POST` | `/api/v1/analyze` | 存量分析（开发中） |
| `POST` | `/api/v1/index` | 存量指数（开发中） |
| `GET` | `/` | Web UI |

**示例调用：**

```bash
# 健康检查
curl http://127.0.0.1:8000/health

# 查询工具列表
curl http://127.0.0.1:8000/api/v1/tools

# Agent 对话
curl -X POST http://127.0.0.1:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: my-session" \
  -d '{"message": "帮我分析平安银行的估值水平"}'

# SSE 流式对话
curl -X POST http://127.0.0.1:8000/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "对比新能源龙头的估值"}'
```

启动后浏览器打开 `http://127.0.0.1:8000/` 进入 Web 聊天界面。

**鉴权模式：** 默认仅监听 `127.0.0.1`，无需鉴权。可通过环境变量 `STOCK_ROBOT_API_KEY` 启用 API Key 校验。

---

### MCP 集成（新增）

MCP Gateway 兼顾两种角色：**内部 Server**（将本地工具标准化暴露给外部 MCP 客户端）和**外部 Client**（接入第三方 MCP Server 的工具）。

#### 启动内部 MCP Server（供 Claude Desktop / VS Code 等外部客户端调用）

在 Claude Desktop 的 `claude_desktop_config.json` 中添加：

```json
{
  "mcpServers": {
    "stock-robot": {
      "command": "python",
      "args": ["-c", "from mcp.gateway import MCPGateway; from agent.pipeline_tools import AnalyzeStockTool; gw = MCPGateway(); gw.register_local_tool(AnalyzeStockTool()); gw.serve_stdio()"]
    }
  }
}
```

#### 接入外部 MCP Server

```python
from mcp.gateway import MCPGateway
from agent.tools import ToolRegistry

gateway = MCPGateway()

# 连接外部 MCP Server（如新闻搜索服务）
client = gateway.connect_external(
    name="news_service",
    command="python",
    args=["-m", "news_mcp_server"],
)

# 自动发现并注册其工具
tools = gateway.discover_external_tools("news_service")
for tool in tools:
    registry.register(tool)

# 使用时通过 ToolRegistry 统一调用，Agent 不区分工具来源
```

所有 MCP 工具通过 `MCPAdapter` 自动转为 `ToolProtocol`，Agent 无差别调用。通过 `source` 标签区分：`mcp_internal`（本地）/ `mcp_external`（外部）。

---

### config — 管理配置

```bash
stock-robot config get llm.provider              # 查看
stock-robot config set llm.temperature 0.1       # 设置
cat ~/.stock_robot/config.yaml                   # 完整配置
```

| 键 | 说明 | 默认值 |
|----|------|--------|
| `llm.provider` | LLM 提供商 | `openai` |
| `llm.model` | 模型名称 | `gpt-4o` |
| `llm.api_key` | API 密钥 | 空 |
| `llm.temperature` | 生成温度 (0-1) | `0.3` |
| `llm.max_tokens` | 最大输出 token | `2000` |
| `llm.enabled` | 是否启用 LLM | `true` |
| `data.cache_ttl.daily` | 日频缓存（秒） | `86400` |
| `data.cache_ttl.quarterly` | 季频缓存（秒） | `604800` |

---

### cache — 管理缓存

```bash
stock-robot cache status     # 查看缓存状态
stock-robot cache clear      # 清空所有缓存
```

缓存存储在 `~/.stock_robot/cache.db`（SQLite），TTL 到期自动失效。

---

## 架构概览

```
┌──────────────────────────────────────────────────┐
│                  接口层                           │
│   CLI (click)  │  HTTP API (FastAPI)  │  Web UI  │
└────────────────────┬─────────────────────────────┘
                     │
┌────────────────────▼─────────────────────────────┐
│               Agent 核心                          │
│   Planner (任务拆解)  +  Executor (逐步执行)       │
│   Memory (对话/计划/事实)  +  ToolRegistry (注册)  │
│   pipeline_tools  │  rag_tools  │  mcp_tools     │
└────────────────────┬─────────────────────────────┘
                     │
┌────────────────────▼─────────────────────────────┐
│                 引擎层                            │
│   Pipeline (存量)  │  RAG (ChromaDB)  │  MCP GW   │
└──────────────────────────────────────────────────┘
```

- **存量兼容** — `analyze` / `index` 命令不变，直接走 Pipeline
- **工具统一** — 所有工具实现 `ToolProtocol`，Agent 不感知工具来源
- **分层隔离** — Agent 核心为新增层，不侵入存量管道代码

## 报告输出

报告在终端中以 Markdown 渲染显示，同时保存到 `reports/` 目录：

```
reports/
├── 000001_20260705_143021.md
├── 600036_20260705_150532.md
└── ...
```

文件名格式：`{股票代码}_{日期}_{时间}.md`

## LLM 成本

每次 LLM 调用记录到 `~/.stock_robot/usage.log`，包含模型、token 消耗和费用估算。

## 常见问题

**Q: 分析失败，提示 API 配置错误？**

确认 API Key 已正确设置：
```bash
stock-robot config get llm.api_key
```

**Q: 想不花钱试用？**

```bash
stock-robot analyze 000001 --no-llm
```

**Q: 支持港股/美股吗？**

个股分析 v1 仅支持 A 股。指数分析已支持港股和美股主要指数（HSI、HSTECH、SPX、NDX、DJI）。

**Q: Agent 对话和传统的 analyze 命令有什么区别？**

`analyze` 是固定管道——一次分析一只股票，按预定流程走完五个维度。`chat` 是 AI Agent——可以自动拆解复杂任务（如"找3只被低估的新能源龙头并对比"），自主选择合适的工具，分步执行并汇总结果。简单问题可以直接用 `chat --ask`，复杂多步骤分析更适合交互式对话。

**Q: 知识库需要手动管理吗？**

新装时知识库为空。用 `rag ingest` 导入文档后，Agent 在分析时会自动通过 `rag_search` 工具检索相关知识。历史分析报告可以通过 `LocalReportSync` 自动同步到 `history_reports` 知识库。

**Q: 如何添加新的 LLM（如 DeepSeek、通义千问）？**

在 `src/llm/` 下创建新的适配器（实现 `LLMBackend` 接口），然后在 `_register_llm` 中注册即可。

**Q: 如何暴露更多本地工具给外部 MCP 客户端？**

通过 `MCPGateway.register_local_tool()` 注册任意 `ToolProtocol` 实例，然后调用 `gateway.serve_stdio()` 以 stdio MCP Server 模式运行。工具会自动通过 MCP 协议的 `tools/list` 和 `tools/call` 暴露。
