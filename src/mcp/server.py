"""内部 MCP Server — stdio JSON-RPC 暴露本地工具"""
import json
import sys
import logging
import asyncio
from mcp.schemas import JSONRPCRequest, JSONRPCResponse, MCPToolCallResult
from mcp.adapter import MCPAdapter
from agent.tools import ToolResult

logger = logging.getLogger(__name__)

ERROR_PARSE = -32700
ERROR_METHOD = -32601
ERROR_PARAMS = -32602
ERROR_INTERNAL = -32603


class InternalMCPServer:
    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, name: str, version: str):
        self._name = name
        self._version = version
        self._tools: dict[str, object] = {}
        self._adapter = MCPAdapter()
        self._initialized = False

    def register_tool(self, tool):
        self._tools[tool.name] = tool

    def register_tools(self, tools: list):
        for t in tools:
            self.register_tool(t)

    def handle_request(self, req: JSONRPCRequest) -> JSONRPCResponse:
        method = req.method
        if method == "initialize":
            return self._handle_initialize(req)
        elif method == "tools/list":
            return self._handle_tools_list(req)
        else:
            return JSONRPCResponse.error(req.id, ERROR_METHOD, f"未知方法: {method}")

    async def handle_request_async(self, req: JSONRPCRequest) -> JSONRPCResponse:
        if req.method == "tools/call":
            return await self._handle_tools_call(req)
        return self.handle_request(req)

    def _handle_initialize(self, req: JSONRPCRequest) -> JSONRPCResponse:
        self._initialized = True
        return JSONRPCResponse.success(req.id, {
            "protocolVersion": self.PROTOCOL_VERSION,
            "serverInfo": {"name": self._name, "version": self._version},
            "capabilities": {"tools": {}},
        })

    def _handle_tools_list(self, req: JSONRPCRequest) -> JSONRPCResponse:
        tools = []
        for tool in self._tools.values():
            tools.append({
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.parameters,
            })
        return JSONRPCResponse.success(req.id, {"tools": tools})

    async def _handle_tools_call(self, req: JSONRPCRequest) -> JSONRPCResponse:
        params = req.params or {}
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        tool = self._tools.get(tool_name)
        if tool is None:
            return JSONRPCResponse.error(req.id, ERROR_PARAMS, f"未知工具: {tool_name}")
        try:
            result = await tool.execute(**arguments)
        except Exception as e:
            logger.error("工具 %s 执行异常: %s", tool_name, e)
            result = ToolResult(status="error", error=str(e))
        mcp_result = MCPAdapter.result_to_mcp(result)
        return JSONRPCResponse.success(req.id, mcp_result.to_dict())

    def serve_stdio(self):
        logger.info("MCP Server %s v%s 启动 (stdio)", self._name, self._version)
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    req = JSONRPCRequest.parse(line)
                except json.JSONDecodeError as e:
                    resp = JSONRPCResponse.error(None, ERROR_PARSE, f"JSON 解析失败: {e}")
                    sys.stdout.write(resp.to_json() + "\n")
                    sys.stdout.flush()
                    continue
                if req.method in ("tools/call",):
                    try:
                        loop = asyncio.get_event_loop()
                    except RuntimeError:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                    resp = loop.run_until_complete(self.handle_request_async(req))
                else:
                    resp = self.handle_request(req)
                if not req.is_notification:
                    sys.stdout.write(resp.to_json() + "\n")
                    sys.stdout.flush()
        except KeyboardInterrupt:
            pass
        finally:
            logger.info("MCP Server 已关闭")
