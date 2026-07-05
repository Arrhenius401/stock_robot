from unittest.mock import MagicMock
from src.llm.claude import ClaudeAdapter


class TestClaudeAdapter:
    def test_model_name(self):
        adapter = ClaudeAdapter(api_key="sk-ant-test", model="claude-sonnet-4-6")
        assert adapter.model_name == "claude-sonnet-4-6"

    def test_generate_calls_anthropic(self, mocker):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = "分析结果：该股票技术面偏多"
        mock_response.usage.input_tokens = 120
        mock_response.usage.output_tokens = 60
        mock_client.messages.create.return_value = mock_response
        mocker.patch("src.llm.claude.Anthropic", return_value=mock_client)

        adapter = ClaudeAdapter(api_key="sk-ant-test", model="claude-sonnet-4-6")
        result = adapter.generate("分析平安银行")

        assert "分析结果" in result
        mock_client.messages.create.assert_called_once()

    def test_generate_with_error_returns_fallback(self, mocker):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API Error")
        mocker.patch("src.llm.claude.Anthropic", return_value=mock_client)

        adapter = ClaudeAdapter(api_key="sk-ant-test")
        result = adapter.generate("分析")
        assert "LLM 分析暂时不可用" in result
