"""模型消息内容规范化：安全提取正文与推理。"""

import ast
import json
from html import unescape
from typing import Any


def _parse_blocks(value: str) -> list[dict[str, Any]] | None:
    """仅解析 JSON 或 Python 字面量形式的内容块列表。"""
    # 旧版页面曾把消息末尾空格序列化成 ``&#x20;``。先还原 HTML 实体并
    # 去掉外围空白，避免合法的内容块列表因尾随实体无法被安全解析。
    value = unescape(value).strip()
    candidate: Any
    try:
        candidate = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        try:
            candidate = ast.literal_eval(value)
        except (SyntaxError, ValueError, TypeError):
            return None
    if not isinstance(candidate, list) or not all(
        isinstance(block, dict) for block in candidate
    ):
        return None
    return candidate


def _as_content_blocks(value: Any) -> list[dict[str, Any]] | None:
    if isinstance(value, list) and all(isinstance(block, dict) for block in value):
        return value
    if isinstance(value, str):
        return _parse_blocks(value)
    if isinstance(value, dict) and isinstance(value.get("type"), str):
        return [value]
    return None


def _block_text(block: dict[str, Any]) -> str:
    value = block.get("text", "")
    return value if isinstance(value, str) else ""


def _block_thinking(block: dict[str, Any]) -> str:
    value = block.get("thinking", block.get("reasoning_content", ""))
    return value if isinstance(value, str) else ""


def _reasoning_text(value: Any) -> str:
    """提取 OpenAI 兼容端点附加字段中的纯文本推理。"""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(item for item in value if isinstance(item, str))
    return ""


def encode_message_content(
    text: str,
    thinking: str = "",
    *,
    thinking_duration_seconds: float | None = None,
) -> str:
    """将助手正文及可见思考编码为可持久化、可向后兼容的内容块。"""
    if not thinking:
        return text
    thinking_block: dict[str, Any] = {"type": "thinking", "thinking": thinking}
    if thinking_duration_seconds is not None and thinking_duration_seconds >= 0:
        thinking_block["thinking_duration_seconds"] = thinking_duration_seconds
    return json.dumps([
        thinking_block,
        {"type": "text", "text": text},
    ], ensure_ascii=False)


def normalize_message_content(value: Any) -> dict[str, Any]:
    """从字符串、内容块或安全解析的历史字面量提取正文与推理。"""
    blocks = _as_content_blocks(value)
    if blocks is None:
        return {"text": str(value or "")}
    text = "".join(
        _block_text(block) for block in blocks if block.get("type") == "text"
    )
    thinking = "\n\n".join(
        _block_thinking(block)
        for block in blocks
        if block.get("type") in ("thinking", "reasoning")
    )
    result: dict[str, Any] = {"text": text}
    if thinking:
        result["thinking"] = thinking
        for block in blocks:
            if not isinstance(block, dict):
                continue
            duration = block.get("thinking_duration_seconds")
            if isinstance(duration, (int, float)) and not isinstance(duration, bool):
                result["thinking_duration_seconds"] = duration
                break
    return result


def normalize_model_message(message: Any) -> dict[str, Any]:
    """从 LangChain 消息读取正文及 OpenAI 兼容端点的推理附加字段。"""
    content = normalize_message_content(getattr(message, "content", ""))
    if content.get("thinking"):
        return content

    additional_kwargs = getattr(message, "additional_kwargs", {})
    if not isinstance(additional_kwargs, dict):
        return content
    thinking = _reasoning_text(
        additional_kwargs.get("reasoning_content", additional_kwargs.get("thinking"))
    ).strip()
    if thinking:
        content["thinking"] = thinking
    return content
