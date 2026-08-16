"""FastAPI HTTP API 端点测试（真实接线：Planner/Executor/SessionManager）"""
import json

import pytest
from httpx import ASGITransport, AsyncClient

from agent.tools import ToolProtocol, ToolRegistry, ToolResult
from api.app import _structured_tool_results, create_app
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


class SymbolLLM:
    """返回含 6 位股票代码步骤描述的 LLM"""

    def generate(self, prompt, system=None, **kwargs):
        return json.dumps({
            "goal": "分析股票估值",
            "complexity": "simple",
            "steps": [{"id": "step-1", "description": "分析 000001 的估值"}],
        }, ensure_ascii=False)


class StockTool:
    name = "analyze_stock"
    description = "分析股票的基本面与估值数据，输入股票代码"
    parameters = {"type": "object", "properties": {"symbol": {"type": "string"}}}
    tags = ["pipeline"]
    source = "pipeline"

    async def execute(self, **kwargs):
        return ToolResult(status="success", data={"symbol": kwargs.get("symbol", "")})


def make_symbol_core():
    """注册 StockTool + SymbolLLM 的核心，验证 tool_args symbol 提取链路"""
    from typing import Any, cast
    registry = ToolRegistry()
    registry.register(StockTool())
    return AgentCore(registry=registry, pipeline=cast(Any, FakePipeline()),
                     index_pipeline=cast(Any, FakeIndexPipeline()),
                     llm=cast(Any, SymbolLLM()))


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


class BrokenRegistry(ToolRegistry):
    """工具匹配即崩溃的注册表，模拟 Agent 执行链路故障

    注：不能通过"LLM 抛异常"触发 500 —— Planner 会吞掉 LLM 异常降级为单步计划，
    工具 execute 异常也被 Executor._safe_execute 隔离；唯一能传播到 API 兜底边界的
    Agent 级故障点是 registry.match（在 _safe_execute 之外调用）。
    """

    def match(self, description: str,
              tags: list[str] | None = None) -> list[ToolProtocol]:
        raise RuntimeError("工具匹配崩溃")


def make_broken_core():
    """Agent 执行链路故障的核心，用于验证 500 兜底与 SSE 错误事件"""
    from typing import Any, cast
    return AgentCore(registry=BrokenRegistry(),
                     pipeline=cast(Any, FakePipeline()),
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
        tools = data["tool_results"]
        assert len(tools) == 1
        assert tools[0]["tool"] == "echo"
        assert tools[0]["status"] == "done"
        assert tools[0]["symbol"] is None
        assert "echo" in tools[0]["content"]

    @pytest.mark.asyncio
    async def test_chat_tool_results_include_symbol(self, tmp_path):
        store = SessionStore(tmp_path / "sessions_sym.db")
        sessions = SessionManager(store, facts_path=tmp_path / "facts_sym.json")
        app_sym = create_app(core=make_symbol_core(), sessions=sessions)
        transport = ASGITransport(app=app_sym)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/v1/chat",
                                json={"message": "分析 000001 的估值"})
        assert resp.status_code == 200
        tools = resp.json()["tool_results"]
        assert len(tools) == 1
        assert tools[0]["tool"] == "analyze_stock"
        assert tools[0]["symbol"] == "000001"
        assert tools[0]["status"] == "done"
        assert "000001" in tools[0]["content"]

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

    @pytest.mark.asyncio
    async def test_chat_returns_500_on_agent_failure(self, tmp_path):
        store = SessionStore(tmp_path / "sessions_err.db")
        sessions = SessionManager(store, facts_path=tmp_path / "facts_err.json")
        app_err = create_app(core=make_broken_core(), sessions=sessions)
        transport = ASGITransport(app=app_err)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/v1/chat",
                                json={"message": "分析平安银行的财务数据"})
        assert resp.status_code == 500
        assert "处理请求时出错" in resp.json()["response"]


class TestStructuredToolResults:
    def test_only_pairs_messages_after_from_index(self):
        from agent.memory import Memory, Plan, TaskStatus, TaskStep

        memory = Memory()
        memory.add_message("tool", "[echo] success: 旧消息")
        before = len(memory.messages)
        memory.add_message("tool", "[echo] success: 新消息")
        plan = Plan(goal="测试", steps=[TaskStep(
            id="step-1", description="echo 测试",
            tool_name="echo", status=TaskStatus.DONE)])
        results = _structured_tool_results(plan, memory, from_index=before)
        assert len(results) == 1
        assert results[0]["content"] == "[echo] success: 新消息"

    def test_without_from_index_uses_all_messages(self):
        from agent.memory import Memory, Plan, TaskStatus, TaskStep

        memory = Memory()
        memory.add_message("tool", "[echo] success: 第一条")
        plan = Plan(goal="测试", steps=[TaskStep(
            id="step-1", description="echo 测试",
            tool_name="echo", status=TaskStatus.DONE)])
        results = _structured_tool_results(plan, memory)
        assert results[0]["content"] == "[echo] success: 第一条"


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

    @pytest.mark.asyncio
    async def test_stream_result_includes_tool_results(self, client):
        async with client.stream("POST", "/api/v1/chat/stream",
                                 json={"message": "echo 测试"}) as resp:
            assert resp.status_code == 200
            body = ""
            async for line in resp.aiter_lines():
                body += line
        assert '"type": "result"' in body
        assert '"tool_results"' in body
        assert '"tool": "echo"' in body

    @pytest.mark.asyncio
    async def test_stream_emits_error_event_on_failure(self, tmp_path):
        store = SessionStore(tmp_path / "sessions_err2.db")
        sessions = SessionManager(store, facts_path=tmp_path / "facts_err2.json")
        app_err = create_app(core=make_broken_core(), sessions=sessions)
        transport = ASGITransport(app=app_err)
        async with (
            AsyncClient(transport=transport, base_url="http://test") as c,
            c.stream("POST", "/api/v1/chat/stream",
                     json={"message": "分析平安银行的财务数据"}) as resp,
        ):
            body = ""
            async for line in resp.aiter_lines():
                body += line
        assert '"type": "error"' in body
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

    @pytest.mark.asyncio
    async def test_analyze_returns_500_on_pipeline_failure(self, tmp_path, mocker):
        mocker.patch("utils.symbols.resolve_name", return_value="平安银行")

        class FailingPipeline:
            def run(self, symbol, name, market="a-shares"):
                raise RuntimeError("数据源崩溃")

        from typing import Any, cast
        core = AgentCore(registry=make_core().registry,
                         pipeline=cast(Any, FailingPipeline()),
                         index_pipeline=cast(Any, FakeIndexPipeline()),
                         llm=cast(Any, FakeLLM()))
        store = SessionStore(tmp_path / "sessions_err3.db")
        sessions = SessionManager(store, facts_path=tmp_path / "facts_err3.json")
        app_err = create_app(core=core, sessions=sessions)
        transport = ASGITransport(app=app_err)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/v1/analyze", json={"symbol": "000001"})
        assert resp.status_code == 500
        assert "数据源崩溃" in resp.json()["error"]


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
