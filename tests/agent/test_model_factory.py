"""model_factory 单元测试"""
from agent.model_factory import create_chat_model


class FakeConfig:
    def __init__(self, data):
        self._data = data

    def get(self, key, default=None):
        node = self._data
        for k in key.split("."):
            if isinstance(node, dict) and k in node:
                node = node[k]
            else:
                return default
        return node


def test_no_api_key_returns_none():
    config = FakeConfig({"llm": {"provider": "openai", "api_key": ""}})
    assert create_chat_model(config) is None


def test_unknown_provider_returns_none():
    config = FakeConfig({"llm": {"provider": "unknown", "api_key": "sk-x"}})
    assert create_chat_model(config) is None


def test_openai_provider_returns_chat_model():
    config = FakeConfig({"llm": {
        "provider": "openai", "api_key": "sk-x", "model": "gpt-4o",
        "base_url": "", "temperature": 0.3, "timeout_seconds": 60,
    }})
    model = create_chat_model(config)
    assert model is not None
    assert model.model_name == "gpt-4o"


def test_claude_provider_returns_chat_model():
    config = FakeConfig({"llm": {
        "provider": "claude", "api_key": "sk-x", "model": "claude-sonnet-4-6",
        "base_url": "", "temperature": 0.3, "timeout_seconds": 60,
    }})
    model = create_chat_model(config)
    assert model is not None
    assert model.model == "claude-sonnet-4-6"


def test_claude_provider_without_model_uses_claude_default():
    config = FakeConfig({"llm": {
        "provider": "claude", "api_key": "sk-x", "model": "",
        "base_url": "", "temperature": 0.3, "timeout_seconds": 60,
    }})
    model = create_chat_model(config)
    assert model is not None
    assert model.model == "claude-sonnet-4-6"


import pytest


@pytest.fixture(autouse=True)
def mock_model_capability_lookup(mocker):
    return mocker.patch("agent.model_factory.model_output_limit", return_value=16000)


@pytest.mark.parametrize("provider,budget,expected", [
    ("openai", None, None), ("openai", 2000, 2000),
    ("claude", None, 8192), ("claude", 2000, 2000),
])
def test_chat_model_uses_same_auto_and_manual_budget(provider, budget, expected):
    config = FakeConfig({"llm": {"provider": provider, "api_key": "test", "max_tokens": budget}})
    model = create_chat_model(config)
    assert model is not None
    assert model.max_tokens == expected


def test_deepseek_manual_budget_uses_supported_wire_field():
    from agent.model_factory import create_chat_model

    values = {"llm.provider": "openai", "llm.api_key": "test", "llm.model": "deepseek-v4-flash",
              "llm.base_url": "https://api.deepseek.com/v1", "llm.max_tokens": 2000}
    class Config:
        def get(self, key, default=None):
            return values.get(key, default)
    model = create_chat_model(Config())
    assert model is not None
    payload = model._get_request_payload("验收")
    assert payload["max_tokens"] == 2000
    assert "max_completion_tokens" not in payload
    values["llm.max_tokens"] = None
    auto_model = create_chat_model(Config())
    assert auto_model is not None
    automatic = auto_model._get_request_payload("验收")
    assert "max_tokens" not in automatic
    assert "max_completion_tokens" not in automatic
    values["llm.base_url"] = "https://proxy.example.com/v1"
    values["llm.max_tokens"] = 2000
    proxy_model = create_chat_model(Config())
    assert proxy_model is not None
    assert proxy_model._get_request_payload("验收")["max_completion_tokens"] == 2000
