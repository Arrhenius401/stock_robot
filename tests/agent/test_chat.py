"""ChatResponder 单元测试"""
import pytest

from agent.chat import (
    LLM_ERROR_REPLY,
    MODEL_NOT_CONFIGURED_REPLY,
    ChatResponder,
    _extract_text,
)
from agent.memory import Memory
from tests.agent.fake_chat_model import FakeChatModel


class TestExtractText:
    def test_str_passthrough(self):
        assert _extract_text("你好") == "你好"

    def test_list_joins_text_blocks_skips_thinking(self):
        content = [
            {"type": "thinking", "signature": "sig-1", "thinking": "内部思考"},
            {"type": "text", "text": "你好！"},
            {"type": "text", "text": "有什么可以帮你？"},
        ]
        assert _extract_text(content) == "你好！有什么可以帮你？"

    def test_list_ignores_non_dict_blocks(self):
        assert _extract_text(["裸字符串块",
                              {"type": "text", "text": "有效文本"}]) == "有效文本"

    def test_empty_content_returns_empty_string(self):
        assert _extract_text([]) == ""
        assert _extract_text(None) == ""
        assert _extract_text(12345) == ""


class TestChatResponder:
    @pytest.mark.asyncio
    async def test_stream_reply_emits_text_chunks(self):
        """普通聊天也应把模型正文以增量形式交给 API 层。"""
        responder = ChatResponder(model=FakeChatModel(content="实时回复"))

        chunks = [chunk async for chunk in responder.stream_reply_content("你好", Memory())]

        assert chunks == [{"text": "实时回复"}]

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
    async def test_reply_does_not_duplicate_current_user_message(self):
        """当前用户消息已写入 memory 时（API 路径），上下文不重复"""
        model = FakeChatModel(content="好的")
        responder = ChatResponder(model=model)
        memory = Memory()
        memory.add_message("user", "你好")

        await responder.reply("你好", memory)

        last_messages = model.calls[0]
        user_msgs = [m for m in last_messages
                     if m.get("role") == "user"
                     and m.get("content") == "你好"]
        assert len(user_msgs) == 1

    @pytest.mark.asyncio
    async def test_reply_filters_non_user_assistant_roles(self):
        """混合会话中 system/tool 消息不进入闲聊上下文（仅 user/assistant）"""
        model = FakeChatModel(content="好的")
        responder = ChatResponder(model=model)
        memory = Memory()
        memory.add_message("user", "你好")
        memory.add_message("assistant", "回答")
        memory.add_message("tool", "[analyze_stock] 数据")
        memory.add_message("system", "执行: 分析")
        memory.add_message("user", "接着聊")

        await responder.reply("接着聊", memory)

        last_messages = model.calls[0]
        roles = [m["role"] for m in last_messages]
        # system prompt + 过滤后的 user/assistant（当前消息已在 memory，去重不追加）
        assert roles == ["system", "user", "assistant", "user"]
        contents = [str(m.get("content", "")) for m in last_messages]
        assert not any("[analyze_stock]" in c for c in contents)

    @pytest.mark.asyncio
    async def test_no_model_returns_not_configured(self):
        responder = ChatResponder(model=None)

        reply = await responder.reply("你好", Memory())

        assert reply == MODEL_NOT_CONFIGURED_REPLY

    @pytest.mark.asyncio
    async def test_model_exception_returns_error_reply_with_reason(self):
        class ExplodingModel(FakeChatModel):
            async def ainvoke(self, messages, **kwargs):
                raise RuntimeError("API 不可用")

        responder = ChatResponder(model=ExplodingModel())

        reply = await responder.reply("你好", Memory())

        assert reply == LLM_ERROR_REPLY.format(error="API 不可用")

    @pytest.mark.asyncio
    async def test_reply_extracts_text_blocks_from_list_content(self):
        """DeepSeek 端点返回 thinking+text 块列表，只取 text 且不含内部思考"""
        model = FakeChatModel(content=[
            {"type": "thinking", "signature": "sig-1", "thinking": "内部思考"},
            {"type": "text", "text": "你好！很高兴见到你。"},
        ])
        responder = ChatResponder(model=model)

        reply = await responder.reply("你好", Memory())

        assert reply == "你好！很高兴见到你。"
        assert "thinking" not in reply and "sig-1" not in reply

    @pytest.mark.asyncio
    async def test_reply_empty_content_returns_error_reply(self):
        model = FakeChatModel(content=[])
        responder = ChatResponder(model=model)

        reply = await responder.reply("你好", Memory())

        assert reply == LLM_ERROR_REPLY.format(error="模型未返回有效回复")
