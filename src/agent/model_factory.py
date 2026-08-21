"""LangChain 聊天模型工厂 — agent 层 tool calling 与闲聊回复共用"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


def create_chat_model(config) -> Any | None:
    """按配置构建 LangChain 聊天模型；未配置 api_key 或初始化失败返回 None

    与 llm 后端（build_llm）共用 llm.* 配置键，但构建独立的 LangChain 模型。
    """
    provider = config.get("llm.provider", "openai")
    api_key = config.get("llm.api_key", "")
    if not api_key:
        return None
    base_url = config.get("llm.base_url", "") or None
    # 默认模型按 provider 区分：llm.model 未配置时避免拿到跨 provider 非法模型名
    default_model = "claude-sonnet-4-6" if provider == "claude" else "gpt-4o"
    model = config.get("llm.model", default_model) or default_model
    temperature = config.get("llm.temperature", 0.3)
    timeout = config.get("llm.timeout_seconds", 60)

    try:
        if provider == "openai":
            from langchain_openai import ChatOpenAI
            from pydantic import SecretStr

            return ChatOpenAI(model=model, api_key=SecretStr(api_key),
                              base_url=base_url, temperature=temperature,
                              timeout=timeout)
        elif provider == "claude":
            from langchain_anthropic import ChatAnthropic
            from pydantic import SecretStr

            # pyright 合成的 __init__ 签名为 model_name（必填）+ stop（必填）
            # （langchain-anthropic 1.5.6 字段 model 的别名，运行时两者皆可）
            return ChatAnthropic(model_name=model, api_key=SecretStr(api_key),
                                 base_url=base_url, temperature=temperature,
                                 timeout=timeout, stop=None)
        logger.warning("未知 LLM provider: %s，LangChain 模型不可用", provider)
    except Exception as e:  # noqa: BLE001 — SDK 初始化失败降级为无模型
        logger.warning("LangChain 模型初始化失败: %s", e)
    return None
