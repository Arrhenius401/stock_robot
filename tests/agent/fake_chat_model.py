"""FakeChatModel — 模拟 LangChain ChatModel 的 bind_tools/ainvoke，供测试注入"""
from typing import Any

from langchain_core.runnables import Runnable


class FakeAIMessage:
    def __init__(self, content: Any = "", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []


class FakeChatModel(Runnable[Any, Any]):
    """bind_tools 返回自身；ainvoke 从 responses 序列弹出下一条，耗尽后返回固定 content/tool_calls

    继承 Runnable：create_react_agent（langgraph 1.2.11）静态 model 路径内部执行
    `prompt | model`（RunnableSequence），要求 model 满足 langchain_core Runnable。
    """

    def __init__(self, responses=None, tool_calls=None, content: Any = "测试回复"):
        self._responses = list(responses or [])
        self._tool_calls = tool_calls or []
        self._content = content
        self.bound_tools = None
        self.calls = []  # 记录每次 ainvoke 的 messages

    def invoke(self, input, config=None, **kwargs):
        """同步调用：ReAct 测试走异步 ainvoke 路径，同步调用直接提示"""
        raise NotImplementedError("FakeChatModel 仅支持异步 ainvoke")

    def bind_tools(self, tools):
        self.bound_tools = tools
        return self

    async def ainvoke(self, messages, config=None, **kwargs):
        """RunnableSequence 以 (input, config) 两个位置参数调用 ainvoke"""
        self.calls.append(messages)
        if self._responses:
            return self._responses.pop(0)
        return FakeAIMessage(content=self._content, tool_calls=self._tool_calls)

    async def astream(self, messages, config=None, **kwargs):
        """以单个 chunk 模拟支持流式输出的 LangChain ChatModel。"""
        yield await self.ainvoke(messages, config=config, **kwargs)


def make_tool_call(name: str, args: dict | None = None,
                   call_id: str = "call_1") -> dict:
    return {"name": name, "args": args or {}, "id": call_id}
