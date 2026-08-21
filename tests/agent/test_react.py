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


class RaisingTool:
    name = "raise_tool"
    description = "执行时抛异常的工具"
    parameters = {"type": "object", "properties": {}}
    tags = ["test"]
    source = "pipeline"

    async def execute(self, **kwargs):
        raise RuntimeError("内部崩溃")


def make_registry():
    registry = ToolRegistry()
    registry.register(EchoTool())
    registry.register(FailingTool())
    registry.register(RaisingTool())
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
                             model=model, session_id="test-5", recursion_limit=3)

    with pytest.raises(GraphRecursionError):
        await executor.run("循环")


@pytest.mark.asyncio
async def test_run_tool_exception_is_error():
    """工具抛真异常：隔离为错误文本回注，状态记为 error 而非误判 success"""
    model = make_model([
        AIMessage(content="", tool_calls=[
            {"name": "raise_tool", "args": {}, "id": "call_7"}]),
        AIMessage(content="已处理", tool_calls=[]),
    ])
    executor = ReActExecutor(registry=make_registry(), memory=Memory(),
                             model=model, session_id="test-7")

    outcome = await executor.run("触发崩溃")

    assert outcome.tool_calls[0]["status"] == "error"
    assert "内部崩溃" in outcome.tool_calls[0]["summary"]


@pytest.mark.asyncio
async def test_run_parallel_tool_calls_paired_by_run_id():
    """单条消息多 tool_call 并行执行：事件按 run_id 配对，统计与 memory 不串名"""
    model = make_model([
        AIMessage(content="", tool_calls=[
            {"name": "echo", "args": {"text": "a"}, "id": "call_8"},
            {"name": "fail_tool", "args": {}, "id": "call_9"},
        ]),
        AIMessage(content="并行完成", tool_calls=[]),
    ])
    events = []
    executor = ReActExecutor(registry=make_registry(), memory=Memory(),
                             model=model, session_id="test-8")

    outcome = await executor.run("并行执行", on_event=events.append)

    by_name = {tc["tool"]: tc for tc in outcome.tool_calls}
    assert set(by_name) == {"echo", "fail_tool"}
    assert by_name["echo"]["status"] == "success"
    assert by_name["fail_tool"]["status"] == "error"
    # 每个 tool_call 事件都有同 run_id 的 tool_result 事件，且工具名一致
    calls = {e["run_id"]: e["tool"] for e in events if e["type"] == "tool_call"}
    results = {e["run_id"]: e["tool"] for e in events if e["type"] == "tool_result"}
    assert calls == results


@pytest.mark.asyncio
async def test_run_model_none_raises():
    executor = ReActExecutor(registry=make_registry(), memory=Memory(),
                             model=None, session_id="test-6")
    with pytest.raises(RuntimeError):
        await executor.run("任务")
