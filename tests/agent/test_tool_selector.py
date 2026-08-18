"""ToolSelector 单元测试 — LLM tool calling 决策与降级阶梯"""
import pytest

from agent.tool_selector import ToolSelector
from agent.tools import ToolRegistry, ToolResult
from tests.agent.fake_chat_model import FakeChatModel, make_tool_call


class FakeTool:
    def __init__(self, name, description=None):
        self.name = name
        self.description = description or f"Tool: {name}"
        self.parameters = {"type": "object", "properties": {}}
        self.tags = ["test"]
        self.source = "pipeline"

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(status="success")


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

        name, args = await selector.select(
            make_step("分析 000001 估值，查询 知识库"), [], [])

        assert name == "analyze_stock"
        assert args == {"symbol": "000001"}
        # 候选工具应绑定给 LLM（不超过 4 个）
        assert model.bound_tools is not None
        assert len(model.bound_tools) == 2
        # match 同分候选顺序依赖 set 迭代顺序（非确定），只断言候选集合
        assert {t["name"] for t in model.bound_tools} == {"analyze_stock", "rag_search"}

    @pytest.mark.asyncio
    async def test_llm_empty_tool_calls_falls_back_to_keyword(self):
        model = FakeChatModel(tool_calls=[])
        selector = ToolSelector(registry=make_registry(), model=model)

        name, _ = await selector.select(
            make_step("执行 analyze_stock 操作"), [], [])

        assert name == "analyze_stock"

    @pytest.mark.asyncio
    async def test_llm_invalid_tool_name_retries_then_falls_back(self):
        model = FakeChatModel(tool_calls=[make_tool_call("not_exist", {})])
        selector = ToolSelector(registry=make_registry(), model=model)

        name, _ = await selector.select(make_step(), [], [])

        # 非法工具名 → 一次强制重试 → 仍非法 → 关键词降级
        assert name == "analyze_stock"
        assert len(model.calls) == 2

    @pytest.mark.asyncio
    async def test_llm_exception_falls_back_to_keyword(self):
        class ExplodingModel(FakeChatModel):
            async def ainvoke(self, messages, **kwargs):
                raise RuntimeError("API 不可用")

        selector = ToolSelector(registry=make_registry(), model=ExplodingModel())

        name, _ = await selector.select(make_step("执行 rag_search 检索"), [], [])

        assert name == "rag_search"

    @pytest.mark.asyncio
    async def test_no_model_uses_keyword_fallback(self):
        selector = ToolSelector(registry=make_registry(), model=None)

        name, _ = await selector.select(make_step(), [], [])

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
