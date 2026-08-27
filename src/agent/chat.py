"""ChatResponder — 闲聊的普通 AI 会话回复（LangChain 模型，无工具绑定）"""
import logging
from collections.abc import AsyncIterator
from typing import Any

from agent.memory import Memory
from api.message_content import normalize_message_content

logger = logging.getLogger(__name__)

MODEL_NOT_CONFIGURED_REPLY = "AI 对话未启用：请在配置中设置 llm.api_key"
LLM_ERROR_REPLY = "（AI 回复暂时不可用：{error}，请检查 API 配置）"

CHAT_SYSTEM_PROMPT = """你是一个友好、专业的股票投研助手。
当用户闲聊（问候、道谢、日常话题）时，自然地进行普通对话；
当用户提出投研相关问题时，简短回答并建议使用分析功能。"""


def _extract_text(content: Any) -> str:
    """从模型响应 content 提取用户可见文本

    LangChain AIMessage.content 两种形态：OpenAI 风格为 str 原样返回；
    Anthropic 风格为内容块列表（含 thinking 块），只拼接 type == "text"
    的块文本，thinking/signature 不展示给用户。
    """
    if not isinstance(content, (str, list, dict)):
        return ""
    if isinstance(content, list) and not all(isinstance(block, dict) for block in content):
        content = [block for block in content if isinstance(block, dict)]
    return normalize_message_content(content)["text"]


class ChatResponder:
    """普通会话回复 — 保持多轮上下文，失败降级为可诊断提示"""

    def __init__(self, model=None):
        self._model = model

    @staticmethod
    def _messages_for_reply(user_input: str, memory: Memory) -> list[dict[str, str]]:
        messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
        for msg in memory.get_context_window(n=10):
            if msg["role"] not in ("user", "assistant"):
                continue
            messages.append({"role": msg["role"], "content": msg["content"]})
        last = messages[-1] if messages else None
        if last is None or last.get("content") != user_input:
            messages.append({"role": "user", "content": user_input})
        return messages

    async def stream_reply_content(
            self, user_input: str, memory: Memory) -> AsyncIterator[dict[str, str]]:
        """逐块产出普通会话正文，供 SSE 在模型生成期间即时转发。"""
        if self._model is None:
            yield {"text": MODEL_NOT_CONFIGURED_REPLY}
            return
        emitted_text = False
        try:
            async for response in self._model.astream(
                    self._messages_for_reply(user_input, memory)):
                content = normalize_message_content(getattr(response, "content", ""))
                if not content["text"] and not content.get("thinking"):
                    continue
                emitted_text = emitted_text or bool(content["text"])
                yield content
            if not emitted_text:
                yield {"text": LLM_ERROR_REPLY.format(error="模型未返回有效回复")}
        except Exception as e:  # noqa: BLE001 — LLM 流式边界异常降级可诊断提示
            logger.error("闲聊流式回复失败: %s", e)
            yield {"text": LLM_ERROR_REPLY.format(error=e)}

    async def reply_content(self, user_input: str, memory: Memory) -> dict[str, str]:
        """返回规范化的正文与可选推理，不负责写入 Memory。"""
        if self._model is None:
            return {"text": MODEL_NOT_CONFIGURED_REPLY}
        try:
            response = await self._model.ainvoke(
                self._messages_for_reply(user_input, memory))
            content = normalize_message_content(getattr(response, "content", ""))
            if not content["text"]:
                return {"text": LLM_ERROR_REPLY.format(error="模型未返回有效回复")}
            return content
        except Exception as e:  # noqa: BLE001 — LLM 边界异常降级可诊断提示
            logger.error("闲聊回复失败: %s", e)
            return {"text": LLM_ERROR_REPLY.format(error=e)}

    async def reply(self, user_input: str, memory: Memory) -> str:
        return (await self.reply_content(user_input, memory))["text"]
