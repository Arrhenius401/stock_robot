from abc import ABC, abstractmethod


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
