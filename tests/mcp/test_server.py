"""内部 MCP Server 单元测试"""
import json
from typing import ClassVar

import pytest

from mcp.schemas import JSONRPCRequest
from mcp.server import InternalMCPServer


class FakeTool:
    name = "test_tool"
    description = "测试工具"
    parameters: ClassVar[dict] = {"type": "object", "properties": {"x": {"type": "integer"}}}
    tags: ClassVar[list[str]] = ["test"]
    source = "pipeline"

    async def execute(self, **kwargs):
        from agent.tools import ToolResult
        return ToolResult(status="success", data={"result": kwargs.get("x", 0) * 2})


class FakeBrokenTool:
    name = "broken"
    description = "故障工具"
    parameters: ClassVar[dict] = {"type": "object", "properties": {}}
    tags: ClassVar[list[str]] = ["test"]
    source = "pipeline"

    async def execute(self, **kwargs):
        raise RuntimeError("模拟崩溃")


class TestInternalMCPServer:
    @pytest.fixture
    def server(self):
        srv = InternalMCPServer(name="stock-robot", version="0.1.0")
        srv.register_tool(FakeTool())
        return srv

    def test_handle_initialize(self, server):
        req = JSONRPCRequest(method="initialize", id=1, params={
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "test-client", "version": "1.0"},
        })
        resp = server.handle_request(req)
        assert resp.result is not None
        assert resp.result["serverInfo"]["name"] == "stock-robot"

    def test_handle_tools_list(self, server):
        req = JSONRPCRequest(method="tools/list", id=2)
        resp = server.handle_request(req)
        tools = resp.result["tools"]
        assert len(tools) == 1
        assert tools[0]["name"] == "test_tool"
        assert "inputSchema" in tools[0]

    def test_handle_tools_list_empty(self):
        srv = InternalMCPServer(name="empty", version="0.1.0")
        req = JSONRPCRequest(method="tools/list", id=1)
        resp = srv.handle_request(req)
        assert resp.result["tools"] == []

    @pytest.mark.asyncio
    async def test_handle_tools_call_success(self, server):
        req = JSONRPCRequest(method="tools/call", id=3, params={
            "name": "test_tool", "arguments": {"x": 5},
        })
        resp = await server.handle_request_async(req)
        assert resp.result is not None
        assert resp.result["isError"] is False
        text = resp.result["content"][0]["text"]
        data = json.loads(text)
        assert data["data"]["result"] == 10

    @pytest.mark.asyncio
    async def test_handle_tools_call_unknown_tool(self, server):
        req = JSONRPCRequest(method="tools/call", id=4, params={
            "name": "nonexistent", "arguments": {},
        })
        resp = await server.handle_request_async(req)
        assert resp.error is not None
        assert resp.error["code"] == -32602

    @pytest.mark.asyncio
    async def test_handle_tools_call_tool_crash(self):
        srv = InternalMCPServer(name="test", version="0.1.0")
        srv.register_tool(FakeBrokenTool())
        req = JSONRPCRequest(method="tools/call", id=5, params={
            "name": "broken", "arguments": {},
        })
        resp = await srv.handle_request_async(req)
        assert resp.result["isError"] is True

    def test_handle_unknown_method(self, server):
        req = JSONRPCRequest(method="unknown/method", id=6)
        resp = server.handle_request(req)
        assert resp.error is not None
        assert resp.error["code"] == -32601

    def test_register_tools_bulk(self):
        srv = InternalMCPServer(name="test", version="0.1.0")
        srv.register_tools([FakeTool(), FakeBrokenTool()])
        req = JSONRPCRequest(method="tools/list", id=1)
        resp = srv.handle_request(req)
        assert len(resp.result["tools"]) == 2
