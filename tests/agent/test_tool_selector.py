"""ToolSelector 单元测试 — LLM tool calling 决策与降级阶梯"""
import pytest

from agent.tool_selector import ToolSelector
from agent.tools import ToolRegistry, ToolResult
from tests.agent.fake_chat_model import (
    FakeAIMessage,
    FakeChatModel,
    make_tool_call,
)


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

        name, args, source = await selector.select(
            make_step("分析 000001 估值，查询 知识库"), [], [])

        assert name == "analyze_stock"
        assert args == {"symbol": "000001"}
        assert source == "llm"
        # 候选工具应绑定给 LLM（不超过 4 个）
        assert model.bound_tools is not None
        assert len(model.bound_tools) == 2
        # match 同分候选顺序依赖 set 迭代顺序（非确定），只断言候选集合
        assert {t["name"] for t in model.bound_tools} == {"analyze_stock", "rag_search"}

    @pytest.mark.asyncio
    async def test_llm_empty_tool_calls_falls_back_to_keyword(self):
        model = FakeChatModel(tool_calls=[])
        selector = ToolSelector(registry=make_registry(), model=model)

        name, _, source = await selector.select(
            make_step("执行 analyze_stock 操作"), [], [])

        assert name == "analyze_stock"
        assert source == "fallback"

    @pytest.mark.asyncio
    async def test_llm_invalid_tool_name_retries_then_falls_back(self):
        model = FakeChatModel(tool_calls=[make_tool_call("not_exist", {})])
        selector = ToolSelector(registry=make_registry(), model=model)

        name, _, source = await selector.select(make_step(), [], [])

        # 非法工具名 → 一次强制重试 → 仍非法 → 关键词降级
        assert name == "analyze_stock"
        assert source == "fallback"
        assert len(model.calls) == 2

    @pytest.mark.asyncio
    async def test_retry_succeeds_when_second_response_valid(self):
        class RetryModel(FakeChatModel):
            def __init__(self):
                super().__init__()
                self._responses = [
                    [make_tool_call("not_exist", {}, call_id="call_bad")],
                    [make_tool_call("analyze_stock", {"symbol": "000001"},
                                    call_id="call_ok")],
                ]

            async def ainvoke(self, messages, **kwargs):
                self.calls.append(messages)
                return FakeAIMessage(tool_calls=self._responses.pop(0))

        model = RetryModel()
        selector = ToolSelector(registry=make_registry(), model=model)

        name, args, source = await selector.select(make_step(), [], [])

        # 重试轮返回合法工具 → 直接返回，验证重试不是死代码
        assert name == "analyze_stock"
        assert args == {"symbol": "000001"}
        assert source == "llm"
        assert len(model.calls) == 2

    @pytest.mark.asyncio
    async def test_retry_messages_include_assistant_and_tool_response(self):
        model = FakeChatModel(tool_calls=[make_tool_call("not_exist", {},
                                                         call_id="call_x")])
        selector = ToolSelector(registry=make_registry(), model=model)

        await selector.select(make_step(), [], [])

        second = model.calls[1]
        # 重试消息在 system+user 之后补回首次 assistant 响应（含 tool_calls），
        # tool 回应紧随其后，tool_call_id 与 assistant tool call 匹配
        assistant_msgs = [m for m in second if m.get("role") == "assistant"]
        assert len(assistant_msgs) == 1
        assert assistant_msgs[0]["tool_calls"][0]["id"] == "call_x"
        tool_msgs = [m for m in second if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "call_x"
        # assistant 消息紧跟 tool 回应（provider 校验顺序要求）
        assert second.index(tool_msgs[0]) == second.index(assistant_msgs[0]) + 1

    @pytest.mark.asyncio
    async def test_llm_exception_falls_back_to_keyword(self):
        class ExplodingModel(FakeChatModel):
            async def ainvoke(self, messages, **kwargs):
                raise RuntimeError("API 不可用")

        selector = ToolSelector(registry=make_registry(), model=ExplodingModel())

        name, _, source = await selector.select(
            make_step("执行 rag_search 检索"), [], [])

        assert name == "rag_search"
        assert source == "fallback"

    @pytest.mark.asyncio
    async def test_no_model_uses_keyword_fallback(self):
        selector = ToolSelector(registry=make_registry(), model=None)

        name, _, source = await selector.select(make_step(), [], [])

        assert name == "analyze_stock"
        assert source == "fallback"

    @pytest.mark.asyncio
    async def test_keyword_fallback_extracts_symbol(self):
        selector = ToolSelector(registry=make_registry(), model=None)

        name, args, source = await selector.select(
            make_step("分析 000001 的估值"), [], [])

        assert name == "analyze_stock"
        assert args == {"symbol": "000001"}
        assert source == "fallback"

    @pytest.mark.asyncio
    async def test_no_candidates_returns_none(self):
        selector = ToolSelector(registry=make_registry(), model=None)

        name, args, source = await selector.select(
            make_step("完全无关的描述"), [], [])

        assert (name, args, source) == (None, None, "fallback")

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
