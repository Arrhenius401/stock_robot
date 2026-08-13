"""外部 MCP Client 单元测试"""
import json
import pytest
from mcp.schemas import MCPToolDefinition
from mcp.client import ExternalMCPClient


class FakeProcess:
    def __init__(self, responses=None):
        self.responses = responses or []
        self._response_idx = 0
        self.stdin_writes = []
        self.terminated = False

    @property
    def stdout(self): return self
    @property
    def stdin(self): return self

    def readline(self):
        if self._response_idx < len(self.responses):
            resp = self.responses[self._response_idx]
            self._response_idx += 1
            return resp.encode("utf-8") if isinstance(resp, str) else resp
        return b""

    def write(self, data):
        self.stdin_writes.append(data.decode("utf-8") if isinstance(data, bytes) else data)

    def flush(self): pass
    def poll(self): return None
    def wait(self, timeout=None): pass
    def terminate(self): self.terminated = True
    def kill(self): self.terminated = True


class TestExternalMCPClient:
    @pytest.fixture
    def client(self):
        return ExternalMCPClient(command="python", args=["-m", "fake_mcp"])

    def test_send_request_writes_jsonrpc(self, client):
        proc = FakeProcess(responses=[json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": []}})])
        client._process = proc
        client._send_request_sync("tools/list")
        assert len(proc.stdin_writes) > 0
        assert "tools/list" in proc.stdin_writes[0]

    def test_list_tools_parses_response(self, client):
        proc = FakeProcess(responses=[json.dumps({"jsonrpc": "2.0", "id": 1, "result": {
            "tools": [
                {"name": "t1", "description": "工具1", "inputSchema": {"type": "object", "properties": {}}},
                {"name": "t2", "description": "工具2", "inputSchema": {"type": "object", "properties": {}}},
            ]
        }})])
        client._process = proc
        tools = client.list_tools()
        assert len(tools) == 2
        assert all(isinstance(t, MCPToolDefinition) for t in tools)
        assert tools[0].name == "t1"
        assert tools[1].name == "t2"

    def test_list_tools_empty(self, client):
        proc = FakeProcess(responses=[json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": []}})])
        client._process = proc
        tools = client.list_tools()
        assert tools == []

    def test_disconnect_terminates_process(self, client):
        proc = FakeProcess()
        client._process = proc
        client.disconnect()
        assert proc.terminated is True

    def test_is_connected(self, client):
        assert client.is_connected is False
        client._process = FakeProcess()
        assert client.is_connected is True
