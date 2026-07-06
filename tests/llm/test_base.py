import pytest
from llm.base import LLMBackend


class FakeLLM(LLMBackend):
    """测试用 LLM 后端"""
    @property
    def model_name(self) -> str:
        return "fake-model"

    def generate(self, prompt: str, **kwargs) -> str:
        return f"Response to: {prompt[:20]}..."


class TestLLMBackend:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            LLMBackend()

    def test_concrete_implementation_works(self):
        llm = FakeLLM()
        assert llm.model_name == "fake-model"
        result = llm.generate("分析这只股票")
        assert result.startswith("Response to:")
