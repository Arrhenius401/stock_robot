"""Anthropic Claude 适配器"""
import logging
from anthropic import Anthropic
from llm.base import LLMBackend

logger = logging.getLogger(__name__)


class ClaudeAdapter(LLMBackend):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6",
                 temperature: float = 0.3, max_tokens: int = 2000):
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._client = Anthropic(api_key=api_key)

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, prompt: str, system: str | None = None, **kwargs) -> str:
        try:
            kwargs_dict = {
                "model": self._model,
                "max_tokens": kwargs.get("max_tokens", self._max_tokens),
                "temperature": kwargs.get("temperature", self._temperature),
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                kwargs_dict["system"] = system

            response = self._client.messages.create(**kwargs_dict)
            content = ""
            for block in response.content:
                if hasattr(block, "text"):
                    content += block.text

            usage = response.usage
            if usage:
                self._log_usage(usage.input_tokens, usage.output_tokens)
            return content
        except Exception as e:
            logger.error(f"Claude API 调用失败: {e}")
            return "（LLM 分析暂时不可用，请检查 API 配置）"

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
            "claude-opus-4-7": (15.00 / 1_000_000, 75.00 / 1_000_000),
            "claude-sonnet-4-6": (3.00 / 1_000_000, 15.00 / 1_000_000),
            "claude-haiku-4-5-20251001": (0.80 / 1_000_000, 4.00 / 1_000_000),
        }
        input_price, output_price = pricing.get(self._model, (0, 0))
        return prompt_tokens * input_price + completion_tokens * output_price
