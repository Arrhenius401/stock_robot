from unittest.mock import MagicMock
from llm.claude import ClaudeAdapter


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
        mocker.patch("llm.claude.Anthropic", return_value=mock_client)

        adapter = ClaudeAdapter(api_key="sk-ant-test", model="claude-sonnet-4-6")
        result = adapter.generate("分析平安银行")

        assert "分析结果" in result
        mock_client.messages.create.assert_called_once()

    def test_generate_with_error_returns_fallback(self, mocker):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API Error")
        mocker.patch("llm.claude.Anthropic", return_value=mock_client)

        adapter = ClaudeAdapter(api_key="sk-ant-test")
        result = adapter.generate("分析")
        assert "LLM 分析暂时不可用" in result
        # 错误详情应出现在返回文本中，避免被静默吞掉
        assert "API Error" in result

    def test_base_url_passed_to_client(self, mocker):
        mock_anthropic = mocker.patch("llm.claude.Anthropic", return_value=MagicMock())
        ClaudeAdapter(api_key="sk-ant-test", base_url="https://api.deepseek.com/anthropic")
        mock_anthropic.assert_called_once_with(
            api_key="sk-ant-test", base_url="https://api.deepseek.com/anthropic"
        )

    def test_no_base_url_omits_arg(self, mocker):
        mock_anthropic = mocker.patch("llm.claude.Anthropic", return_value=MagicMock())
        ClaudeAdapter(api_key="sk-ant-test")
        mock_anthropic.assert_called_once_with(api_key="sk-ant-test")
