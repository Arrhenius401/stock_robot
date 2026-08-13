"""MCP JSON-RPC 消息类型与工具定义单元测试"""
import json

from mcp.schemas import (
    MCP_LIST_TOOLS_REQUEST,
    JSONRPCRequest,
    JSONRPCResponse,
    MCPToolCallResult,
    MCPToolDefinition,
)


class TestJSONRPCRequest:
    def test_parse_valid_request(self):
        raw = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        req = JSONRPCRequest.parse(raw)
        assert req.jsonrpc == "2.0"
        assert req.id == 1
        assert req.method == "tools/list"
        assert req.params is None

    def test_parse_request_with_params(self):
        raw = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                          "params": {"name": "test_tool", "arguments": {"a": 1}}})
        req = JSONRPCRequest.parse(raw)
        assert req.params == {"name": "test_tool", "arguments": {"a": 1}}

    def test_to_json_roundtrip(self):
        req = JSONRPCRequest(method="tools/list", id=42)
        raw = req.to_json()
        parsed = JSONRPCRequest.parse(raw)
        assert parsed.method == "tools/list"
        assert parsed.id == 42

    def test_is_notification_when_id_is_none(self):
        req = JSONRPCRequest(method="notifications/initialized", id=None)
        assert req.is_notification is True

    def test_is_notification_false_with_id(self):
        req = JSONRPCRequest(method="tools/call", id=1)
        assert req.is_notification is False


class TestJSONRPCResponse:
    def test_serialize_success_response(self):
        resp = JSONRPCResponse.success(id=1, result={"tools": []})
        d = resp.to_dict()
        assert d["jsonrpc"] == "2.0"
        assert d["id"] == 1
        assert "result" in d
        assert "error" not in d

    def test_serialize_error_response(self):
        resp = JSONRPCResponse.error(id=2, code=-32600, message="Invalid Request")
        d = resp.to_dict()
        assert "error" in d
        assert d["error"]["code"] == -32600


class TestMCPToolDefinition:
    def test_from_dict_maps_fields(self):
        d = {"name": "fetch_stock_data", "description": "获取股票行情数据",
             "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}}}}
        tool = MCPToolDefinition.from_dict(d)
        assert tool.name == "fetch_stock_data"
        assert tool.input_schema == d["inputSchema"]

    def test_to_dict_roundtrip(self):
        original = MCPToolDefinition(name="calc_indicator", description="计算技术指标",
                                     input_schema={"type": "object", "properties": {}})
        restored = MCPToolDefinition.from_dict(original.to_dict())
        assert restored.name == original.name
        assert restored.input_schema == original.input_schema

    def test_list_tools_request_is_correct(self):
        d = MCP_LIST_TOOLS_REQUEST
        assert d["method"] == "tools/list"
        assert d["jsonrpc"] == "2.0"


class TestMCPToolCallResult:
    def test_success_result(self):
        result = MCPToolCallResult(content=[{"type": "text", "text": "分析完成"}], is_error=False)
        d = result.to_dict()
        assert d["content"][0]["text"] == "分析完成"
        assert d["isError"] is False

    def test_error_result(self):
        result = MCPToolCallResult(content=[{"type": "text", "text": "工具执行失败"}], is_error=True)
        d = result.to_dict()
        assert d["isError"] is True
