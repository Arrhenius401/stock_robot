"""FastAPI HTTP API 端点测试（真实接线：Planner/Executor/SessionManager）"""
import json

import pytest
from httpx import ASGITransport, AsyncClient

from agent.tools import ToolProtocol, ToolRegistry, ToolResult
from api.app import _structured_tool_results, create_app
from api.bootstrap import AgentCore
from api.sessions import SessionManager, SessionStore
from tests.agent.fake_chat_model import FakeChatModel


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
        compare = None
        if len(targets) >= 2:
            compare = SimpleNamespace(
                headers=["指数", "收盘"],
                rows=[{"指数": "000300", "收盘": 3854},
                      {"指数": "000905", "收盘": 5921}],
            )
        return SimpleNamespace(
            reports=[FakeIndexReport() for _ in targets],
            compare=compare, errors=[],
        )


def make_core():
    from typing import Any, cast
    registry = ToolRegistry()
    registry.register(EchoTool())
    # 假实现不继承重型 Pipeline/IndexPipeline，构造处诚实标注 Any
    return AgentCore(registry=registry, pipeline=cast(Any, FakePipeline()),
                     index_pipeline=cast(Any, FakeIndexPipeline()),
                     llm=cast(Any, FakeLLM()))


class ChatModeLLM:
    """返回 mode=chat 计划的 LLM"""

    def generate(self, prompt, system=None, **kwargs):
        return json.dumps({
            "goal": "闲聊",
            "complexity": "simple",
            "mode": "chat",
            "steps": [],
        }, ensure_ascii=False)


def make_chat_core():
    from typing import Any, cast
    registry = ToolRegistry()
    registry.register(EchoTool())
    return AgentCore(registry=registry, pipeline=cast(Any, FakePipeline()),
                     index_pipeline=cast(Any, FakeIndexPipeline()),
                     llm=cast(Any, ChatModeLLM()),
                     model=FakeChatModel(content="你好呀！有什么可以帮你？"))


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

    @pytest.mark.asyncio
    async def test_chat_chat_mode_returns_direct_reply(self, tmp_path):
        sessions = SessionManager(SessionStore(tmp_path / "s.db"))
        app_chat = create_app(core=make_chat_core(), sessions=sessions)
        async with AsyncClient(transport=ASGITransport(app=app_chat),
                               base_url="http://test") as c:
            resp = await c.post("/api/v1/chat", json={"message": "你好"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["response"] == "你好呀！有什么可以帮你？"
        assert body["plan"]["mode"] == "chat"
        assert body["plan"]["steps"] == []
        assert body["tool_results"] == []
        # 回复写入会话消息
        msgs = sessions.get_messages(body["session_id"])
        assert msgs is not None
        assert any(m["role"] == "assistant"
                   and "你好呀" in m["content"] for m in msgs)


class TestStructuredToolResults:
    def _make_plan(self, step_count, max_messages=None):
        from agent.memory import Memory, TaskStatus
        from agent.planner import Plan, TaskStep

        steps = [TaskStep(id=f"step-{i}", description=f"步骤 {i}",
                          tool_name="echo", status=TaskStatus.DONE)
                 for i in range(1, step_count + 1)]
        memory = Memory() if max_messages is None else Memory(max_messages=max_messages)
        return memory, Plan(goal="测试", steps=steps)

    def test_pairs_only_latest_round_messages(self):
        memory, plan = self._make_plan(1)
        memory.add_message("tool", "[echo] success: 旧消息")
        memory.add_message("tool", "[echo] success: 新消息")
        results = _structured_tool_results(plan, memory)
        assert len(results) == 1
        assert results[0]["content"] == "[echo] success: 新消息"

    def test_pairs_steps_in_order(self):
        memory, plan = self._make_plan(2)
        memory.add_message("tool", "[echo] success: 第一条")
        memory.add_message("tool", "[echo] success: 第二条")
        results = _structured_tool_results(plan, memory)
        assert [r["content"] for r in results] == [
            "[echo] success: 第一条", "[echo] success: 第二条"]

    def test_truncation_does_not_mispair(self):
        """Memory 截断到 max_messages 时尾部配对仍正确"""
        memory, plan = self._make_plan(2, max_messages=5)
        memory.add_message("system", "填充1")
        memory.add_message("system", "填充2")
        memory.add_message("system", "填充3")
        memory.add_message("tool", "[echo] success: 旧消息")
        memory.add_message("system", "填充4")
        # 此时 5 条已满，后续追加触发截断
        memory.add_message("system", "开始执行")
        memory.add_message("tool", "[echo] success: 本轮一")
        memory.add_message("tool", "[echo] success: 本轮二")
        # 固化前提：截断确实发生（8 条追加、上限 5，最旧的 3 条被丢弃）
        assert len(memory.messages) == 5
        assert all(m["content"] != "填充1" for m in memory.messages)
        results = _structured_tool_results(plan, memory)
        assert [r["content"] for r in results] == [
            "[echo] success: 本轮一", "[echo] success: 本轮二"]

    def test_failed_step_gets_none_content(self):
        from agent.memory import Memory, TaskStatus
        from agent.planner import Plan, TaskStep

        memory = Memory()
        memory.add_message("tool", "[echo] success: 唯一消息")
        plan = Plan(goal="测试", steps=[
            TaskStep(id="s1", description="失败步骤",
                     tool_name="echo", status=TaskStatus.FAILED),
            TaskStep(id="s2", description="成功步骤",
                     tool_name="echo", status=TaskStatus.DONE),
        ])
        results = _structured_tool_results(plan, memory)
        assert results[0]["content"] is None
        assert results[1]["content"] == "[echo] success: 唯一消息"


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

    @pytest.mark.asyncio
    async def test_stream_chat_mode_emits_text(self, tmp_path):
        sessions = SessionManager(SessionStore(tmp_path / "s2.db"))
        app_chat = create_app(core=make_chat_core(), sessions=sessions)
        transport = ASGITransport(app=app_chat)
        async with (
            AsyncClient(transport=transport, base_url="http://test") as c,
            c.stream("POST", "/api/v1/chat/stream",
                     json={"message": "你好"}) as resp,
        ):
            body = b""
            async for chunk in resp.aiter_bytes():
                body += chunk

        text = body.decode()
        assert '"type": "text"' in text
        assert "你好呀" in text


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
        assert "change_pct" in data["overview"]
        assert data["overview"]["change_pct"] is None  # FakePipeline 无价格数据

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
        assert data["compare"] is None

    @pytest.mark.asyncio
    async def test_index_invalid_symbol_returns_422(self, client):
        resp = await client.post("/api/v1/index", json={"symbol": "###"})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_index_multi_symbols_returns_compare(self, client, mocker):
        from types import SimpleNamespace
        mocker.patch("data.index_mapping.IndexMapping.lookup",
                     return_value=SimpleNamespace(
                         name="测试指数", market="a-shares", index_style="broad"))
        resp = await client.post("/api/v1/index",
                                 json={"symbols": ["000300", "000905"]})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["reports"]) == 2
        assert data["compare"]["headers"] == ["指数", "收盘"]
        assert len(data["compare"]["rows"]) == 2
        assert data["errors"] == []

    @pytest.mark.asyncio
    async def test_index_symbols_space_string(self, client, mocker):
        from types import SimpleNamespace
        mocker.patch("data.index_mapping.IndexMapping.lookup",
                     return_value=SimpleNamespace(
                         name="测试指数", market="a-shares", index_style="broad"))
        resp = await client.post("/api/v1/index",
                                 json={"symbols": "000300 000905"})
        assert resp.status_code == 200
        assert len(resp.json()["reports"]) == 2

    @pytest.mark.asyncio
    async def test_index_mixed_valid_invalid(self, client):
        resp = await client.post("/api/v1/index",
                                 json={"symbols": ["000300", "###"]})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["reports"]) == 1
        assert any("无效的指数代码" in e for e in data["errors"])

    @pytest.mark.asyncio
    async def test_index_all_invalid_returns_422(self, client):
        resp = await client.post("/api/v1/index", json={"symbols": ["###"]})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_index_symbols_comma_string(self, client, mocker):
        from types import SimpleNamespace
        mocker.patch("data.index_mapping.IndexMapping.lookup",
                     return_value=SimpleNamespace(
                         name="测试指数", market="a-shares", index_style="broad"))
        resp = await client.post("/api/v1/index",
                                 json={"symbols": "000300,000905"})
        assert resp.status_code == 200
        assert len(resp.json()["reports"]) == 2

    @pytest.mark.asyncio
    async def test_index_empty_symbols_returns_422(self, client):
        resp = await client.post("/api/v1/index", json={"symbols": []})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_index_non_list_symbols_returns_422(self, client):
        resp = await client.post("/api/v1/index", json={"symbols": 123})
        assert resp.status_code == 422
        assert "格式无效" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_index_null_item_skipped(self, client):
        resp = await client.post("/api/v1/index",
                                 json={"symbols": [None, "000300"]})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["reports"]) == 1
        assert data["errors"] == []


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

    @pytest.mark.asyncio
    async def test_get_messages_returns_history(self, client):
        r = await client.post("/api/v1/chat", json={"message": "echo 测试"})
        sid = r.json()["session_id"]
        resp = await client.get(f"/api/v1/sessions/{sid}/messages")
        assert resp.status_code == 200
        msgs = resp.json()["messages"]
        roles = [m["role"] for m in msgs]
        assert "user" in roles
        assert "tool" in roles
        assert all("content" in m and "role" in m for m in msgs)

    @pytest.mark.asyncio
    async def test_get_messages_unknown_session_returns_404(self, client):
        resp = await client.get("/api/v1/sessions/nope/messages")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_messages_after_clear_returns_empty(self, client):
        r = await client.post("/api/v1/chat", json={"message": "echo 测试"})
        sid = r.json()["session_id"]
        await client.post(f"/api/v1/sessions/{sid}/clear")
        resp = await client.get(f"/api/v1/sessions/{sid}/messages")
        assert resp.status_code == 200
        assert resp.json()["messages"] == []


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

    @pytest.mark.asyncio
    async def test_messages_returns_503(self, empty_client):
        resp = await empty_client.get("/api/v1/sessions/any/messages")
        assert resp.status_code == 503
