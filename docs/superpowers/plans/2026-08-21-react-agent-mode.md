# 自主循环（ReAct）模式实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 plan-then-execute agent 旁新增自主循环（ReAct）模式——Planner mode 三态（plan/agent/chat），agent 模式由 create_react_agent 封装执行，全部 ToolRegistry 工具接入，SSE 实时工具卡片流。

**Architecture:** Planner 的 mode 字段从 `task|chat` 扩展为 `plan|agent|chat`；`/chat/stream` 与 `/chat` 按 mode 分流：plan → 现有 Executor（不动），agent → 新 `ReActExecutor`（`src/agent/react.py`，包装 `create_react_agent`），chat → ChatResponder（不动）。agent 模式工具执行错误回注模型自行处理；run 异常降级 plan 单步。

**Tech Stack:** Python 3.11+, LangGraph 1.2.11（`create_react_agent`）、langchain-core（`AIMessage`/`tool`）、FastAPI SSE、原生前端 JS。

**设计文档:** `docs/superpowers/specs/2026-08-21-react-agent-mode-design.md`

**环境事实（勿重复探测）：** 所有 Python 命令用 `.venv/Scripts/python`；pyright 直接可用；ruff 用 VS Code 扩展通配符路径 `~/.vscode/extensions/charliermarsh.ruff-*/bundled/libs/bin/ruff.exe`；全量检查约 4 分钟，日常用单文件检查。

---

### Task 1: Plan.mode 三态（memory + planner + 测试）

**Files:**
- Modify: `src/agent/memory.py:32`
- Modify: `src/agent/planner.py`（PLANNER_SYSTEM_PROMPT 规则 5 + 输出格式 + `_parse_response` 165 行附近）
- Test: `tests/agent/test_memory.py:111`
- Test: `tests/agent/test_planner.py:212`

- [ ] **Step 1: 改 mode 类型定义（memory.py）**

`src/agent/memory.py:32` 将：

```python
    mode: Literal["task", "chat"] = "task"   # task=拆计划执行；chat=普通会话
```

改为：

```python
    mode: Literal["plan", "agent", "chat"] = "plan"  # plan=拆计划执行；agent=自主循环；chat=普通会话
```

- [ ] **Step 2: 改 Planner 提示词（planner.py）**

`PLANNER_SYSTEM_PROMPT`（planner.py:29-57）中，将规则 5 整段：

```python
5. mode 判断：涉及任何证券标的（股票代码、公司名如贵州茅台/宁德时代、指数名如
   上证指数/沪深300/中证500、行业、宏观政策、数据查询）或可拆解为多步的投研请求
   → task；仅当与投资研究完全无关（问候、道谢、闲聊、非金融话题）→ chat。
   chat 模式 steps 必须为空数组。
```

替换为：

```python
5. mode 判断：结构化分析（明确指定股票代码/指数，如"分析一下 600519"）→ plan；
   探索式/对比/开放问题（如"茅台和宁德时代哪个更值得关注""新能源板块最近有什么机会"）
   → agent；仅当与投资研究完全无关（问候、道谢、闲聊、非金融话题）→ chat。
   plan 模式 steps 为执行步骤；agent 与 chat 模式 steps 必须为空数组。
```

输出格式部分（planner.py:44-56）将：

```json
  "mode": "task|chat",
```

改为：

```json
  "mode": "plan|agent|chat",
```

- [ ] **Step 3: 改 `_parse_response`（planner.py）**

将（planner.py:165-168 附近）：

```python
        mode = data.get("mode", "task")
        if mode == "chat":
            return Plan(goal=data.get("goal", fallback_goal), steps=[],
                        mode="chat")
```

替换为：

```python
        mode = data.get("mode", "plan")
        if mode == "chat":
            return Plan(goal=data.get("goal", fallback_goal), steps=[],
                        mode="chat")
        if mode == "agent":
            return Plan(goal=data.get("goal", fallback_goal), steps=[],
                        mode="agent")
```

（`_fallback_plan` 不传 mode，默认值已变为 "plan"，无需改动。）

- [ ] **Step 4: 更新既有断言并新增 agent 模式测试（test_planner.py + test_memory.py）**

`tests/agent/test_planner.py:212`：`assert plan.mode == "task"` → `assert plan.mode == "plan"`（并把该测试方法名 `test_plan_llm_task_mode_returns_task_plan` 改为 `test_plan_llm_plan_mode_returns_plan`）。

`tests/agent/test_planner.py:87` 的 `test_plan_complex_query_calls_llm` 末尾追加一行 `assert plan.mode == "plan"`（make_multi_step_response 不带 mode，验证默认值）。

`tests/agent/test_memory.py:111`：`assert plan.mode == "task"` → `assert plan.mode == "plan"`。

`tests/agent/test_planner.py` 顶部 `make_chat_response` 后新增：

```python
def make_agent_response():
    return json.dumps({
        "goal": "对比分析",
        "complexity": "complex",
        "mode": "agent",
        "steps": [],
    }, ensure_ascii=False)
```

`TestChatDetection` 类末尾新增：

```python
    def test_plan_llm_agent_mode_returns_agent_plan(self, registry, memory):
        llm = FakeLLM(fixed_response=make_agent_response())
        planner = Planner(llm=llm, registry=registry, memory=memory)

        plan = planner.plan("对比茅台和宁德时代")

        assert plan.mode == "agent"
        assert plan.steps == []
```

- [ ] **Step 5: 运行测试验证**

```bash
.venv/Scripts/python -m pytest tests/agent/test_planner.py tests/agent/test_memory.py -q
```

Expected: 全部 PASS（约 20 个用例）。

- [ ] **Step 6: 单文件类型检查**

```bash
pyright src/agent/planner.py src/agent/memory.py
```

Expected: 0 errors（Literal 三态与 `_parse_response` 分支类型一致）。

- [ ] **Step 7: 提交**

```bash
git add src/agent/memory.py src/agent/planner.py tests/agent/test_planner.py tests/agent/test_memory.py
git commit -m "feat(agent): Planner mode 三态扩展（plan/agent/chat）
task 更名为 plan，新增 agent 自主循环模式判定"
```

---

### Task 2: fake_chat_model 支持序列响应

**Files:**
- Modify: `tests/agent/fake_chat_model.py`

- [ ] **Step 1: 扩展 FakeChatModel（保持既有测试兼容）**

将 `FakeChatModel.__init__` 与 `ainvoke`（fake_chat_model.py:11-26）替换为：

```python
class FakeChatModel:
    """bind_tools 返回自身；ainvoke 从 responses 序列弹出下一条，耗尽后返回固定 content"""

    def __init__(self, responses=None, content: Any = "测试回复"):
        self._responses = list(responses or [])
        self._content = content
        self.bound_tools = None
        self.calls = []  # 记录每次 ainvoke 的 messages

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages, **kwargs):
        self.calls.append(messages)
        if self._responses:
            return self._responses.pop(0)
        return FakeAIMessage(content=self._content)
```

（`responses` 元素为 `langchain_core.messages.AIMessage` 实例——create_react_agent 的 add_messages reducer 要求 BaseMessage。）

- [ ] **Step 2: 运行既有测试验证无回归**

```bash
.venv/Scripts/python -m pytest tests/agent/test_tool_selector.py tests/agent/test_chat.py tests/api/test_app.py -q
```

Expected: 全部 PASS。

- [ ] **Step 3: 提交**

```bash
git add tests/agent/fake_chat_model.py
git commit -m "test(agent): FakeChatModel 支持序列响应（自主循环测试用）"
```

---

### Task 3: ReActExecutor（react.py）+ 单元测试

**Files:**
- Create: `src/agent/react.py`
- Create: `tests/agent/test_react.py`

- [ ] **Step 1: 探针验证 create_react_agent 集成路径**

写临时探针 `probe_react.py`（放仓库根目录，验证后删除）：

```python
"""探针：create_react_agent + FakeChatModel + tool() args_schema 转换路径"""
import asyncio
import json
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent
from pydantic import BaseModel, Field, create_model
from tests.agent.fake_chat_model import FakeChatModel


def schema_to_model(name: str, parameters: dict) -> type[BaseModel]:
    properties = parameters.get("properties", {})
    required = set(parameters.get("required", []))
    fields = {}
    for pname, prop in properties.items():
        ptype = {"string": str, "integer": int, "number": float,
                 "boolean": bool, "array": list}.get(prop.get("type", "string"), str)
        kwargs = {"description": prop.get("description", "")}
        if pname not in required:
            kwargs["default"] = None
        fields[pname] = (ptype, Field(**kwargs))
    return create_model(name, __base__=BaseModel, **fields)


async def _echo(**kwargs):
    return json.dumps({"echo": kwargs.get("text", "")}, ensure_ascii=False)


async def main():
    echo_tool = tool(_echo, name="echo",
                     description="回显工具",
                     args_schema=schema_to_model("echo", {
                         "type": "object",
                         "properties": {"text": {"type": "string"}},
                         "required": ["text"],
                     }))
    model = FakeChatModel(responses=[
        AIMessage(content="", tool_calls=[
            {"name": "echo", "args": {"text": "hi"}, "id": "call_1"}]),
        AIMessage(content="最终回答", tool_calls=[]),
    ])
    agent = create_react_agent(model, [echo_tool], checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "probe"}, "recursion_limit": 10}
    events = []
    async for ev in agent.astream_events(
            {"messages": [AIMessage(content="hi")]}, config=config, version="v2"):
        events.append(ev.get("event"))
    state = await agent.aget_state(config)
    msgs = state.values.get("messages", [])
    final = ""
    for m in reversed(msgs):
        if isinstance(m, AIMessage) and m.content and not m.tool_calls:
            final = str(m.content)
            break
    print("events:", sorted(set(events)))
    print("final:", final)
    assert "on_tool_start" in events and "on_tool_end" in events
    assert final == "最终回答"


asyncio.run(main())
```

运行：

```bash
.venv/Scripts/python probe_react.py
```

Expected: `events:` 含 `on_tool_start`/`on_tool_end`；`final: 最终回答`。若 `tool()` 装饰器对 `**kwargs` 签名报错（args_schema 未生效），改用 `StructuredTool.from_function(coroutine=_echo, name="echo", description="回显工具", args_schema=...)`（`from langchain_core.tools import StructuredTool`）。验证成功后删除 `probe_react.py`。

- [ ] **Step 2: 写失败测试（test_react.py）**

```python
"""ReActExecutor 单元测试（FakeChatModel + 真实 AIMessage 驱动）"""
import pytest
from langchain_core.messages import AIMessage
from langgraph.errors import GraphRecursionError

from agent.memory import Memory
from agent.react import ReActExecutor
from agent.tools import ToolRegistry, ToolResult


class EchoTool:
    name = "echo"
    description = "回显工具"
    parameters = {"type": "object",
                  "properties": {"text": {"type": "string"}},
                  "required": ["text"]}
    tags = ["test"]
    source = "pipeline"

    async def execute(self, **kwargs):
        return ToolResult(status="success",
                          data={"echo": kwargs.get("text", "")})


class FailingTool:
    name = "fail_tool"
    description = "总是失败的工具"
    parameters = {"type": "object", "properties": {}}
    tags = ["test"]
    source = "pipeline"

    async def execute(self, **kwargs):
        return ToolResult(status="error", error="数据源不可用")


def make_registry():
    registry = ToolRegistry()
    registry.register(EchoTool())
    registry.register(FailingTool())
    return registry


def make_model(responses):
    from tests.agent.fake_chat_model import FakeChatModel
    return FakeChatModel(responses=responses)


@pytest.mark.asyncio
async def test_run_multi_step_tool_calls():
    """模型先调 echo 工具，再给最终回答；工具结果写入 memory"""
    model = make_model([
        AIMessage(content="", tool_calls=[
            {"name": "echo", "args": {"text": "你好"}, "id": "call_1"}]),
        AIMessage(content="已为您回显", tool_calls=[]),
    ])
    memory = Memory()
    executor = ReActExecutor(registry=make_registry(), memory=memory,
                             model=model, session_id="test-1")

    outcome = await executor.run("回显一下")

    assert outcome.final_reply == "已为您回显"
    assert len(outcome.tool_calls) == 1
    assert outcome.tool_calls[0]["tool"] == "echo"
    assert outcome.tool_calls[0]["status"] == "success"
    assert any(m["role"] == "tool" and "echo" in m["content"]
               for m in memory.messages)
    assert any(m["role"] == "assistant" and m["content"] == "已为您回显"
               for m in memory.messages)


@pytest.mark.asyncio
async def test_run_tool_error_returns_to_model():
    """工具失败：错误信息回注模型，模型仍能给出最终回答"""
    model = make_model([
        AIMessage(content="", tool_calls=[
            {"name": "fail_tool", "args": {}, "id": "call_2"}]),
        AIMessage(content="工具不可用，已跳过", tool_calls=[]),
    ])
    executor = ReActExecutor(registry=make_registry(), memory=Memory(),
                             model=model, session_id="test-2")

    outcome = await executor.run("查一下")

    assert outcome.final_reply == "工具不可用，已跳过"
    assert outcome.tool_calls[0]["status"] == "error"


@pytest.mark.asyncio
async def test_run_emits_live_events():
    """astream_events 事件经 on_event 回调透出（thinking/tool_call/tool_result）"""
    model = make_model([
        AIMessage(content="", tool_calls=[
            {"name": "echo", "args": {"text": "hi"}, "id": "call_3"}]),
        AIMessage(content="完成", tool_calls=[]),
    ])
    events = []
    executor = ReActExecutor(registry=make_registry(), memory=Memory(),
                             model=model, session_id="test-3")

    await executor.run("回显", on_event=events.append)

    types = [e["type"] for e in events]
    assert "tool_call" in types
    assert "tool_result" in types
    tool_call = next(e for e in events if e["type"] == "tool_call")
    assert tool_call["tool"] == "echo"
    assert tool_call["args"] == {"text": "hi"}


@pytest.mark.asyncio
async def test_run_includes_history():
    """Memory 最近消息拼接进初始 messages"""
    model = make_model([AIMessage(content="直接回答", tool_calls=[])])
    memory = Memory()
    memory.add_message("user", "上一轮问题")
    memory.add_message("assistant", "上一轮回答")
    executor = ReActExecutor(registry=make_registry(), memory=memory,
                             model=model, session_id="test-4")

    await executor.run("新问题")

    first_call = model.calls[0]
    contents = " ".join(getattr(m, "content", "") for m in first_call)
    assert "上一轮问题" in contents
    assert "上一轮回答" in contents


@pytest.mark.asyncio
async def test_run_recursion_limit_raises():
    """模型无限调工具：recursion_limit 触发 GraphRecursionError"""
    model = make_model([
        AIMessage(content="", tool_calls=[
            {"name": "echo", "args": {"text": "x"}, "id": f"call_{i}"}])
        for i in range(10)
    ])
    executor = ReActExecutor(registry=make_registry(), memory=Memory(),
                             model=model, session_id="test-5", max_iterations=3)

    with pytest.raises(GraphRecursionError):
        await executor.run("循环")


@pytest.mark.asyncio
async def test_run_model_none_raises():
    executor = ReActExecutor(registry=make_registry(), memory=Memory(),
                             model=None, session_id="test-6")
    with pytest.raises(RuntimeError):
        await executor.run("任务")
```

- [ ] **Step 3: 运行测试确认失败**

```bash
.venv/Scripts/python -m pytest tests/agent/test_react.py -q
```

Expected: FAIL — `ModuleNotFoundError: agent.react`。

- [ ] **Step 4: 实现 react.py**

```python
"""自主循环执行器 — create_react_agent 封装

模型在对话循环中自主决定工具调用时机（可零次或多次），工具结果回注，
直到模型认为任务完成。与 plan 模式的差异：不预拆步骤、不强制每步选工具。
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.errors import GraphRecursionError  # noqa: F401  # 重新导出供调用方捕获
from langgraph.prebuilt import create_react_agent

from agent.memory import Memory
from agent.tools import ToolRegistry

logger = logging.getLogger(__name__)

_JSON_TYPE_MAP = {"string": str, "integer": int, "number": float,
                  "boolean": bool, "array": list}

EventCallback = Callable[[dict], None]


@dataclass
class AgentOutcome:
    """agent 模式执行结果：最终回答 + 工具调用统计"""
    final_reply: str
    tool_calls: list[dict] = field(default_factory=list)
    # tool_calls 元素: {"tool", "args", "status": "success|error", "summary"}


def _schema_to_model(name: str, parameters: dict) -> type[Any]:
    """ToolProtocol 的 JSON Schema parameters → pydantic 模型（args_schema 要求）"""
    from pydantic import BaseModel, Field, create_model

    properties = parameters.get("properties", {})
    required = set(parameters.get("required", []))
    fields: dict[str, Any] = {}
    for pname, prop in properties.items():
        ptype = _JSON_TYPE_MAP.get(prop.get("type", "string"), str)
        kwargs: dict[str, Any] = {"description": prop.get("description", "")}
        if pname not in required:
            kwargs["default"] = None
        fields[pname] = (ptype, Field(**kwargs))
    return create_model(name, __base__=BaseModel, **fields)


def _as_lc_tool(t) -> Any:
    """ToolProtocol → LangChain tool

    过滤 None 参数：schema 里 optional 字段默认 None，直接传入会让
    RAGSearchTool 等工具 int(None) 崩溃。
    """
    async def _run(**kwargs):
        result = await t.execute(
            **{k: v for k, v in kwargs.items() if v is not None})
        if result.status == "error":
            return f"错误: {result.error}"
        return json.dumps(result.data, ensure_ascii=False, default=str)

    return tool(_run, name=t.name, description=t.description,
                args_schema=_schema_to_model(t.name, t.parameters))


def _role_to_message(role: str, content: str) -> BaseMessage:
    if role == "user":
        return HumanMessage(content=content)
    if role == "assistant":
        return AIMessage(content=content)
    return SystemMessage(content=content)  # system / tool 历史无 tool_call_id，统一降级


class ReActExecutor:
    """自主循环执行器 — 模型自主决定工具调用时机，直到它认为任务完成"""

    def __init__(self, registry: ToolRegistry, memory: Memory, model=None,
                 session_id: str = "", persist_dir: str | None = None,
                 max_iterations: int = 25):
        self._registry = registry
        self._memory = memory
        self._model = model
        self._session_id = session_id
        self._persist_dir = persist_dir
        self._max_iterations = max_iterations

    async def run(self, user_input: str,
                  on_event: EventCallback | None = None) -> AgentOutcome:
        if self._model is None:
            raise RuntimeError("LangChain 模型未注入，无法执行自主循环")

        from agent.graph import close_checkpointer, create_checkpointer

        history: list[BaseMessage] = [
            _role_to_message(m["role"], m["content"])
            for m in self._memory.get_context_window(n=10)
        ]
        history.append(HumanMessage(content=user_input))

        lc_tools = [_as_lc_tool(t) for t in self._registry.list_all()]
        checkpointer = create_checkpointer(self._persist_dir)
        agent = create_react_agent(self._model, lc_tools, checkpointer=checkpointer)
        config = {
            "configurable": {"thread_id": self._session_id or "react"},
            "recursion_limit": self._max_iterations,
        }

        tool_calls: list[dict] = []
        pending_tool: dict | None = None
        final_reply = ""
        try:
            async for event in agent.astream_events(
                    {"messages": history}, config=config, version="v2"):
                etype = event.get("event")
                if etype == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    content = getattr(chunk, "content", "") if chunk else ""
                    # 工具调用参数流不当作推理文本；空内容跳过（节流）
                    if content and not getattr(chunk, "tool_call_chunks", None):
                        if on_event:
                            on_event({"type": "thinking", "content": content})
                elif etype == "on_tool_start":
                    data = event.get("data", {})
                    args = data.get("input", {})
                    pending_tool = {"tool": data.get("name", "tool"), "args": args}
                    if on_event:
                        on_event({"type": "tool_call", "tool": pending_tool["tool"],
                                  "args": args})
                elif etype == "on_tool_end":
                    data = event.get("data", {})
                    output = str(data.get("output", "") or "")
                    name = pending_tool["tool"] if pending_tool else "tool"
                    status = "error" if output.startswith("错误:") else "success"
                    summary = output[:500]
                    if pending_tool is not None:
                        tool_calls.append({"tool": name, "args": pending_tool["args"],
                                           "status": status, "summary": summary})
                    self._memory.add_message(
                        "tool", f"[{name}] {status}: {summary}")
                    if on_event:
                        on_event({"type": "tool_result", "tool": name,
                                  "content": summary})
                    pending_tool = None
            # 必须在关闭 checkpointer 之前读取终态（SQLite 连接关闭后无法查询）
            state = await agent.aget_state(config)
            for msg in reversed(state.values.get("messages", [])):
                if isinstance(msg, AIMessage) and msg.content and not msg.tool_calls:
                    final_reply = str(msg.content)
                    break
        finally:
            await close_checkpointer(agent)
        if not final_reply:
            final_reply = "（模型未给出回答）"
        self._memory.add_message("assistant", final_reply)
        return AgentOutcome(final_reply=final_reply, tool_calls=tool_calls)
```

（注意：`GraphRecursionError` 重新导出仅为调用方（app.py）捕获方便；如 ruff F401 报未使用，删除该行并让 app.py 从 `langgraph.errors` 直接导入。）

- [ ] **Step 5: 运行测试验证通过**

```bash
.venv/Scripts/python -m pytest tests/agent/test_react.py -q
```

Expected: 6 个用例全部 PASS。

- [ ] **Step 6: 单文件检查**

```bash
pyright src/agent/react.py tests/agent/test_react.py
~/.vscode/extensions/charliermarsh.ruff-*/bundled/libs/bin/ruff.exe check src/agent/react.py tests/agent/test_react.py
```

Expected: 0 errors / 0 warnings。若 pyright 报 `tool(...)` 返回类型问题，将 `_as_lc_tool` 返回类型标注为 `Any` 已覆盖；若报 `create_model` 返回值，保持 `type[Any]`。

- [ ] **Step 7: 提交**

```bash
git add src/agent/react.py tests/agent/test_react.py
git commit -m "feat(agent): ReActExecutor 自主循环执行器
create_react_agent 封装：全部工具绑定、流式事件回调、recursion_limit 兜底、
工具错误回注模型、memory 工具/回答写入"
```

---

### Task 4: API 集成（/chat 与 /chat/stream 的 agent 分支）

**Files:**
- Modify: `src/api/app.py`（`_build_agent` 之后新增 `_agent_fallback`；chat 141 行附近、chat_stream 199 行附近）
- Test: `tests/api/test_app.py`

- [ ] **Step 1: 写失败测试（test_app.py）**

在 `make_chat_core` 后新增：

```python
class AgentModeLLM:
    """返回 mode=agent 计划的 LLM"""

    def generate(self, prompt, system=None, **kwargs):
        return json.dumps({
            "goal": "对比分析",
            "complexity": "complex",
            "mode": "agent",
            "steps": [],
        }, ensure_ascii=False)


def make_agent_core():
    """注册 EchoTool + AgentModeLLM + 序列响应的 FakeChatModel"""
    from langchain_core.messages import AIMessage
    from typing import Any, cast

    registry = ToolRegistry()
    registry.register(EchoTool())
    model = FakeChatModel(responses=[
        AIMessage(content="", tool_calls=[
            {"name": "echo", "args": {"text": "对比"}, "id": "call_1"}]),
        AIMessage(content="对比结论", tool_calls=[]),
    ])
    return AgentCore(registry=registry, pipeline=cast(Any, FakePipeline()),
                     index_pipeline=cast(Any, FakeIndexPipeline()),
                     llm=cast(Any, AgentModeLLM()), model=model)
```

`TestChatEndpoint` 类内新增：

```python
    @pytest.mark.asyncio
    async def test_chat_agent_mode_returns_final_reply(self, tmp_path):
        sessions = SessionManager(SessionStore(tmp_path / "s_agent.db"))
        app_agent = create_app(core=make_agent_core(), sessions=sessions)
        async with AsyncClient(transport=ASGITransport(app=app_agent),
                               base_url="http://test") as c:
            resp = await c.post("/api/v1/chat",
                                json={"message": "对比茅台和宁德时代"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["plan"]["mode"] == "agent"
        assert body["response"] == "对比结论"
        tools = body["tool_results"]
        assert len(tools) == 1
        assert tools[0]["tool"] == "echo"
        assert tools[0]["status"] == "done"
        # 最终回答写入会话消息
        msgs = sessions.get_messages(body["session_id"])
        assert msgs is not None
        assert any(m["role"] == "assistant"
                   and m["content"] == "对比结论" for m in msgs)
```

`TestStreamEndpoint` 类内新增：

```python
    @pytest.mark.asyncio
    async def test_stream_agent_mode_emits_tool_events(self, tmp_path):
        sessions = SessionManager(SessionStore(tmp_path / "s_agent2.db"))
        app_agent = create_app(core=make_agent_core(), sessions=sessions)
        transport = ASGITransport(app=app_agent)
        async with (
            AsyncClient(transport=transport, base_url="http://test") as c,
            c.stream("POST", "/api/v1/chat/stream",
                     json={"message": "对比茅台和宁德时代"}) as resp,
        ):
            body = b""
            async for chunk in resp.aiter_bytes():
                body += chunk

        text = body.decode()
        assert '"type": "tool_call"' in text
        assert '"type": "tool_result"' in text
        assert '"type": "text"' in text
        assert "对比结论" in text
        assert '"type": "done"' in text

    @pytest.mark.asyncio
    async def test_stream_agent_mode_without_model_falls_back(self, tmp_path):
        """agent 模式但 model 为 None：降级 plan 单步并正常完成"""
        from typing import Any, cast

        sessions = SessionManager(SessionStore(tmp_path / "s_agent3.db"))
        core = make_agent_core()
        core.model = None
        app_fallback = create_app(core=core, sessions=sessions)
        transport = ASGITransport(app=app_fallback)
        async with (
            AsyncClient(transport=transport, base_url="http://test") as c,
            c.stream("POST", "/api/v1/chat/stream",
                     json={"message": "对比茅台和宁德时代"}) as resp,
        ):
            body = b""
            async for chunk in resp.aiter_bytes():
                body += chunk

        text = body.decode()
        assert '"type": "result"' in text
        assert '"type": "done"' in text
```

- [ ] **Step 2: 运行测试确认失败**

```bash
.venv/Scripts/python -m pytest tests/api/test_app.py -q
```

Expected: 新增 3 个用例 FAIL（`plan.mode == "agent"` 分支不存在，agent 计划按 task 路径执行报错）。

- [ ] **Step 3: 实现 app.py agent 分支**

a) 顶部导入新增（app.py:14-19 区域）：

```python
from agent.react import ReActExecutor
```

b) `_structured_tool_results` 之后新增降级辅助函数：

```python
async def _agent_fallback(executor, memory, goal: str) -> tuple[str, dict, list[dict]]:
    """agent 模式降级：以 plan 单步执行目标，返回与 plan 模式一致的响应结构"""
    from agent.memory import Plan, TaskStep

    plan = Plan(goal=goal, steps=[TaskStep(id="step-1", description=goal)])
    plan = await executor.execute(plan)
    done = sum(1 for s in plan.steps if s.status == TaskStatus.DONE)
    return (
        f"目标: {goal}\n完成: {done}/1 步骤",
        {"goal": goal, "mode": "plan", "steps": [
            {"id": s.id, "description": s.description, "status": s.status.value}
            for s in plan.steps]},
        _structured_tool_results(plan, memory),
    )
```

c) `chat` 端点（app.py:141 附近）`if plan.mode == "chat":` 分支后新增：

```python
            if plan.mode == "agent":
                # agent 模式：自主循环执行；无模型或异常降级 plan 单步
                react = ReActExecutor(registry=core.registry, memory=memory,
                                      model=core.model, session_id=sid,
                                      persist_dir=str(DEFAULT_CHECKPOINT_DIR))
                try:
                    outcome = await react.run(message)
                    tool_results = [{
                        "tool": tc["tool"],
                        "symbol": tc["args"].get("symbol"),
                        "status": "done" if tc["status"] == "success" else "error",
                        "content": tc.get("summary", ""),
                    } for tc in outcome.tool_calls]
                    return JSONResponse({
                        "response": outcome.final_reply,
                        "plan": {"goal": plan.goal, "mode": "agent", "steps": []},
                        "tool_results": tool_results,
                        "session_id": sid,
                    })
                except Exception as e:  # noqa: BLE001 — agent 失败降级 plan 单步
                    logger.warning("agent 模式失败，降级 plan 单步: %s", e)
                    response, plan_payload, tool_results = await _agent_fallback(
                        executor, memory, message)
                    return JSONResponse({
                        "response": response,
                        "plan": plan_payload,
                        "tool_results": tool_results,
                        "session_id": sid,
                    })
```

d) `chat_stream` 端点 `if plan.mode == "chat":` 分支（app.py:199 附近）后新增：

```python
                    if plan.mode == "agent":
                        # agent 模式：plan 事件空步骤（携带 session_id 供前端接管会话），
                        # 实时 thinking/tool_call/tool_result 事件，最终 text 事件收尾
                        await queue.put({"type": "plan", "goal": plan.goal,
                                         "steps": [], "session_id": sid})
                        react = ReActExecutor(
                            registry=core.registry, memory=memory, model=core.model,
                            session_id=sid, persist_dir=str(DEFAULT_CHECKPOINT_DIR))
                        try:
                            outcome = await react.run(message, on_event=queue.put_nowait)
                            await queue.put({"type": "text",
                                             "content": outcome.final_reply})
                        except Exception as e:  # noqa: BLE001 — agent 失败降级 plan 单步
                            logger.warning("agent 模式失败，降级 plan 单步: %s", e)
                            summary, _, tool_results = await _agent_fallback(
                                executor, memory, message)
                            await queue.put({"type": "result",
                                             "summary": summary,
                                             "tool_results": tool_results})
                        await queue.put({"type": "done"})
                        return
```

- [ ] **Step 4: 运行测试验证通过**

```bash
.venv/Scripts/python -m pytest tests/api/test_app.py -q
```

Expected: 全部 PASS（含新增 3 个，既有 30+ 个无回归）。

- [ ] **Step 5: 单文件检查**

```bash
pyright src/api/app.py tests/api/test_app.py
~/.vscode/extensions/charliermarsh.ruff-*/bundled/libs/bin/ruff.exe check src/api/app.py tests/api/test_app.py
```

Expected: 0 errors / 0 warnings（`core.model` 为 object|None，传参 ReActExecutor 已接受任意类型；`core.registry` 闭包捕获后先断言再使用，沿用文件内既有模式）。

- [ ] **Step 6: 提交**

```bash
git add src/api/app.py tests/api/test_app.py
git commit -m "feat(api): /chat 与 /chat/stream 接入 agent 自主循环模式
mode=agent 走 ReActExecutor，SSE 实时工具事件，失败降级 plan 单步"
```

---

### Task 5: 前端实时工具卡片流（chat.js）

**Files:**
- Modify: `src/api/static/js/chat.js`
- Modify: `src/api/static/css/app.css`

- [ ] **Step 1: 新增事件处理器与工具卡片状态（chat.js）**

`sendMessage`（chat.js:121）中，`let myPlan = null;`（139 行）后新增闭包变量：

```js
    // 自主循环当前工具卡片：tool_call 打开"调用中"，tool_result 更新内容
    let myToolCard = null;
```

`handlers`（chat.js:145）中 `plan:` 与 `progress:` 之间插入：

```js
      thinking: (e) => {
        // 模型推理片段追加到占位元素（后端已节流，仅非空片段）
        thinking.textContent += e.content || "";
      },
      tool_call: (e) => {
        if (store.currentSessionId !== streamSid) return;
        myToolCard = toolResultCard({ tool: e.tool, content: "调用中…" });
        agentBox.appendChild(myToolCard);
      },
      tool_result: (e) => {
        if (store.currentSessionId !== streamSid || !myToolCard) return;
        const body = myToolCard.querySelector(".tooltext");
        body.innerHTML = renderMarkdown(String(e.content || ""));
        myToolCard = null;
      },
```

`text:` 处理器（chat.js:202-205）替换为：

```js
      text: (e) => {
        thinking.remove();
        agentBox.appendChild(mdDiv(e.content));
        // agent 模式最终回答写入会话缓存并触发侧边栏刷新
        if (streamSid) {
          (store.sessionMessages[streamSid] = store.sessionMessages[streamSid] || [])
            .push({ role: "assistant", content: e.content || "" });
        }
        bus.dispatchEvent(new Event("chat-done"));
      },
```

- [ ] **Step 2: 样式微调（app.css）**

`src/api/static/css/app.css` 中 `.thinking`（147 行附近）后追加：

```css
/* 自主循环推理片段：与占位共用 .thinking，追加内容后常驻显示 */
.thinking { font-style: italic; }
```

（原 `.thinking` 的 pulse 动画仍在，若想停止动画需同时去掉 `animation`——保留动画可接受，不作为本任务范围。）

- [ ] **Step 3: 静态资源测试确认无回归**

```bash
.venv/Scripts/python -m pytest tests/api/test_static.py -q
```

Expected: PASS（静态文件可访问性不受影响）。

- [ ] **Step 4: 提交**

```bash
git add src/api/static/js/chat.js src/api/static/css/app.css
git commit -m "feat(web): 聊天流支持 thinking/tool_call/tool_result 实时事件"
```

---

### Task 6: 全量回归验证

**Files:** 无（仅验证）

- [ ] **Step 1: 全量测试**

```bash
.venv/Scripts/python -m pytest -q
```

Expected: 全部 PASS（约 3 分钟）。CLI 的 `plan.mode == "chat"` 判断（cli.py:485/524）不受更名影响——mode "plan" 走 executor 路径，行为与旧 "task" 完全一致。

- [ ] **Step 2: 全量类型与 lint**

```bash
pyright
~/.vscode/extensions/charliermarsh.ruff-*/bundled/libs/bin/ruff.exe check .
```

Expected: 0 errors / 0 warnings。

- [ ] **Step 3: 手工浏览器验收（需启动服务）**

```bash
.venv/Scripts/python -m stock_robot web --port 8000   # 或项目既有的启动方式
```

在浏览器验证：
1. 发送探索式问题（如"对比茅台和宁德时代哪个更值得关注"）→ 出现 plan 卡片（目标 + 空步骤）、工具卡片"调用中…"→ 结果内容、最终回答文本
2. 发送"分析一下 600519" → 走 plan 模式，行为与改动前一致
3. 发送"谢谢" → chat 模式直接回复
4. 会话切换后历史恢复正常（agent 模式回答在历史中可见）

若无法启动服务，明确说明"前端未经浏览器验收"。

- [ ] **Step 4: 验收标准核对**

| 验收标准 | 验证方式 |
|----------|----------|
| 双模式回归无破坏 | Task 6 Step 1 全量测试 + Step 3 场景 2/3 |
| 探索式场景达标 | Task 6 Step 3 场景 1（多步工具调用 + 综合回答） |
| 模式判断准确 | Task 1 新增 agent 模式测试 + Step 3 场景 1/2/3 分流正确 |
