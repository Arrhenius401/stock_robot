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
            api_key="sk-test", base_url="https://api.deepseek.com/v1", timeout=60.0,
            max_retries=0,
        )

    def test_no_base_url_omits_arg(self, mocker):
        mock_openai = mocker.patch("llm.openai.OpenAI", return_value=MagicMock())
        OpenAIAdapter(api_key="sk-test")
        mock_openai.assert_called_once_with(
            api_key="sk-test", timeout=60.0, max_retries=0,
        )


def test_auto_budget_omits_max_tokens_and_manual_override_is_preserved(mocker):
    from types import SimpleNamespace

    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="正文"), finish_reason="stop")], usage=None)
    mocker.patch("llm.openai.OpenAI", return_value=client)
    adapter = OpenAIAdapter(api_key="test")
    assert adapter.generate("分析") == "正文"
    assert "max_tokens" not in client.chat.completions.create.call_args.kwargs
    assert adapter.generate("分析", max_tokens=1234) == "正文"
    assert client.chat.completions.create.call_args.kwargs["max_tokens"] == 1234


def test_auto_length_recovery_is_bounded_and_explicit_limit_never_expands(mocker):
    from types import SimpleNamespace

    empty = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None),
        finish_reason="length")], usage=SimpleNamespace(completion_tokens=2000))
    final = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="最终正文"),
        finish_reason="stop")], usage=None)
    client = MagicMock()
    client.chat.completions.create.side_effect = [empty, final]
    mocker.patch("llm.openai.OpenAI", return_value=client)
    mocker.patch("llm.openai.model_output_limit", return_value=10000)
    adapter = OpenAIAdapter(api_key="test")
    assert adapter.generate("分析") == "最终正文"
    assert client.chat.completions.create.call_count == 2
    assert client.chat.completions.create.call_args.kwargs["max_tokens"] == 8192
    client.chat.completions.create.reset_mock(side_effect=True)
    client.chat.completions.create.return_value = empty
    assert "长度上限" in adapter.generate("分析", max_tokens=2000)
    assert client.chat.completions.create.call_count == 1


def test_partial_text_is_retained_without_repeating_request(mocker, caplog):
    from types import SimpleNamespace

    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="已有部分正文"), finish_reason="length")], usage=None)
    mocker.patch("llm.openai.OpenAI", return_value=client)
    assert OpenAIAdapter(api_key="test").generate("分析") == "已有部分正文"
    assert client.chat.completions.create.call_count == 1
    assert "截断" in caplog.text
