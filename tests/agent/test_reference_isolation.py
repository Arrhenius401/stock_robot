"""用户归档保留历史参考，但所有模型文本路径只消费当前事实。"""
import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from agent.memory import Memory, TaskStep
from agent.react import ReActExecutor, _as_lc_tool
from agent.tool_selector import ToolSelector
from agent.tools import ToolRegistry, ToolResult
from tests.agent.fake_chat_model import FakeChatModel


def payload():
    return {"symbol": "000001", "data_quality": {"dimensions": [{"fields": [
        {"label": "当前营收", "value": "123"},
        {"label": "历史参考", "value": "旧参考独有987654321", "reference_only": True}]}]}}


class StockTool:
    name = "analyze_stock"
    description = "报告测试"
    parameters = {"type": "object", "properties": {}}
    tags = ["test"]
    source = "pipeline"

    async def execute(self, **kwargs):
        return ToolResult(status="success", data=payload())


@pytest.mark.asyncio
async def test_langchain_tool_separates_model_content_from_user_artifact():
    tool = _as_lc_tool(StockTool())
    message = await tool.ainvoke({"type": "tool_call", "name": tool.name, "args": {}, "id": "one"})
    assert isinstance(message, ToolMessage)
    assert "987654321" not in str(message.content)
    assert "123" in str(message.content)
    assert message.artifact == payload()


@pytest.mark.asyncio
async def test_react_model_and_memory_are_clean_but_archive_output_is_complete(tmp_path):
    model = FakeChatModel(responses=[AIMessage(content="", tool_calls=[
        {"name": "analyze_stock", "args": {}, "id": "one"}]), AIMessage(content="完成")])
    registry = ToolRegistry()
    registry.register(StockTool())
    memory = Memory(facts_path=tmp_path / "facts.json")
    outcome = await ReActExecutor(registry, memory, model=model).run("分析")
    assert "987654321" not in " ".join(str(message.content) for message in model.calls[-1])
    assert "987654321" not in str(memory.get_context_window())
    assert "987654321" in outcome.tool_calls[0]["raw_output"]
    assert json.loads(outcome.tool_calls[0]["raw_output"]) == payload()


@pytest.mark.parametrize("serialized", [str, json.dumps])
def test_plan_tool_history_keeps_raw_user_data_but_hides_it_from_future_models(tmp_path, serialized):
    memory = Memory(facts_path=tmp_path / "facts.json")
    memory.add_message("tool", "[analyze_stock] success: " + serialized(payload()))
    assert "987654321" in memory.messages[0]["content"]
    model_context = memory.get_context_window()
    assert "987654321" not in str(model_context)
    assert "123" in str(model_context)
    assert memory.messages[0]["content"].endswith(serialized(payload()))


def test_plan_tool_selector_omits_reference_fields():
    selector = ToolSelector(registry=ToolRegistry())
    messages = selector._build_messages(TaskStep(id="one", description="继续分析"), [
        {"step_id": "previous", "status": "success", "data": payload()}])
    assert "987654321" not in str(messages) and "123" in str(messages)
