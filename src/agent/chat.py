"""ChatResponder — 闲聊的普通 AI 会话回复（LangChain 模型，无工具绑定）"""
import logging

from agent.memory import Memory

logger = logging.getLogger(__name__)

FALLBACK_REPLY = ("这个问题我暂时无法回答。可以试试让我分析某只股票"
                  "（如 600519）或查询指数（如上证指数）。")

CHAT_SYSTEM_PROMPT = """你是一个友好、专业的股票投研助手。
当用户闲聊（问候、道谢、日常话题）时，自然地进行普通对话；
当用户提出投研相关问题时，简短回答并建议使用分析功能。"""


class ChatResponder:
    """普通会话回复 — 保持多轮上下文，失败降级为固定提示"""

    def __init__(self, model=None):
        self._model = model

    async def reply(self, user_input: str, memory: Memory) -> str:
        if self._model is None:
            return FALLBACK_REPLY
        try:
            messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
            for msg in memory.get_context_window(n=10):
                if msg["role"] not in ("user", "assistant"):
                    continue
                messages.append({"role": msg["role"], "content": msg["content"]})
            # API 调用方已把当前用户消息写入 memory，避免上下文重复
            last = messages[-1] if messages else None
            if last is None or last.get("content") != user_input:
                messages.append({"role": "user", "content": user_input})

            response = await self._model.ainvoke(messages)
            content = getattr(response, "content", "")
            return content if isinstance(content, str) else str(content)
        except Exception as e:  # noqa: BLE001 — LLM 边界异常降级固定提示
            logger.error("闲聊回复失败: %s", e)
            return FALLBACK_REPLY
