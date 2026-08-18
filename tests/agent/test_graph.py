"""LangGraph 执行图单元测试（真实跑图，仅 mock LLM 模型）"""
import pytest

from agent.graph import build_execution_graph
from agent.memory import Memory, Plan, TaskStep
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

        # 首次执行填充 checkpoint（返回值无需断言）
        await graph.ainvoke(plan_to_state(plan), config=cfg)
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
