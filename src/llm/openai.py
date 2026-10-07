"""OpenAI GPT 适配器"""
import logging
import time
from typing import Any

from openai import OpenAI

from llm.base import LLMBackend
from llm.budget import (
    model_output_limit,
    normalize_budget,
    recovery_budget,
    remaining_time,
)
from llm.transport import deadline_http_client

logger = logging.getLogger(__name__)


class OpenAIAdapter(LLMBackend):
    def __init__(self, api_key: str, model: str = "gpt-4o", temperature: float = 0.3,
                 max_tokens: int | None = None, base_url: str | None = None,
                 timeout: float = 60.0, retry_times: int = 2):
        self._model = model
        self._temperature = temperature
        self._max_tokens = normalize_budget(max_tokens)
        self._timeout = timeout
        self._retry_times = retry_times
        # 禁用 SDK 内置重试（默认 2 次），重试策略由 _call_with_retry 统一控制，避免叠加放大请求数
        client_kwargs: dict[str, Any] = {
            "api_key": api_key, "timeout": timeout, "max_retries": 0,
            "http_client": deadline_http_client(),
        }
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = OpenAI(**client_kwargs)
        # 提前加载 SDK 延迟资源，避免首次导入占用生成截止预算。
        _ = self._client.chat.completions

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, prompt: str, system: str | None = None, **kwargs) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        deadline = time.monotonic() + self._timeout

        def _create(budget: int | None):
            request: dict[str, Any] = {
                "model": self._model, "messages": messages,
                "temperature": kwargs.get("temperature", self._temperature),
                "timeout": remaining_time(deadline),
            }
            if budget is not None:
                request["max_tokens"] = budget
            return self._client.chat.completions.create(**request)

        try:
            budget = normalize_budget(kwargs.get("max_tokens", self._max_tokens))
            response = self._call_with_retry(
                lambda: _create(budget), retry_times=kwargs.get("retry_times", self._retry_times),
                deadline=deadline,
            )
            self._record_usage(response)
            choice = response.choices[0]
            content = choice.message.content or ""
            if choice.finish_reason == "length":
                logger.warning("模型正文截断：finish_reason=length，正文字符数=%d", len(content))
                if not content.strip() and budget is None:
                    remaining_time(deadline)
                    used = getattr(response.usage, "completion_tokens", None)
                    if type(used) is int and used > 0:
                        limit = model_output_limit(self._client, self._model,
                                                   protocol="openai", deadline=deadline)
                        retry_budget = recovery_budget(used, limit)
                        if retry_budget is not None:
                            response = self._call_with_retry(
                                lambda: _create(retry_budget), retry_times=0, deadline=deadline,
                            )
                            self._record_usage(response)
                            choice = response.choices[0]
                            content = choice.message.content or ""
                            if choice.finish_reason == "length":
                                logger.warning("模型预算恢复后仍截断，正文字符数=%d", len(content))
            if not content.strip():
                reason = "已达到长度上限" if choice.finish_reason == "length" else "模型未返回正文"
                return f"（LLM 分析暂时不可用：{reason}）"
            return content
        except Exception as e:  # noqa: BLE001 — SDK异常不可穷举，保留确定性分析降级
            logger.error("OpenAI API 调用失败: %s", e)
            return f"（LLM 分析暂时不可用：{e}，请检查 API 配置）"

    def _record_usage(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", None)
        output_tokens = getattr(usage, "completion_tokens", None)
        if type(input_tokens) is int and type(output_tokens) is int:
            self._log_usage(input_tokens, output_tokens)

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
            "gpt-4o": (2.50 / 1_000_000, 10.00 / 1_000_000),
            "gpt-4o-mini": (0.15 / 1_000_000, 0.60 / 1_000_000),
        }
        input_price, output_price = pricing.get(self._model, (0, 0))
        return prompt_tokens * input_price + completion_tokens * output_price
