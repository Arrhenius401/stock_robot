"""FastAPI HTTP API 端点测试（真实接线：Planner/Executor/SessionManager）"""
import json

import pytest
from httpx import ASGITransport, AsyncClient

from agent.tools import ToolRegistry, ToolResult
from api.app import create_app
from api.bootstrap import AgentCore
from api.sessions import SessionManager, SessionStore


class FakeLLM:
    """返回合法 JSON 计划的 LLM，验证 Planner 解析路径"""

    def generate(self, prompt, system=None, **kwargs):
        return json.dumps({
            "goal": "测试目标",
            "complexity": "simple",
            "steps": [{"id": "step-1", "description": "echo 测试"}],
        }, ensure_ascii=False)


class EchoTool:
    name = "echo"
    description = "回显工具，echo 输入内容"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}
    tags = ["test"]
    source = "pipeline"

    async def execute(self, **kwargs):
        return ToolResult(status="success", data={"echo": kwargs.get("text", "")})


class FakePipeline:
    def run(self, symbol, name, market="a-shares"):
        from data.schemas import AnalysisContext, AnalysisResult
        results = [AnalysisResult(
            dimension="financial", status="ok", summary="财务健康", score=8.0,
        )]
        return results, {"bulk": "AI 解读"}, AnalysisContext(
            symbol=symbol, name=name, market=market,
        )


class FakeIndexReport:
    def __init__(self):
        self.code = "000300"
        self.name = "沪深300"

    def model_dump(self, mode="json"):
        return {"code": self.code, "name": self.name}


class FakeIndexPipeline:
    def run(self, targets, on_progress=None):
        from types import SimpleNamespace
        return SimpleNamespace(reports=[FakeIndexReport()], errors=[])


def make_core():
    from typing import Any, cast
    registry = ToolRegistry()
    registry.register(EchoTool())
    # 假实现不继承重型 Pipeline/IndexPipeline，构造处诚实标注 Any
    return AgentCore(registry=registry, pipeline=cast(Any, FakePipeline()),
                     index_pipeline=cast(Any, FakeIndexPipeline()),
                     llm=cast(Any, FakeLLM()))


@pytest.fixture
def app(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    sessions = SessionManager(store, facts_path=tmp_path / "facts.json")
    return create_app(core=make_core(), sessions=sessions)


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
        assert tools[0]["name"] == "echo"


class TestChatEndpoint:
    @pytest.mark.asyncio
    async def test_chat_runs_agent_end_to_end(self, client):
        resp = await client.post("/api/v1/chat", json={"message": "echo 测试"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"]
        assert "完成: 1/1 步骤" in data["response"]
        assert data["plan"]["steps"][0]["status"] == "done"
        assert any("echo" in t for t in data["tool_results"])

    @pytest.mark.asyncio
    async def test_chat_empty_message_returns_422(self, client):
        resp = await client.post("/api/v1/chat", json={"message": ""})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_chat_with_session_id_reuses_session(self, client):
        r1 = await client.post("/api/v1/chat", json={"message": "echo 一"})
        sid = r1.json()["session_id"]
        r2 = await client.post("/api/v1/chat", json={"message": "echo 二", "session_id": sid})
        assert r2.status_code == 200
        assert r2.json()["session_id"] == sid


class TestStreamEndpoint:
    @pytest.mark.asyncio
    async def test_stream_returns_sse(self, client):
        resp = await client.post("/api/v1/chat/stream", json={"message": "echo 测试"})
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_stream_contains_plan_and_done_events(self, client):
        async with client.stream("POST", "/api/v1/chat/stream",
                                 json={"message": "echo 测试"}) as resp:
            assert resp.status_code == 200
            body = ""
            async for line in resp.aiter_lines():
                body += line
        assert '"type": "start"' in body
        assert '"type": "plan"' in body
        assert '"type": "done"' in body


class TestAnalyzeEndpoint:
    @pytest.mark.asyncio
    async def test_analyze_returns_report_json(self, client, mocker):
        mocker.patch("utils.symbols.resolve_name", return_value="平安银行")
        resp = await client.post("/api/v1/analyze", json={"symbol": "000001"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "000001"
        assert data["name"] == "平安银行"
        assert data["dimensions"]["financial"]["score"] == 8.0
        assert data["score"]["base"] == 8.0
        assert data["commentary"] == "AI 解读"

    @pytest.mark.asyncio
    async def test_analyze_invalid_symbol_returns_422(self, client):
        resp = await client.post("/api/v1/analyze", json={"symbol": "abc"})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_analyze_empty_symbol_returns_422(self, client):
        resp = await client.post("/api/v1/analyze", json={"symbol": ""})
        assert resp.status_code == 422


class TestIndexEndpoint:
    @pytest.mark.asyncio
    async def test_index_returns_report_json(self, client):
        resp = await client.post("/api/v1/index", json={"symbol": "000300"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["reports"][0]["code"] == "000300"
        assert data["errors"] == []

    @pytest.mark.asyncio
    async def test_index_invalid_symbol_returns_422(self, client):
        resp = await client.post("/api/v1/index", json={"symbol": "###"})
        assert resp.status_code == 422


class TestSessionsEndpoints:
    @pytest.mark.asyncio
    async def test_sessions_crud(self, client):
        create_resp = await client.post("/api/v1/sessions")
        assert create_resp.status_code == 200
        sid = create_resp.json()["session_id"]

        list_resp = await client.get("/api/v1/sessions")
        ids = [s["session_id"] for s in list_resp.json()["sessions"]]
        assert sid in ids

        clear_resp = await client.post(f"/api/v1/sessions/{sid}/clear")
        assert clear_resp.status_code == 200

        del_resp = await client.delete(f"/api/v1/sessions/{sid}")
        assert del_resp.status_code == 200

        del_again = await client.delete(f"/api/v1/sessions/{sid}")
        assert del_again.status_code == 404

    @pytest.mark.asyncio
    async def test_clear_unknown_session_returns_404(self, client):
        resp = await client.post("/api/v1/sessions/nope/clear")
        assert resp.status_code == 404


class TestNoCoreMode:
    @pytest.fixture
    def empty_app(self):
        return create_app()

    @pytest.fixture
    async def empty_client(self, empty_app):
        transport = ASGITransport(app=empty_app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    @pytest.mark.asyncio
    async def test_chat_returns_not_injected_message(self, empty_client):
        resp = await empty_client.post("/api/v1/chat", json={"message": "你好"})
        assert resp.status_code == 200
        assert "Agent 核心未注入" in resp.json()["response"]

    @pytest.mark.asyncio
    async def test_analyze_returns_503(self, empty_client):
        resp = await empty_client.post("/api/v1/analyze", json={"symbol": "000001"})
        assert resp.status_code == 503
