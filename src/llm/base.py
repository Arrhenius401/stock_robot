import logging
import time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class LLMBackend(ABC):
    """LLM 后端抽象接口 — 所有模型适配器需实现此接口"""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """模型名称"""
        ...

    @abstractmethod
    def generate(self, prompt: str, **kwargs) -> str:
        """生成回复"""
        ...

    def _call_with_retry(self, fn, retry_times: int = 2, base_delay: float = 1.0):
        """调用 fn，失败时指数退避重试；重试耗尽后抛出最后一次异常"""
        last_exc: Exception | None = None
        for attempt in range(retry_times + 1):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001 — 重试逻辑需捕获全部异常类型
                last_exc = e
                if attempt < retry_times:
                    delay = base_delay * (2 ** attempt)
                    logger.warning("LLM 调用失败（第 %d/%d 次），%.0fs 后重试: %s",
                                   attempt + 1, retry_times, delay, e)
                    time.sleep(delay)
        assert last_exc is not None
        raise last_exc
