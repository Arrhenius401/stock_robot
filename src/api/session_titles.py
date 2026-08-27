"""会话标题的本地生成与 LLM 润色。"""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)
TITLE_MAX_LENGTH = 20

_INDEX_NAMES = (
    "沪深300",
    "中证红利",
    "上证指数",
    "深证成指",
    "创业板指",
    "科创50",
    "中证500",
    "中证1000",
)
_PAIRED_QUOTES = {"\"": "\"", "'": "'", "“": "”", "‘": "’"}
_SPOKEN_PREFIX = re.compile(
    r"^(?:请(?:帮我)?|帮我|麻烦(?:你)?|请问|能否|可以)?(?:分析|研究|看看|看下|关注|了解)(?:一下)?\s*"
)


def normalize_generated_title(value: str) -> str | None:
    """清理模型返回的单行标题，无法安全使用时返回空。"""
    if "\n" in value or "\r" in value:
        return None
    title = value.strip()
    if len(title) >= 2 and _PAIRED_QUOTES.get(title[0]) == title[-1]:
        title = title[1:-1].strip()
    title = re.sub(r"\s+", " ", title)
    if not title or len(title) > TITLE_MAX_LENGTH:
        return None
    return title


def _extract_targets(message: str) -> list[str]:
    codes = re.findall(r"(?<!\d)\d{6}(?!\d)", message)
    indexes = [name for name in _INDEX_NAMES if name in message]
    return list(dict.fromkeys([*codes, *indexes]))


def _extract_intent(message: str) -> str | None:
    """提取设计范围内的投研意图。"""
    if "比较" in message or "对比" in message:
        return "比较"
    has_valuation = "估值" in message
    has_trend = "技术" in message or "趋势" in message
    if has_valuation and has_trend:
        return "估值与趋势"
    if has_valuation:
        return "估值"
    if has_trend:
        return "趋势"
    if "财务" in message or "业绩" in message:
        return "财务"
    if "行业" in message or "赛道" in message:
        return "行业"
    if "风险" in message:
        return "风险"
    if "择时" in message or "买入时机" in message or "卖出时机" in message:
        return "择时"
    return None


def _fallback_title(message: str) -> str:
    cleaned = _SPOKEN_PREFIX.sub("", message.strip())
    cleaned = re.sub(r"^(?:请|帮我)\s*", "", cleaned)
    return cleaned[:TITLE_MAX_LENGTH] or "新会话"


def derive_session_title(message: str) -> str:
    """由首条用户消息生成无需远程查询的确定性会话标题。"""
    text = message.strip()
    if not text:
        return "新会话"

    targets = _extract_targets(text)
    intent = _extract_intent(text)
    if intent == "比较" and len(targets) < 2:
        pair = re.search(r"(?:比较|对比)?(.+?)(?:和|与|及)(.+?)(?:数据|情况|表现|$)", text)
        if pair:
            targets = [pair.group(1).strip(), pair.group(2).strip()]
    if intent == "比较" and len(targets) >= 2:
        return f"{targets[0]}与{targets[1]}比较"[:TITLE_MAX_LENGTH]
    if targets and intent:
        return f"{targets[0]}{intent}"[:TITLE_MAX_LENGTH]

    fallback = _fallback_title(text)
    if re.search(r"分析|研究", text) and "的" not in fallback and len(fallback) <= 10:
        return f"{fallback}分析"[:TITLE_MAX_LENGTH]
    return fallback


def derive_session_title_from_messages(messages: list[str]) -> str:
    """从前两条用户消息生成标题；首条信息不足时使用后续消息。"""
    candidates = [item.strip() for item in messages[:2] if item and item.strip()]
    if not candidates:
        return "新会话"
    first = derive_session_title(candidates[0])
    if first != "新会话" and len(first) >= 6 and not _is_low_information(candidates[0]):
        return first
    for message in candidates[1:]:
        refined = derive_session_title(message)
        if refined != "新会话" and not _is_low_information(message):
            return refined
    return first


def _is_low_information(message: str) -> bool:
    cleaned = re.sub(r"[\s，。！？、,.!?]+", "", message)
    return len(cleaned) < 4 or cleaned in {"你好", "您好", "谢谢", "好的", "嗯"}


class SessionTitleRefiner:
    """可选地使用 LLM 润色本地会话标题。"""

    def __init__(self, model: Any = None):
        self._model = model

    async def refine(self, message: str, fallback: str) -> str:
        """模型不可用或输出无效时保留本地标题。"""
        if self._model is None:
            return fallback
        try:
            response = await self._model.ainvoke([
                {"role": "system", "content": "将用户请求概括为8至20字中文投研会话标题，只输出标题。"},
                {"role": "user", "content": message},
            ])
            return normalize_generated_title(str(response.content)) or fallback
        except Exception as exc:  # noqa: BLE001 — LLM 边界失败保留本地标题
            logger.warning("会话标题润色失败: %s", exc)
            return fallback
