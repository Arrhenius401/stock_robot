"""Anthropic Claude 适配器"""
import logging
import time
from typing import Any

from anthropic import Anthropic

from llm.base import LLMBackend
from llm.budget import (
    automatic_budget,
    model_output_limit,
    normalize_budget,
    recovery_budget,
    remaining_time,
)

logger = logging.getLogger(__name__)


class ClaudeAdapter(LLMBackend):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6",
                 temperature: float = 0.3, max_tokens: int | None = None,
                 base_url: str | None = None, timeout: float = 60.0,
                 retry_times: int = 2):
        self._model = model
        self._temperature = temperature
        self._max_tokens = normalize_budget(max_tokens)
        self._timeout = timeout
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
        deadline = time.monotonic() + self._timeout

        def _create(request_max_tokens: int):
            kwargs_dict = {
                "model": self._model,
                "max_tokens": request_max_tokens,
                "timeout": remaining_time(deadline),
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
            explicit = normalize_budget(kwargs.get("max_tokens", self._max_tokens))
            limit = None
            if explicit is None:
                limit = model_output_limit(self._client, self._model,
                                           protocol="claude", deadline=deadline)
            budget = explicit if explicit is not None else automatic_budget(limit)
            response = self._call_with_retry(
                lambda: _create(budget), retry_times=kwargs.get("retry_times", self._retry_times),
                deadline=deadline,
            )
            self._record_usage(response)
            content = self._extract_text(response)
            if response.stop_reason == "max_tokens":
                logger.warning("模型正文截断：stop_reason=max_tokens，正文字符数=%d", len(content))
                if not content and explicit is None:
                    retry_budget = recovery_budget(budget, limit)
                    if retry_budget is not None:
                        response = self._call_with_retry(
                            lambda: _create(retry_budget), retry_times=0, deadline=deadline,
                        )
                        self._record_usage(response)
                        content = self._extract_text(response)
                        if response.stop_reason == "max_tokens":
                            logger.warning("模型预算恢复后仍截断，正文字符数=%d", len(content))
            if not content:
                reason = "已达到长度上限" if response.stop_reason == "max_tokens" else "模型未返回正文"
                return f"（LLM 分析暂时不可用：{reason}）"
            return content
        except Exception as e:  # noqa: BLE001 — SDK异常不可穷举，保留确定性分析降级
            logger.error("Claude API 调用失败: %s", e)
            return f"（LLM 分析暂时不可用：{e}，请检查 API 配置）"

    def _record_usage(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        if type(input_tokens) is int and type(output_tokens) is int:
            self._log_usage(input_tokens, output_tokens)

    @staticmethod
    def _extract_text(response: Any) -> str:
        """仅拼接兼容接口返回的最终正文块。"""
        return "".join(
            block.text for block in response.content
            if hasattr(block, "text") and isinstance(block.text, str)
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
