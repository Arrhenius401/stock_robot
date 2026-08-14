"""LLM 重试机制测试"""
from unittest.mock import MagicMock

from llm.openai import OpenAIAdapter


class TestRetry:
    def test_retries_then_succeeds(self, mocker):
        mocker.patch("llm.base.time.sleep")
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "分析完成"
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_client.chat.completions.create.side_effect = [
            Exception("临时故障"), Exception("再次失败"), mock_response,
        ]
        mocker.patch("llm.openai.OpenAI", return_value=mock_client)

        adapter = OpenAIAdapter(api_key="sk-test", model="gpt-4o")
        result = adapter.generate("分析")

        assert "分析完成" in result
        assert mock_client.chat.completions.create.call_count == 3

    def test_retry_exhausted_returns_error_text(self, mocker):
        mocker.patch("llm.base.time.sleep")
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("持续失败")
        mocker.patch("llm.openai.OpenAI", return_value=mock_client)

        adapter = OpenAIAdapter(api_key="sk-test")
        result = adapter.generate("分析")

        assert "LLM 分析暂时不可用" in result
        assert mock_client.chat.completions.create.call_count == 3

    def test_retry_times_zero_single_attempt(self, mocker):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("失败")
        mocker.patch("llm.openai.OpenAI", return_value=mock_client)

        adapter = OpenAIAdapter(api_key="sk-test", retry_times=0)
        result = adapter.generate("分析")

        assert "LLM 分析暂时不可用" in result
        assert mock_client.chat.completions.create.call_count == 1

    def test_claude_retries_then_succeeds(self, mocker):
        mocker.patch("llm.base.time.sleep")
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = "解读完成"
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 5
        mock_client.messages.create.side_effect = [Exception("临时故障"), mock_response]
        mocker.patch("llm.claude.Anthropic", return_value=mock_client)

        from llm.claude import ClaudeAdapter
        adapter = ClaudeAdapter(api_key="sk-ant-test")
        result = adapter.generate("分析")

        assert "解读完成" in result
        assert mock_client.messages.create.call_count == 2
