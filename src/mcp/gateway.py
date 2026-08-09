"""MCP Gateway 入口 — 统一管理内部 Server 与外部 Client"""
import logging
from mcp.server import InternalMCPServer
from mcp.client import ExternalMCPClient
from mcp.adapter import MCPAdapter

logger = logging.getLogger(__name__)


class MCPGateway:
    """MCP Gateway — 双角色入口

    - 内部 Server: 将本地 ToolProtocol 工具通过 MCP 暴露
    - 外部 Client: 连接第三方 MCP Server，发现其工具并适配为 ToolProtocol
    """

    def __init__(self, server_name: str = "stock-robot",
                 server_version: str = "0.1.0"):
        self._server = InternalMCPServer(name=server_name, version=server_version)
        self._clients: dict[str, ExternalMCPClient] = {}
        self._adapter = MCPAdapter()

    # ------------------------------------------------------------------
    # 内部 Server
    # ------------------------------------------------------------------

    @property
    def server_name(self) -> str:
        return self._server._name

    def register_local_tool(self, tool):
        """向内部 MCP Server 注册本地工具"""
        self._server.register_tool(tool)

    def list_internal_tools(self) -> list[dict]:
        """列出内部 Server 已注册的工具定义"""
        from mcp.schemas import JSONRPCRequest
        resp = self._server._handle_tools_list(
            JSONRPCRequest(method="tools/list", id=0)
        )
        return resp.result.get("tools", [])

    def get_server(self) -> InternalMCPServer:
        return self._server

    # ------------------------------------------------------------------
    # 外部 Client
    # ------------------------------------------------------------------

    def connect_external(self, name: str, command: str,
                         args: list[str] | None = None) -> ExternalMCPClient:
        """连接一个外部 MCP Server"""
        client = ExternalMCPClient(command=command, args=args)
        client.connect()
        self._clients[name] = client
        logger.info("已连接外部 MCP Server: %s (%s)", name, command)
        return client

    def add_external_client(self, name: str, client):
        """注册一个已连接的外部 MCP Client"""
        self._clients[name] = client

    def list_external_clients(self) -> list[str]:
        return list(self._clients.keys())

    def get_external_client(self, name: str) -> ExternalMCPClient | None:
        return self._clients.get(name)

    def discover_external_tools(self, client_name: str):
        """发现并注册外部 MCP Server 的所有工具"""
        client = self._clients.get(client_name)
        if not client:
            raise ValueError(f"外部 MCP Client 不存在: {client_name}")

        mcp_tools = client.list_tools()
        protocols = MCPAdapter.bulk_translate(mcp_tools, source="mcp_external")
        for tool in protocols:
            self._server.register_tool(tool)
        logger.info("从 %s 发现 %d 个工具", client_name, len(protocols))
        return protocols

    def disconnect_all(self):
        """断开所有外部 MCP Client"""
        for name, client in list(self._clients.items()):
            try:
                client.disconnect()
            except Exception as e:
                logger.warning("断开 %s 失败: %s", name, e)
        self._clients.clear()

    # ------------------------------------------------------------------
    # stdio 模式
    # ------------------------------------------------------------------

    def serve_stdio(self):
        """以 stdio MCP Server 模式运行（供外部客户端连接）"""
        self._server.serve_stdio()
