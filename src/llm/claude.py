"""Anthropic Claude 适配器"""
import logging
from typing import Any

from anthropic import Anthropic

from llm.base import LLMBackend

logger = logging.getLogger(__name__)


class ClaudeAdapter(LLMBackend):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6",
                 temperature: float = 0.3, max_tokens: int = 2000,
                 base_url: str | None = None, timeout: float = 60.0,
                 retry_times: int = 2):
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._retry_times = retry_times
        self._disable_thinking = bool(base_url and "deepseek.com" in base_url.lower())
        # 禁用 SDK 内置重试（默认 2 次），重试策略由 _call_with_retry 统一控制，避免叠加放大请求数
        client_kwargs: dict[str, Any] = {
            "api_key": api_key, "timeout": timeout, "max_retries": 0,
        }
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = Anthropic(**client_kwargs)

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, prompt: str, system: str | None = None, **kwargs) -> str:
        max_tokens = kwargs.get("max_tokens", self._max_tokens)

        def _create(request_max_tokens: int):
            kwargs_dict = {
                "model": self._model,
                "max_tokens": request_max_tokens,
                "temperature": kwargs.get("temperature", self._temperature),
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                kwargs_dict["system"] = system
            # DeepSeek Anthropic 兼容端默认启用高强度推理，可能耗尽正文预算。
            # 仅对该兼容端显式关闭，不改变原生 Claude 的请求语义。
            if self._disable_thinking:
                kwargs_dict["thinking"] = {"type": "disabled"}
            return self._client.messages.create(**kwargs_dict)

        try:
            response = self._call_with_retry(
                lambda: _create(max_tokens),
                retry_times=kwargs.get("retry_times", self._retry_times),
            )
            content = self._extract_text(response)
            # 部分 Anthropic 兼容端会先返回 ThinkingBlock；当预算耗尽时可能没有
            # TextBlock。仅此场景提高一次预算重试，避免把内部推理当作报告正文。
            if not content and self._has_thinking_block(response):
                retry_max_tokens = max(max_tokens * 4, 8192)
                logger.warning(
                    "LLM 响应只有推理块，使用 %d token 重试一次以获取正文",
                    retry_max_tokens,
                )
                response = self._call_with_retry(
                    lambda: _create(retry_max_tokens), retry_times=0,
                )
                content = self._extract_text(response)

            usage = response.usage
            if usage:
                self._log_usage(usage.input_tokens, usage.output_tokens)
            return content
        except Exception as e:  # noqa: BLE001 — SDK 异常类型不可预测，契约是永不抛出
            logger.error(f"Claude API 调用失败: {e}")
            return f"（LLM 分析暂时不可用：{e}，请检查 API 配置）"

    @staticmethod
    def _extract_text(response: Any) -> str:
        """仅拼接兼容接口返回的最终正文块。"""
        return "".join(
            block.text for block in response.content
            if hasattr(block, "text") and isinstance(block.text, str)
        )

    @staticmethod
    def _has_thinking_block(response: Any) -> bool:
        """判断响应是否包含非空推理块，不暴露其具体内容。"""
        return any(
            isinstance(getattr(block, "thinking", None), str) and block.thinking
            for block in response.content
        )

    def _log_usage(self, prompt_tokens: int, completion_tokens: int):
        try:
            cost = self._estimate_cost(prompt_tokens, completion_tokens)
            from llm.usage import UsageLogger
            from utils.paths import project_state_dir

            log_path = project_state_dir() / "usage.log"
            UsageLogger(log_path).log(self._model, prompt_tokens, completion_tokens, cost)
        except Exception:  # noqa: BLE001 — 用量记录失败不得影响生成流程
            logger.debug("用量记录失败")

    def _estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        pricing = {
            "claude-opus-4-7": (15.00 / 1_000_000, 75.00 / 1_000_000),
            "claude-sonnet-4-6": (3.00 / 1_000_000, 15.00 / 1_000_000),
            "claude-haiku-4-5-20251001": (0.80 / 1_000_000, 4.00 / 1_000_000),
        }
        input_price, output_price = pricing.get(self._model, (0, 0))
        return prompt_tokens * input_price + completion_tokens * output_price
