"""FastAPI HTTP API 端点测试"""
import pytest
from httpx import ASGITransport, AsyncClient
from api.app import create_app


@pytest.fixture
def app():
    from agent.tools import ToolRegistry
    registry = ToolRegistry()

    class MockTool:
        name = "test_tool"
        description = "测试工具"
        parameters = {"type": "object", "properties": {}}
        tags = ["test"]
        source = "pipeline"
        async def execute(self, **kwargs):
            from agent.tools import ToolResult
            return ToolResult(status="success", data={"result": "ok"})

    registry.register(MockTool())
    return create_app(registry=registry)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestToolsEndpoint:
    @pytest.mark.asyncio
    async def test_list_tools(self, client):
        resp = await client.get("/api/v1/tools")
        assert resp.status_code == 200
        tools = resp.json()["tools"]
        assert len(tools) == 1
        assert tools[0]["name"] == "test_tool"

    @pytest.mark.asyncio
    async def test_tool_summary_format(self, client):
        resp = await client.get("/api/v1/tools")
        tool = resp.json()["tools"][0]
        assert "name" in tool
        assert "description" in tool
        assert "tags" in tool


class TestChatEndpoint:
    @pytest.mark.asyncio
    async def test_chat_accepts_request(self, client):
        resp = await client.post("/api/v1/chat", json={"message": "帮我分析平安银行"})
        assert resp.status_code == 200
        data = resp.json()
        assert "response" in data

    @pytest.mark.asyncio
    async def test_chat_empty_message_returns_422(self, client):
        resp = await client.post("/api/v1/chat", json={"message": ""})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_chat_with_session_id(self, client):
        resp = await client.post("/api/v1/chat", json={"message": "你好"},
                                 headers={"X-Session-Id": "sess-001"})
        assert resp.status_code == 200


class TestStreamEndpoint:
    @pytest.mark.asyncio
    async def test_stream_returns_sse(self, client):
        resp = await client.post("/api/v1/chat/stream", json={"message": "测试流式"})
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")


class TestLegacyEndpoints:
    @pytest.mark.asyncio
    async def test_analyze_endpoint_exists(self, client):
        resp = await client.post("/api/v1/analyze", json={"symbol": "000001"})
        assert resp.status_code in (200, 400, 422, 501)

    @pytest.mark.asyncio
    async def test_index_endpoint_exists(self, client):
        resp = await client.post("/api/v1/index", json={"symbol": "000300"})
        assert resp.status_code in (200, 400, 422, 501)
