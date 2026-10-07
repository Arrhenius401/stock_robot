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
        mocker.patch("llm.base.time.sleep")
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API Error")
        mocker.patch("llm.claude.Anthropic", return_value=mock_client)

        adapter = ClaudeAdapter(api_key="sk-ant-test")
        result = adapter.generate("分析")
        assert "LLM 分析暂时不可用" in result
        # 错误详情应出现在返回文本中，避免被静默吞掉
        assert "API Error" in result

    def test_generate_retries_once_when_only_thinking_block_returned(self, mocker):
        mock_client = MagicMock()
        thinking_response = MagicMock()
        thinking_block = MagicMock(spec=["thinking"])
        thinking_block.thinking = "内部推理"
        thinking_response.content = [thinking_block]
        thinking_response.usage.input_tokens = 100
        thinking_response.usage.output_tokens = 8192
        thinking_response.stop_reason = "max_tokens"
        final_response = MagicMock()
        text_block = MagicMock(spec=["text"])
        text_block.text = "最终分析正文"
        final_response.content = [text_block]
        final_response.usage.input_tokens = 100
        final_response.usage.output_tokens = 300
        mock_client.messages.create.side_effect = [thinking_response, final_response]
        mocker.patch("llm.claude.Anthropic", return_value=mock_client)

        mocker.patch("llm.claude.model_output_limit", return_value=20000)
        adapter = ClaudeAdapter(api_key="sk-ant-test")
        result = adapter.generate("分析")

        assert result == "最终分析正文"
        assert mock_client.messages.create.call_count == 2
        assert mock_client.messages.create.call_args_list[1].kwargs["max_tokens"] == 16384

    def test_base_url_passed_to_client(self, mocker):
        mock_anthropic = mocker.patch("llm.claude.Anthropic", return_value=MagicMock())
        ClaudeAdapter(api_key="sk-ant-test", base_url="https://api.deepseek.com/anthropic")
        mock_anthropic.assert_called_once_with(
            api_key="sk-ant-test", base_url="https://api.deepseek.com/anthropic", timeout=60.0,
            max_retries=0, http_client=mocker.ANY,
        )

    def test_deepseek_compatible_endpoint_disables_thinking(self, mocker):
        mock_client = MagicMock()
        mock_response = MagicMock()
        text_block = MagicMock(spec=["text"])
        text_block.text = "最终分析正文"
        mock_response.content = [text_block]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 300
        mock_client.messages.create.return_value = mock_response
        mocker.patch("llm.claude.Anthropic", return_value=mock_client)

        adapter = ClaudeAdapter(
            api_key="sk-ant-test", base_url="https://api.deepseek.com/anthropic/v1",
        )
        assert adapter.generate("分析") == "最终分析正文"
        assert mock_client.messages.create.call_args.kwargs["thinking"] == {"type": "disabled"}

    def test_no_base_url_omits_arg(self, mocker):
        mock_anthropic = mocker.patch("llm.claude.Anthropic", return_value=MagicMock())
        ClaudeAdapter(api_key="sk-ant-test")
        mock_anthropic.assert_called_once_with(
            api_key="sk-ant-test", timeout=60.0, max_retries=0, http_client=mocker.ANY,
        )


def test_automatic_budget_clamps_to_model_limit_and_manual_does_not_query(mocker):
    from types import SimpleNamespace

    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(text="正文")], stop_reason="end_turn", usage=None)
    mocker.patch("llm.claude.Anthropic", return_value=client)
    lookup = mocker.patch("llm.claude.model_output_limit", return_value=4096)
    adapter = ClaudeAdapter(api_key="test")
    assert adapter.generate("分析") == "正文"
    assert client.messages.create.call_args.kwargs["max_tokens"] == 4096
    lookup.reset_mock()
    assert adapter.generate("分析", max_tokens=2000) == "正文"
    assert client.messages.create.call_args.kwargs["max_tokens"] == 2000
    lookup.assert_not_called()


def test_empty_thinking_with_explicit_limit_is_not_retried(mocker):
    from types import SimpleNamespace

    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(content=[SimpleNamespace(thinking="内部思考")],
        stop_reason="max_tokens", usage=None)
    mocker.patch("llm.claude.Anthropic", return_value=client)
    assert "长度上限" in ClaudeAdapter(api_key="test", max_tokens=2000).generate("分析")
    assert client.messages.create.call_count == 1


def test_model_metadata_cache_separates_endpoint_model_and_credentials(mocker):
    from types import SimpleNamespace

    from llm.budget import clear_model_limit_cache, model_output_limit

    clear_model_limit_cache()
    client = MagicMock()
    client.base_url = "https://a.example/v1"
    client.api_key = "first-key"
    client.with_options.return_value = client
    client.models.retrieve.return_value = SimpleNamespace(max_tokens=12345)
    assert model_output_limit(client, "model-a", protocol="claude", deadline=None) == 12345
    assert model_output_limit(client, "model-a", protocol="claude", deadline=None) == 12345
    assert client.models.retrieve.call_count == 1
    client.base_url = "https://b.example/v1"
    assert model_output_limit(client, "model-a", protocol="claude", deadline=None) == 12345
    client.api_key = "second-key"
    assert model_output_limit(client, "model-a", protocol="claude", deadline=None) == 12345
    assert model_output_limit(client, "model-b", protocol="claude", deadline=None) == 12345
    assert client.models.retrieve.call_count == 4
    clear_model_limit_cache()


def test_missing_metadata_is_cached_and_does_not_prevent_generation(mocker):
    from llm.budget import clear_model_limit_cache, model_output_limit

    clear_model_limit_cache()
    client = MagicMock()
    client.base_url = "https://unavailable.example/v1"
    client.api_key = "test"
    client.with_options.return_value = client
    client.models.retrieve.side_effect = RuntimeError("unsupported")
    for _ in range(2):
        assert model_output_limit(client, "model", protocol="claude", deadline=None) is None
    assert client.models.retrieve.call_count == 1
    clear_model_limit_cache()


def test_cache_hit_does_not_wait_for_unrelated_query_and_same_key_is_single_flight():
    import time
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from types import SimpleNamespace

    from llm.budget import automatic_budget, clear_model_limit_cache, model_output_limit
    from llm.transport import request_deadline

    clear_model_limit_cache()
    entered = Event()
    release = Event()
    client = MagicMock()
    client.api_key = "test"
    client.base_url = "https://cache.example/v1"
    client.with_options.return_value = client

    def retrieve(model, **kwargs):
        assert request_deadline.get() is not None
        if model == "slow-model":
            entered.set()
            assert release.wait(timeout=2)
        return SimpleNamespace(max_tokens=4096)

    client.models.retrieve.side_effect = retrieve
    assert model_output_limit(client, "cached-model", protocol="claude", deadline=None) == 4096
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(model_output_limit, client, "slow-model", protocol="claude", deadline=None)
        try:
            assert entered.wait(timeout=1)
            second = executor.submit(model_output_limit, client, "slow-model", protocol="claude", deadline=None)
            limit = model_output_limit(client, "cached-model", protocol="claude", deadline=time.monotonic() + 0.1)
            assert automatic_budget(limit) == 4096
        finally:
            release.set()
        assert first.result(timeout=1) == second.result(timeout=1) == 4096
    assert client.models.retrieve.call_count == 2
    assert request_deadline.get() is None
    clear_model_limit_cache()


def test_success_and_missing_metadata_cache_expire_at_their_respective_ttl(mocker):
    from types import SimpleNamespace

    from llm.budget import clear_model_limit_cache, model_output_limit

    clock = [100.0]
    mocker.patch("llm.budget.time.monotonic", side_effect=lambda: clock[0])
    for limit, ttl in [(4096, 3600), (None, 300)]:
        clear_model_limit_cache()
        client = MagicMock()
        client.api_key = "test"
        client.base_url = "https://ttl.example/v1"
        client.with_options.return_value = client
        client.models.retrieve.return_value = SimpleNamespace(max_tokens=limit)
        start = clock[0]
        assert model_output_limit(client, "model", protocol="claude", deadline=None) == limit
        clock[0] = start + ttl - 0.01
        assert model_output_limit(client, "model", protocol="claude", deadline=None) == limit
        assert client.models.retrieve.call_count == 1
        clock[0] = start + ttl
        assert model_output_limit(client, "model", protocol="claude", deadline=None) == limit
        assert client.models.retrieve.call_count == 2
    clear_model_limit_cache()


def test_metadata_cache_capacity_evicts_least_recently_used_entry():
    from types import SimpleNamespace

    from llm.budget import clear_model_limit_cache, model_output_limit

    clear_model_limit_cache()
    client = MagicMock()
    client.api_key = "test"
    client.base_url = "https://capacity.example/v1"
    client.with_options.return_value = client
    client.models.retrieve.return_value = SimpleNamespace(max_tokens=4096)
    for index in range(128):
        assert model_output_limit(client, f"model-{index}", protocol="claude", deadline=None) == 4096
    assert client.models.retrieve.call_count == 128
    assert model_output_limit(client, "model-0", protocol="claude", deadline=None) == 4096
    assert model_output_limit(client, "model-128", protocol="claude", deadline=None) == 4096
    assert model_output_limit(client, "model-0", protocol="claude", deadline=None) == 4096
    assert client.models.retrieve.call_count == 129
    assert model_output_limit(client, "model-1", protocol="claude", deadline=None) == 4096
    assert client.models.retrieve.call_count == 130
    clear_model_limit_cache()
