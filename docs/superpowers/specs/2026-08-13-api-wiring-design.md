# Web UI 跑通 — 子项目 A：后端接通设计

> 日期：2026-08-13
> 状态：已评审通过
> 前置：`2026-08-07-agent-architecture-design.md`（Agent 架构）
> 后续：子项目 B（前端产品化，另立 spec）

## 1. 背景与问题

Agent 架构、MCP Gateway、RAG 与 FastAPI Web UI 原型已陆续落地，但 Web 端实际无法使用：

1. **启动方式无注入**：README 指引 `uvicorn api.app:app` 启动的是模块级空 app（`create_app()` 无参数），chat 只返回"[API 模式] Agent 核心未注入"。
2. **API 与 Agent 签名不匹配**（`src/api/app.py`）：
   - `await planner.plan(message)` — `Planner.plan()` 是同步方法返回 `Plan`，await 非 awaitable 抛 TypeError；
   - `executor.execute(plan, session_id=...)` — 真实签名是 `execute(plan, on_progress=None)`。
   两条路径只靠测试中的 mock 通过，真实对象必然 500。
3. **核心端点缺失**：`/api/v1/analyze`、`/api/v1/index` 返回 501。
4. **每次工具调用重建 Pipeline**：`AnalyzeStockTool._get_pipeline()` 每次调用新建 Registry/适配器/分析器/Pipeline 并加载行业分类 CSV，浪费严重。
5. **会话管理形同虚设**：`X-Session-Id` 仅透传，Memory 单例、消息不持久化、多会话串记忆。
6. **LLM 调用无重试/超时**：网络抖动一次，报告即缺 AI 解读。
7. **事件循环阻塞**：`AnalyzeStockTool.execute` 为 async 但内部同步跑 Pipeline（内含 ThreadPoolExecutor + 网络请求），在 FastAPI 中会阻塞整个服务。

## 2. 目标与范围

### 目标

让 Web UI 后端真正可用：`stock-robot api` 一键启动，聊天、个股分析、指数分析、会话管理全部可用，CLI 行为不变。

### 范围（子项目 A）

- API 与 Agent 核心接线修复
- `stock-robot api` 启动命令（注入真实 Agent 核心）
- `/api/v1/analyze`、`/api/v1/index` 端点实现
- Pipeline 单例化（bootstrap 注入）
- 会话隔离 + 消息持久化（SQLite）+ 会话管理端点
- LLM 重试/超时
- 事件循环阻塞修复（`asyncio.to_thread`）
- 评分逻辑抽取共享（CLI 与 API 共用）

### 非目标（YAGNI）

- 前端改动（子项目 B）
- 多 worker/分布式会话存储（本地单用户工具）
- 用户认证/鉴权
- Executor 并行执行、工具匹配打分（遗留 P3 项）
- plan_history 持久化（facts 维持现状：全局、持久化）

## 3. 架构方案

三个候选方案对比：

| 方案 | 要点 | 取舍 |
|---|---|---|
| **方案 1：应用工厂 + 显式注入 + 共享组装函数（选定）** | `create_app(core, sessions)` 显式接收依赖；`build_agent_core(config)` 供 CLI chat 与 API 共用 | 测试友好、启动即验证配置、消除重复组装；需改启动方式为 `stock-robot api` |
| 方案 2：模块级懒加载单例 | api.app 首次请求惰性构建 | 启动命令不变，但隐式依赖、单例跨测试泄漏、配置错误延迟暴露、组装仍与 CLI 重复 |
| 方案 3：服务容器 | 声明式容器统一 CLI/API/MCP 三处装配 | 彻底解决组装，但抽象偏重、需改造 MCP Gateway 才体现价值，范围膨胀 |

选定方案 1：最小抽象解决最大痛点，`build_agent_core` 内部实现将来可平滑替换为服务容器。

## 4. 组件设计

### 4.1 新增文件

| 文件 | 职责 |
|---|---|
| `src/api/bootstrap.py` | `build_agent_core(config) -> AgentCore`：构建 Config → Pipeline + IndexPipeline（各一次）→ 注册 4 个 pipeline 工具（`AnalyzeStockTool`、`AnalyzeIndexTool`、`GetSnapshotTool`、`ScreenStocksTool`，注入 pipeline 实例，移除每次调用重建的 `_get_pipeline` fallback）→ RAG 工具（不可用则跳过，同 CLI）→ LLM（搬移 cli.py `_get_llm_for_agent`）→ 返回 `AgentCore(registry, pipeline, index_pipeline, llm)` |
| `src/api/sessions.py` | `SessionStore`（SQLite）+ `SessionManager`：`create/list/get/clear/delete`；内存缓存 `dict[session_id, Memory]`；消息 append 即落盘；重启后惰性恢复 |
| `src/report/scoring.py` | 从 cli.py 搬移评分逻辑（维度加权、风险扣分、价格位置、ReportBuilder 调用，约 120 行纯搬移），CLI 与 API 共用 |

### 4.2 修改文件

| 文件 | 改动 |
|---|---|
| `src/api/app.py` | `create_app(core, sessions)`；修复两处接线错误（await 同步方法、execute 签名）；实现 analyze/index 端点；新增会话端点 `GET/POST /api/v1/sessions`、`DELETE /api/v1/sessions/{id}`、`POST /api/v1/sessions/{id}/clear`；修复 chat/stream |
| `src/agent/memory.py` | 构造参数加 `session_id`、`message_store`（均默认 None），`add_message` 时落盘；CLI 现有调用不受影响 |
| `src/agent/pipeline_tools.py` | 工具 execute 内用 `asyncio.to_thread` 包装同步 Pipeline 调用；移除 `_get_pipeline` 静态构建，pipeline 一律注入 |
| `src/llm/base.py` + `openai.py` + `claude.py` | 基类加 `_call_with_retry`（重试 2 次、指数退避 1s/2s）；客户端 timeout（默认 60s）；失败仍返回错误文本（保持契约） |
| `src/utils/config.py` | DEFAULT_CONFIG 增加 `llm.retry_times: 2`、`llm.timeout_seconds: 60` |
| `src/stock_robot/cli.py` | 新增 `stock-robot api` 命令（build_agent_core → create_app → uvicorn.run）；`chat` 命令改用 `build_agent_core`；`analyze` 命令改用共享评分函数 |
| `README.md` | 启动方式改为 `stock-robot api`；`uvicorn api.app:app` 标注"无 Agent 模式"（仅调试静态页） |

### 4.3 关键设计点

1. **Planner/Executor 按会话构建**：两者无状态（轻量），每次请求用该会话 Memory 现建，agent 核心类零改动。
2. **`api.app:app` 空 app 保留**：无注入时 chat 返回"[API 模式] Agent 核心未注入"（现状），供前端调试。
3. **analyze 端点一次到位**：返回完整报告 JSON（含综合评分），子项目 B 报告页无需改契约。
4. **并发**：单用户本地工具，`SessionManager` 内存缓存用 `threading.Lock`，不做多 worker。

### 4.4 会话存储 Schema

```sql
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX idx_messages_session ON messages(session_id);
```

- title：自动取第一条 user 消息前 20 字
- 消息 append-only 逐条落盘；`clear` 删除该会话 messages + 内存 `clear_session()`（facts 保留）

## 5. 数据流

### 5.1 会话化 Agent 对话（chat，非流式）

```
POST /api/v1/chat {message, session_id?}
  → 无 session_id 则新建（title = 消息前 20 字）
  → SessionManager.get_memory(sid)（内存缓存，冷启动从 DB 恢复）
  → user 消息写入 memory（落盘）
  → planner = Planner(llm, registry, memory=该会话 memory)    # 按会话现建
  → plan = await asyncio.to_thread(planner.plan, message)      # 同步方法 → 线程池
  → executor = Executor(registry, memory=该会话 memory)
  → plan = await executor.execute(plan)                         # 工具内部已 to_thread
  → 响应 {response: 文本摘要, plan: {goal, steps[{id, description, status}]},
          tool_results: [...], session_id}
```

- `response` 文本摘要：`目标: <goal>\n完成: <done>/<total> 步骤`（对齐 CLI `render_summary` 的纯文本形式，不依赖 Rich）
- `tool_results`：取该会话 memory 中 `role == "tool"` 的最新消息（文本形式），不新增 TaskStep 字段（agent 核心零改动）

### 5.2 流式对话（chat/stream，修复接线）

同一流程，SSE 事件序列：`start` → `plan`（计划+步骤）→ 每步 `step`（id/描述/状态）→ `result`（摘要）→ `done`。
进度桥接：`Executor.execute(on_progress=cb)` 的同步回调写入 `asyncio.Queue`（`put_nowait` 线程安全），event_stream 异步消费转发为 SSE。

### 5.3 个股分析（analyze）

```
POST /api/v1/analyze {symbol}
  → validate_symbol 失败 → 422
  → normalize_symbol → resolve_name（失败回退 symbol）
  → results, commentary, ctx = await asyncio.to_thread(pipeline.run, ...)
  → scoring.py 计算综合评分 → 返回完整报告 JSON
```

响应：`{symbol, name, overview{最新收盘/涨跌幅}, score{base/final/risk_deduction}, score_rows, dimensions{...: {status, summary, score, metrics, risk_flags}}, commentary, generated_at}`。数据不足维度 `status: "unavailable"`（AnalysisResult 原始枚举值），HTTP 仍 200。

### 5.4 指数分析（index）

```
POST /api/v1/index {symbol, index_style?}
  → 默认 broad；构建 AnalysisTarget → to_thread(index_pipeline.run, [target])
  → 返回 {reports: [...], errors: [...]}
```

### 5.5 会话生命周期

- `GET /api/v1/sessions` → `[{session_id, title, created_at, updated_at, message_count}]`（按 updated_at 倒序）
- `DELETE /api/v1/sessions/{id}` → 不存在 404；删除 DB 行 + 内存缓存
- `POST /api/v1/sessions/{id}/clear` → 清空该会话 messages 表 + `memory.clear_session()`

## 6. 错误处理

| 场景 | 行为 |
|---|---|
| 股票代码非法 | 422 + `{detail: "无效的股票代码: xxx"}` |
| 数据源全失败 | 维度 `status: "unavailable"`（AnalysisResult 原始枚举值），报告正常返回，HTTP 200 |
| LLM 失败（重试耗尽） | commentary 为缺省提示文本，报告仍返回，HTTP 200（保持 CLI 契约） |
| 未知 session_id | 404 |
| 未注入 Agent（空 app 模式） | chat 返回现状提示文本 |
| 意外异常 | 500 + `{response: "处理请求时出错: ..."}`（现状），日志记录 |

## 7. 测试策略

| 测试文件 | 内容 |
|---|---|
| `tests/api/test_sessions.py`（新增） | SessionStore CRUD、消息恢复、title 生成、clear/delete、并发加锁 |
| `tests/api/test_app.py`（扩展） | **接线回归测试**：真实 Planner（注入 fake LLM）+ 真实 Executor（fake 工具）+ 真实 SessionManager 走通 chat/stream（用真实对象防签名回归）；analyze/index 端点（mock pipeline）；422/404/500 分支 |
| `tests/api/test_bootstrap.py`（新增） | build_agent_core 组装正确性：pipeline 实例注入工具、RAG 不可用时跳过、LLM 未配置时 llm=None |
| `tests/llm/test_retry.py`（新增） | 重试次数、指数退避、超时传递、最终错误文本格式 |
| `tests/report/test_scoring.py`（新增） | 评分函数：加权、风险扣分、缺失维度处理（从 test_cli.py 相关断言迁移） |

## 8. 兼容性要求

- 现有 73 个测试文件全部保持绿
- CLI 三个入口行为不变：`analyze`（改用共享评分函数后输出一致）、`chat`（改用 bootstrap 后功能一致）、`index`
- `api.app:app` 无 Agent 模式行为不变
- Memory 新参数默认 None，agent 模块现有调用不受影响
- 老配置无需迁移（DEFAULT_CONFIG 合并机制处理新键）

## 9. 验收标准

1. `stock-robot api` 启动后，浏览器打开 Web UI 能与真实 Agent 对话（含 LLM 降级路径：无 key 时单步计划 + 确定性工具仍工作）
2. `curl /api/v1/analyze {symbol: "600519"}` 返回完整报告 JSON
3. `curl /api/v1/sessions` 列表/切换/清空可用，重启服务后历史消息恢复
4. 全量 pytest 通过

## 10. 后续（子项目 B 预告）

聊天页 SSE 流式 + 计划步骤/执行进度实时展示；快捷按钮（择时研判、工具列表、清空会话）真正可用；报告展示页（渲染 analyze 端点的完整 JSON）；会话切换/管理 UI。另立 spec。
