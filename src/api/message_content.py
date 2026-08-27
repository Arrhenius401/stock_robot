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


def normalize_message_content(value: Any) -> dict[str, str]:
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
    return {"text": text, **({"thinking": thinking} if thinking else {})}
