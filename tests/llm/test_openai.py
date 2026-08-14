from unittest.mock import MagicMock

from llm.openai import OpenAIAdapter


class TestOpenAIAdapter:
    def test_model_name(self):
        adapter = OpenAIAdapter(api_key="sk-test", model="gpt-4o")
        assert adapter.model_name == "gpt-4o"

    def test_generate_calls_openai(self, mocker):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "分析结果：该股票表现良好"
        mock_response.usage.prompt_tokens = 100
        mock_response.usage.completion_tokens = 50
        mock_client.chat.completions.create.return_value = mock_response
        mocker.patch("llm.openai.OpenAI", return_value=mock_client)

        adapter = OpenAIAdapter(api_key="sk-test", model="gpt-4o")
        result = adapter.generate("分析平安银行")

        assert "分析结果" in result
        mock_client.chat.completions.create.assert_called_once()
        call_args = mock_client.chat.completions.create.call_args.kwargs
        assert call_args["model"] == "gpt-4o"
        assert call_args["temperature"] == 0.3

    def test_generate_with_error_returns_data_only_message(self, mocker):
        mocker.patch("llm.base.time.sleep")
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("API Error")
        mocker.patch("llm.openai.OpenAI", return_value=mock_client)

        adapter = OpenAIAdapter(api_key="sk-test", model="gpt-4o")
        result = adapter.generate("分析")
        assert "LLM 分析暂时不可用" in result
        # 错误详情应出现在返回文本中，避免被静默吞掉
        assert "API Error" in result

    def test_base_url_passed_to_client(self, mocker):
        mock_openai = mocker.patch("llm.openai.OpenAI", return_value=MagicMock())
        OpenAIAdapter(api_key="sk-test", base_url="https://api.deepseek.com/v1")
        mock_openai.assert_called_once_with(
            api_key="sk-test", base_url="https://api.deepseek.com/v1", timeout=60.0
        )

    def test_no_base_url_omits_arg(self, mocker):
        mock_openai = mocker.patch("llm.openai.OpenAI", return_value=MagicMock())
        OpenAIAdapter(api_key="sk-test")
        mock_openai.assert_called_once_with(api_key="sk-test", timeout=60.0)
