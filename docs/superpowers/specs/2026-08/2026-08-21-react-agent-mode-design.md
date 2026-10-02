# 自主循环（ReAct）模式设计

日期：2026-08-21
状态：已批准（brainstorming 流程完成）

## 背景与动机

当前 agent 采用 plan-then-execute 范式：Planner 将用户问题拆解为有序步骤，Executor 驱动 LangGraph 图逐步执行，每步由 ToolSelector 决定一个工具。该范式执行可观测、失败可隔离，但 LLM 无法根据中间结果临场调整策略，工具调用时机由规划器预先定死。

本项目目标是引入**自主循环（ReAct）范式**作为并行模式：LLM 在对话循环中自主决定何时调用工具、调用几个、是否结束，工具结果实时回注。RAG / MCP / pipeline 工具统一通过 ToolRegistry 接入，两种范式共享同一工具集。

## 已确认决策

| 决策点 | 结论 |
|--------|------|
| 演进方式 | 双模式共存，不替换现有 plan-then-execute |
| 模式选择 | LLM 自动判断（Planner mode 三态），快路径优先 |
| 技术实现 | LangGraph `create_react_agent` 封装 |
| 工具范围 | ToolRegistry 全部工具（pipeline / RAG / MCP） |
| 前端交互 | 实时工具卡片流（thinking + tool_call + tool_result） |
| 组织方式 | 统一入口 `/chat/stream` 内部分支 |
| mode 命名 | `task` 更名为 `plan`，新增 `agent` |
| 验收标准 | 双模式回归无破坏；探索式场景达标；模式判断准确 |

## 架构总览

```
/chat/stream（统一入口，现有）
  └─ Planner.mode 三态判断（LLM + 快路径）
       ├─ chat  → ChatResponder（不变）
       ├─ plan  → 现有 Executor / LangGraph 图（不变）
       └─ agent → 新 ReActExecutor（create_react_agent 封装）
                    ├─ 工具：ToolRegistry 全部
                    ├─ checkpointer：复用 graph.create_checkpointer
                    └─ 流式：astream_events → SSE 新事件
```

核心原则：**现有 plan 链路零改动**，新增组件全部走独立文件，通过 Planner 的 mode 字段分流，回归风险集中在 mode 判断一处。

## 设计 1：Planner mode 三态扩展

`src/agent/planner.py`：

- `mode` 值从 `task|chat` 扩展为 `plan|agent|chat`（`task` 更名为 `plan`）
- 提示词规则：
  - **结构化分析**（指定股票代码/指数，如"分析一下 600519"）→ plan
  - **探索式/对比/开放问题**（如"茅台和宁德时代哪个更值得关注""新能源板块最近有什么机会"）→ agent
  - **无关闲聊** → chat
- 硬编码快路径保留且优先级最高：
  - 纯客套短语 → chat（零成本，`_is_chat_message` 不变）
  - 简单查询前缀 → 直接 plan 单步（`_is_simple_query` 不变）
- mode 判断不依赖工具存在性：registry 为空时 agent 模式优雅降级（见设计 5）

同步改动：`app.py` 中 `plan.mode == "chat"` 分支逻辑，及现有引用 `"task"` 的测试。

## 设计 2：ReActExecutor

新文件 `src/agent/react.py`，职责单一：把 `create_react_agent` 包装为项目内可注入、可测试的执行单元。

```python
@dataclass
class AgentOutcome:
    """agent 模式执行结果：最终回答 + 工具调用统计"""
    final_reply: str
    tool_calls: list[dict]   # [{"tool": name, "args": {...}, "status": "success|error"}]

class ReActExecutor:
    def __init__(self, registry, memory, model, session_id="", persist_dir=None):
        # model = LangChain ChatModel（与 Executor 同源注入）

    async def run(self, user_input: str, on_event=None) -> AgentOutcome:
        # 1. 历史拼接：Memory 最近窗口 → 初始 messages
        # 2. 工具绑定：ToolRegistry.list_all() → [{"name","description","parameters"}]
        #    （与 tool_selector 的转换格式一致，可提取公共函数复用）
        # 3. agent = create_react_agent(model, tools, checkpointer=...)
        # 4. astream_events 循环 → on_event 回调（on_tool_start/on_tool_end/on_chat_model_stream）
        # 5. 终止：模型自主 END；recursion_limit 兜底防死循环（如 25 步）
```

关键设计点：

- **工具绑定不裁剪**：全部工具直接绑给模型，工具描述质量是决策质量的唯一依赖
- **工具执行错误由模型处理**：create_react_agent 默认将工具异常作为 tool 消息回注，模型自行决定继续或放弃——这是自主循环相对 plan 模式的核心优势，不需要 plan 模式的失败隔离逻辑（ToolResult 内部错误捕获机制仍生效）
- **checkpointer 复用** `graph.create_checkpointer` / `close_checkpointer`，会话恢复能力与 plan 模式一致
- **事件回调抽象**：`run()` 接受可选 `on_event(callable)`，与 API 层 SSE 解耦，纯逻辑层可单测

## 设计 3：SSE 事件扩展 + 前端

`/chat/stream` 的 agent 分支新增事件（plan 模式事件不变）：

```
{"type": "thinking",   "content": "模型推理片段"}          # 按模型流式输出节流
{"type": "tool_call",  "tool": "rag_search", "args": {...}}  # 触发工具时
{"type": "tool_result","tool": "rag_search", "content": "结果摘要"}  # 工具返回时
```

前端 `chat.js`：

- 新增 `thinking` 块渲染（灰色斜体，可折叠）
- `tool_call` + `tool_result` 成对渲染为工具卡片——复用现有 `toolResultCard`（chat.js:83）渲染路径，tool_call 前置"调用中"状态
- 最终回答仍走现有 `text` 事件流式输出

后端 `app.py` 的 `chat_stream` 增加 `if plan.mode == "agent"` 分支：构造 ReActExecutor，把 astream_events 转译为 SSE 事件；`chat`（非流式）分支同样加 agent 处理（只返回最终结果）。

## 设计 4：记忆集成

与现有 chat 路径一致，保证多轮一致性：

- 用户消息 → `memory.add_message("user", ...)`（入口已做，app.py:195）
- 执行过程 → 工具调用以 system/tool 消息写入（与 graph.py feedback_node 格式一致），供后续轮次 Planner 的 context window 使用
- 最终回答 → `memory.add_message("assistant", final_reply)`
- facts 提取：暂不新增，agent 模式复用同一 Memory 实例，后续如需可扩展

## 设计 5：错误降级

agent 分支内降级阶梯：

```
ReActExecutor.run 异常/超限
  → 降级 plan 模式：_fallback_plan 单步执行（复用现有逻辑）
  → 仍失败 → SSE error 事件（现有 error 通道）
```

- LLM 决策失败由循环内自然处理：模型给出无工具调用的文本回答在自主循环里是合法结束，直接作为最终回答输出
- 工具执行错误不降级整体——错误回注模型自行处理

## 设计 6：测试

| 层 | 内容 |
|----|------|
| Planner | mode 三态判断：探索式→agent、结构化→plan、闲聊→chat（快路径覆盖） |
| ReActExecutor | mock 工具注入：断言多步工具调用、工具错误回注后模型继续、recursion_limit 截断、历史消息拼接 |
| API | `/chat/stream` agent 分支的 SSE 事件序列（thinking/tool_call/tool_result/text/done）；mode 分流回归（chat/plan 行为不变） |
| 前端 | 事件解析与卡片渲染（以手测为准） |
| 回归 | 现有 agent 测试全量：graph/executor/tool_selector/planner 用例不因 mode 更名而破坏 |

## 验收标准

1. **双模式回归无破坏**：同一类问题在 plan 与 agent 两种模式下都产出可用结果；分析、RAG 检索、闲聊能力不受影响
2. **探索式场景达标**：探索式问题（对比、板块机会等）在 agent 模式下自然触发多步工具调用并给出综合回答
3. **模式判断准确**：结构化问题走 plan，探索式问题走 agent，闲聊直接 chat

## 影响面

- 修改：`src/agent/planner.py`（mode 三态 + 提示词）、`src/api/app.py`（chat_stream / chat 分支）、`src/api/static/js/chat.js`（thinking / tool_call / tool_result 渲染）、相关测试
- 新增：`src/agent/react.py`（ReActExecutor）
- 复用不动：`executor.py`、`graph.py`、`tool_selector.py`、`tools.py`、RAG / MCP 全部
