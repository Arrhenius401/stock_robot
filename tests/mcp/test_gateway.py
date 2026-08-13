"""MCP Gateway 集成测试"""
import pytest
from mcp.gateway import MCPGateway


class TestMCPGateway:
    @pytest.fixture
    def gateway(self):
        return MCPGateway(server_name="test-gw", server_version="0.1.0")

    def test_initial_state(self, gateway):
        assert gateway.server_name == "test-gw"
        assert len(gateway.list_internal_tools()) == 0
        assert len(gateway.list_external_clients()) == 0

    def test_register_local_tools(self, gateway):
        class DummyTool:
            name = "dummy"
            description = "test"
            parameters = {"type": "object", "properties": {}}
            tags = ["test"]
            source = "pipeline"
            async def execute(self, **kwargs):
                from agent.tools import ToolResult
                return ToolResult(status="success", data="ok")

        gateway.register_local_tool(DummyTool())
        tools = gateway.list_internal_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "dummy"

    def test_add_external_client(self, gateway, mocker):
        mock_client = mocker.MagicMock()
        mock_client.is_connected = True
        gateway.add_external_client("news_service", mock_client)
        assert "news_service" in gateway.list_external_clients()

    def test_disconnect_all(self, gateway, mocker):
        c1 = mocker.MagicMock()
        c2 = mocker.MagicMock()
        gateway.add_external_client("s1", c1)
        gateway.add_external_client("s2", c2)
        gateway.disconnect_all()
        c1.disconnect.assert_called_once()
        c2.disconnect.assert_called_once()

    def test_get_server(self, gateway):
        server = gateway.get_server()
        assert server is not None
        assert server._name == "test-gw"
