"""ChatResponder 单元测试"""
import pytest

from agent.chat import FALLBACK_REPLY, ChatResponder
from agent.memory import Memory
from tests.agent.fake_chat_model import FakeChatModel


class TestChatResponder:
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
    async def test_no_model_returns_fallback(self):
        responder = ChatResponder(model=None)

        reply = await responder.reply("你好", Memory())

        assert reply == FALLBACK_REPLY

    @pytest.mark.asyncio
    async def test_model_exception_returns_fallback(self):
        class ExplodingModel(FakeChatModel):
            async def ainvoke(self, messages, **kwargs):
                raise RuntimeError("API 不可用")

        responder = ChatResponder(model=ExplodingModel())

        reply = await responder.reply("你好", Memory())

        assert reply == FALLBACK_REPLY
