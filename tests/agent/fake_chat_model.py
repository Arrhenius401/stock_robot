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


def make_tool_call(name: str, args: dict | None = None,
                   call_id: str = "call_1") -> dict:
    return {"name": name, "args": args or {}, "id": call_id}
