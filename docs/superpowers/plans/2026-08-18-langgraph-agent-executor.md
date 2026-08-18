# LangGraph 重构 Agent 执行器 + 闲聊识别 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 LangGraph 状态图重写 Executor 执行循环，实现 LLM 驱动的工具决策（含关键词降级），并为无投研指令的闲聊提供普通 AI 会话。

**Architecture:** Executor 包装一个 LangGraph 状态图（决策 → 执行 → 反馈 → 条件循环），Planner 保留图外并新增闲聊识别（硬编码纯客套快路径 + LLM 顺带分类 `mode` 字段）；agent 层用 LangChain 模型（双轨，自建 LLM 后端不动）；SqliteSaver checkpointer 按 session_id 持久化，同时双写现有 SessionStore。

**Tech Stack:** Python 3.11, langgraph, langgraph-checkpoint-sqlite, langchain-openai, langchain-anthropic, pytest-asyncio（已装）

**Spec:** `docs/superpowers/specs/2026-08-17-langgraph-agent-executor-design.md`

---

## 文件结构

**新建：**
- `src/agent/model_factory.py` — 按 Config 构建 LangChain 聊天模型（openai/claude 双 provider）
- `src/agent/tool_selector.py` — LLM 工具决策（原生 tool calling + 关键词降级 + `_extract_tool_args` 迁移至此）
- `src/agent/graph.py` — LangGraph 状态图（GraphState、节点、checkpointer 工厂）
- `src/agent/chat.py` — ChatResponder（闲聊普通会话回复）
- `tests/agent/test_tool_selector.py`、`tests/agent/test_graph.py`、`tests/agent/test_chat.py`
- `tests/agent/fake_chat_model.py` — FakeChatModel/FakeAIMessage 共享测试桩（tests 下共用模块，避免重复）

**修改：**
- `src/agent/memory.py` — `Plan` 增加 `mode: Literal["task", "chat"] = "task"` 字段
- `src/agent/planner.py` — CHAT_PHRASES 纯客套快路径 + LLM 规划输出 `mode` 字段
- `src/agent/executor.py` — 重写为图包装（保留 `execute()` 签名与语义）
- `src/api/bootstrap.py` — `AgentCore` 增加 `model` 字段，`build_agent_core` 构建 LangChain 模型
- `src/stock_robot/cli.py` — chat 命令的闲聊分支
- `src/api/app.py` — chat / chat_stream 的闲聊分支
- `pyproject.toml` — 新增依赖
- `tests/agent/test_planner.py`、`tests/agent/test_executor.py`、`tests/api/test_app.py` — 适配/新增用例

---

### Task 0: 安装依赖并探针验证 LangGraph/LangChain API

**Files:**
- Modify: `pyproject.toml`（dependencies 追加 4 个包）
- Create: `probe_langgraph.py`（临时探针，验证后删除）

- [ ] **Step 1: 添加依赖到 pyproject.toml**

```toml
    "openai>=1.0",
    "anthropic>=0.30",
    "chromadb>=0.5",
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "langgraph>=0.2",
    "langgraph-checkpoint-sqlite>=2.0",
    "langchain-openai>=0.2",
    "langchain-anthropic>=0.2",
]
```

- [ ] **Step 2: 安装依赖**

Run: `.venv/Scripts/python -m pip install langgraph langgraph-checkpoint-sqlite langchain-openai langchain-anthropic`
Expected: Successfully installed 若干包（输出含 langgraph 与 langchain 相关包名）。若镜像超时改用 `-i https://pypi.tuna.tsinghua.edu.cn/simple`。

- [ ] **Step 3: 写探针脚本验证核心 API**

创建 `probe_langgraph.py`（仓库根目录，pyright 只检查 src/tests，不影响检查）：

```python
"""临时探针：验证 LangGraph/LangChain 关键 API（验证后删除）"""
import os
from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langchain_openai import ChatOpenAI


class State(TypedDict):
    count: int
    items: list


async def node_a(state):
    return {"count": state["count"] + 1, "items": state["items"] + ["a"]}


def route(state):
    return "b" if state["count"] < 3 else END


def node_b(state):
    return {"count": state["count"]}


def main():
    # 1) 同步 invoke + 异步节点 + 条件边
    g = StateGraph(State)
    g.add_node("a", node_a)
    g.add_node("b", node_b)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_conditional_edges("b", route, {"b": "a", END: END})
    graph = g.compile(checkpointer=MemorySaver())
    out = graph.invoke({"count": 0, "items": []},
                       config={"configurable": {"thread_id": "t1"}})
    print("sync-invoke-async-nodes:", out["count"], out["items"])

    # 2) SqliteSaver 持久化 + 恢复
    saver = SqliteSaver.from_conn_string("probe_checkpoints.sqlite")
    graph2 = g.compile(checkpointer=saver)
    cfg = {"configurable": {"thread_id": "t2"}}
    graph2.invoke({"count": 0, "items": []}, config=cfg)
    resume = graph2.invoke({"count": 0, "items": []}, config=cfg)  # 同 thread 恢复
    print("sqlite-saver-resume:", resume["count"])

    # 3) bind_tools 接受 JSON Schema dict
    schemas = [{
        "name": "analyze_stock",
        "description": "分析股票",
        "parameters": {"type": "object",
                       "properties": {"symbol": {"type": "string"}}},
    }]
    model = ChatOpenAI(model="gpt-4o-mini",
                       api_key=os.environ.get("OPENAI_API_KEY", "sk-probe"),
                       timeout=10)
    bound = model.bind_tools(schemas)
    print("bind_tools:", type(bound).__name__)

    saver = None  # noqa: F841 — 释放连接避免 Windows 文件锁


if __name__ == "__main__":
    main()
```

Run: `.venv/Scripts/python probe_langgraph.py`
Expected: 三行输出均正常（`sync-invoke-async-nodes: ...`、`sqlite-saver-resume: ...`、`bind_tools: RunnableBinding`）。若某个 API 不兼容（如 `from_conn_string` 迁移），按最新版本 API 修正探针并记录到 Task 5 的实现里（如 `SqliteSaver(conn=...)`）。

- [ ] **Step 4: 删除探针并提交**

Run: `rm probe_langgraph.py probe_checkpoints.sqlite`

```bash
git add pyproject.toml
git commit -m "chore(依赖): 新增 langgraph / langgraph-checkpoint-sqlite / langchain-openai / langchain-anthropic"
```

---

### Task 1: Plan 增加 mode 字段 + Planner 闲聊识别

**Files:**
- Modify: `src/agent/memory.py:17-31`（Plan dataclass）
- Modify: `src/agent/planner.py`（CHAT_PHRASES、`_is_chat_message`、prompt mode、`_parse_response`）
- Test: `tests/agent/test_memory.py`、`tests/agent/test_planner.py`

- [ ] **Step 1: 写失败测试 — Plan.mode 默认值**

在 `tests/agent/test_memory.py` 的 `TestPlan` 类中追加：

```python
    def test_plan_default_mode_is_task(self):
        plan = Plan(goal="测试", steps=[])
        assert plan.mode == "task"

    def test_plan_chat_mode(self):
        plan = Plan(goal="你好", steps=[], mode="chat")
        assert plan.mode == "chat"
```

Run: `.venv/Scripts/python -m pytest tests/agent/test_memory.py::TestPlan::test_plan_default_mode_is_task -q`
Expected: FAIL（TypeError: 意外的关键字参数 'mode' 或属性不存在）

- [ ] **Step 2: 实现 Plan.mode**

在 `src/agent/memory.py` 修改 `Plan`：

```python
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
```

```python
@dataclass
class Plan:
    goal: str                          # 原始用户意图
    steps: list[TaskStep]
    context_summary: str = ""          # 从 Memory 提取的相关历史摘要
    mode: Literal["task", "chat"] = "task"   # task=拆计划执行；chat=普通会话
```

Run: `.venv/Scripts/python -m pytest tests/agent/test_memory.py::TestPlan -q`
Expected: PASS（含新增 2 例与原有用例）

- [ ] **Step 3: 写失败测试 — Planner 闲聊识别**

在 `tests/agent/test_planner.py` 追加（注意 `from agent.planner import CHAT_PHRASES, SIMPLE_QUERY_PREFIXES, Planner` 更新导入）：

```python
def make_chat_response():
    return json.dumps({
        "goal": "闲聊",
        "complexity": "simple",
        "mode": "chat",
        "steps": [],
    }, ensure_ascii=False)


class TestChatDetection:
    def test_chat_phrases_defined(self):
        assert isinstance(CHAT_PHRASES, set)
        assert len(CHAT_PHRASES) > 0

    def test_pure_politeness_is_chat(self):
        planner = Planner(llm=None, registry=None)
        assert planner._is_chat_message("谢谢") is True
        assert planner._is_chat_message("谢谢你") is True
        assert planner._is_chat_message("你好") is True
        assert planner._is_chat_message("你是谁") is True
        assert planner._is_chat_message("谢谢，辛苦了") is True

    def test_thanks_followed_by_task_is_not_chat(self):
        planner = Planner(llm=None, registry=None)
        assert planner._is_chat_message("谢谢，帮我分析600519") is False

    def test_company_name_query_is_not_chat(self):
        planner = Planner(llm=None, registry=None)
        assert planner._is_chat_message("贵州茅台怎么样") is False
        assert planner._is_chat_message("沪深300走势如何") is False

    def test_long_message_is_not_chat(self):
        planner = Planner(llm=None, registry=None)
        assert planner._is_chat_message("谢谢你的帮助，我接下来想了解新能源行业的整体情况") is False

    def test_plan_chat_message_returns_chat_plan(self, registry, memory):
        planner = Planner(llm=None, registry=registry, memory=memory)
        plan = planner.plan("谢谢")

        assert plan.mode == "chat"
        assert plan.steps == []

    def test_plan_llm_chat_mode_returns_chat_plan(self, registry, memory):
        llm = FakeLLM(fixed_response=make_chat_response())
        planner = Planner(llm=llm, registry=registry, memory=memory)

        plan = planner.plan("随便聊聊")

        assert plan.mode == "chat"
        assert plan.steps == []

    def test_plan_llm_task_mode_returns_task_plan(self, registry, memory):
        llm = FakeLLM(fixed_response=make_multi_step_response())
        planner = Planner(llm=llm, registry=registry, memory=memory)

        plan = planner.plan("分析新能源板块")

        assert plan.mode == "task"
        assert len(plan.steps) == 3
```

Run: `.venv/Scripts/python -m pytest tests/agent/test_planner.py::TestChatDetection -q`
Expected: FAIL（`_is_chat_message` 不存在、mode 缺失）

- [ ] **Step 4: 实现闲聊识别**

`src/agent/planner.py` 修改：

```python
import json
import logging
import re

from agent.memory import Memory, Plan, TaskStep

logger = logging.getLogger(__name__)

SIMPLE_QUERY_PREFIXES = [
    "什么是", "最近", "最新", "当前", "现在", "今天",
    "怎么", "如何", "为什么", "解释",
]

# 纯客套短语（快路径：整条消息由这些短语拼接而成才判定为闲聊；
# 宁可漏判多花一次 LLM 调用，不可误判吞掉业务请求）
CHAT_PHRASES = {
    "谢谢", "谢谢你", "感谢", "辛苦了", "好的", "明白了", "了解",
    "你好", "您好", "嗨", "哈喽", "hello", "hi", "hey",
    "再见", "拜拜", "bye",
    "你是谁", "你能做什么", "你会什么", "介绍一下你自己",
}

_CHAT_PHRASE_PATTERN = re.compile(
    "(?:" + "|".join(re.escape(p) for p in sorted(CHAT_PHRASES, key=len, reverse=True)) + ")+"
)
_PUNCTUATION = "，。！？,．.!?~～、… \t\n"
```

`Planner` 类内新增方法（放在 `_is_simple_query` 之前）：

```python
    def _is_chat_message(self, text: str) -> bool:
        """纯客套快路径：整条消息去掉标点后完全由客套短语拼接而成"""
        stripped = text.strip()
        if len(stripped) > 10:
            return False
        if any(ch.isdigit() for ch in stripped):
            return False
        cleaned = "".join(ch for ch in stripped.lower() if ch not in _PUNCTUATION)
        if not cleaned:
            return False
        return _CHAT_PHRASE_PATTERN.fullmatch(cleaned) is not None
```

`plan()` 方法开头（`_is_simple_query` 判断之前）插入：

```python
        # 硬编码纯客套快路径 —— 闲聊直接 chat 模式，零成本
        if self._is_chat_message(user_input):
            return Plan(goal=user_input.strip(), steps=[], mode="chat",
                        context_summary="闲聊，普通会话")
```

`PLANNER_SYSTEM_PROMPT` 修改（规则与输出格式增加 mode）：

```python
PLANNER_SYSTEM_PROMPT = """你是一个股票投研任务规划器。你的职责是将用户的投资研究问题拆解为有序的执行步骤。

## 规则
1. 先判断问题复杂度：简单查询（单一信息）→ 1步，复杂研究（多维度）→ 多步
2. 每步只描述"做什么"，不指定"用哪个工具"（工具选择由执行器负责）
3. 标注步骤间的依赖关系
4. 复杂问题步骤数不超过5步，简单问题1步
5. mode 判断：涉及任何证券标的（股票代码、公司名如贵州茅台/宁德时代、指数名如
   上证指数/沪深300/中证500、行业、宏观政策、数据查询）或可拆解为多步的投研请求
   → task；仅当与投资研究完全无关（问候、道谢、闲聊、非金融话题）→ chat。
   chat 模式 steps 必须为空数组。

## 可用能力概览
{capabilities}

## 输出格式
严格输出 JSON，不要包含其他文字:
```json
{{
  "goal": "用户目标的简洁概括",
  "complexity": "simple|complex",
  "mode": "task|chat",
  "steps": [
    {{"id": "step-1", "description": "步骤描述"}},
    {{"id": "step-2", "description": "步骤描述", "depends_on": ["step-1"]}}
  ]
}}
```
""".strip()
```

`_parse_response` 修改（`if not steps:` 判断之前插入 chat 分支）：

```python
        mode = data.get("mode", "task")
        if mode == "chat":
            return Plan(goal=data.get("goal", fallback_goal), steps=[],
                        mode="chat")
```

- [ ] **Step 5: 运行全部相关测试**

Run: `.venv/Scripts/python -m pytest tests/agent/test_planner.py tests/agent/test_memory.py -q`
Expected: 全绿（新增 TestChatDetection 全过，原有用例不受影响——FakeLLM 响应无 mode 字段默认 task）

- [ ] **Step 6: 类型检查 + 提交**

Run: `pyright src/agent/planner.py src/agent/memory.py`
Expected: 0 errors

```bash
git add src/agent/memory.py src/agent/planner.py tests/agent/test_memory.py tests/agent/test_planner.py
git commit -m "feat(Agent): 闲聊识别 — Plan.mode 字段与纯客套快路径

无投研指令的闲聊返回 chat 模式计划，不再拆解执行；
LLM 规划输出顺带分类 mode（公司名/指数名查询归 task，为后续功能预留）。
"
```

---

### Task 2: FakeChatModel 测试桩

**Files:**
- Create: `tests/agent/fake_chat_model.py`

- [ ] **Step 1: 创建测试桩**

```python
"""FakeChatModel — 模拟 LangChain ChatModel 的 bind_tools/ainvoke，供测试注入"""


class FakeAIMessage:
    def __init__(self, content: str = "", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class FakeChatModel:
    """bind_tools 返回自身；ainvoke 返回固定 tool_calls 或文本回复"""

    def __init__(self, tool_calls=None, content: str = "测试回复"):
        self._tool_calls = tool_calls or []
        self._content = content
        self.bound_tools = None
        self.calls = []  # 记录每次 ainvoke 的 messages

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages, **kwargs):
        self.calls.append(messages)
        return FakeAIMessage(content=self._content, tool_calls=self._tool_calls)


def make_tool_call(name: str, args: dict | None = None) -> dict:
    return {"name": name, "args": args or {}}
```

- [ ] **Step 2: 提交**

```bash
git add tests/agent/fake_chat_model.py
git commit -m "test(Agent): 新增 FakeChatModel 测试桩"
```

---

### Task 3: ToolSelector — LLM 工具决策 + 关键词降级

**Files:**
- Create: `src/agent/tool_selector.py`
- Create: `tests/agent/test_tool_selector.py`
- Modify: `src/agent/executor.py`（删除 `_extract_tool_args`，它迁移到 tool_selector）
- Modify: `tests/agent/test_executor.py:11-13`（`_extract_tool_args` 导入改到 tool_selector）

- [ ] **Step 1: 写失败测试**

创建 `tests/agent/test_tool_selector.py`：

```python
"""ToolSelector 单元测试 — LLM tool calling 决策与降级阶梯"""
import pytest

from agent.tools import ToolRegistry
from tests.agent.fake_chat_model import FakeChatModel, make_tool_call
from agent.tool_selector import ToolSelector


class FakeTool:
    def __init__(self, name, description=None):
        self.name = name
        self.description = description or f"Tool: {name}"
        self.parameters = {"type": "object", "properties": {}}
        self.tags = ["test"]
        self.source = "pipeline"

    async def execute(self, **kwargs):
        return None


def make_registry():
    reg = ToolRegistry()
    reg.register(FakeTool("analyze_stock", description="分析股票"))
    reg.register(FakeTool("rag_search", description="知识库语义检索"))
    return reg


def make_step(description="分析 000001 的估值"):
    from agent.memory import TaskStep
    return TaskStep(id="s1", description=description)


class TestToolSelector:
    @pytest.mark.asyncio
    async def test_llm_tool_call_used(self):
        model = FakeChatModel(tool_calls=[make_tool_call(
            "analyze_stock", {"symbol": "000001"})])
        selector = ToolSelector(registry=make_registry(), model=model)

        name, args = await selector.select(make_step(), [], [])

        assert name == "analyze_stock"
        assert args == {"symbol": "000001"}
        # 候选工具应绑定给 LLM（不超过 4 个）
        assert len(model.bound_tools) == 2
        assert model.bound_tools[0]["name"] == "analyze_stock"

    @pytest.mark.asyncio
    async def test_llm_empty_tool_calls_falls_back_to_keyword(self):
        model = FakeChatModel(tool_calls=[])
        selector = ToolSelector(registry=make_registry(), model=model)

        name, args = await selector.select(
            make_step("执行 analyze_stock 操作"), [], [])

        assert name == "analyze_stock"

    @pytest.mark.asyncio
    async def test_llm_invalid_tool_name_retries_then_falls_back(self):
        model = FakeChatModel(tool_calls=[make_tool_call("not_exist", {})])
        selector = ToolSelector(registry=make_registry(), model=model)

        name, args = await selector.select(make_step(), [], [])

        # 非法工具名 → 一次强制重试 → 仍非法 → 关键词降级
        assert name == "analyze_stock"
        assert len(model.calls) == 2

    @pytest.mark.asyncio
    async def test_llm_exception_falls_back_to_keyword(self):
        class ExplodingModel(FakeChatModel):
            async def ainvoke(self, messages, **kwargs):
                raise RuntimeError("API 不可用")

        selector = ToolSelector(registry=make_registry(), model=ExplodingModel())

        name, args = await selector.select(make_step("执行 rag_search 检索"), [], [])

        assert name == "rag_search"

    @pytest.mark.asyncio
    async def test_no_model_uses_keyword_fallback(self):
        selector = ToolSelector(registry=make_registry(), model=None)

        name, args = await selector.select(make_step(), [], [])

        assert name == "analyze_stock"

    @pytest.mark.asyncio
    async def test_keyword_fallback_extracts_symbol(self):
        selector = ToolSelector(registry=make_registry(), model=None)

        name, args = await selector.select(
            make_step("分析 000001 的估值"), [], [])

        assert name == "analyze_stock"
        assert args == {"symbol": "000001"}

    @pytest.mark.asyncio
    async def test_no_candidates_returns_none(self):
        selector = ToolSelector(registry=make_registry(), model=None)

        name, args = await selector.select(make_step("完全无关的描述"), [], [])

        assert name is None
        assert args is None

    @pytest.mark.asyncio
    async def test_decision_context_includes_recent_results(self):
        model = FakeChatModel(tool_calls=[make_tool_call(
            "analyze_stock", {"symbol": "000001"})])
        selector = ToolSelector(registry=make_registry(), model=model)
        results = [{"step_id": "s0", "status": "success", "data": {"x": 1}}]

        await selector.select(make_step(), [], results)

        last_messages = model.calls[0]
        # 用户消息中应包含最近工具结果
        assert any("s0" in str(m.get("content", "")) for m in last_messages)
```

Run: `.venv/Scripts/python -m pytest tests/agent/test_tool_selector.py -q`
Expected: FAIL（ModuleNotFoundError: agent.tool_selector）

- [ ] **Step 2: 实现 tool_selector.py**

```python
"""ToolSelector — LLM 工具决策（原生 tool calling）+ 关键词降级"""
import logging
import re

from agent.memory import TaskStep
from agent.tools import ToolRegistry

logger = logging.getLogger(__name__)

_SYMBOL_PATTERN = re.compile(r"\d{6}")

# 仅处理带 symbol 参数的确定性工具的降级参数提取
_SYMBOL_TOOLS = ("analyze_stock", "analyze_index", "get_snapshot")

_MAX_CANDIDATES = 4

TOOL_SELECTION_SYSTEM_PROMPT = """你是股票投研 Agent 的执行器。给定当前步骤、已完成步骤的结果和候选工具，选择最合适的工具并生成参数。

## 规则
1. 必须从候选工具中选择一个
2. 参数严格按工具 schema 生成，缺失的必填参数用合理默认值（如 top_k=5）
3. 只选择工具，不要执行，不要解释"""


def _extract_tool_args(tool_name: str, description: str) -> dict:
    """从步骤描述提取工具参数；仅处理带 symbol 参数的确定性工具"""
    if tool_name in _SYMBOL_TOOLS:
        m = _SYMBOL_PATTERN.search(description)
        if m:
            return {"symbol": m.group(0)}
    return {}


class ToolSelector:
    """步骤 → (tool_name, tool_args)；无法决策返回 (None, None)

    降级阶梯: LLM tool calling → 关键词匹配（ToolRegistry.match）→ None
    """

    def __init__(self, registry: ToolRegistry, model=None):
        self._registry = registry
        self._model = model

    async def select(self, step: TaskStep, decision_history: list[dict],
                     tool_results: list[dict]) -> tuple[str | None, dict | None]:
        """决策一步的工具与参数；返回 (None, None) 表示无法决策"""
        if self._model is None:
            return self._keyword_fallback(step)
        try:
            chosen = await self._llm_select(step, tool_results)
            if chosen is not None:
                return chosen
        except Exception as e:  # noqa: BLE001 — LLM 边界异常统一降级
            logger.warning("LLM 工具决策失败，降级关键词匹配: %s", e)
        return self._keyword_fallback(step)

    async def _llm_select(self, step: TaskStep,
                          tool_results: list[dict]) -> tuple[str, dict] | None:
        candidates = self._registry.match(step.description)[:_MAX_CANDIDATES]
        if not candidates:
            return None

        bound = self._model.bind_tools([
            {"name": t.name, "description": t.description,
             "parameters": t.parameters}
            for t in candidates
        ])
        messages = self._build_messages(step, candidates, tool_results)
        response = await bound.ainvoke(messages)

        valid_names = {t.name for t in candidates}
        for tc in response.tool_calls:
            if tc.get("name") in valid_names:
                return tc["name"], tc.get("args") or {}

        # 一次强制重试：明确候选范围
        messages = messages + [
            {"role": "assistant", "content": "（未选择合法工具）"},
            {"role": "user", "content": "只能从候选工具中选择一个: "
                                        + ", ".join(sorted(valid_names))},
        ]
        response = await bound.ainvoke(messages)
        for tc in response.tool_calls:
            if tc.get("name") in valid_names:
                return tc["name"], tc.get("args") or {}
        return None

    def _build_messages(self, step: TaskStep, candidates, tool_results) -> list[dict]:
        parts = [f"## 当前步骤\n{step.description}"]
        if tool_results:
            recent = tool_results[-3:]
            lines = []
            for r in recent:
                lines.append(f"  [{r.get('step_id', '')}] {r.get('status', '')}: "
                             f"{r.get('data') or r.get('error')}")
            parts.append("## 最近工具结果\n" + "\n".join(lines))
        return [
            {"role": "system", "content": TOOL_SELECTION_SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(parts)},
        ]

    def _keyword_fallback(self, step: TaskStep) -> tuple[str | None, dict | None]:
        candidates = self._registry.match(step.description)
        if not candidates:
            return None, None
        tool = candidates[0]
        return tool.name, _extract_tool_args(tool.name, step.description)
```

- [ ] **Step 3: 迁移 `_extract_tool_args`**

`src/agent/executor.py` 删除 `_SYMBOL_PATTERN` 与 `_extract_tool_args` 定义及 `import re`；`tests/agent/test_executor.py:11-13` 的导入改为：

```python
from agent.tool_selector import _extract_tool_args
```

Run: `.venv/Scripts/python -m pytest tests/agent/test_tool_selector.py tests/agent/test_executor.py -q`
Expected: 全绿（test_tool_selector 新增用例通过；test_executor 现有用例不受影响——此时 Executor 尚未重写，行为不变）

- [ ] **Step 4: 类型检查 + 提交**

Run: `pyright src/agent/tool_selector.py src/agent/executor.py tests/agent/test_tool_selector.py`
Expected: 0 errors

```bash
git add src/agent/tool_selector.py src/agent/executor.py tests/agent/test_tool_selector.py tests/agent/test_executor.py
git commit -m "feat(Agent): ToolSelector — LLM 工具决策与关键词降级

原生 tool calling 选择工具并生成完整参数（含 symbol/top_k 等）；
非法工具名强制重试一次；LLM 异常/不可用时降级 ToolRegistry 关键词匹配。
"
```

---

### Task 4: 模型工厂 + AgentCore.model

**Files:**
- Create: `src/agent/model_factory.py`
- Modify: `src/api/bootstrap.py:24-30,125-126`（AgentCore 加 model 字段、build_agent_core 构建模型）
- Create: `tests/agent/test_model_factory.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/agent/test_model_factory.py`：

```python
"""model_factory 单元测试"""
from agent.model_factory import create_chat_model


class FakeConfig:
    def __init__(self, data):
        self._data = data

    def get(self, key, default=None):
        node = self._data
        for k in key.split("."):
            if isinstance(node, dict) and k in node:
                node = node[k]
            else:
                return default
        return node


def test_no_api_key_returns_none():
    config = FakeConfig({"llm": {"provider": "openai", "api_key": ""}})
    assert create_chat_model(config) is None


def test_unknown_provider_returns_none():
    config = FakeConfig({"llm": {"provider": "unknown", "api_key": "sk-x"}})
    assert create_chat_model(config) is None


def test_openai_provider_returns_chat_model():
    config = FakeConfig({"llm": {
        "provider": "openai", "api_key": "sk-x", "model": "gpt-4o",
        "base_url": "", "temperature": 0.3, "timeout_seconds": 60,
    }})
    model = create_chat_model(config)
    assert model is not None
    assert model.model_name == "gpt-4o"
```

Run: `.venv/Scripts/python -m pytest tests/agent/test_model_factory.py -q`
Expected: FAIL（ModuleNotFoundError: agent.model_factory）

- [ ] **Step 2: 实现 model_factory.py**

```python
"""LangChain 聊天模型工厂 — agent 层 tool calling 与闲聊回复共用"""
import logging

logger = logging.getLogger(__name__)


def create_chat_model(config) -> object | None:
    """按配置构建 LangChain 聊天模型；未配置 api_key 或初始化失败返回 None

    与 llm 后端（build_llm）共用 llm.* 配置键，但构建独立的 LangChain 模型。
    """
    provider = config.get("llm.provider", "openai")
    api_key = config.get("llm.api_key", "")
    if not api_key:
        return None
    base_url = config.get("llm.base_url", "") or None
    model = config.get("llm.model", "gpt-4o")
    temperature = config.get("llm.temperature", 0.3)
    timeout = config.get("llm.timeout_seconds", 60)

    try:
        if provider == "openai":
            from langchain_core.utils.secret_str import SecretStr
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(model=model, api_key=SecretStr(api_key),
                              base_url=base_url, temperature=temperature,
                              timeout=timeout)
        elif provider == "claude":
            from langchain_core.utils.secret_str import SecretStr
            from langchain_anthropic import ChatAnthropic
            return ChatAnthropic(model=model, api_key=SecretStr(api_key),
                                 base_url=base_url, temperature=temperature,
                                 timeout=timeout)
        logger.warning("未知 LLM provider: %s，LangChain 模型不可用", provider)
    except Exception as e:  # noqa: BLE001 — SDK 初始化失败降级为无模型
        logger.warning("LangChain 模型初始化失败: %s", e)
    return None
```

- [ ] **Step 3: AgentCore 增加 model 字段**

`src/api/bootstrap.py`：

```python
@dataclass
class AgentCore:
    """Agent 运行所需的核心依赖集合"""
    registry: ToolRegistry
    pipeline: "Pipeline"          # 真实 Pipeline（run(symbol, name, market) 接口）
    index_pipeline: "IndexPipeline"
    llm: LLMBackend | None = None
    model: object | None = None   # LangChain 聊天模型（agent 层 tool calling / 闲聊）
```

`build_agent_core` 末尾：

```python
    from agent.model_factory import create_chat_model

    return AgentCore(registry=registry, pipeline=pipeline,
                     index_pipeline=index_pipeline, llm=llm,
                     model=create_chat_model(config))
```

Run: `.venv/Scripts/python -m pytest tests/agent/test_model_factory.py tests/api/test_bootstrap.py -q`
Expected: 全绿

- [ ] **Step 4: 类型检查 + 提交**

Run: `pyright src/agent/model_factory.py src/api/bootstrap.py tests/agent/test_model_factory.py`
Expected: 0 errors（若 langchain_openai 导入报 missing stub，确认包已装且 venv 路径正确）

```bash
git add src/agent/model_factory.py src/api/bootstrap.py tests/agent/test_model_factory.py
git commit -m "feat(Agent): LangChain 模型工厂与 AgentCore.model

按 llm.* 配置构建 ChatOpenAI/ChatAnthropic，与自建 LLM 后端双轨并存。
"
```

---

### Task 5: ChatResponder — 闲聊普通会话

**Files:**
- Create: `src/agent/chat.py`
- Create: `tests/agent/test_chat.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/agent/test_chat.py`：

```python
"""ChatResponder 单元测试"""
import pytest

from agent.chat import ChatResponder, FALLBACK_REPLY
from agent.memory import Memory
from tests.agent.fake_chat_model import FakeChatModel


class TestChatResponder:
    @pytest.mark.asyncio
    async def test_reply_returns_model_content(self):
        model = FakeChatModel(content="你好呀！有什么可以帮你？")
        responder = ChatResponder(model=model)

        reply = await responder.reply("你好", Memory())

        assert reply == "你好呀！有什么可以帮你？"

    @pytest.mark.asyncio
    async def test_reply_includes_conversation_history(self):
        model = FakeChatModel(content="继续")
        responder = ChatResponder(model=model)
        memory = Memory()
        memory.add_message("user", "昨天问过的问题")
        memory.add_message("assistant", "昨天的回答")

        await responder.reply("接着聊", memory)

        last_messages = model.calls[0]
        contents = [str(m.get("content", "")) for m in last_messages]
        assert any("昨天问过的问题" in c for c in contents)
        assert any("昨天的回答" in c for c in contents)
        assert last_messages[0]["role"] == "system"

    @pytest.mark.asyncio
    async def test_no_model_returns_fallback(self):
        responder = ChatResponder(model=None)

        reply = await responder.reply("你好", Memory())

        assert reply == FALLBACK_REPLY

    @pytest.mark.asyncio
    async def test_model_exception_returns_fallback(self):
        class ExplodingModel(FakeChatModel):
            async def ainvoke(self, messages, **kwargs):
                raise RuntimeError("API 不可用")

        responder = ChatResponder(model=ExplodingModel())

        reply = await responder.reply("你好", Memory())

        assert reply == FALLBACK_REPLY
```

Run: `.venv/Scripts/python -m pytest tests/agent/test_chat.py -q`
Expected: FAIL（ModuleNotFoundError: agent.chat）

- [ ] **Step 2: 实现 chat.py**

```python
"""ChatResponder — 闲聊的普通 AI 会话回复（LangChain 模型，无工具绑定）"""
import logging

from agent.memory import Memory

logger = logging.getLogger(__name__)

FALLBACK_REPLY = ("这个问题我暂时无法回答。可以试试让我分析某只股票"
                  "（如 600519）或查询指数（如上证指数）。")

CHAT_SYSTEM_PROMPT = """你是一个友好、专业的股票投研助手。
当用户闲聊（问候、道谢、日常话题）时，自然地进行普通对话；
当用户提出投研相关问题时，简短回答并建议使用分析功能。"""


class ChatResponder:
    """普通会话回复 — 保持多轮上下文，失败降级为固定提示"""

    def __init__(self, model=None):
        self._model = model

    async def reply(self, user_input: str, memory: Memory) -> str:
        if self._model is None:
            return FALLBACK_REPLY
        try:
            messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
            for msg in memory.get_context_window(n=10):
                role = "assistant" if msg["role"] == "assistant" else "user"
                messages.append({"role": role, "content": msg["content"]})
            messages.append({"role": "user", "content": user_input})

            response = await self._model.ainvoke(messages)
            content = getattr(response, "content", "")
            return content if isinstance(content, str) else str(content)
        except Exception as e:  # noqa: BLE001 — LLM 边界异常降级固定提示
            logger.error("闲聊回复失败: %s", e)
            return FALLBACK_REPLY
```

- [ ] **Step 3: 运行测试 + 类型检查 + 提交**

Run: `.venv/Scripts/python -m pytest tests/agent/test_chat.py -q && pyright src/agent/chat.py tests/agent/test_chat.py`
Expected: 全绿 + 0 errors

```bash
git add src/agent/chat.py tests/agent/test_chat.py
git commit -m "feat(Agent): ChatResponder — 闲聊普通会话回复

LangChain 模型无工具绑定对话，携带 Memory 上下文保持多轮；
模型不可用或异常时降级固定提示语。
"
```

---

### Task 6: LangGraph 执行图

**Files:**
- Create: `src/agent/graph.py`
- Create: `tests/agent/test_graph.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/agent/test_graph.py`：

```python
"""LangGraph 执行图单元测试（真实跑图，仅 mock LLM 模型）"""
import pytest

from agent.graph import build_execution_graph
from agent.memory import Memory, Plan, TaskStatus, TaskStep
from agent.tools import ToolRegistry, ToolResult
from tests.agent.fake_chat_model import FakeChatModel, make_tool_call


class FakeTool:
    def __init__(self, name, description=None, return_data=None, should_fail=False):
        self.name = name
        self.description = description or f"Tool: {name}"
        self.parameters = {"type": "object", "properties": {}}
        self.tags = ["test"]
        self.source = "pipeline"
        self._return = return_data
        self._should_fail = should_fail
        self.execute_calls = []

    async def execute(self, **kwargs):
        self.execute_calls.append(kwargs)
        if self._should_fail:
            return ToolResult(status="error", error=f"{self.name} 执行失败")
        return ToolResult(status="success", data=self._return)


def make_registry():
    reg = ToolRegistry()
    reg.register(FakeTool("tool_a", return_data="result_a"))
    reg.register(FakeTool("tool_b", return_data="result_b"))
    return reg


def plan_to_state(plan, session_id="s1"):
    from agent.graph import plan_to_state
    return plan_to_state(plan, session_id)


class TestExecutionGraph:
    @pytest.mark.asyncio
    async def test_single_step_with_explicit_tool(self):
        reg = make_registry()
        memory = Memory()
        model = FakeChatModel(tool_calls=[make_tool_call("tool_a")])
        graph = build_execution_graph(reg, memory, model)
        plan = Plan(goal="测试", steps=[
            TaskStep(id="s1", description="执行操作", tool_name="tool_a",
                     tool_args={})])

        state = await graph.ainvoke(
            plan_to_state(plan),
            config={"configurable": {"thread_id": "t1"}})

        assert state["done_ids"] == ["s1"]
        assert state["tool_results"][0]["status"] == "success"
        assert memory.messages[-1]["role"] == "tool"

    @pytest.mark.asyncio
    async def test_multi_step_respects_dependencies(self):
        reg = make_registry()
        memory = Memory()
        graph = build_execution_graph(reg, memory, model=None)
        plan = Plan(goal="测试", steps=[
            TaskStep(id="s1", description="第一步", tool_name="tool_a", tool_args={}),
            TaskStep(id="s2", description="第二步", tool_name="tool_b", tool_args={},
                     depends_on=["s1"]),
        ])

        state = await graph.ainvoke(
            plan_to_state(plan),
            config={"configurable": {"thread_id": "t2"}})

        assert state["done_ids"] == ["s1", "s2"]
        assert state["pending_ids"] == []

    @pytest.mark.asyncio
    async def test_failed_step_cascades_skipped(self):
        reg = make_registry()
        reg.register(FakeTool("fail_tool", should_fail=True))
        memory = Memory()
        graph = build_execution_graph(reg, memory, model=None)
        plan = Plan(goal="测试", steps=[
            TaskStep(id="s1", description="会失败", tool_name="fail_tool", tool_args={}),
            TaskStep(id="s2", description="依赖s1", tool_name="tool_a", tool_args={},
                     depends_on=["s1"]),
            TaskStep(id="s3", description="独立", tool_name="tool_b", tool_args={}),
        ])

        state = await graph.ainvoke(
            plan_to_state(plan),
            config={"configurable": {"thread_id": "t3"}})

        assert state["failed_ids"] == ["s1"]
        assert state["skipped_ids"] == ["s2"]
        assert state["done_ids"] == ["s3"]

    @pytest.mark.asyncio
    async def test_llm_decision_fills_tool_name_and_args(self):
        reg = make_registry()
        memory = Memory()
        model = FakeChatModel(tool_calls=[make_tool_call("tool_b", {"x": 1})])
        graph = build_execution_graph(reg, memory, model)
        plan = Plan(goal="测试", steps=[
            TaskStep(id="s1", description="执行 tool_b 相关操作")])

        state = await graph.ainvoke(
            plan_to_state(plan),
            config={"configurable": {"thread_id": "t4"}})

        assert state["steps"][0]["tool_name"] == "tool_b"
        assert state["steps"][0]["tool_args"] == {"x": 1}
        assert state["steps"][0]["status"] == "done"
        # 决策历史记录 LLM 来源
        assert state["decision_history"][0]["chosen_tool"] == "tool_b"
        assert state["decision_history"][0]["reason"] == "llm"

    @pytest.mark.asyncio
    async def test_checkpointer_resumes_same_thread(self):
        reg = make_registry()
        memory = Memory()
        graph = build_execution_graph(reg, memory, model=None)
        plan = Plan(goal="测试", steps=[
            TaskStep(id="s1", description="第一步", tool_name="tool_a", tool_args={}),
            TaskStep(id="s2", description="第二步", tool_name="tool_b", tool_args={},
                     depends_on=["s1"]),
        ])
        cfg = {"configurable": {"thread_id": "t5"}}

        # 第一次只执行第一个可执行步骤：把 s2 依赖改成未来 id 让 s1 先入 pending？
        # 直接跑完整计划，验证同一 thread 二次 invoke 状态延续
        state1 = await graph.ainvoke(plan_to_state(plan), config=cfg)
        state2 = await graph.ainvoke(
            {"goal": "测试", "steps": [
                {"id": "s1", "description": "第一步", "tool_name": "tool_a",
                 "tool_args": {}, "status": "done", "depends_on": []},
                {"id": "s2", "description": "第二步", "tool_name": "tool_b",
                 "tool_args": {}, "status": "pending", "depends_on": ["s1"]},
            ], "pending_ids": ["s2"], "done_ids": ["s1"], "failed_ids": [],
             "skipped_ids": [], "decision_history": [], "tool_results": [],
             "messages": []}, config=cfg)

        # 同 thread 恢复：s2 执行完成，s1 状态不被覆盖
        assert state2["done_ids"] == ["s1", "s2"]
```

Run: `.venv/Scripts/python -m pytest tests/agent/test_graph.py -q`
Expected: FAIL（ModuleNotFoundError: agent.graph）

- [ ] **Step 2: 实现 graph.py**

```python
"""LangGraph 执行图 — 决策→执行→反馈条件循环，带 checkpointer 持久化"""
import logging
from pathlib import Path
from typing import Any, TypedDict

from agent.memory import Memory, Plan, TaskStatus, TaskStep
from agent.tools import ToolRegistry
from agent.tool_selector import ToolSelector

logger = logging.getLogger(__name__)

DEFAULT_CHECKPOINT_DIR = Path.home() / ".stock_robot" / "langgraph_checkpoints.sqlite"

_TERMINAL = ("done", "failed", "skipped")


class GraphState(TypedDict):
    goal: str
    steps: list[dict]            # [{id, description, tool_name, tool_args, status, depends_on}]
    pending_ids: list[str]
    done_ids: list[str]
    failed_ids: list[str]
    skipped_ids: list[str]
    decision_history: list[dict]
    tool_results: list[dict]
    messages: list[dict]         # 决策上下文消息（跨步累积）


def plan_to_state(plan: Plan, session_id: str = "") -> GraphState:
    return {
        "goal": plan.goal,
        "steps": [{
            "id": s.id, "description": s.description,
            "tool_name": s.tool_name, "tool_args": s.tool_args,
            "status": s.status.value, "depends_on": list(s.depends_on),
        } for s in plan.steps],
        "pending_ids": [s.id for s in plan.get_pending_steps()],
        "done_ids": [], "failed_ids": [], "skipped_ids": [],
        "decision_history": [], "tool_results": [],
        "messages": [],
    }


def state_to_plan(state: GraphState, plan: Plan) -> Plan:
    """把图状态写回 Plan（步骤状态/工具选择），保持原步骤对象顺序"""
    by_id = {s["id"]: s for s in state["steps"]}
    for step in plan.steps:
        snap = by_id.get(step.id)
        if snap is None:
            continue
        step.tool_name = snap.get("tool_name")
        step.tool_args = snap.get("tool_args")
        step.status = TaskStatus(snap.get("status", "pending"))
    return plan


def create_checkpointer(persist_dir: str | None) -> Any:
    """SqliteSaver 持久化；初始化失败降级 MemorySaver

    langgraph-checkpoint-sqlite 3.x 中 from_conn_string 是上下文管理器，
    不能直接作为 saver 返回；改为自建 sqlite3 连接传入 SqliteSaver(conn)，
    连接保持打开直至 Executor/图被回收。check_same_thread=False 供 ainvoke
    （事件循环线程）访问。
    """
    if persist_dir is None:
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()
    try:
        import sqlite3

        from langgraph.checkpoint.sqlite import SqliteSaver
        conn = sqlite3.connect(str(persist_dir), check_same_thread=False)
        return SqliteSaver(conn)
    except Exception as e:  # noqa: BLE001 — checkpointer 失败降级内存
        logger.warning("SqliteSaver 初始化失败 (%s)，降级 MemorySaver", e)
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()


def _step_by_id(state: GraphState, step_id: str) -> dict | None:
    for s in state["steps"]:
        if s["id"] == step_id:
            return s
    return None


def _recompute_pending(state: GraphState) -> list[str]:
    """重算可执行步骤：pending 且依赖全部终态（done/skipped）"""
    terminal = set(state["done_ids"]) | set(state["skipped_ids"])
    ready = []
    for s in state["steps"]:
        if s["status"] != "pending":
            continue
        if all(dep in terminal for dep in s.get("depends_on", [])):
            ready.append(s["id"])
    return ready


def _cascade_skip(state: GraphState, failed_id: str) -> None:
    """失败步骤的依赖者级联标记 skipped（递归）"""
    for s in state["steps"]:
        if s["status"] != "pending":
            continue
        if failed_id in s.get("depends_on", []):
            s["status"] = "skipped"
            state["skipped_ids"].append(s["id"])
            _cascade_skip(state, s["id"])


def build_execution_graph(registry: ToolRegistry, memory: Memory, model=None,
                          persist_dir: str | None = None) -> Any:
    """构建 LangGraph 执行图（决策→执行→反馈循环）

    persist_dir=None 用内存 checkpointer（测试/无会话场景）。
    """
    from langgraph.graph import END, START, StateGraph

    selector = ToolSelector(registry, model)

    async def decide_node(state: GraphState) -> dict:
        if not state["pending_ids"]:
            return {}
        step = _step_by_id(state, state["pending_ids"][0])
        assert step is not None

        chosen_tool = step.get("tool_name")
        chosen_args = step.get("tool_args")
        reason = "plan"
        if chosen_tool is None:
            chosen_tool, chosen_args = await selector.select(
                TaskStep(id=step["id"], description=step["description"]),
                state["decision_history"], state["tool_results"])
            if chosen_tool is None:
                step["status"] = "failed"
                state["failed_ids"].append(step["id"])
                return {"steps": state["steps"], "failed_ids": state["failed_ids"]}
            reason = "llm"
        step["tool_name"] = chosen_tool
        step["tool_args"] = chosen_args or {}
        state["decision_history"].append({
            "step_id": step["id"], "chosen_tool": chosen_tool, "reason": reason})
        return {"steps": state["steps"],
                "decision_history": state["decision_history"]}

    async def execute_node(state: GraphState) -> dict:
        if not state["pending_ids"]:
            return {}
        step = _step_by_id(state, state["pending_ids"][0])
        assert step is not None and step.get("tool_name")
        tool = registry.get(step["tool_name"])
        result = await _safe_execute(tool, step.get("tool_args") or {})
        state["tool_results"].append({
            "step_id": step["id"], "status": result.status,
            "data": result.data, "error": result.error,
        })
        return {"tool_results": state["tool_results"]}

    def feedback_node(state: GraphState) -> dict:
        if not state["pending_ids"]:
            return {}
        step_id = state["pending_ids"][0]
        step = _step_by_id(state, step_id)
        assert step is not None
        result = next((r for r in state["tool_results"]
                       if r["step_id"] == step_id), None)
        memory.add_message("system", f"执行: {step['description']}")
        if result is None or result["status"] == "error":
            step["status"] = "failed"
            state["failed_ids"].append(step_id)
            memory.add_message("system",
                               f"步骤 {step_id} 失败: {result and result['error']}")
            _cascade_skip(state, step_id)
        else:
            step["status"] = "done"
            state["done_ids"].append(step_id)
            memory.add_message(
                "tool", f"[{step.get('tool_name')}] {result['status']}: "
                        f"{result['data']}")
        state["pending_ids"] = _recompute_pending(state)
        return {"steps": state["steps"], "pending_ids": state["pending_ids"],
                "done_ids": state["done_ids"], "failed_ids": state["failed_ids"],
                "skipped_ids": state["skipped_ids"]}

    def route(state: GraphState) -> str:
        return "decide" if state["pending_ids"] else END

    builder = StateGraph(GraphState)
    builder.add_node("decide", decide_node)
    builder.add_node("execute", execute_node)
    builder.add_node("feedback", feedback_node)
    builder.add_edge(START, "decide")
    builder.add_edge("decide", "execute")
    builder.add_edge("execute", "feedback")
    builder.add_conditional_edges("feedback", route,
                                  {"decide": "decide", END: END})
    return builder.compile(checkpointer=create_checkpointer(persist_dir))


async def _safe_execute(tool, kwargs: dict) -> Any:
    """工具执行隔离：异常转为 error 结果"""
    if tool is None:
        from agent.tools import ToolResult
        return ToolResult(status="error", error="工具不存在")
    try:
        return await tool.execute(**kwargs)
    except Exception as e:  # noqa: BLE001 — 工具执行隔离，失败以 ToolResult 返回
        from agent.tools import ToolResult
        logger.error(f"工具 {tool.name} 执行异常: {e}")
        return ToolResult(status="error", error=str(e),
                          metadata={"source": getattr(tool, "source", "unknown")})
```

- [ ] **Step 3: 运行测试**

Run: `.venv/Scripts/python -m pytest tests/agent/test_graph.py -q`
Expected: 全绿。

**探针已验证（Task 0，langgraph 1.2.11）：异步节点必须用 `ainvoke`，sync `invoke` 不再桥接。** 若 `ainvoke` + `SqliteSaver` 组合报异步不支持错误，改用 `AsyncSqliteSaver`：

```python
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
# create_checkpointer 中：saver = await AsyncSqliteSaver.from_conn_string(str(persist_dir))
# 或 sqlite3.connect + AsyncSqliteSaver(conn, autocommit=True) 后 await saver.setup()
```

（此时代码路径需适配 async 生命周期，测试与 Executor 仍统一用 `ainvoke`。）

- [ ] **Step 4: 类型检查 + 提交**

Run: `pyright src/agent/graph.py tests/agent/test_graph.py`
Expected: 0 errors

```bash
git add src/agent/graph.py tests/agent/test_graph.py
git commit -m "feat(Agent): LangGraph 执行图 — 决策/执行/反馈条件循环

状态图重写执行循环：LLM 决策工具、结果反馈跨步累积、失败级联跳过；
SqliteSaver 按 thread_id 持久化，失败降级 MemorySaver。
"
```

---

### Task 7: Executor 重写为图包装

**Files:**
- Modify: `src/agent/executor.py`（整文件重写）
- Test: `tests/agent/test_executor.py`（适配构造参数）

- [ ] **Step 1: 写失败测试 — 新构造参数**

在 `tests/agent/test_executor.py` 的 `TestExecutor` 追加（FakeChatModel 导入）：

```python
    @pytest.mark.asyncio
    async def test_execute_with_model_uses_llm_decision(self, registry, memory):
        from tests.agent.fake_chat_model import FakeChatModel, make_tool_call
        model = FakeChatModel(tool_calls=[make_tool_call("tool_b", {"x": 1})])
        plan = self.make_plan()
        plan.steps[0].description = "执行 tool_b 相关操作"
        plan.steps[0].tool_name = None
        executor = Executor(registry=registry, memory=memory, model=model)

        updated = await executor.execute(plan)

        assert updated.steps[0].tool_name == "tool_b"
        assert updated.steps[0].tool_args == {"x": 1}
        assert updated.steps[0].status == TaskStatus.DONE

    @pytest.mark.asyncio
    async def test_execute_without_model_falls_back_to_keyword(self, registry, memory):
        plan = self.make_plan()
        plan.steps[0].description = "执行 tool_a 操作"
        plan.steps[0].tool_name = None
        executor = Executor(registry=registry, memory=memory)

        updated = await executor.execute(plan)

        assert updated.steps[0].tool_name == "tool_a"
        assert updated.steps[0].status == TaskStatus.DONE
```

Run: `.venv/Scripts/python -m pytest tests/agent/test_executor.py -q`
Expected: FAIL（Executor 不接受 model 参数——尚未重写）

- [ ] **Step 2: 重写 executor.py**

```python
"""Executor — LangGraph 执行图包装，保留逐步执行、失败隔离语义"""
import logging
from collections.abc import Callable

from agent.graph import DEFAULT_CHECKPOINT_DIR, build_execution_graph, \
    plan_to_state, state_to_plan
from agent.memory import Memory, Plan
from agent.tools import ToolRegistry

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, int, int, str], None] | None


class Executor:
    """逐步执行 Plan 中的 TaskStep（由 LangGraph 状态图驱动）

    工具决策：LLM tool calling（model 注入时）→ ToolRegistry 关键词匹配降级。
    失败隔离：单步失败 → 级联 skip 依赖步骤，其余继续。
    """

    def __init__(self, registry: ToolRegistry, memory: Memory, model=None,
                 session_id: str = "", persist_dir: str | None = None):
        self._registry = registry
        self._memory = memory
        self._model = model
        self._session_id = session_id
        # persist_dir=None 时用内存 checkpointer（测试/无会话场景）
        self._persist_dir = persist_dir
        self._graph = None
        self._on_progress: ProgressCallback = None

    async def execute(self, plan: Plan,
                      on_progress: ProgressCallback = None) -> Plan:
        """执行 Plan，返回更新后的 Plan（含步骤状态和结果）"""
        self._on_progress = on_progress
        self._memory.add_plan(plan)
        self._memory.add_message("system", f"开始执行计划: {plan.goal}")

        if self._graph is None:
            self._graph = build_execution_graph(
                self._registry, self._memory, self._model, self._persist_dir,
                on_progress=self._on_progress)

        state = plan_to_state(plan, self._session_id)
        from langchain_core.runnables import RunnableConfig
        config: RunnableConfig = {
            "configurable": {"thread_id": self._session_id or "cli"}}
        try:
            await self._graph.ainvoke(state, config)  # 异步节点须用 ainvoke
        finally:
            self._on_progress = None

        state_to_plan(state, plan)
        self._memory.add_message("system", f"计划执行完成: {plan.goal}")
        return plan
```

**说明：** 图节点内部负责 per-step 的 `memory.add_message("system"/"tool", ...)` 与 `on_progress("execute", ...)` 回调（feedback_node 中触发）。为最小化改动，`on_progress` 在 `execute()` 前通过 `self._on_progress` 注入并在图节点闭包中读取：

在 `graph.py` 的 `build_execution_graph` 签名后追加闭包访问（修改 Task 6 代码）：

```python
def build_execution_graph(registry: ToolRegistry, memory: Memory, model=None,
                          persist_dir: str | None = None,
                          on_progress: Callable[[str, int, int, str], None] | None = None) -> Any:
```

并在 `feedback_node` 中调用进度回调（在 `memory.add_message("system", f"执行: ...")` 之后）：

```python
        if on_progress:
            idx = next((i for i, s in enumerate(state["steps"])
                        if s["id"] == step_id), 0) + 1
            on_progress("execute", idx, len(state["steps"]), step["description"])
```

（graph.py 顶部 `from collections.abc import Callable` 导入。）

`Executor.execute` 传入进度回调：

```python
        if self._graph is None:
            self._graph = build_execution_graph(
                self._registry, self._memory, self._model, self._persist_dir,
                on_progress=self._on_progress)
```

- [ ] **Step 3: 运行现有测试适配**

Run: `.venv/Scripts/python -m pytest tests/agent/test_executor.py tests/agent/test_graph.py -q`
Expected: 全绿（现有 10 个用例 + 新增 2 个）。若有失败（如 `test_execute_with_missing_tool_marks_failed`——`_safe_execute` 对 None tool 返回 error 结果），核对语义后修正断言或实现。

- [ ] **Step 4: 全量 agent 测试 + 类型检查 + 提交**

Run: `.venv/Scripts/python -m pytest tests/agent -q && pyright src/agent/executor.py src/agent/graph.py tests/agent/test_executor.py tests/agent/test_graph.py`
Expected: 全绿 + 0 errors

```bash
git add src/agent/executor.py src/agent/graph.py tests/agent/test_executor.py tests/agent/test_graph.py
git commit -m "refactor(Agent): Executor 重写为 LangGraph 图包装

execute() 签名与语义不变，内部改为图执行；
LLM 模型注入时走 tool calling 决策，否则关键词降级；progress 回调透传。
"
```

---

### Task 8: CLI 闲聊分支

**Files:**
- Modify: `src/stock_robot/cli.py:440-506`（chat 命令、_run_agent_query、_run_interactive_chat）

- [ ] **Step 1: 修改 chat 命令与执行流程**

`chat` 命令（cli.py:452-464）增加 ChatResponder 构建与 persist 参数：

```python
def chat(ask, verbose):
    """进入 AI Agent 对话模式，支持复杂投研任务的自主拆解和分析"""
    from agent.chat import ChatResponder
    from agent.executor import Executor
    from agent.graph import DEFAULT_CHECKPOINT_DIR
    from agent.memory import Memory
    from agent.planner import Planner
    from api.bootstrap import build_agent_core
    from output.renderer import RichRenderer
    from utils.config import Config

    config = Config()
    renderer = RichRenderer(console=console)
    core = build_agent_core(config)

    memory = Memory()
    planner = Planner(llm=core.llm, registry=core.registry, memory=memory)
    executor = Executor(registry=core.registry, memory=memory, model=core.model,
                        persist_dir=str(DEFAULT_CHECKPOINT_DIR))
    chat_responder = ChatResponder(model=core.model)

    if ask:
        _run_agent_query(ask, planner, executor, memory, renderer, chat_responder)
        return

    _run_interactive_chat(planner, executor, memory, renderer, chat_responder)
```

`_run_agent_query` 与 `_run_interactive_chat` 增加闲聊分支（签名加 `chat_responder` 参数）：

```python
def _run_agent_query(query, planner, executor, memory, renderer, chat_responder):
    """单次 Agent 查询"""
    plan = planner.plan(query)
    if plan.mode == "chat":
        import asyncio
        reply = asyncio.run(chat_responder.reply(query, memory))
        memory.add_message("assistant", reply)
        console.print(reply)
        return
    console.print(renderer.render_plan(plan))

    import asyncio
    result = asyncio.run(executor.execute(plan))

    console.print(renderer.render_summary(result))
```

`_run_interactive_chat` 循环内（`plan = planner.plan(user_input)` 之后）：

```python
        plan = planner.plan(user_input)
        if plan.mode == "chat":
            reply = asyncio.run(chat_responder.reply(user_input, memory))
            memory.add_message("assistant", reply)
            console.print(reply)
            continue

        console.print(renderer.render_plan(plan))

        import asyncio
        result = asyncio.run(executor.execute(plan))

        console.print(renderer.render_summary(result))
```

（函数签名同步更新；`_run_agent_query` 内部已有 `import asyncio`，保持局部导入风格一致。）

- [ ] **Step 2: 手动验收**

Run: `.venv/Scripts/python -m stock_robot.cli chat --ask "谢谢"`
Expected: 输出闲聊回复（无执行计划/摘要）。若 API key 未配置则输出 FALLBACK_REPLY 提示语——也属于正确行为。

Run: `.venv/Scripts/python -m stock_robot.cli chat --ask "谢谢，帮我分析600519"`
Expected: 走计划执行路径（渲染执行计划，而非闲聊回复）。

- [ ] **Step 3: 类型检查 + 提交**

Run: `pyright src/stock_robot/cli.py`
Expected: 0 errors

```bash
git add src/stock_robot/cli.py
git commit -m "feat(CLI): chat 闲聊分支 — 普通会话回复

plan.mode == chat 时跳过计划执行，由 ChatResponder 直接回复并写入记忆。
"
```

---

### Task 9: API 闲聊分支

**Files:**
- Modify: `src/api/app.py`（_build_agent、chat、chat_stream）
- Modify: `tests/api/test_app.py`（闲聊用例）

- [ ] **Step 1: 写失败测试**

`tests/api/test_app.py` 追加：

```python
from tests.agent.fake_chat_model import FakeChatModel


class ChatModeLLM:
    """返回 mode=chat 计划的 LLM"""

    def generate(self, prompt, system=None, **kwargs):
        return json.dumps({
            "goal": "闲聊",
            "complexity": "simple",
            "mode": "chat",
            "steps": [],
        }, ensure_ascii=False)


def make_chat_core():
    from typing import Any, cast
    registry = ToolRegistry()
    registry.register(EchoTool())
    return AgentCore(registry=registry, pipeline=cast(Any, FakePipeline()),
                     index_pipeline=cast(Any, FakeIndexPipeline()),
                     llm=cast(Any, ChatModeLLM()),
                     model=FakeChatModel(content="你好呀！有什么可以帮你？"))
```

在 `TestChatEndpoint` 追加：

```python
    async def test_chat_chat_mode_returns_direct_reply(self, tmp_path):
        sessions = SessionManager(SessionStore(tmp_path / "s.db"))
        app_chat = create_app(core=make_chat_core(), sessions=sessions)
        async with AsyncClient(transport=ASGITransport(app=app_chat),
                               base_url="http://test") as c:
            resp = await c.post("/api/v1/chat", json={"message": "你好"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["response"] == "你好呀！有什么可以帮你？"
        assert body["plan"]["mode"] == "chat"
        assert body["plan"]["steps"] == []
        assert body["tool_results"] == []
        # 回复写入会话消息
        msgs = sessions.get_messages(body["session_id"])
        assert any(m["role"] == "assistant"
                   and "你好呀" in m["content"] for m in msgs)
```

在 `TestStreamEndpoint` 追加：

```python
    async def test_stream_chat_mode_emits_text(self, tmp_path):
        sessions = SessionManager(SessionStore(tmp_path / "s2.db"))
        app_chat = create_app(core=make_chat_core(), sessions=sessions)
        async with AsyncClient(transport=ASGITransport(app=app_chat),
                               base_url="http://test") as c:
            async with c.stream("POST", "/api/v1/chat/stream",
                                json={"message": "你好"}) as resp:
                body = b""
                async for chunk in resp.aiter_bytes():
                    body += chunk

        text = body.decode()
        assert '"type": "text"' in text
        assert "你好呀" in text
```

Run: `.venv/Scripts/python -m pytest tests/api/test_app.py -q`
Expected: FAIL（现有 chat 路径对 mode=chat 计划仍走执行，断言不满足）

- [ ] **Step 2: 实现 app.py 闲聊分支**

`_build_agent` 增加 chat_responder：

```python
    def _build_agent(session_memory):
        """按会话现建轻量 Planner/Executor/ChatResponder（无状态，构造廉价）"""
        # 调用方（chat/run_agent）已校验 core 注入，复制到局部变量并断言收窄类型
        agent_core = core
        assert agent_core is not None
        from agent.chat import ChatResponder
        from agent.executor import Executor
        from agent.graph import DEFAULT_CHECKPOINT_DIR
        from agent.planner import Planner
        planner = Planner(llm=agent_core.llm, registry=agent_core.registry,
                          memory=session_memory)
        executor = Executor(registry=agent_core.registry, memory=session_memory,
                            model=agent_core.model,
                            session_id=session_memory.session_id or "",
                            persist_dir=str(DEFAULT_CHECKPOINT_DIR))
        chat_responder = ChatResponder(model=agent_core.model)
        return planner, executor, chat_responder
```

`chat` 端点（`_build_agent` 返回三元组后）：

```python
            sid, memory = sessions.get_or_create(session_id, message)
            memory.add_message("user", message)
            planner, executor, chat_responder = _build_agent(memory)
            plan = await asyncio.to_thread(planner.plan, message)
            if plan.mode == "chat":
                reply = await chat_responder.reply(message, memory)
                memory.add_message("assistant", reply)
                return JSONResponse({
                    "response": reply,
                    "plan": {"goal": plan.goal, "mode": "chat", "steps": []},
                    "tool_results": [],
                    "session_id": sid,
                })
            plan = await executor.execute(plan)
```

`chat_stream` 的 `run_agent`（`_build_agent` 返回三元组后）：

```python
                    sid, memory = manager.get_or_create(session_id, message)
                    memory.add_message("user", message)
                    planner, executor, chat_responder = _build_agent(memory)
                    plan = await asyncio.to_thread(planner.plan, message)
                    if plan.mode == "chat":
                        reply = await chat_responder.reply(message, memory)
                        memory.add_message("assistant", reply)
                        await queue.put({"type": "text", "content": reply})
                        await queue.put({"type": "done"})
                        return
                    await queue.put({"type": "plan", "goal": plan.goal,
                                     "steps": [s.description for s in plan.steps],
                                     "session_id": sid})
```

（注意 `_build_agent` 原两处调用都改为三元组解包；`chat` 端点内后续 `executor.execute` 的 plan 分支用 elif 语义不变。）

- [ ] **Step 3: 运行测试**

Run: `.venv/Scripts/python -m pytest tests/api/test_app.py -q`
Expected: 全绿（新增 2 例 + 现有 chat/stream 用例——现有 FakeLLM 无 mode 字段默认 task，路径不变）

- [ ] **Step 4: 类型检查 + 提交**

Run: `pyright src/api/app.py tests/api/test_app.py`
Expected: 0 errors

```bash
git add src/api/app.py tests/api/test_app.py
git commit -m "feat(API): chat 闲聊分支 — 普通会话回复与 SSE text 事件

plan.mode == chat 时直接 ChatResponder 回复，写入会话消息持久化。
"
```

---

### Task 10: 全量验证与收尾

**Files:**
- 无新增（验证）

- [ ] **Step 1: 全量检查**

Run: `ruff check .`
Expected: 0 errors

Run: `pyright`
Expected: 0 errors

Run: `.venv/Scripts/python -m pytest -q`
Expected: 全绿

- [ ] **Step 2: 手动验收（浏览器 + CLI）**

1. `.venv/Scripts/python -m stock_robot.cli chat --ask "你好"` → 普通会话回复，无计划
2. `.venv/Scripts/python -m stock_robot.cli chat --ask "分析一下600519"` → 计划执行
3. 启动 API（`.venv/Scripts/python -m uvicorn api.app:app` 于 src 下），Web UI 发"谢谢" → 正常回复；发"分析600519" → 计划执行
4. 检查 `~/.stock_robot/langgraph_checkpoints.sqlite` 存在（API 会话持久化生效）

- [ ] **Step 3: 收尾提交（如有未提交改动）**

```bash
git status   # 确认工作区干净
```

---

## 自检记录（Self-Review）

- **Spec 覆盖**：决策表（Executor 图化/双轨模型/双写 checkpointer/闲聊识别）→ Task 0-9 ✓；图结构（StateSchema/三节点/条件边）→ Task 6 ✓；LLM 工具决策（原生 tool calling/候选粗筛/重试/降级阶梯）→ Task 3 ✓；双写（SqliteSaver + message_store）→ Task 6/7/9 ✓；闲聊识别（纯客套快路径/LLM mode 分类/公司名归 task）→ Task 1 ✓；ChatResponder → Task 5 ✓；调用方适配（cli/app 闲聊分支）→ Task 8/9 ✓；测试策略 → 各任务 TDD ✓。
- **占位符扫描**：无 TBD/TODO；所有代码步骤含完整实现。
- **类型一致性**：`Executor(registry, memory, model, session_id, persist_dir)` 在 Task 6/7/8/9 一致；`plan_to_state/state_to_plan/build_execution_graph` 签名跨 Task 6/7 一致；`_extract_tool_args` 迁移至 tool_selector 后 test_executor 导入同步（Task 3 Step 3）；`GraphState` 键名在 Task 6 测试与实现一致。
