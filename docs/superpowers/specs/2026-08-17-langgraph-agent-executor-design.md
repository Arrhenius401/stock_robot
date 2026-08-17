# LangGraph 重构 Agent 执行器设计

- 日期: 2026-08-17
- 状态: 已确认
- 相关代码: `src/agent/executor.py`、`src/agent/planner.py`、`src/agent/tools.py`、`src/agent/memory.py`、`src/api/app.py`、`src/stock_robot/cli.py`

## 背景与动机

- 项目已有自建 agent 编排层（Planner 拆解任务 → Executor 拓扑执行 → Memory 记忆），工具匹配依赖关键词子串 + CJK 二元组（`executor.py:72`），LLM 二次校验工具匹配是预留能力（`executor.py:36` `self._llm`），至今未实现。
- 目标：用 LangGraph 重构 Executor 执行循环，落地 LLM 驱动的工具决策，作为 LangChain 相关项目经验。

## 决策要点

| 决策 | 选择 | 理由 |
|---|---|---|
| 重构范围 | Executor 循环入图，Planner 保留图外 | CLI/API 的 plan 展示时序不变，改动可控 |
| 模型层 | 双轨：agent 用 LangChain 模型（tool calling），自建 LLM 后端不动 | 确定性管道层不引入框架依赖 |
| 会话持久化 | 双写：SqliteSaver checkpointer + 现有 SessionStore 消息表 | 简历卖点（状态持久化）+ 前端 API 不变 |
| 图形态 | 单图循环：决策 → 执行 → 反馈 →（条件边回决策）| 保留"先计划后执行"产品语义 |

## 架构与组件

新增模块（`src/agent/` 下）：

| 文件 | 职责 |
|---|---|
| `graph.py` | LangGraph 状态图定义（StateSchema、节点、边、编译）+ `Executor` 接口兼容层 |
| `tool_selector.py` | LLM 工具决策（步骤描述 + 候选工具 + 执行历史 → 工具名和参数）|

改造：
- `Executor.execute(plan, on_progress=None)` 签名保留，内部替换为图执行：Plan → state → 跑图 → state → Plan。
- 保留 `ToolRegistry.match()` 作为 LLM 决策失败的降级路径，现有关键词/二元组逻辑零改动。
- `Memory` 不动：继续负责 facts、plan_history、消息窗口；graph state 只存执行上下文，不复制 Memory 职责。
- `ToolResult`/`ToolRegistry` 协议不动。

## 图结构与数据流

### StateSchema（TypedDict）

```python
class GraphState(TypedDict):
    goal: str                    # Plan.goal
    steps: list[dict]            # 步骤快照 [{id, description, tool_name, tool_args, status, depends_on}]
    pending_ids: list[str]       # 当前可执行（依赖满足）的步骤 id
    done_ids: list[str]
    failed_ids: list[str]
    skipped_ids: list[str]
    decision_history: list[dict] # 每步决策记录 [{step_id, chosen_tool, reason}]
    tool_results: list[dict]     # 工具执行结果 [{step_id, status, data, error}]
    messages: list               # 决策上下文消息（LLM 对话历史，跨步累积）
    max_llm_retries: int         # 决策失败重试上限（1 次）
```

### 节点

1. `decide_node` — 取 `pending_ids` 第一个步骤，构建 LLM 消息（系统提示：工具协议说明 + 候选工具 JSON Schema；用户消息：步骤描述 + 相关上下文 + 最近 tool_results），调用绑定工具的 ChatModel 做 tool calling → 解析工具名 + 参数写入 `tool_name/tool_args`；LLM 失败/解析失败 → `ToolRegistry.match()` 降级；再失败 → 该步 FAILED。
2. `execute_node` — 调工具（沿用 `_safe_execute` 隔离逻辑），结果写入 `tool_results`。
3. `feedback_node` — 更新步骤状态（DONE/FAILED → 级联 skipped）、重算 `pending_ids`、工具结果追加进 `messages`。

### 边

- 恒等边：`decide → execute → feedback`
- 条件边：`feedback → decide`（有 pending 则循环）、`feedback → END`（全部终态）

### 外部接口数据流

```
Executor.execute(plan):
  state = plan_to_state(plan)
  final = graph.invoke(state, config={"configurable": {"thread_id": session_id or ""}})
  return state_to_plan(final)
```

- `session_id`：CLI 无会话传空串，API 传真实 session_id。
- checkpointer（SqliteSaver）持久化在 `~/.stock_robot/langgraph_checkpoints.sqlite`，与现有 `sessions.db` 分开。
- 双写：graph 执行后同步 `memory.add_message("tool", ...)` 走现有 `message_store` 落 SQLite，前端 API 不变。

## LLM 工具决策

- 用 LangChain 原生 tool calling（`model.bind_tools([...])`），LLM 返回结构化 `tool_calls`，不解析 JSON 文本。
- 候选工具范围：`ToolRegistry.match(step.description)` 粗筛结果（保留 tags 过滤），通常 2-4 个候选，避免一次绑定全部工具。
- 输出解析：
  - `tool_calls` 有工具名 + 参数 → 映射为 `step.tool_name/tool_args`
  - 空 `tool_calls` → 降级 `ToolRegistry.match()` 第一个候选；无候选 → FAILED
  - 非法工具名（候选外）→ 一次重试（强制候选列表），仍失败 → 降级关键词匹配

### 与现状差异

| | 现状 | 重构后 |
|---|---|---|
| 工具选择 | 关键词子串匹配 + CJK 二元组 | LLM tool calling 语义理解 + 关键词兜底 |
| 参数 | `_extract_tool_args` 正则只抠 symbol | LLM 生成完整参数；`_extract_tool_args` 保留，仅用于关键词降级路径的参数提取 |
| 跨步反馈 | 无 | 工具结果累积进决策上下文 |

- `decision_history` 记录每步 `{step_id, chosen_tool, reason}`（reason 取自 tool_calls 响应或标注 "fallback"），CLI verbose 与调试用。

## 兼容、错误处理与依赖

### 对外接口

- `Executor.execute` 签名保留，`cli.py`/`api/app.py` 13 处调用方零改动。
- `render_plan`/`render_summary` UI 语义不变。
- `on_progress` 回调在节点内触发（"execute" 阶段事件），SSE 进度流不变。
- `ToolResult`/`ToolRegistry`/`Memory` 全部不动。

### 新增依赖（pyproject.toml）

- `langgraph` + `langgraph-checkpoint-sqlite`
- `langchain-openai` + `langchain-anthropic`（按 `config.get("llm.provider")` 选建模型，与现有 config 语义一致）
- 版本用当前最新稳定版，实现时先探针验证 API（LangGraph 版本间 API 有漂移），再大规模应用。

### 错误处理阶梯（每层兜底，保证 chat 可用）

1. LLM tool calling 失败 → `ToolRegistry.match()` 降级 → 无候选 → 步骤 FAILED → 级联 SKIPPED
2. checkpointer 初始化失败（SqliteSaver 异常）→ 降级 `MemorySaver`（功能不变，仅丢跨请求恢复），记 `logger.warning`
3. 图执行异常（节点内兜底）→ 单步失败走上述阶梯，绝不向上抛（沿用 BLE001 工具边界豁免）
4. LLM 决策超时 → 由 LangChain 模型调用 timeout 配置控制，超时同走降级阶梯

### 并发

- graph 编译一次后 `invoke` 线程安全，可复用实例；`_build_agent` 仍按会话现建 Executor，内部 graph 缓存单例。
- `SessionManager` 锁逻辑不动。

## 测试策略（TDD 先行）

新增测试（先写测试再实现）：

- `tests/agent/test_tool_selector.py`：LLM 返回合法 tool_calls / 空调用 / 非法工具名 / 抛异常 → 各自走对应降级路径；参数映射正确。
- `tests/agent/test_graph.py`：单步执行、多步依赖串行、失败级联 SKIPPED、checkpointer 按 thread_id 恢复（中断后继续）、决策上下文累积。
- `tests/agent/test_executor.py`：现有用例语义保留适配（mock LLM 注入，FakeTool 现成模式），确认 `execute(plan)` 返回更新后 Plan、级联、progress 回调。

Mock 方式：LangChain 模型层用轻量 FakeChatModel（实现 `bind_tools`/`ainvoke` 返回固定 `tool_calls`），不 mock LangGraph 运行时本身，真实跑图。

适配现有：`tests/api/test_app.py` 的 chat 流程用例（注入 FakeChatModel 的 core）。

验证：单文件 `pyright`/`ruff` 秒级检查；提交前全量 `ruff check .` + `pyright` + pytest。
