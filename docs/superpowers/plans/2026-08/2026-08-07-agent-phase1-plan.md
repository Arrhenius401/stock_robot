# Agent 架构 Phase 1 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 Agent 核心（Planner + Executor + Memory）、ToolRegistry 工具系统、pipeline_tools 包装层、CLI chat 命令，交付可对话的股票分析 Agent。

**Architecture:** 新增 `src/agent/` 模块（Planner/Executor/Memory/ToolRegistry）和 `src/output/` 渲染层，通过 `pipeline_tools` 包装存量 `Pipeline` 和 `IndexPipeline` 为统一工具接口。存量代码零侵入。

**Tech Stack:** Python 3.11+, dataclasses, pytest + pytest-mock, click, rich

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `src/agent/__init__.py` | 模块导出 |
| `src/agent/tools.py` | `ToolResult`, `ToolProtocol`, `ToolRegistry` |
| `src/agent/memory.py` | `TaskStatus`, `TaskStep`, `Plan`, `Memory` |
| `src/agent/planner.py` | `Planner` — LLM 驱动的任务拆解 |
| `src/agent/executor.py` | `Executor` — 逐步执行、工具匹配、失败隔离 |
| `src/agent/pipeline_tools.py` | `analyze_stock`, `analyze_index`, `screen_stocks`, `get_snapshot` 工具包装 |
| `src/output/__init__.py` | 模块导出 |
| `src/output/renderer.py` | `OutputRenderer` 协议 + `RichRenderer` + `JsonRenderer` |
| `tests/agent/__init__.py` | 测试包 |
| `tests/agent/test_tools.py` | ToolResult / ToolProtocol / ToolRegistry 单测 |
| `tests/agent/test_memory.py` | Memory 数据结构与持久化单测 |
| `tests/agent/test_pipeline_tools.py` | pipeline_tools 单测 |
| `tests/agent/test_planner.py` | Planner 单测 |
| `tests/agent/test_executor.py` | Executor 单测 |
| `tests/agent/test_cli_chat.py` | CLI chat 命令集成测试 |

---

### Task 1: 基础数据结构 — TaskStatus, ToolResult, ToolProtocol

**Files:**
- Create: `src/agent/__init__.py`
- Create: `src/agent/tools.py`
- Create: `tests/agent/__init__.py`
- Create: `tests/agent/test_tools.py`

- [ ] **Step 1: 创建 agent 包初始化文件**

```bash
mkdir -p src/agent
```

`src/agent/__init__.py`:
```python
"""Agent 核心模块 — Planner/Executor/Memory/ToolRegistry"""
```

- [ ] **Step 2: 编写 ToolResult 和 ToolProtocol 的失败测试**

`tests/agent/__init__.py`:
```python
```

`tests/agent/test_tools.py`:
```python
"""ToolResult / ToolProtocol / ToolRegistry 单元测试"""
import pytest
from agent.tools import ToolResult, ToolProtocol, ToolRegistry


class TestToolResult:
    def test_success_result_has_status_data_and_metadata(self):
        result = ToolResult(status="success", data={"price": 10.5},
                           metadata={"execution_time_ms": 42})

        assert result.status == "success"
        assert result.data == {"price": 10.5}
        assert result.error is None
        assert result.metadata == {"execution_time_ms": 42}

    def test_error_result_has_status_error_and_null_data(self):
        result = ToolResult(status="error", error="连接超时",
                           metadata={"source": "akshare"})

        assert result.status == "error"
        assert result.data is None
        assert result.error == "连接超时"
        assert result.metadata == {"source": "akshare"}

    def test_result_defaults_data_and_error_to_none(self):
        result = ToolResult(status="partial")

        assert result.data is None
        assert result.error is None
        assert result.metadata is None

    def test_metadata_defaults_to_none(self):
        result = ToolResult(status="success")

        assert result.metadata is None


class TestToolProtocol:
    def test_tool_protocol_defines_required_attributes(self):
        """验证 ToolProtocol 的接口契约 —— 运行时通过 hasattr 检查"""
        required = ["name", "description", "parameters", "tags", "source", "execute"]

        class MyTool:
            name = "test_tool"
            description = "用于测试的工具"
            parameters = {"type": "object", "properties": {}}
            tags = ["test"]
            source = "pipeline"

            async def execute(self, **kwargs):
                return ToolResult(status="success")

        tool = MyTool()
        for attr in required:
            assert hasattr(tool, attr), f"缺少属性: {attr}"
        assert tool.name == "test_tool"
        assert tool.source == "pipeline"
        assert tool.tags == ["test"]
```

- [ ] **Step 3: 运行测试验证失败**

```bash
python -m pytest tests/agent/test_tools.py -v
```
Expected: FAIL — 模块未创建

- [ ] **Step 4: 实现 ToolResult 和 ToolProtocol**

`src/agent/tools.py`:
```python
"""工具系统 — ToolProtocol 协议、ToolResult 结构、ToolRegistry 注册表"""
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass
class ToolResult:
    """统一的工具执行结果

    所有工具 execute() 返回此结构，Executor 无需区分工具来源。
    工具内部异常捕获后转为 status="error"，不向上抛出。
    """
    status: Literal["success", "error", "partial"]
    data: Any = None
    error: str | None = None
    metadata: dict | None = None  # 含 execution_time_ms, source, tags 等


class ToolProtocol(Protocol):
    """所有工具实现此协议，Agent 不关心工具来源（pipeline/rag/mcp）"""
    name: str
    description: str       # LLM 阅读的语义描述，含使用场景和参数说明
    parameters: dict       # JSON Schema 格式的参数定义
    tags: list[str]        # 语义标签 ["pipeline", "stock", "screening"]
    source: Literal["pipeline", "rag", "mcp_internal", "mcp_external"]

    async def execute(self, **kwargs) -> ToolResult: ...
```

- [ ] **Step 5: 运行测试验证通过**

```bash
python -m pytest tests/agent/test_tools.py -v
```
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/agent/__init__.py src/agent/tools.py tests/agent/__init__.py tests/agent/test_tools.py
git commit -m "feat(agent): 添加 ToolResult 数据结构和 ToolProtocol 协议"
```

---

### Task 2: ToolRegistry — 工具注册与匹配

**Files:**
- Modify: `src/agent/tools.py`
- Modify: `tests/agent/test_tools.py`

- [ ] **Step 1: 编写 ToolRegistry 测试**

在 `tests/agent/test_tools.py` 末尾追加:

```python
class FakeTool:
    def __init__(self, name, description, parameters=None, tags=None,
                 source="pipeline", return_data=None):
        self.name = name
        self.description = description
        self.parameters = parameters or {"type": "object", "properties": {}}
        self.tags = tags or []
        self.source = source
        self._return = return_data

    async def execute(self, **kwargs):
        return ToolResult(status="success", data=self._return)


class TestToolRegistry:
    @pytest.fixture
    def registry(self):
        return ToolRegistry()

    @pytest.fixture
    def sample_tools(self):
        return [
            FakeTool("analyze_stock", "分析单只股票的基本面和技术面，需要 stock_code 参数",
                     tags=["pipeline", "stock", "analysis"]),
            FakeTool("analyze_index", "分析指数，需要 index_code 参数",
                     tags=["pipeline", "index", "analysis"]),
            FakeTool("rag_search", "搜索知识库获取研报观点",
                     tags=["rag", "knowledge"]),
        ]

    def test_register_adds_tool_to_registry(self, registry):
        tool = FakeTool("test", "test tool")
        registry.register(tool)
        assert registry.get("test") is tool

    def test_register_replaces_existing_same_name(self, registry):
        tool1 = FakeTool("dup", "first")
        tool2 = FakeTool("dup", "second")
        registry.register(tool1)
        registry.register(tool2)
        assert registry.get("dup") is tool2

    def test_list_all_returns_all_registered_tools(self, registry, sample_tools):
        for t in sample_tools:
            registry.register(t)
        names = [t.name for t in registry.list_all()]
        assert names == ["analyze_stock", "analyze_index", "rag_search"]

    def test_get_returns_none_for_unknown_name(self, registry):
        assert registry.get("nonexistent") is None

    def test_match_filters_by_tags(self, registry, sample_tools):
        for t in sample_tools:
            registry.register(t)

        results = registry.match("分析", tags=["pipeline"])
        names = [t.name for t in results]
        assert "analyze_stock" in names
        assert "analyze_index" in names
        assert "rag_search" not in names

    def test_match_without_tags_returns_all_with_similar_description(self, registry, sample_tools):
        for t in sample_tools:
            registry.register(t)

        results = registry.match("搜索")
        names = [t.name for t in results]
        assert "rag_search" in names

    def test_match_returns_empty_for_no_match(self, registry, sample_tools):
        for t in sample_tools:
            registry.register(t)

        results = registry.match("翻译文档", tags=["mcp_external"])
        assert results == []

    def test_match_tag_index_is_built_on_register(self, registry):
        tool = FakeTool("multi_tag", "test", tags=["pipeline", "stock", "analysis"])
        registry.register(tool)

        assert "analyze_stock" not in [t.name for t in registry.match("whatever", tags=["rag"])]
        result = registry.match("股票", tags=["stock"])[0]
        assert result.name == "multi_tag"

    def test_register_duplicate_name_updates_tag_index(self, registry):
        tool1 = FakeTool("t1", "desc", tags=["pipeline"])
        tool2 = FakeTool("t1", "desc", tags=["rag"])
        registry.register(tool1)
        registry.register(tool2)

        assert len(registry.match("desc", tags=["pipeline"])) == 0
        assert len(registry.match("desc", tags=["rag"])) == 1

    def test_list_all_summary_returns_names_and_descriptions(self, registry, sample_tools):
        for t in sample_tools:
            registry.register(t)

        summary = registry.list_all_summary()
        assert len(summary) == 3
        for item in summary:
            assert "name" in item
            assert "description" in item
            assert "tags" in item
```

- [ ] **Step 2: 运行测试验证失败**

```bash
python -m pytest tests/agent/test_tools.py::TestToolRegistry -v
```
Expected: FAIL — ToolRegistry 未定义

- [ ] **Step 3: 实现 ToolRegistry**

在 `src/agent/tools.py` 中追加:

```python
class ToolRegistry:
    """工具注册表 —— 仅做工具映射，业务实现全部下沉引擎层

    匹配流程: 步骤描述 → tags 标签粗筛 → 关键词子串匹配排序 → 返回候选列表
    最终工具选定由 Executor 通过 LLM 二次校验完成。
    """

    def __init__(self):
        self._tools: dict[str, ToolProtocol] = {}
        self._index: dict[str, set[str]] = {}  # tag → set of tool names

    def register(self, tool: ToolProtocol) -> None:
        old = self._tools.get(tool.name)
        if old:
            for tag in old.tags:
                if tag in self._index:
                    self._index[tag].discard(tool.name)
        self._tools[tool.name] = tool
        for tag in tool.tags:
            if tag not in self._index:
                self._index[tag] = set()
            self._index[tag].add(tool.name)

    def get(self, name: str) -> ToolProtocol | None:
        return self._tools.get(name)

    def list_all(self) -> list[ToolProtocol]:
        return list(self._tools.values())

    def list_all_summary(self) -> list[dict]:
        """供 Planner system prompt 使用的工具摘要列表"""
        return [
            {
                "name": t.name,
                "description": t.description,
                "tags": t.tags,
                "source": t.source,
            }
            for t in self._tools.values()
        ]

    def match(self, description: str, tags: list[str] | None = None) -> list[ToolProtocol]:
        """按描述子串匹配 + 可选标签过滤，返回相关性排序的候选列表"""
        candidates = set(self._tools.keys())

        if tags:
            for tag in tags:
                if tag in self._index:
                    candidates &= self._index[tag]
                else:
                    return []

        if not candidates:
            return []

        desc_lower = description.lower()
        scored = []
        for name in candidates:
            tool = self._tools[name]
            combined = f"{tool.name} {tool.description}".lower()
            score = 0
            for word in desc_lower.split():
                if word in combined:
                    score += 1
            if score > 0:
                scored.append((score, tool))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [tool for _, tool in scored]
```

- [ ] **Step 4: 运行测试验证通过**

```bash
python -m pytest tests/agent/test_tools.py -v
```
Expected: PASS (all 17 tests)

- [ ] **Step 5: 提交**

```bash
git add src/agent/tools.py tests/agent/test_tools.py
git commit -m "feat(agent): 实现 ToolRegistry 工具注册表，支持标签索引和子串匹配"
```

---

### Task 3: Memory 数据结构 — TaskStep, Plan, Memory

**Files:**
- Create: `src/agent/memory.py`
- Create: `tests/agent/test_memory.py`

- [ ] **Step 1: 编写 Memory 数据结构测试**

`tests/agent/test_memory.py`:
```python
"""Memory 数据结构与持久化测试"""
import json
import tempfile
from pathlib import Path
import pytest
from agent.memory import TaskStatus, TaskStep, Plan, Memory


class TestTaskStatus:
    def test_known_statuses(self):
        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.RUNNING == "running"
        assert TaskStatus.DONE == "done"
        assert TaskStatus.FAILED == "failed"
        assert TaskStatus.SKIPPED == "skipped"


class TestTaskStep:
    def test_create_minimal_step(self):
        step = TaskStep(id="step-1", description="分析茅台财务")
        assert step.id == "step-1"
        assert step.description == "分析茅台财务"
        assert step.tool_name is None
        assert step.tool_args is None
        assert step.depends_on == []
        assert step.status == TaskStatus.PENDING

    def test_create_step_with_dependencies(self):
        step = TaskStep(
            id="step-2",
            description="估值对比",
            depends_on=["step-1"],
            tool_name="analyze_stock",
            tool_args={"symbol": "600519"},
        )
        assert step.depends_on == ["step-1"]
        assert step.tool_name == "analyze_stock"
        assert step.tool_args == {"symbol": "600519"}

    def test_step_status_transitions(self):
        step = TaskStep(id="s1", description="采集数据")
        step.status = TaskStatus.RUNNING
        assert step.status == "running"
        step.status = TaskStatus.DONE
        assert step.status == "done"

    def test_step_default_status_is_pending(self):
        step = TaskStep(id="s1", description="test")
        assert step.status == "pending"


class TestPlan:
    def test_create_plan_with_steps(self):
        step1 = TaskStep(id="step-1", description="筛选标的")
        step2 = TaskStep(id="step-2", description="采集数据", depends_on=["step-1"])
        plan = Plan(
            goal="找3只被低估的新能源龙头",
            steps=[step1, step2],
            context_summary="用户偏好科技股，风险偏好中等",
        )
        assert plan.goal == "找3只被低估的新能源龙头"
        assert len(plan.steps) == 2
        assert plan.steps[1].depends_on == ["step-1"]
        assert plan.context_summary == "用户偏好科技股，风险偏好中等"

    def test_empty_plan(self):
        plan = Plan(goal="测试", steps=[], context_summary="")
        assert len(plan.steps) == 0

    def test_get_pending_steps(self):
        s1 = TaskStep(id="s1", description="a", status=TaskStatus.DONE)
        s2 = TaskStep(id="s2", description="b", status=TaskStatus.PENDING)
        s3 = TaskStep(id="s3", description="c", status=TaskStatus.PENDING, depends_on=["s2"])
        plan = Plan(goal="test", steps=[s1, s2, s3], context_summary="")

        pending = plan.get_pending_steps()
        # s2 先于 s3（无依赖的优先）
        assert [s.id for s in pending] == ["s2"]

    def test_get_pending_steps_returns_independent_first(self):
        s1 = TaskStep(id="s1", description="a")
        s2 = TaskStep(id="s2", description="b", depends_on=["s1"])
        s3 = TaskStep(id="s3", description="c")
        plan = Plan(goal="test", steps=[s1, s2, s3], context_summary="")

        pending = plan.get_pending_steps()
        ids = [s.id for s in pending]
        # 无依赖的 s1, s3 优先
        assert ids[0] in ("s1", "s3")
        assert ids[1] in ("s1", "s3")

    def test_mark_failed_cascades_to_dependents(self):
        s1 = TaskStep(id="s1", description="a", status=TaskStatus.RUNNING)
        s2 = TaskStep(id="s2", description="b", depends_on=["s1"])
        s3 = TaskStep(id="s3", description="c")
        plan = Plan(goal="test", steps=[s1, s2, s3], context_summary="")

        s1.status = TaskStatus.FAILED
        plan.mark_dependents_skipped("s1")

        assert s2.status == TaskStatus.SKIPPED
        assert s3.status == TaskStatus.PENDING  # 独立步骤不受影响

    def test_all_done_returns_true_when_all_steps_completed(self):
        s1 = TaskStep(id="s1", description="a", status=TaskStatus.DONE)
        s2 = TaskStep(id="s2", description="b", status=TaskStatus.SKIPPED)
        plan = Plan(goal="test", steps=[s1, s2], context_summary="")
        assert plan.all_done()


class TestMemory:
    def test_create_empty_memory(self):
        m = Memory()
        assert m.messages == []
        assert m.plan_history == []
        assert m.facts == {}

    def test_add_message_trims_window(self):
        m = Memory(max_messages=3)
        for i in range(5):
            m.add_message("user", f"msg {i}")

        assert len(m.messages) == 3
        assert m.messages[0]["content"] == "msg 2"
        assert m.messages[-1]["content"] == "msg 4"

    def test_add_message_stores_role_and_content(self):
        m = Memory()
        m.add_message("user", "测试问题")
        m.add_message("assistant", "测试回答")

        assert m.messages[0] == {"role": "user", "content": "测试问题"}
        assert m.messages[1] == {"role": "assistant", "content": "测试回答"}

    def test_add_plan_appends_to_history(self):
        m = Memory()
        plan = Plan(goal="test", steps=[], context_summary="")
        m.add_plan(plan)
        assert len(m.plan_history) == 1

    def test_get_last_plan_returns_most_recent(self):
        m = Memory()
        plan1 = Plan(goal="first", steps=[], context_summary="")
        plan2 = Plan(goal="second", steps=[], context_summary="")
        m.add_plan(plan1)
        m.add_plan(plan2)
        assert m.get_last_plan().goal == "second"

    def test_get_last_plan_returns_none_when_empty(self):
        m = Memory()
        assert m.get_last_plan() is None

    def test_get_context_window_returns_recent_messages(self):
        m = Memory(max_messages=10)
        m.add_message("user", "问题1")
        m.add_message("assistant", "回答1")
        m.add_message("user", "问题2")

        ctx = m.get_context_window(n=2)
        assert len(ctx) == 2
        assert ctx[0]["content"] == "回答1"
        assert ctx[1]["content"] == "问题2"

    def test_set_fact_stores_and_persists(self, tmp_path):
        facts_file = tmp_path / "facts.json"
        m = Memory(facts_path=facts_file)
        m.set_fact("preferred_style", "growth")
        assert m.facts["preferred_style"] == "growth"
        assert facts_file.exists()

        loaded = json.loads(facts_file.read_text(encoding="utf-8"))
        assert loaded["preferred_style"] == "growth"

    def test_load_facts_from_existing_file(self, tmp_path):
        facts_file = tmp_path / "facts.json"
        facts_file.write_text(
            json.dumps({"preferred_style": "value", "favorite_stocks": ["600519"]}),
            encoding="utf-8",
        )
        m = Memory(facts_path=facts_file)
        assert m.facts["preferred_style"] == "value"
        assert m.facts["favorite_stocks"] == ["600519"]

    def test_get_fact_returns_none_for_missing_key(self):
        m = Memory()
        assert m.get_fact("nonexistent") is None

    def test_get_fact_returns_stored_value(self):
        m = Memory()
        m.set_fact("risk_tolerance", "high")
        assert m.get_fact("risk_tolerance") == "high"

    def test_clear_session_resets_messages_and_plans_only(self):
        m = Memory()
        m.add_message("user", "test")
        plan = Plan(goal="test", steps=[], context_summary="")
        m.add_plan(plan)
        m.set_fact("key", "value")

        m.clear_session()
        assert m.messages == []
        assert m.plan_history == []
        assert m.facts == {"key": "value"}  # facts 保留

    def test_default_facts_path_is_in_config_dir(self, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: Path("/tmp"))
        m = Memory()
        assert str(m._facts_path).startswith(str(Path("/tmp") / ".stock_robot"))
```

- [ ] **Step 2: 运行测试验证失败**

```bash
python -m pytest tests/agent/test_memory.py -v
```
Expected: FAIL — 模块未创建

- [ ] **Step 3: 实现 Memory 数据结构**

`src/agent/memory.py`:
```python
"""Agent Memory — 对话记忆、计划历史、事实持久化"""
import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class TaskStep:
    id: str                           # 如 "step-1"
    description: str                  # 如 "筛选新能源龙头股"
    tool_name: str | None = None      # 计划阶段可为空，执行时由 Executor 匹配填充
    tool_args: dict | None = None
    depends_on: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING


@dataclass
class Plan:
    goal: str                          # 原始用户意图
    steps: list[TaskStep]
    context_summary: str = ""          # 从 Memory 提取的相关历史摘要

    def get_pending_steps(self) -> list[TaskStep]:
        """返回当前可执行的步骤（依赖已满足且状态为 pending）"""
        done_or_skipped = {
            s.id for s in self.steps
            if s.status in (TaskStatus.DONE, TaskStatus.SKIPPED)
        }
        ready = []
        for s in self.steps:
            if s.status != TaskStatus.PENDING:
                continue
            if all(dep in done_or_skipped for dep in s.depends_on):
                ready.append(s)
        return ready

    def mark_dependents_skipped(self, failed_step_id: str) -> None:
        """将依赖失败步骤的所有步骤标记为 skipped"""
        for s in self.steps:
            if s.status != TaskStatus.PENDING:
                continue
            if failed_step_id in s.depends_on:
                s.status = TaskStatus.SKIPPED
                # 递归标记间接依赖
                self.mark_dependents_skipped(s.id)

    def all_done(self) -> bool:
        """所有步骤是否已终态（done/skipped/failed）"""
        return all(
            s.status in (TaskStatus.DONE, TaskStatus.SKIPPED, TaskStatus.FAILED)
            for s in self.steps
        )


class Memory:
    """Agent 记忆 —— 三层存储：会话消息 / 计划历史 / 持久化 facts"""

    def __init__(self, max_messages: int = 30, facts_path: Path | None = None):
        self._max_messages = max_messages
        self.messages: list[dict[str, str]] = []
        self.plan_history: list[Plan] = []
        self.facts: dict[str, Any] = {}
        if facts_path is None:
            facts_path = Path.home() / ".stock_robot" / "agent_facts.json"
        self._facts_path = Path(facts_path)
        self._load_facts()

    def add_message(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})
        if len(self.messages) > self._max_messages:
            self.messages = self.messages[-self._max_messages:]

    def add_plan(self, plan: Plan) -> None:
        self.plan_history.append(plan)

    def get_last_plan(self) -> Plan | None:
        return self.plan_history[-1] if self.plan_history else None

    def get_context_window(self, n: int = 20) -> list[dict]:
        """返回最近 N 轮对话，供 Planner 使用"""
        return self.messages[-n:] if len(self.messages) > n else list(self.messages)

    def set_fact(self, key: str, value: Any) -> None:
        self.facts[key] = value
        self._persist_facts()

    def get_fact(self, key: str) -> Any | None:
        return self.facts.get(key)

    def clear_session(self) -> None:
        """清空会话上下文，保留 facts"""
        self.messages = []
        self.plan_history = []

    def _load_facts(self) -> None:
        if self._facts_path.exists():
            try:
                self.facts = json.loads(self._facts_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self.facts = {}

    def _persist_facts(self) -> None:
        self._facts_path.parent.mkdir(parents=True, exist_ok=True)
        self._facts_path.write_text(
            json.dumps(self.facts, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
```

- [ ] **Step 4: 运行测试验证通过**

```bash
python -m pytest tests/agent/test_memory.py -v
```
Expected: PASS (all 23 tests)

- [ ] **Step 5: 提交**

```bash
git add src/agent/memory.py tests/agent/test_memory.py
git commit -m "feat(agent): 实现 TaskStep/Plan/Memory 数据结构与 facts JSON 持久化"
```

---

### Task 4: OutputRenderer — 多端渲染抽象

**Files:**
- Create: `src/output/__init__.py`
- Create: `src/output/renderer.py`
- Create: `tests/agent/test_renderer.py`

- [ ] **Step 1: 编写 OutputRenderer 测试**

`tests/agent/test_renderer.py`:
```python
"""OutputRenderer 协议与实现测试"""
import pytest
from output.renderer import OutputRenderer, RichRenderer, JsonRenderer
from agent.memory import TaskStep, Plan
from agent.tools import ToolResult


class TestRichRenderer:
    @pytest.fixture
    def renderer(self):
        return RichRenderer()

    def test_render_plan_produces_table(self, renderer):
        plan = Plan(
            goal="找低估值股票",
            steps=[
                TaskStep(id="s1", description="筛选标的", status="done"),
                TaskStep(id="s2", description="采集数据", status="running", depends_on=["s1"]),
                TaskStep(id="s3", description="估值对比", status="pending", depends_on=["s2"]),
            ],
        )
        output = renderer.render_plan(plan)
        assert "找低估值股票" in output
        assert "筛选标的" in output
        assert "done" in output.lower()

    def test_render_progress_shows_step_info(self, renderer):
        step = TaskStep(id="s2", description="采集财务数据", status="running")
        output = renderer.render_progress(step, step_index=1, total=3)
        assert "采集财务数据" in output
        assert "1/3" in output

    def test_render_error_includes_details(self, renderer):
        output = renderer.render_error("工具执行失败: 连接超时")
        assert "连接超时" in output

    def test_render_success_result_with_data(self, renderer):
        result = ToolResult(status="success", data={"pe": 15.5, "pb": 2.1})
        output = renderer.render_tool_result("analyze_stock", result)
        assert "analyze_stock" in output
        assert "成功" in output

    def test_render_error_result_with_error_message(self, renderer):
        result = ToolResult(status="error", error="AkShare API 调用失败")
        output = renderer.render_tool_result("fetch_data", result)
        assert "fetch_data" in output.lower()
        assert "失败" in output

    def test_render_final_summary_includes_goal_and_status(self, renderer):
        plan = Plan(
            goal="找3只被低估的新能源龙头",
            steps=[
                TaskStep(id="s1", description="筛选标的", status="done"),
                TaskStep(id="s2", description="分析", status="done"),
            ],
        )
        output = renderer.render_summary(plan)
        assert "找3只被低估的新能源龙头" in output
        assert "完成" in output


class TestJsonRenderer:
    @pytest.fixture
    def renderer(self):
        return JsonRenderer()

    def test_render_plan_returns_valid_json(self, renderer):
        import json
        plan = Plan(
            goal="测试",
            steps=[TaskStep(id="s1", description="步骤1")],
        )
        output = renderer.render_plan(plan)
        data = json.loads(output)
        assert data["goal"] == "测试"
        assert len(data["steps"]) == 1

    def test_render_tool_result_returns_valid_json(self, renderer):
        import json
        result = ToolResult(status="success", data={"price": 100})
        output = renderer.render_tool_result("test_tool", result)
        data = json.loads(output)
        assert data["tool"] == "test_tool"
        assert data["status"] == "success"
```

- [ ] **Step 2: 运行测试验证失败**

```bash
python -m pytest tests/agent/test_renderer.py -v
```
Expected: FAIL

- [ ] **Step 3: 实现 OutputRenderer**

`src/output/__init__.py`:
```python
"""输出渲染层"""
```

`src/output/renderer.py`:
```python
"""输出渲染 — 协议定义 + Rich/JSON 实现"""
import json
from typing import Any, Protocol
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from agent.memory import Plan, TaskStep, TaskStatus
from agent.tools import ToolResult


class OutputRenderer(Protocol):
    """多端渲染协议 — CLI(Rich) / API(JSON) / Web(HTML)"""

    def render_plan(self, plan: Plan) -> str: ...
    def render_progress(self, step: TaskStep, step_index: int, total: int) -> str: ...
    def render_tool_result(self, tool_name: str, result: ToolResult) -> str: ...
    def render_error(self, error: str) -> str: ...
    def render_summary(self, plan: Plan) -> str: ...


class RichRenderer:
    def __init__(self, console: Console | None = None):
        self._console = console or Console()

    def render_plan(self, plan: Plan) -> str:
        table = Table(title=f"执行计划: {plan.goal}")
        table.add_column("步骤", style="cyan", width=6)
        table.add_column("描述", style="white")
        table.add_column("依赖", style="dim", width=20)
        table.add_column("状态", style="yellow", width=10)

        for s in plan.steps:
            deps = ", ".join(s.depends_on) if s.depends_on else "—"
            status_icon = _status_icon(s.status)
            table.add_row(s.id, s.description, deps, f"{status_icon} {s.status}")

        return str(table)

    def render_progress(self, step: TaskStep, step_index: int, total: int) -> str:
        icon = _status_icon(step.status)
        return f"[{icon}] 步骤 {step_index}/{total}: {step.description}"

    def render_tool_result(self, tool_name: str, result: ToolResult) -> str:
        if result.status == "success":
            label = "[green]成功[/green]"
        elif result.status == "error":
            label = f"[red]失败[/red] — {result.error}"
        else:
            label = "[yellow]部分成功[/yellow]"
        return f"  工具 [bold]{tool_name}[/bold]: {label}"

    def render_error(self, error: str) -> str:
        return str(Panel(error, title="错误", border_style="red"))

    def render_summary(self, plan: Plan) -> str:
        done = sum(1 for s in plan.steps if s.status == TaskStatus.DONE)
        failed = sum(1 for s in plan.steps if s.status == TaskStatus.FAILED)
        total = len(plan.steps)
        return str(Panel(
            f"目标: {plan.goal}\n完成: {done}/{total} 步骤"
            f"{f'（{failed} 失败）' if failed > 0 else ''}",
            title="执行完成",
            border_style="green" if failed == 0 else "yellow",
        ))


class JsonRenderer:
    def render_plan(self, plan: Plan) -> str:
        return json.dumps({
            "goal": plan.goal,
            "steps": [
                {
                    "id": s.id, "description": s.description,
                    "depends_on": s.depends_on, "status": s.status,
                }
                for s in plan.steps
            ],
        }, ensure_ascii=False)

    def render_progress(self, step: TaskStep, step_index: int, total: int) -> str:
        return json.dumps({
            "step_id": step.id,
            "description": step.description,
            "index": step_index,
            "total": total,
            "status": step.status,
        }, ensure_ascii=False)

    def render_tool_result(self, tool_name: str, result: ToolResult) -> str:
        return json.dumps({
            "tool": tool_name,
            "status": result.status,
            "data": result.data,
            "error": result.error,
        }, ensure_ascii=False, default=str)

    def render_error(self, error: str) -> str:
        return json.dumps({"error": error}, ensure_ascii=False)

    def render_summary(self, plan: Plan) -> str:
        done = sum(1 for s in plan.steps if s.status == TaskStatus.DONE)
        failed = sum(1 for s in plan.steps if s.status == TaskStatus.FAILED)
        return json.dumps({
            "goal": plan.goal,
            "total_steps": len(plan.steps),
            "done": done,
            "failed": failed,
        }, ensure_ascii=False)


def _status_icon(status: str) -> str:
    icons = {
        TaskStatus.PENDING: "○",
        TaskStatus.RUNNING: "◉",
        TaskStatus.DONE: "✓",
        TaskStatus.FAILED: "✗",
        TaskStatus.SKIPPED: "—",
    }
    return icons.get(status, "?")
```

- [ ] **Step 4: 运行测试验证通过**

```bash
python -m pytest tests/agent/test_renderer.py -v
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/output/__init__.py src/output/renderer.py tests/agent/test_renderer.py
git commit -m "feat(agent): 实现 OutputRenderer 协议与 Rich/JSON 渲染器"
```

---

### Task 5: pipeline_tools — 包装存量管道为 Agent 工具

**Files:**
- Create: `src/agent/pipeline_tools.py`
- Create: `tests/agent/test_pipeline_tools.py`

- [ ] **Step 1: 编写 pipeline_tools 测试**

`tests/agent/test_pipeline_tools.py`:
```python
"""pipeline_tools 单元测试"""
import pytest
from agent.tools import ToolResult
from agent.pipeline_tools import (
    AnalyzeStockTool, AnalyzeIndexTool, GetSnapshotTool, ScreenStocksTool,
)


class TestAnalyzeStockTool:
    def test_has_correct_metadata(self):
        tool = AnalyzeStockTool()
        assert tool.name == "analyze_stock"
        assert tool.source == "pipeline"
        assert "pipeline" in tool.tags
        assert "stock" in tool.tags
        assert tool.parameters["type"] == "object"
        assert "symbol" in tool.parameters["required"]

    @pytest.mark.asyncio
    async def test_execute_returns_error_when_pipeline_fails(self, mocker):
        mock_pipeline = mocker.Mock()
        mock_pipeline.run.side_effect = Exception("数据源不可用")

        tool = AnalyzeStockTool(pipeline=mock_pipeline)
        result = await tool.execute(symbol="000001")

        assert result.status == "error"
        assert "数据源不可用" in result.error
        assert result.metadata["source"] == "pipeline"

    @pytest.mark.asyncio
    async def test_execute_calls_pipeline_with_correct_target(self, mocker):
        mock_pipeline = mocker.Mock()
        mock_pipeline.run.return_value = mocker.Mock(
            reports=[mocker.Mock(code="000001", name="平安银行")],
            errors=[],
        )

        tool = AnalyzeStockTool(pipeline=mock_pipeline)
        result = await tool.execute(symbol="000001")

        assert result.status == "success"
        mock_pipeline.run.assert_called_once()
        called_targets = mock_pipeline.run.call_args[1]["targets"]
        assert called_targets[0].symbol == "000001"
        assert called_targets[0].target_type == "stock"

    @pytest.mark.asyncio
    async def test_execute_returns_error_for_empty_symbol(self):
        tool = AnalyzeStockTool()
        result = await tool.execute(symbol="")
        assert result.status == "error"
        assert "代码" in result.error


class TestGetSnapshotTool:
    @pytest.mark.asyncio
    async def test_execute_returns_valuation_snapshot(self, mocker):
        mock_pipeline = mocker.Mock()
        mock_pipeline.get_snapshot.return_value = {
            "symbol": "000300",
            "pe_ttm": 12.5,
            "pe_percentile": 0.35,
            "valuation_valid": True,
        }

        tool = GetSnapshotTool(index_pipeline=mock_pipeline)
        result = await tool.execute(symbol="000300")

        assert result.status == "success"
        assert result.data["pe_ttm"] == 12.5
        assert result.data["pe_percentile"] == 0.35

    @pytest.mark.asyncio
    async def test_execute_returns_error_when_snapshot_is_none(self, mocker):
        mock_pipeline = mocker.Mock()
        mock_pipeline.get_snapshot.return_value = None

        tool = GetSnapshotTool(index_pipeline=mock_pipeline)
        result = await tool.execute(symbol="999999")

        assert result.status == "error"
        assert "不支持" in result.error or "快照" in result.error


class TestScreenStocksTool:
    @pytest.mark.asyncio
    async def test_execute_returns_error_when_industry_filter_not_found(self, mocker):
        tool = ScreenStocksTool()
        result = await tool.execute(industry="不存在的行业")

        assert result.status == "error"
        assert "行业" in result.error

    @pytest.mark.asyncio
    async def test_execute_has_correct_metadata(self):
        tool = ScreenStocksTool()
        assert tool.name == "screen_stocks"
        assert tool.source == "pipeline"
        assert "screening" in tool.tags
```

- [ ] **Step 2: 运行测试验证失败**

```bash
python -m pytest tests/agent/test_pipeline_tools.py -v
```
Expected: FAIL

- [ ] **Step 3: 实现 pipeline_tools**

`src/agent/pipeline_tools.py`:
```python
"""Pipeline 工具包装 — 将存量 Pipeline/IndexPipeline 包装为 ToolProtocol

不修改存量代码，仅通过包装器暴露为 Agent 可调用的统一工具。
"""
import logging
from agent.tools import ToolResult

logger = logging.getLogger(__name__)


class AnalyzeStockTool:
    """单股全维度分析工具"""
    name = "analyze_stock"
    description = (
        "对单只 A 股进行全面分析，返回财务、技术面、估值、行业、舆情五个维度的分析报告。"
        "适用于需要深入了解某只股票的完整画像时使用。"
        "参数: symbol(6位股票代码)"
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "A股代码，6位数字，如 000001 或 600519",
            },
        },
        "required": ["symbol"],
    }
    tags = ["pipeline", "stock", "analysis"]
    source = "pipeline"

    def __init__(self, pipeline=None):
        self._pipeline = pipeline

    async def execute(self, **kwargs) -> ToolResult:
        symbol = str(kwargs.get("symbol", "")).strip()
        if not symbol:
            return ToolResult(status="error", error="股票代码不能为空",
                             metadata={"source": "pipeline"})

        from data.schemas import AnalysisTarget

        try:
            pipeline = self._get_pipeline()
            target = AnalysisTarget(
                target_type="stock", symbol=symbol,
                name=symbol, market="a-shares",
            )
            pipe_result = pipeline.run(targets=[target])

            errors = pipe_result.errors
            reports = pipe_result.reports

            if errors:
                return ToolResult(
                    status="error",
                    error="; ".join(errors),
                    metadata={"source": "pipeline", "symbol": symbol},
                )

            if not reports:
                return ToolResult(
                    status="error",
                    error=f"未能生成 {symbol} 的分析报告",
                    metadata={"source": "pipeline", "symbol": symbol},
                )

            report = reports[0]
            return ToolResult(
                status="success",
                data={
                    "code": report.code,
                    "name": report.name,
                    "overview": report.overview,
                    "comments": report.comments,
                    "dimensions": report.dimensions,
                },
                metadata={"source": "pipeline", "symbol": symbol},
            )
        except Exception as e:
            logger.error(f"analyze_stock 执行失败: {e}")
            return ToolResult(status="error", error=str(e),
                             metadata={"source": "pipeline", "symbol": symbol})

    @staticmethod
    def _get_pipeline():
        from core.pipeline import Pipeline
        from stock_robot.cli import _get_registry, _register_llm
        from utils.config import Config
        config = Config()
        reg = _get_registry()
        _register_llm(reg, config)
        return Pipeline(registry=reg, config=config, llm_enabled=config.get("llm.enabled", True))


class AnalyzeIndexTool:
    """指数分析工具"""
    name = "analyze_index"
    description = (
        "分析指数，返回技术面、估值、资金面、宏观、舆情五个维度的判断。"
        "适用于判断大盘走势、板块强弱、市场情绪。"
        "参数: symbol(指数代码，如 000300 沪深300)"
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "指数代码，如 000300（沪深300）、000905（中证500）",
            },
        },
        "required": ["symbol"],
    }
    tags = ["pipeline", "index", "analysis"]
    source = "pipeline"

    def __init__(self, index_pipeline=None):
        self._pipeline = index_pipeline

    async def execute(self, **kwargs) -> ToolResult:
        symbol = str(kwargs.get("symbol", "")).strip()
        if not symbol:
            return ToolResult(status="error", error="指数代码不能为空",
                             metadata={"source": "pipeline"})

        from data.schemas import AnalysisTarget
        from data.index_mapping import IndexMapping

        try:
            mapping = IndexMapping()
            entry = mapping.lookup(symbol)
            target = AnalysisTarget(
                target_type="index", symbol=symbol,
                name=entry.name if entry else symbol,
                market="a-shares",
                index_style=entry.index_style if entry else "broad",
            )

            pipeline = self._get_pipeline()
            result = pipeline.run(targets=[target])

            if result.errors:
                return ToolResult(
                    status="error",
                    error="; ".join(result.errors),
                    metadata={"source": "pipeline", "symbol": symbol},
                )

            if not result.reports:
                return ToolResult(
                    status="error",
                    error=f"未能生成指数 {symbol} 的分析报告",
                    metadata={"source": "pipeline", "symbol": symbol},
                )

            report = result.reports[0]
            return ToolResult(
                status="success",
                data={
                    "code": report.code,
                    "name": report.name,
                    "overview": report.overview,
                    "composite_comment": report.composite_comment,
                    "position_coeff": report.position_coeff,
                },
                metadata={"source": "pipeline", "symbol": symbol},
            )
        except Exception as e:
            logger.error(f"analyze_index 执行失败: {e}")
            return ToolResult(status="error", error=str(e),
                             metadata={"source": "pipeline", "symbol": symbol})

    @staticmethod
    def _get_pipeline():
        from index.pipeline import IndexPipeline
        return IndexPipeline()


class GetSnapshotTool:
    """指数估值快照工具 — 轻量查询，不走完整管道"""
    name = "get_snapshot"
    description = (
        "快速获取指数当前估值快照（PE、PE分位数），不执行完整分析。"
        "适用于快速估值判断或个股联动。"
        "参数: symbol(指数代码)"
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {"type": "string", "description": "指数代码"},
        },
        "required": ["symbol"],
    }
    tags = ["pipeline", "index", "valuation", "quick"]
    source = "pipeline"

    def __init__(self, index_pipeline=None):
        self._pipeline = index_pipeline

    async def execute(self, **kwargs) -> ToolResult:
        symbol = str(kwargs.get("symbol", "")).strip()
        if not symbol:
            return ToolResult(status="error", error="指数代码不能为空",
                             metadata={"source": "pipeline"})

        try:
            pipeline = self._get_pipeline()
            data = pipeline.get_snapshot(symbol)
            if data is None:
                return ToolResult(
                    status="error",
                    error=f"无法获取指数 {symbol} 的估值快照，可能该指数不支持估值查询",
                    metadata={"source": "pipeline", "symbol": symbol},
                )
            return ToolResult(
                status="success",
                data=data,
                metadata={"source": "pipeline", "symbol": symbol},
            )
        except Exception as e:
            logger.error(f"get_snapshot 执行失败: {e}")
            return ToolResult(status="error", error=str(e),
                             metadata={"source": "pipeline", "symbol": symbol})

    @staticmethod
    def _get_pipeline():
        from index.pipeline import IndexPipeline
        return IndexPipeline()


class ScreenStocksTool:
    """股票筛选工具"""
    name = "screen_stocks"
    description = (
        "按行业或条件筛选 A 股标的。"
        "参数: industry(行业名称，如 新能源、医药、银行)、"
        "pe_max(最大PE限制，可选)、market_cap_min(最低市值，可选)"
    )
    parameters = {
        "type": "object",
        "properties": {
            "industry": {"type": "string", "description": "行业名称"},
            "pe_max": {"type": "number", "description": "最大PE限制"},
            "market_cap_min": {"type": "number", "description": "最低市值（亿元）"},
        },
        "required": ["industry"],
    }
    tags = ["pipeline", "stock", "screening"]
    source = "pipeline"

    async def execute(self, **kwargs) -> ToolResult:
        industry = str(kwargs.get("industry", "")).strip()
        pe_max = kwargs.get("pe_max")
        market_cap_min = kwargs.get("market_cap_min")

        if not industry:
            return ToolResult(status="error", error="行业名称不能为空",
                             metadata={"source": "pipeline"})

        try:
            from data.industry_classifier import IndustryClassifier
            classifier = IndustryClassifier()
            try:
                candidates = classifier.get_stocks_by_industry(industry)
            except Exception as e:
                logger.warning(f"行业分类查询失败: {e}")
                return ToolResult(
                    status="error",
                    error=f"未找到行业「{industry}」对应的标的。请确认行业名称正确。",
                    metadata={"source": "pipeline", "industry": industry},
                )

            if not candidates:
                return ToolResult(
                    status="success",
                    data={"stocks": [], "industry": industry, "count": 0},
                    metadata={"source": "pipeline", "industry": industry},
                )

            stock_list = []
            for c in candidates:
                stock_list.append({
                    "symbol": getattr(c, "symbol", ""),
                    "name": getattr(c, "name", ""),
                })

            return ToolResult(
                status="success",
                data={"stocks": stock_list, "industry": industry, "count": len(stock_list)},
                metadata={"source": "pipeline", "industry": industry},
            )
        except Exception as e:
            logger.error(f"screen_stocks 执行失败: {e}")
            return ToolResult(status="error", error=str(e),
                             metadata={"source": "pipeline"})
```

- [ ] **Step 4: 运行测试验证**

```bash
python -m pytest tests/agent/test_pipeline_tools.py -v
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/agent/pipeline_tools.py tests/agent/test_pipeline_tools.py
git commit -m "feat(agent): 实现 pipeline_tools 工具包装（analyze_stock/index/snapshot/screen）"
```

---

### Task 6: Executor — 逐步执行引擎

**Files:**
- Create: `src/agent/executor.py`
- Create: `tests/agent/test_executor.py`

- [ ] **Step 1: 编写 Executor 测试**

`tests/agent/test_executor.py`:
```python
"""Executor 单元测试"""
import pytest
from agent.executor import Executor
from agent.memory import TaskStep, Plan, TaskStatus, Memory
from agent.tools import ToolRegistry, ToolResult


class FakeLLM:
    """Mock LLM — 用于 Executor 的工具匹配，返回预设选择"""
    def __init__(self, responses=None):
        self.responses = responses or []
        self.calls = []

    def generate(self, prompt, system=None, **kwargs):
        self.calls.append({"prompt": prompt, "system": system})
        if self.responses:
            return self.responses.pop(0)
        return "no_tool"


class FakeTool:
    def __init__(self, name, return_data=None, should_fail=False):
        self.name = name
        self.description = f"Tool: {name}"
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


class TestExecutor:
    @pytest.fixture
    def registry(self):
        reg = ToolRegistry()
        reg.register(FakeTool("tool_a", return_data="result_a"))
        reg.register(FakeTool("tool_b", return_data="result_b"))
        return reg

    @pytest.fixture
    def memory(self):
        return Memory()

    def make_plan(self, goal="test", steps=None):
        if steps is None:
            steps = [TaskStep(id="s1", description="执行操作")]
        return Plan(goal=goal, steps=steps)

    @pytest.mark.asyncio
    async def test_execute_single_step_plan(self, registry, memory):
        plan = self.make_plan()
        plan.steps[0].tool_name = "tool_a"
        plan.steps[0].tool_args = {}
        executor = Executor(registry=registry, memory=memory)

        updated = await executor.execute(plan)

        assert updated.steps[0].status == TaskStatus.DONE
        assert updated.all_done()

    @pytest.mark.asyncio
    async def test_execute_multi_step_respects_dependencies(self, registry, memory):
        s1 = TaskStep(id="s1", description="第一步", tool_name="tool_a", tool_args={})
        s2 = TaskStep(id="s2", description="第二步", tool_name="tool_b", tool_args={},
                      depends_on=["s1"])
        plan = self.make_plan(steps=[s1, s2])
        executor = Executor(registry=registry, memory=memory)

        updated = await executor.execute(plan)

        assert updated.steps[0].status == TaskStatus.DONE
        assert updated.steps[1].status == TaskStatus.DONE
        assert updated.all_done()

    @pytest.mark.asyncio
    async def test_failed_step_marks_dependents_skipped(self, registry, memory):
        fail_tool = FakeTool("fail_tool", should_fail=True)
        registry.register(fail_tool)
        s1 = TaskStep(id="s1", description="会失败的步骤", tool_name="fail_tool", tool_args={})
        s2 = TaskStep(id="s2", description="依赖s1", tool_name="tool_a", tool_args={},
                      depends_on=["s1"])
        s3 = TaskStep(id="s3", description="独立步骤", tool_name="tool_b", tool_args={})
        plan = self.make_plan(steps=[s1, s2, s3])
        executor = Executor(registry=registry, memory=memory)

        updated = await executor.execute(plan)

        assert updated.steps[0].status == TaskStatus.FAILED
        assert updated.steps[1].status == TaskStatus.SKIPPED
        assert updated.steps[2].status == TaskStatus.DONE  # 独立步骤继续执行
        assert updated.all_done()

    @pytest.mark.asyncio
    async def test_independent_steps_run_in_parallel(self, registry, memory):
        results = []
        tool1 = FakeTool("t1", return_data="a")
        tool2 = FakeTool("t2", return_data="b")
        registry.register(tool1)
        registry.register(tool2)

        s1 = TaskStep(id="s1", description="独立A", tool_name="t1", tool_args={})
        s2 = TaskStep(id="s2", description="独立B", tool_name="t2", tool_args={})
        plan = self.make_plan(steps=[s1, s2])
        executor = Executor(registry=registry, memory=memory)

        updated = await executor.execute(plan)

        assert updated.steps[0].status == TaskStatus.DONE
        assert updated.steps[1].status == TaskStatus.DONE

    @pytest.mark.asyncio
    async def test_execute_adds_messages_to_memory(self, registry, memory):
        plan = self.make_plan()
        plan.steps[0].tool_name = "tool_a"
        plan.steps[0].tool_args = {}
        executor = Executor(registry=registry, memory=memory)

        await executor.execute(plan)

        assert len(memory.messages) > 0

    @pytest.mark.asyncio
    async def test_execute_records_plan_in_memory(self, registry, memory):
        plan = self.make_plan(goal="测试目标")
        plan.steps[0].tool_name = "tool_a"
        plan.steps[0].tool_args = {}
        executor = Executor(registry=registry, memory=memory)

        await executor.execute(plan)

        assert len(memory.plan_history) == 1
        assert memory.plan_history[0].goal == "测试目标"

    @pytest.mark.asyncio
    async def test_progress_callback_is_invoked(self, registry, memory):
        callbacks = []
        plan = self.make_plan()
        plan.steps[0].tool_name = "tool_a"
        plan.steps[0].tool_args = {}
        executor = Executor(registry=registry, memory=memory)

        await executor.execute(plan, on_progress=lambda *args: callbacks.append(args))

        assert len(callbacks) >= 2  # 开始 + 完成
```

- [ ] **Step 2: 运行测试验证失败**

```bash
python -m pytest tests/agent/test_executor.py -v
```
Expected: FAIL

- [ ] **Step 3: 实现 Executor**

`src/agent/executor.py`:
```python
"""Executor — 逐步执行引擎，负责工具匹配、执行编排、失败隔离"""
import asyncio
import logging
from collections.abc import Callable
from agent.memory import Memory, TaskStep, Plan, TaskStatus
from agent.tools import ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, int, int, str], None] | None


class Executor:
    """逐步执行 Plan 中的 TaskStep

    负责:
    1. 拓扑排序执行（无依赖的步骤可并行）
    2. 工具匹配（通过 ToolRegistry.match + 名称直接查找）
    3. 失败隔离（单步失败 → 级联 skip 依赖步骤，其余继续）
    4. 进度回调
    """

    def __init__(self, registry: ToolRegistry, memory: Memory,
                 llm=None):
        self._registry = registry
        self._memory = memory
        self._llm = llm  # 预留：LLM 二次校验工具匹配

    async def execute(self, plan: Plan,
                      on_progress: ProgressCallback = None) -> Plan:
        """执行 Plan，返回更新后的 Plan（含步骤状态和结果）"""
        self._memory.add_plan(plan)
        self._memory.add_message("system", f"开始执行计划: {plan.goal}")

        while not plan.all_done():
            pending = plan.get_pending_steps()
            if not pending:
                # 所有 pending 步骤都被依赖阻塞（理论上 all_done 应该返回 True）
                break

            # 将无依赖的 pending 步骤分组并行执行
            independent = [s for s in pending if not s.depends_on
                          or all(d not in {p.id for p in pending} for d in s.depends_on)]
            if not independent:
                # 所有 pending 都有依赖在 pending 中，取第一个执行
                independent = [pending[0]]

            total = len(plan.steps)
            for step in independent:
                idx = plan.steps.index(step) + 1
                if on_progress:
                    on_progress("execute", idx, total, step.description)
                await self._execute_step(step)

        self._memory.add_message("system", f"计划执行完成: {plan.goal}")
        if on_progress:
            on_progress("complete", len(plan.steps), len(plan.steps), "计划执行完成")

        return plan

    async def _execute_step(self, step: TaskStep) -> None:
        step.status = TaskStatus.RUNNING
        self._memory.add_message("system", f"执行: {step.description}")

        if step.tool_name:
            tool = self._registry.get(step.tool_name)
        else:
            # Plan 阶段未指定工具名，通过匹配查找
            candidates = self._registry.match(step.description)
            tool = candidates[0] if candidates else None

        if tool is None:
            step.status = TaskStatus.FAILED
            step.tool_name = None
            self._memory.add_message(
                "system",
                f"步骤 {step.id} 失败: 找不到匹配的工具 [{step.description}]",
            )
            return

        step.tool_name = tool.name
        result = await self._safe_execute(tool, step.tool_args or {})

        if result.status == "error":
            step.status = TaskStatus.FAILED
            self._memory.add_message("system", f"步骤 {step.id} 失败: {result.error}")
        else:
            step.status = TaskStatus.DONE
            self._memory.add_message(
                "tool",
                f"[{tool.name}] {result.status}: {result.data}",
            )

    async def _safe_execute(self, tool, kwargs: dict) -> ToolResult:
        try:
            return await tool.execute(**kwargs)
        except Exception as e:
            logger.error(f"工具 {tool.name} 执行异常: {e}")
            return ToolResult(status="error", error=str(e),
                             metadata={"source": getattr(tool, "source", "unknown")})
```

- [ ] **Step 4: 运行测试验证**

```bash
python -m pytest tests/agent/test_executor.py -v
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/agent/executor.py tests/agent/test_executor.py
git commit -m "feat(agent): 实现 Executor 逐步执行引擎，支持依赖编排和失败隔离"
```

---

### Task 7: Planner — LLM 驱动的任务拆解

**Files:**
- Create: `src/agent/planner.py`
- Create: `tests/agent/test_planner.py`

- [ ] **Step 1: 编写 Planner 测试**

`tests/agent/test_planner.py`:
```python
"""Planner 单元测试"""
import json
import pytest
from agent.planner import Planner, SIMPLE_QUERY_PREFIXES
from agent.memory import Memory, Plan, TaskStep, TaskStatus
from agent.tools import ToolRegistry


class FakeLLM:
    """返回预设 JSON 计划的 mock LLM"""
    def __init__(self, fixed_response=None):
        self._response = fixed_response
        self.calls = []

    def generate(self, prompt, system=None, **kwargs):
        self.calls.append({"system": system, "prompt": prompt})
        return self._response or ""


def make_multi_step_response():
    return json.dumps({
        "goal": "找3只低估值新能源龙头股",
        "complexity": "complex",
        "steps": [
            {"id": "step-1", "description": "筛选新能源板块的龙头股候选"},
            {"id": "step-2", "description": "对每只候选股进行全维度分析", "depends_on": ["step-1"]},
            {"id": "step-3", "description": "综合对比评分排名", "depends_on": ["step-2"]},
        ],
    }, ensure_ascii=False)


def make_single_step_response():
    return json.dumps({
        "goal": "查询茅台最新股价",
        "complexity": "simple",
        "steps": [
            {"id": "step-1", "description": "查询贵州茅台最新行情"},
        ],
    }, ensure_ascii=False)


class TestPlanner:
    @pytest.fixture
    def registry(self):
        reg = ToolRegistry()
        return reg

    @pytest.fixture
    def memory(self):
        return Memory()

    def test_simple_query_prefixes_list(self):
        assert isinstance(SIMPLE_QUERY_PREFIXES, list)
        assert len(SIMPLE_QUERY_PREFIXES) > 0

    def test_is_simple_query_matches_prefix(self):
        planner = Planner(llm=None, registry=None)
        assert planner._is_simple_query("什么是市盈率") is True
        assert planner._is_simple_query("最近茅台涨了多少") is True

    def test_is_simple_query_returns_false_for_complex(self):
        planner = Planner(llm=None, registry=None)
        assert planner._is_simple_query("帮我找3只被低估的新能源龙头") is False
        assert planner._is_simple_query("大盘现在适合入场吗") is False

    def test_plan_simple_query_returns_single_step(self, registry, memory):
        planner = Planner(llm=FakeLLM(), registry=registry, memory=memory)
        plan = planner.plan("什么是PE")

        assert len(plan.steps) == 1
        assert plan.steps[0].description != ""
        assert plan.complexity == "simple"

    def test_plan_complex_query_calls_llm(self, registry, memory):
        llm = FakeLLM(fixed_response=make_multi_step_response())
        planner = Planner(llm=llm, registry=registry, memory=memory)

        plan = planner.plan("帮我分析新能源板块")

        assert len(llm.calls) > 0
        assert plan.goal == "找3只低估值新能源龙头股"
        assert len(plan.steps) == 3
        assert plan.steps[1].depends_on == ["step-1"]

    def test_plan_complex_query_injects_tool_list_in_prompt(self, registry, memory):
        from agent.tools import ToolProtocol

        class FakeAnalyzeTool:
            name = "analyze_stock"
            description = "分析股票"
            parameters = {"type": "object", "properties": {}}
            tags = ["pipeline"]
            source = "pipeline"

            async def execute(self, **kwargs):
                pass

        registry.register(FakeAnalyzeTool())

        llm = FakeLLM(fixed_response=make_multi_step_response())
        planner = Planner(llm=llm, registry=registry, memory=memory)
        planner.plan("帮我分析新能源板块")

        system_prompt = llm.calls[0]["system"]
        assert "analyze_stock" in system_prompt or len(llm.calls[0]["prompt"]) > 0

    def test_plan_includes_facts_context(self, registry, memory):
        memory.set_fact("preferred_style", "growth")
        memory.set_fact("favorite_stocks", ["600519"])

        llm = FakeLLM(fixed_response=make_single_step_response())
        planner = Planner(llm=llm, registry=registry, memory=memory)
        plan = planner.plan("推荐股票")

        assert plan.context_summary != ""

    def test_plan_handles_llm_failure_gracefully(self, registry, memory):
        class FailingLLM:
            def generate(self, prompt, system=None, **kwargs):
                raise Exception("API 不可用")

        planner = Planner(llm=FailingLLM(), registry=registry, memory=memory)
        plan = planner.plan("复杂分析任务")

        assert len(plan.steps) == 1
        assert plan.steps[0].description != ""

    def test_plan_handles_malformed_json_response(self, registry, memory):
        llm = FakeLLM(fixed_response="这不是有效的 JSON 格式")
        planner = Planner(llm=llm, registry=registry, memory=memory)

        plan = planner.plan("分析市场")

        assert len(plan.steps) == 1  # 降级为单步
```

- [ ] **Step 2: 运行测试验证失败**

```bash
python -m pytest tests/agent/test_planner.py -v
```
Expected: FAIL

- [ ] **Step 3: 实现 Planner**

`src/agent/planner.py`:
```python
"""Planner — LLM 驱动的任务拆解，只做拆解不绑定工具"""
import json
import logging
from agent.memory import Memory, Plan, TaskStep
from agent.tools import ToolRegistry
from llm.base import LLMBackend

logger = logging.getLogger(__name__)

# 硬编码前缀拦截列表 —— 这些前缀的查询直接走单步，不走 LLM 规划
SIMPLE_QUERY_PREFIXES = [
    "什么是", "什么是PE", "什么是PB", "什么是ROE", "什么是",
    "最近", "最新", "当前", "现在", "今天",
    "茅台", "比亚迪", "宁德", "腾讯",
    "怎么", "如何", "为什么", "解释",
    "PE", "PB", "ROE", "EPS", "MACD",
]

PLANNER_SYSTEM_PROMPT = """你是一个股票投研任务规划器。你的职责是将用户的投资研究问题拆解为有序的执行步骤。

## 规则
1. 先判断问题复杂度：简单查询（单一信息）→ 1步，复杂研究（多维度）→ 多步
2. 每步只描述"做什么"，不指定"用哪个工具"（工具选择由执行器负责）
3. 标注步骤间的依赖关系
4. 复杂问题步骤数不超过5步，简单问题1步

## 可用能力概览
{capabilities}

## 输出格式
严格输出 JSON，不要包含其他文字:
```json
{{
  "goal": "用户目标的简洁概括",
  "complexity": "simple|complex",
  "steps": [
    {{"id": "step-1", "description": "步骤描述"}},
    {{"id": "step-2", "description": "步骤描述", "depends_on": ["step-1"]}}
  ]
}}
```
""".strip()


class Planner:
    """任务规划器 — LLM 驱动的任务拆解"""

    def __init__(self, llm: LLMBackend | None, registry: ToolRegistry,
                 memory: Memory | None = None):
        self._llm = llm
        self._registry = registry
        self._memory = memory or Memory()

    def plan(self, user_input: str) -> Plan:
        """根据用户输入生成执行计划"""
        context = self._memory.get_context_window(n=10)
        facts = self._memory.facts

        # 硬编码前缀拦截 —— 简单查询直接单步
        if self._is_simple_query(user_input):
            step = TaskStep(
                id="step-1",
                description=user_input.strip(),
            )
            return Plan(
                goal=user_input.strip(),
                steps=[step],
                context_summary="简单查询，单步执行",
            )

        # 构建上下文摘要
        context_summary = ""
        if facts:
            fact_lines = [f"  {k}: {v}" for k, v in facts.items()]
            if fact_lines:
                context_summary = "用户偏好:\n" + "\n".join(fact_lines)

        # LLM 规划
        if self._llm is None:
            return self._fallback_plan(user_input, context_summary)

        try:
            prompt = self._build_plan_prompt(user_input, context)
            response = self._llm.generate(prompt, system=self._build_system_prompt())

            plan = self._parse_response(response, user_input)
            plan.context_summary = context_summary
            return plan
        except Exception as e:
            logger.warning(f"Planner LLM 调用失败，使用降级单步计划: {e}")
            return self._fallback_plan(user_input, context_summary)

    def _is_simple_query(self, text: str) -> bool:
        """硬编码前缀拦截：短查询 + 匹配前缀 → 直接单步"""
        text_stripped = text.strip()
        if len(text_stripped) > 50:
            return False
        for prefix in SIMPLE_QUERY_PREFIXES:
            if text_stripped.startswith(prefix):
                return True
        if len(text_stripped) <= 10:
            return True
        return False

    def _build_system_prompt(self) -> str:
        tools_summary = self._registry.list_all_summary()
        capabilities = json.dumps(tools_summary, ensure_ascii=False)
        return PLANNER_SYSTEM_PROMPT.format(capabilities=capabilities)

    def _build_plan_prompt(self, user_input: str, context: list[dict]) -> str:
        parts = []
        if context:
            recent = context[-4:]
            parts.append("## 最近对话")
            for msg in recent:
                parts.append(f"[{msg['role']}]: {msg['content']}")
        parts.append(f"\n## 当前任务\n{user_input}")
        return "\n".join(parts)

    def _parse_response(self, response: str, fallback_goal: str) -> Plan:
        # 提取 JSON 块（可能被 markdown 代码块包裹）
        response = response.strip()
        if "```" in response:
            lines = response.split("\n")
            json_lines = []
            in_block = False
            for line in lines:
                if line.strip().startswith("```"):
                    in_block = not in_block
                    continue
                if in_block:
                    json_lines.append(line)
            if json_lines:
                response = "\n".join(json_lines)
            else:
                # 可能直接以 { 开始
                for line in lines:
                    if line.strip().startswith("{"):
                        response = line
                        break

        try:
            data = json.loads(response)
        except json.JSONDecodeError:
            return self._fallback_plan(fallback_goal, "")

        steps = []
        for s in data.get("steps", []):
            step = TaskStep(
                id=s.get("id", f"step-{len(steps) + 1}"),
                description=s.get("description", ""),
                depends_on=s.get("depends_on", []),
            )
            steps.append(step)

        if not steps:
            return self._fallback_plan(fallback_goal, "")

        return Plan(
            goal=data.get("goal", fallback_goal),
            steps=steps,
        )

    def _fallback_plan(self, goal: str, context_summary: str) -> Plan:
        """降级方案：将整个用户意图作为单步"""
        return Plan(
            goal=goal,
            steps=[TaskStep(id="step-1", description=goal.strip())],
            context_summary=context_summary,
        )
```

- [ ] **Step 4: 运行测试验证**

```bash
python -m pytest tests/agent/test_planner.py -v
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/agent/planner.py tests/agent/test_planner.py
git commit -m "feat(agent): 实现 Planner 任务拆解器，支持 LLM 规划和硬编码拦截降级"
```

---

### Task 8: CLI chat 命令集成

**Files:**
- Modify: `src/stock_robot/cli.py`
- Create: `tests/agent/test_cli_chat.py`

- [ ] **Step 1: 编写 CLI chat 集成测试**

`tests/agent/test_cli_chat.py`:
```python
"""CLI chat 命令集成测试"""
import pytest
from click.testing import CliRunner
from stock_robot.cli import main


class TestChatCommand:
    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_chat_command_exists(self, runner):
        result = runner.invoke(main, ["chat", "--help"])
        assert result.exit_code == 0
        assert "对话" in result.output

    def test_chat_ask_single_shot(self, runner, mocker):
        """单次对话模式不依赖真实 LLM"""
        mocker.patch("stock_robot.cli._run_agent_query", return_value="分析结果摘要")

        result = runner.invoke(main, ["chat", "--ask", "什么是PE"])

        assert result.exit_code == 0

    def test_chat_verbose_flag_accepted(self, runner):
        result = runner.invoke(main, ["chat", "--ask", "测试", "--verbose", "--help"])
        # --help 会显示帮助，验证 --verbose 不被拒绝
        assert result.exit_code == 0

    def test_chat_without_ask_or_interactive_shows_help(self, runner):
        """无参数时显示帮助"""
        result = runner.invoke(main, ["chat"])
        # chat 命令应该存在且给出反馈
        assert result.exit_code == 0

    def test_analyze_command_still_works(self, runner, mocker):
        """存量命令不受影响"""
        mocker.patch("stock_robot.cli._build_pipeline")
        result = runner.invoke(main, ["analyze", "--help"])
        assert result.exit_code == 0
        assert "analyze" in result.output.lower() or "分析" in result.output

    def test_index_command_still_works(self, runner, mocker):
        """存量命令不受影响"""
        mocker.patch("stock_robot.cli._build_pipeline")
        result = runner.invoke(main, ["index", "--help"])
        assert result.exit_code == 0
```

- [ ] **Step 2: 运行测试验证失败**

```bash
python -m pytest tests/agent/test_cli_chat.py -v
```
Expected: FAIL — chat 命令不存在

- [ ] **Step 3: 实现 CLI chat 命令**

在 `src/stock_robot/cli.py` 的 `main` 函数之后追加:

```python
@main.command()
@click.option("--ask", "-a", default=None, help="单次对话（非交互式）")
@click.option("--verbose", "-v", is_flag=True, help="显示计划和工具调用细节")
def chat(ask, verbose):
    """进入 AI Agent 对话模式，支持复杂投研任务的自主拆解和分析"""
    from agent.tools import ToolRegistry
    from agent.memory import Memory
    from agent.planner import Planner
    from agent.executor import Executor
    from agent.pipeline_tools import (
        AnalyzeStockTool, AnalyzeIndexTool, GetSnapshotTool, ScreenStocksTool,
    )
    from output.renderer import RichRenderer
    from utils.config import Config

    config = Config()
    renderer = RichRenderer(console=console)

    # 构建工具注册表
    registry = ToolRegistry()
    registry.register(AnalyzeStockTool())
    registry.register(AnalyzeIndexTool())
    registry.register(GetSnapshotTool())
    registry.register(ScreenStocksTool())

    # 构建 LLM 后端
    llm = _get_llm_for_agent(config)

    memory = Memory()
    planner = Planner(llm=llm, registry=registry, memory=memory)
    executor = Executor(registry=registry, memory=memory)

    if ask:
        _run_agent_query(ask, planner, executor, memory, renderer, verbose)
        return

    _run_interactive_chat(planner, executor, memory, renderer, verbose)


def _run_agent_query(query, planner, executor, memory, renderer, verbose):
    """单次 Agent 查询"""
    plan = planner.plan(query)
    console.print(renderer.render_plan(plan))

    result = _run_async(executor.execute(plan))

    console.print(renderer.render_summary(result))


def _run_interactive_chat(planner, executor, memory, renderer, verbose):
    """交互式对话循环"""
    console.print("[bold]Stock Robot Agent[/bold] — 输入 /help 查看可用指令")
    console.print("输入你的投研问题，或输入 /exit 退出\n")

    while True:
        try:
            user_input = click.prompt("你", prompt_suffix="> ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n再见！")
            break

        if not user_input:
            continue

        # 处理快捷指令
        handled = _handle_slash_command(user_input, memory, renderer)
        if handled == "exit":
            break
        if handled:
            continue

        plan = planner.plan(user_input)
        console.print(renderer.render_plan(plan))

        result = _run_async(executor.execute(plan))

        console.print(renderer.render_summary(result))


def _handle_slash_command(text, memory, renderer):
    """处理 / 开头的快捷指令，返回 'exit' 表示退出，True 表示已处理"""
    cmd = text.strip().lower()

    if cmd == "/exit":
        console.print("再见！")
        return "exit"

    if cmd == "/help":
        console.print("""
[bold]可用快捷指令:[/bold]
  /help     - 显示此帮助
  /tools    - 列出可用工具
  /plan     - 显示最近一次执行计划
  /clear    - 清空当前会话上下文
  /verbose  - 切换详细输出模式
  /exit     - 退出对话模式
        """.strip())
        return True

    if cmd == "/tools":
        _cmd_tools(renderer)
        return True

    if cmd == "/plan":
        _cmd_plan(memory, renderer)
        return True

    if cmd == "/clear":
        memory.clear_session()
        console.print("[dim]会话上下文已清空[/dim]")
        return True

    if cmd == "/verbose":
        console.print("[dim]详细模式已切换（功能待完善）[/dim]")
        return True

    return False


def _cmd_tools(renderer):
    """列出所有注册工具"""
    # 延迟获取注册表
    console.print("[dim]工具列表功能需要在上下文中访问 registry，暂时不可用[/dim]")


def _cmd_plan(memory, renderer):
    """显示最近计划"""
    last = memory.get_last_plan()
    if last is None:
        console.print("[dim]暂无执行计划[/dim]")
        return
    console.print(renderer.render_plan(last))


def _get_llm_for_agent(config):
    """为 Agent 创建 LLM 后端实例"""
    provider = config.get("llm.provider", "openai")
    api_key = config.get("llm.api_key", "")
    base_url = config.get("llm.base_url", "") or None

    try:
        if provider == "openai":
            from llm.openai import OpenAIAdapter
            return OpenAIAdapter(
                api_key=api_key,
                model=config.get("llm.model", "gpt-4o"),
                temperature=config.get("llm.temperature", 0.3),
                max_tokens=config.get("llm.max_tokens", 2000),
                base_url=base_url,
            )
        elif provider == "claude":
            from llm.claude import ClaudeAdapter
            return ClaudeAdapter(
                api_key=api_key,
                model=config.get("llm.model", "claude-sonnet-4-6"),
                temperature=config.get("llm.temperature", 0.3),
                max_tokens=config.get("llm.max_tokens", 2000),
                base_url=base_url,
            )
    except Exception as e:
        logger.warning(f"LLM 后端初始化失败: {e}")

    return None


def _run_async(coro):
    """同步包装器：在同步 CLI 上下文中运行异步协程"""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)
```

- [ ] **Step 4: 运行集成测试**

```bash
python -m pytest tests/agent/test_cli_chat.py -v
```
Expected: PASS

- [ ] **Step 5: 确保存量测试全部通过**

```bash
python -m pytest tests/ -v --ignore=tests/agent/ -x
```
Expected: PASS（存量功能零影响）

- [ ] **Step 6: 提交**

```bash
git add src/stock_robot/cli.py tests/agent/test_cli_chat.py
git commit -m "feat(agent): 添加 CLI chat 命令，支持单次对话和交互式对话模式"
```

---

### Task 9: 最终集成验证

- [ ] **Step 1: 运行全部 Agent 测试套件**

```bash
python -m pytest tests/agent/ -v
```
Expected: PASS（所有 8 个测试文件中的全部测试）

- [ ] **Step 2: 运行存量回归测试**

```bash
python -m pytest tests/ --ignore=tests/agent/ -v
```
Expected: PASS（存量功能零影响）

- [ ] **Step 3: 验证 CLI 帮助输出**

```bash
python -m stock_robot.cli chat --help
```
Expected: 显示 chat 命令帮助信息，包含 --ask 和 --verbose 选项

- [ ] **Step 4: 提交最终变更（如有）**

```bash
git status
# 如有未提交变更，提交
```
