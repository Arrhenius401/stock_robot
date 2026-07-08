"""OpenAI GPT 适配器"""
import logging
from openai import OpenAI
from llm.base import LLMBackend

logger = logging.getLogger(__name__)


class OpenAIAdapter(LLMBackend):
    def __init__(self, api_key: str, model: str = "gpt-4o", temperature: float = 0.3,
                 max_tokens: int = 2000, base_url: str | None = None):
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = OpenAI(**client_kwargs)

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, prompt: str, system: str | None = None, **kwargs) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=kwargs.get("temperature", self._temperature),
                max_tokens=kwargs.get("max_tokens", self._max_tokens),
            )
            content = response.choices[0].message.content or ""
            usage = response.usage
            if usage:
                self._log_usage(usage.prompt_tokens, usage.completion_tokens)
            return content
        except Exception as e:
            logger.error(f"OpenAI API 调用失败: {e}")
            return f"（LLM 分析暂时不可用：{e}，请检查 API 配置）"

    def _log_usage(self, prompt_tokens: int, completion_tokens: int):
        try:
            cost = self._estimate_cost(prompt_tokens, completion_tokens)
            from llm.usage import UsageLogger
            from pathlib import Path
            log_path = Path.home() / ".stock_robot" / "usage.log"
            UsageLogger(log_path).log(self._model, prompt_tokens, completion_tokens, cost)
        except Exception:
            pass

    def _estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        pricing = {
            "gpt-4o": (2.50 / 1_000_000, 10.00 / 1_000_000),
            "gpt-4o-mini": (0.15 / 1_000_000, 0.60 / 1_000_000),
        }
        input_price, output_price = pricing.get(self._model, (0, 0))
        return prompt_tokens * input_price + completion_tokens * output_price
