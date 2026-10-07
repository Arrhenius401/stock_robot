"""LLM 重试机制测试"""
from unittest.mock import MagicMock

from llm.claude import ClaudeAdapter
from llm.openai import OpenAIAdapter


class TestRetry:
    def test_retries_then_succeeds(self, mocker):
        sleep_mock = mocker.patch("llm.base.time.sleep")
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
        assert sleep_mock.call_args_list == [mocker.call(1.0), mocker.call(2.0)]

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

        adapter = ClaudeAdapter(api_key="sk-ant-test")
        result = adapter.generate("分析")

        assert "解读完成" in result
        assert mock_client.messages.create.call_count == 2


def test_shared_deadline_prevents_network_retry_after_budget_expires(mocker):
    from types import SimpleNamespace

    clock = [0.0]
    mocker.patch("llm.base.time.monotonic", side_effect=lambda: clock[0])
    client = MagicMock()
    def consume_budget(**kwargs):
        clock[0] = 10.0
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None),
            finish_reason="length")], usage=SimpleNamespace(completion_tokens=2000))
    client.chat.completions.create.side_effect = consume_budget
    mocker.patch("llm.openai.OpenAI", return_value=client)
    result = OpenAIAdapter(api_key="test", timeout=10).generate("分析")
    assert client.chat.completions.create.call_count == 1
    assert "不可用" in result


def test_continuous_response_chunks_cannot_extend_total_deadline(mocker):
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class SlowHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                for _ in range(30):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.08)
                self.wfile.write(b'{"choices":[{"message":{"content":"late"},"finish_reason":"stop"}]}')
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        adapter = OpenAIAdapter(api_key="test", timeout=0.3, retry_times=0,
                                base_url=f"http://127.0.0.1:{server.server_port}/v1")
        start = time.monotonic()
        assert "不可用" in adapter.generate("截止测试")
        assert time.monotonic() - start < 1.0
        adapter._client.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_whitespace_is_empty_body_for_both_adapters(mocker):
    from types import SimpleNamespace

    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=" \n\t"), finish_reason="length")],
        usage=None)
    client.messages.create.return_value = SimpleNamespace(
        content=[SimpleNamespace(text=" \n\t")], stop_reason="max_tokens", usage=None)
    mocker.patch("llm.openai.OpenAI", return_value=client)
    mocker.patch("llm.claude.Anthropic", return_value=client)
    for adapter in [OpenAIAdapter(api_key="test", max_tokens=2000),
                    ClaudeAdapter(api_key="test", max_tokens=2000)]:
        assert "长度上限" in adapter.generate("分析")


def test_slow_dns_does_not_delay_cancelled_request(mocker):
    import socket
    import time

    original = socket.getaddrinfo
    def slow_dns(*args, **kwargs):
        time.sleep(0.8)
        return original(*args, **kwargs)
    adapter = OpenAIAdapter(api_key="test", timeout=0.15, retry_times=0,
                            base_url="http://slow-budget.invalid/v1")
    mocker.patch("socket.getaddrinfo", side_effect=slow_dns)
    started = time.monotonic()
    assert "不可用" in adapter.generate("DNS截止测试")
    assert time.monotonic() - started < 0.6
    adapter._client.close()


def test_redirect_and_gzip_response_from_sync_sdk_inside_event_loop():
    import asyncio
    import gzip
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if self.path != "/final":
                self.send_response(307)
                self.send_header("Location", "/final")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = gzip.compress(b'{"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}]}')
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    adapter = OpenAIAdapter(api_key="test", timeout=3, retry_times=0,
                            base_url=f"http://127.0.0.1:{server.server_port}/v1")
    async def generate():
        return adapter.generate("兼容验收")
    try:
        assert asyncio.run(generate()) == "ok"
    finally:
        adapter._client.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
