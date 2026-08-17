"""Executor 单元测试"""
import pytest

from agent.executor import Executor, _extract_tool_args
from agent.memory import Memory, Plan, TaskStatus, TaskStep
from agent.tools import ToolRegistry, ToolResult


def test_extract_tool_args_symbol():
    """_extract_tool_args 仅从确定性工具的步骤描述提取 6 位代码"""
    assert _extract_tool_args("analyze_stock", "分析 000001 的财务数据") == {"symbol": "000001"}
    assert _extract_tool_args("analyze_stock", "没有代码的描述") == {}
    assert _extract_tool_args("screen_stocks", "筛选新能源股票") == {}


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
    async def test_independent_steps_run_correctly(self, registry, memory):
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
    async def test_execute_with_missing_tool_marks_failed(self, registry, memory):
        plan = self.make_plan()
        plan.steps[0].tool_name = "nonexistent_tool"
        plan.steps[0].tool_args = {}
        executor = Executor(registry=registry, memory=memory)

        updated = await executor.execute(plan)

        assert updated.steps[0].status == TaskStatus.FAILED

    @pytest.mark.asyncio
    async def test_match_tool_by_description_when_no_tool_name(self, registry, memory):
        s1 = TaskStep(id="s1", description="执行 tool_a 操作", tool_name=None)
        plan = self.make_plan(steps=[s1])
        executor = Executor(registry=registry, memory=memory)

        updated = await executor.execute(plan)

        assert updated.steps[0].tool_name == "tool_a"
        assert updated.steps[0].status == TaskStatus.DONE

    @pytest.mark.asyncio
    async def test_extracts_symbol_from_description(self, registry, memory):
        """工具名匹配成功但无参数时，从步骤描述提取 6 位股票代码"""
        analyze_tool = FakeTool("analyze_stock",
                                description="对股票进行分析，输出估值报告",
                                return_data="分析完成")
        registry.register(analyze_tool)
        s1 = TaskStep(id="s1", description="分析 000001 的估值", tool_name=None)
        plan = self.make_plan(steps=[s1])
        executor = Executor(registry=registry, memory=memory)

        updated = await executor.execute(plan)

        assert updated.steps[0].tool_name == "analyze_stock"
        assert updated.steps[0].status == TaskStatus.DONE
        assert analyze_tool.execute_calls == [{"symbol": "000001"}]
