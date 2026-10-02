"""官方探针的离线预算、错误留证与真实样本回归。"""
import json
from datetime import date
from pathlib import Path

import pytest
import requests

from index import source_probe
from index.valuation_factsheet import parse_factsheet_text

RESOURCES = Path(__file__).resolve().parents[1] / "resources" / "index"
END = date(2026, 9, 30)


def test_real_history_excerpt(tmp_path, monkeypatch):
    raw = (RESOURCES / "csi-930740-price-pe-history.json").read_bytes()
    monkeypatch.setattr(source_probe, "_request_bytes", lambda *_: raw)
    result = source_probe.run_probe("930740", "history", END, tmp_path / "tmp" / "run", root=tmp_path)
    item = result["sources"][0]
    assert item["attempts"] == 1
    assert item["coverage"]["row_count"] == 3
    assert item["coverage"]["last_date"] == "20260930"
    assert "peg" in item["coverage"]["fields"]
    assert Path(item["raw_path"]).read_bytes() == raw
    assert (tmp_path / "tmp" / "run" / "manifest.json").exists()


def test_real_cni_layout_excerpt():
    text = (RESOURCES / "cni-980092-valuation-factsheet-layout.txt").read_text(encoding="utf-8")
    parsed = parse_factsheet_text(text, "980092", "cni", END)
    assert parsed["pe_snapshot"] == 13.5
    assert parsed["pb"] == 1.27
    assert parsed["as_of"] == END


@pytest.mark.parametrize("retries, attempts", [(0, 1), (2, 3)])
def test_explicit_bounded_retry(tmp_path, monkeypatch, retries, attempts):
    calls = []
    def fail(*_):
        calls.append(1)
        raise requests.Timeout("离线超时")
    monkeypatch.setattr(source_probe, "_request_bytes", fail)
    result = source_probe.run_probe("930740", "history", END, tmp_path / "tmp" / "run", root=tmp_path, retries=retries)
    assert len(calls) == attempts
    assert result["sources"][0]["failure"] == "timeout"


def test_total_budget_prevents_following_requests(tmp_path, monkeypatch):
    elapsed = [0.0]
    monkeypatch.setattr(source_probe.time, "monotonic", lambda: elapsed[0])
    calls = []
    def fail(*_):
        calls.append(1)
        elapsed[0] = 31
        raise requests.Timeout("总预算耗尽")
    monkeypatch.setattr(source_probe, "_request_bytes", fail)
    result = source_probe.run_probe("930740", "all", END, tmp_path / "tmp" / "run", root=tmp_path, budget=30, retries=2)
    assert len(calls) == 1
    assert all(item["failure"] == "budget" for item in result["sources"])
    assert result["sources"][1]["attempts"] == 0


def test_parse_failure_preserves_original_response(tmp_path, monkeypatch):
    monkeypatch.setattr(source_probe, "_request_bytes", lambda *_: b"<html>gateway</html>")
    result = source_probe.run_probe("930740", "history", END, tmp_path / "tmp" / "run", root=tmp_path)
    item = result["sources"][0]
    assert item["failure"] == "parse"
    assert Path(item["raw_path"]).read_bytes() == b"<html>gateway</html>"


def test_output_cannot_escape_tmp(tmp_path):
    with pytest.raises(ValueError, match="tmp"):
        source_probe.run_probe("930740", "history", END, tmp_path / "data", root=tmp_path)


def test_pb_matches_exact_index_name(tmp_path, monkeypatch):
    payload = {"data": {"indexValuations": [{"indexName": "中证红利", "pb": 99}, {"indexName": "红利指数", "pb": 0.76, "tradeDate": "20260930"}]}}
    monkeypatch.setattr(source_probe, "_request_bytes", lambda *_: json.dumps(payload).encode())
    result = source_probe.run_probe("000015", "pb", END, tmp_path / "tmp" / "run", root=tmp_path)
    assert result["sources"][0]["coverage"]["matched_rows"][0]["pb"] == 0.76


def test_recovered_retry_clears_failure(tmp_path, monkeypatch):
    calls = []
    def request(*_):
        calls.append(1)
        if len(calls) == 1:
            raise requests.ConnectionError("首次失败")
        return (RESOURCES / "csi-930740-price-pe-history.json").read_bytes()
    monkeypatch.setattr(source_probe, "_request_bytes", request)
    result = source_probe.run_probe("930740", "history", END, tmp_path / "tmp" / "run", root=tmp_path, retries=1)
    assert result["sources"][0]["status"] == "ok"
    assert "failure" not in result["sources"][0]


def test_stream_request_checks_remaining_budget(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(source_probe.time, "monotonic", lambda: now[0])
    observed = []
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return None
        def raise_for_status(self):
            return None
        def iter_content(self, chunk_size):
            now[0] = 11
            yield b"slow"
    def get(*_, **kwargs):
        observed.append(kwargs)
        return Response()
    monkeypatch.setattr(source_probe.requests, "get", get)
    with pytest.raises(TimeoutError, match="总预算"):
        source_probe._read_response_bytes("https://example.invalid", None, 12, 10)
    assert observed[0]["timeout"] == (5, 5)


@pytest.mark.parametrize("options", [{"timeout": float("nan")}, {"budget": float("inf")}, {"retries": 3}])
def test_invalid_budget_options_rejected(tmp_path, options):
    with pytest.raises(ValueError, match="预算"):
        source_probe.run_probe("930740", "history", END, tmp_path / "tmp" / "run", root=tmp_path, **options)


@pytest.mark.parametrize("payload", [{}, {"data": []}, {"data": {}}, {"data": {"indexValuations": None}}])
def test_invalid_pb_structure_is_parse_failure(tmp_path, monkeypatch, payload):
    monkeypatch.setattr(source_probe, "_request_bytes", lambda *_: json.dumps(payload).encode())
    result = source_probe.run_probe("000015", "pb", END, tmp_path / "tmp" / "run", root=tmp_path)
    assert result["sources"][0]["status"] == "failed"
    assert result["sources"][0]["failure"] == "parse"


def test_valid_empty_pb_list_is_success(tmp_path, monkeypatch):
    monkeypatch.setattr(source_probe, "_request_bytes", lambda *_: b'{"data":{"indexValuations":[]}}')
    result = source_probe.run_probe("000015", "pb", END, tmp_path / "tmp" / "run", root=tmp_path)
    assert result["sources"][0]["status"] == "ok"
    assert result["sources"][0]["coverage"]["row_count"] == 0


def test_absolute_deadline_terminates_dripping_reader(tmp_path, monkeypatch):
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    connected = threading.Event()
    disconnected = threading.Event()
    stop = threading.Event()
    class DripHandler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            return None
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "1000000")
            self.end_headers()
            connected.set()
            try:
                while not stop.wait(0.05):
                    self.wfile.write(b"x")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                disconnected.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), DripHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    processes = []
    original = source_probe.subprocess.Popen
    def popen(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(source_probe.subprocess, "Popen", popen)
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError, match="总预算"):
            source_probe._request_bytes(f"http://127.0.0.1:{server.server_port}/", None, 10, started + 4, tmp_path)
        assert time.monotonic() - started < 5.5
        assert connected.is_set(), "应实际进入持续滴流，不能仅覆盖启动超时"
        assert processes and all(process.poll() is not None for process in processes)
        assert disconnected.wait(1), "超时后不能留下持续请求的读取进程"
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=1)


def test_worker_returns_complete_original_bytes(tmp_path):
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    expected = (RESOURCES / "csi-930740-price-pe-history.json").read_bytes()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            return None
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(expected)))
            self.end_headers()
            self.wfile.write(expected)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        raw = source_probe._request_bytes(f"http://127.0.0.1:{server.server_port}/", None, 5, time.monotonic() + 8, tmp_path)
        assert raw == expected
        assert not list(tmp_path.glob("probe-worker-*"))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=1)
