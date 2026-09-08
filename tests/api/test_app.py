"""FastAPI HTTP API 端点测试（真实接线：Planner/Executor/SessionManager）"""
import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from agent.tools import ToolProtocol, ToolRegistry, ToolResult
from api.app import (
    _extract_stock_reports,
    _persist_artifacts_if_present,
    _structured_tool_results,
    create_app,
)
from api.bootstrap import AgentCore
from api.runtime import RuntimeManager
from api.sessions import SessionManager, SessionStore
from data.industry_mapping_builder import IndustryMappingError
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


class MultiSymbolLLM:
    """返回两个报告步骤，验证一次执行产生多份成果。"""

    def generate(self, prompt, system=None, **kwargs):
        return json.dumps({
            "goal": "连续分析两只股票",
            "complexity": "complex",
            "steps": [
                {"id": "step-1", "description": "分析 000001 的估值"},
                {"id": "step-2", "description": "分析 600036 的估值"},
            ],
        }, ensure_ascii=False)


class StockTool:
    name = "analyze_stock"
    description = "分析股票的基本面与估值数据，输入股票代码"
    parameters = {"type": "object", "properties": {"symbol": {"type": "string"}}}
    tags = ["pipeline"]
    source = "pipeline"

    async def execute(self, **kwargs):
        symbol = kwargs.get("symbol", "")
        return ToolResult(status="success", data={
            "symbol": symbol,
            "name": "平安银行",
            "overview": {"industry": "银行"},
            "score": {"base": 7.2, "final": 7.0, "risk_deduction": 0.2},
            "score_rows": [{"dimension": "财务", "score": 7.2}],
            "dimensions": {"financial": {"summary": "财务稳健"}},
            "comments": ["基本面稳健", "关注净息差变化"],
            "generated_at": "2026-08-24T10:00:00+08:00",
        })


def make_symbol_core():
    """注册 StockTool + SymbolLLM 的核心，验证 tool_args symbol 提取链路"""
    from typing import Any, cast
    registry = ToolRegistry()
    registry.register(StockTool())
    return AgentCore(registry=registry, pipeline=cast(Any, FakePipeline()),
                     index_pipeline=cast(Any, FakeIndexPipeline()),
                     llm=cast(Any, SymbolLLM()))


def make_multi_symbol_core():
    """注册报告工具与双步骤计划。"""
    from typing import Any, cast
    registry = ToolRegistry()
    registry.register(StockTool())
    return AgentCore(registry=registry, pipeline=cast(Any, FakePipeline()),
                     index_pipeline=cast(Any, FakeIndexPipeline()),
                     llm=cast(Any, MultiSymbolLLM()))


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


class TestAnalyzeSignal:
    def test_analyze_response_contains_signal(self, mocker, tmp_path):
        """/api/v1/analyze 响应含结构化 signal 字段（FakePipeline 得分为 8 → 进攻）"""
        from fastapi.testclient import TestClient

        from api.app import create_app

        mocker.patch("utils.symbols.resolve_name", return_value="平安银行")
        sessions = SessionManager(SessionStore(tmp_path / "signal_sessions.db"))
        app = create_app(core=make_core(), sessions=sessions, push=False)
        client = TestClient(app)
        resp = client.post("/api/v1/analyze", json={"symbol": "000001"})
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["signal"] == {
            "level": "attack",
            "label": "进攻",
            "action": "可考虑建仓/加仓",
            "position": "60%-80%",
        }


class ChatModeLLM:
    """返回 mode=chat 计划的 LLM"""

    def generate(self, prompt, system=None, **kwargs):
        return json.dumps({
            "goal": "闲聊",
            "complexity": "simple",
            "mode": "chat",
            "steps": [],
        }, ensure_ascii=False)


def make_chat_core(model=None):
    from typing import Any, cast
    registry = ToolRegistry()
    registry.register(EchoTool())
    return AgentCore(registry=registry, pipeline=cast(Any, FakePipeline()),
                     index_pipeline=cast(Any, FakeIndexPipeline()),
                     llm=cast(Any, ChatModeLLM()),
                     model=model or FakeChatModel(content="你好呀！有什么可以帮你？"))


class TitleAwareModel:
    """区分正文与标题请求的模型桩，不触发任何真实网络调用。"""

    def __init__(self, *, title: str = "模型润色标题", title_error: bool = False,
                 title_delay: float = 0.0):
        self.title = title
        self.title_error = title_error
        self.title_delay = title_delay

    async def ainvoke(self, messages):
        from types import SimpleNamespace

        system = str(messages[0].get("content", "")) if messages else ""
        if "投研会话标题" in system:
            if self.title_delay:
                await asyncio.sleep(self.title_delay)
            if self.title_error:
                raise RuntimeError("标题模型不可用")
            return SimpleNamespace(content=self.title)
        return SimpleNamespace(content="正文回复")


def make_titled_chat_core(model: TitleAwareModel):
    core = make_chat_core()
    core.model = model
    return core


def parse_sse_events(text: str) -> list[dict]:
    """将测试响应中的 SSE data 行解析为事件。"""
    return [json.loads(line.removeprefix("data: "))
            for line in text.splitlines() if line.startswith("data: ")]


class AgentModeLLM:
    """返回 mode=agent 计划的 LLM"""

    def generate(self, prompt, system=None, **kwargs):
        return json.dumps({
            "goal": "对比分析",
            "complexity": "complex",
            "mode": "agent",
            "steps": [],
        }, ensure_ascii=False)


def make_agent_core():
    """注册 EchoTool + AgentModeLLM + 序列响应的 FakeChatModel"""
    from typing import Any, cast

    from langchain_core.messages import AIMessage

    registry = ToolRegistry()
    registry.register(EchoTool())
    model = FakeChatModel(responses=[
        AIMessage(content="", tool_calls=[
            {"name": "echo", "args": {"text": "对比"}, "id": "call_1"}]),
        AIMessage(content="对比结论", tool_calls=[]),
    ])
    return AgentCore(registry=registry, pipeline=cast(Any, FakePipeline()),
                     index_pipeline=cast(Any, FakeIndexPipeline()),
                     llm=cast(Any, AgentModeLLM()), model=model)


def make_multi_report_agent_core():
    """ReAct 一轮调用两次报告工具，验证 agent 多成果事件。"""
    from typing import Any, cast

    from langchain_core.messages import AIMessage

    registry = ToolRegistry()
    registry.register(StockTool())
    model = FakeChatModel(responses=[
        AIMessage(content="", tool_calls=[
            {"name": "analyze_stock", "args": {"symbol": "000001"}, "id": "call_1"},
            {"name": "analyze_stock", "args": {"symbol": "000001"}, "id": "call_2"},
        ]),
        AIMessage(content="两次分析完成", tool_calls=[]),
    ])
    return AgentCore(registry=registry, pipeline=cast(Any, FakePipeline()),
                     index_pipeline=cast(Any, FakeIndexPipeline()),
                     llm=cast(Any, AgentModeLLM()), model=model)


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


def test_create_app_uses_runtime_as_the_only_initial_push_owner(tmp_path, monkeypatch):
    """生产 core 注入应复用既有 core，且不得先创建传统推送调度器。"""
    monkeypatch.chdir(tmp_path)
    created: list[Any] = []

    class RuntimeRecorder:
        def __init__(self, config, *, core_factory):
            self.initial_core = core_factory(config)
            self._snapshot = SimpleNamespace(
                push_store=object(), push_executor=object(), push_scheduler=object())
            created.append(self)

        def snapshot(self):
            return self._snapshot

    def fail_legacy_push(*_args, **_kwargs):
        raise AssertionError("不应创建传统 PushExecutor")

    monkeypatch.setattr("api.app.RuntimeManager", RuntimeRecorder)
    monkeypatch.setattr("push.executor.PushExecutor", fail_legacy_push)
    core = make_core()

    create_app(core=core, sessions=object())

    assert len(created) == 1
    assert created[0].initial_core is core


@pytest.fixture
def app(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    sessions = SessionManager(store, facts_path=tmp_path / "facts.json")
    return create_app(core=make_core(), sessions=sessions, push=False)


@pytest.fixture(autouse=True)
def local_checkpoint_dir(tmp_path, monkeypatch):
    """测试图检查点固定写入临时目录，避免访问用户目录。"""
    monkeypatch.setattr("api.app.DEFAULT_CHECKPOINT_DIR",
                        tmp_path / "langgraph_checkpoints.sqlite")


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


class TestRuntimeSnapshotEndpoints:
    @pytest.mark.asyncio
    async def test_analyze_uses_one_snapshot_and_next_request_uses_reloaded_core(
            self, tmp_path, monkeypatch):
        """运行中的请求固定旧 core，下一请求才读取热更新后的新 core。"""
        from utils.config import Config

        class LabeledPipeline:
            def __init__(self, label):
                self.label = label

            def run(self, symbol, name, market="a-shares"):
                results, _, context = FakePipeline().run(symbol, name, market)
                return results, {"bulk": self.label}, context

        def labeled_core(label):
            from typing import Any, cast

            template = make_core()
            return AgentCore(
                registry=template.registry,
                pipeline=cast(Any, LabeledPipeline(label)),
                index_pipeline=template.index_pipeline,
                llm=template.llm,
            )

        class SnapshotRuntime(RuntimeManager):
            def __init__(self, current):
                self.current = current
                self.calls = 0

            def snapshot(self):
                self.calls += 1
                return self.current

        monkeypatch.chdir(tmp_path)
        config = Config()
        old_core = labeled_core("旧快照")
        new_core = labeled_core("新快照")
        runtime = SnapshotRuntime(SimpleNamespace(core=old_core, config=config))
        sessions = SessionManager(SessionStore(tmp_path / "snapshot_sessions.db"))
        app_snapshot = create_app(
            core=old_core, sessions=sessions, push=False, runtime=runtime)

        monkeypatch.setattr("utils.symbols.resolve_name", lambda _symbol: "平安银行")
        async with AsyncClient(transport=ASGITransport(app=app_snapshot),
                               base_url="http://test") as client:
            first = await client.post("/api/v1/analyze", json={"symbol": "000001"})
            runtime.current = SimpleNamespace(core=new_core, config=config)
            second = await client.post("/api/v1/analyze", json={"symbol": "000001"})

        assert first.status_code == 200
        assert first.json()["commentary"] == "旧快照"
        assert second.status_code == 200
        assert second.json()["commentary"] == "新快照"
        assert runtime.calls == 2


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
        assert all("raw_output" not in tool for tool in tools)
        assert tools[0]["symbol"] is None
        assert "echo" in tools[0]["content"]

    @pytest.mark.asyncio
    async def test_chat_tool_results_include_symbol(self, tmp_path):
        store = SessionStore(tmp_path / "sessions_sym.db")
        sessions = SessionManager(store, facts_path=tmp_path / "facts_sym.json")
        app_sym = create_app(core=make_symbol_core(), sessions=sessions, push=False)
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
    async def test_chat_replaces_draft_session_id_with_persisted_id(self, tmp_path):
        store = SessionStore(tmp_path / "draft_rest.db")
        sessions = SessionManager(store, facts_path=tmp_path / "facts.json")
        app_draft = create_app(core=make_core(), sessions=sessions, push=False)
        async with AsyncClient(transport=ASGITransport(app=app_draft),
                               base_url="http://test") as client:
            response = await client.post(
                "/api/v1/chat",
                json={"message": "echo 测试", "session_id": "draft-browser-1"},
            )

        assert response.status_code == 200
        sid = response.json()["session_id"]
        assert uuid.UUID(hex=sid).version == 4
        assert all(not item["session_id"].startswith("draft-")
                   for item in store.list_sessions())

    @pytest.mark.asyncio
    async def test_chat_returns_500_on_agent_failure(self, tmp_path):
        store = SessionStore(tmp_path / "sessions_err.db")
        sessions = SessionManager(store, facts_path=tmp_path / "facts_err.json")
        app_err = create_app(core=make_broken_core(), sessions=sessions, push=False)
        transport = ASGITransport(app=app_err)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/v1/chat",
                                json={"message": "分析平安银行的财务数据"})
        assert resp.status_code == 500
        assert "处理请求时出错" in resp.json()["response"]

    @pytest.mark.asyncio
    async def test_chat_chat_mode_returns_direct_reply(self, tmp_path):
        sessions = SessionManager(SessionStore(tmp_path / "s.db"))
        app_chat = create_app(core=make_chat_core(), sessions=sessions, push=False)
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

    @pytest.mark.asyncio
    async def test_chat_agent_mode_returns_final_reply(self, tmp_path):
        sessions = SessionManager(SessionStore(tmp_path / "s_agent.db"))
        app_agent = create_app(core=make_agent_core(), sessions=sessions, push=False)
        async with AsyncClient(transport=ASGITransport(app=app_agent),
                               base_url="http://test") as c:
            resp = await c.post("/api/v1/chat",
                                json={"message": "对比茅台和宁德时代"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["plan"]["mode"] == "agent"
        assert body["response"] == "对比结论"
        tools = body["tool_results"]
        assert len(tools) == 1
        assert tools[0]["tool"] == "echo"
        assert tools[0]["status"] == "done"
        assert all("raw_output" not in tool for tool in tools)
        # 最终回答写入会话消息
        msgs = sessions.get_messages(body["session_id"])
        assert msgs is not None
        assert any(m["role"] == "assistant"
                   and m["content"] == "对比结论" for m in msgs)


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

    def test_pairs_tool_message_ids_in_order(self):
        memory, plan = self._make_plan(2)
        memory.messages.extend([
            {"role": "tool", "content": "同代码成功", "message_id": 101},
            {"role": "tool", "content": "同代码失败", "message_id": 102},
        ])

        results = _structured_tool_results(plan, memory)

        assert [result["message_id"] for result in results] == [101, 102]

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


class TestStockReportExtraction:
    def test_extracts_successful_executor_repr(self):
        """Executor 的 Python repr 应安全解析并规范化为股票报告。"""
        tool_results = [{
            "tool": "analyze_stock",
            "status": "done",
            "content": (
                "[analyze_stock] success: {'code': '000001', 'name': '平安银行', "
                "'overview': {'industry': '银行'}, 'comments': ['第一段', '第二段'], "
                "'generated_at': '2026-08-24T10:00:00+08:00'}"
            ),
        }]

        extracted = _extract_stock_reports(tool_results)

        assert extracted == [("000001", {
            "symbol": "000001",
            "name": "平安银行",
            "overview": {"industry": "银行"},
            "score": {},
            "score_rows": [],
            "dimensions": {},
            "commentary": "第一段\n\n第二段",
            "generated_at": "2026-08-24T10:00:00+08:00",
        }, None)]

    def test_persists_only_successful_report_tool_message_id(self):
        """同代码成功/失败并存时，成果只能关联成功的具体工具消息。"""
        class ArtifactManager:
            def __init__(self):
                self.kwargs = None

            def save_artifact(self, session_id, **kwargs):
                self.kwargs = {"session_id": session_id, **kwargs}
                return {"artifact_id": "a1", **self.kwargs}

        manager = ArtifactManager()
        results = _persist_artifacts_if_present(manager, "s1", [
            {
                "tool": "analyze_stock", "status": "done", "message_id": 101,
                "content": "[analyze_stock] success: {'symbol': '000001'}",
            },
            {
                "tool": "analyze_stock", "status": "error", "message_id": 102,
                "content": "[analyze_stock] error: 同代码失败",
            },
        ], memory=object())

        assert len(results) == 1
        assert results[0]["artifact"]["message_id"] == 101
        assert manager.kwargs is not None
        assert manager.kwargs["message_id"] == 101

    def test_persists_all_successful_reports_with_distinct_message_ids(self):
        """一次多个成功报告（含同代码重复）必须逐项持久化并保留关联 ID。"""
        class ArtifactManager:
            def __init__(self):
                self.saved = []

            def save_artifact(self, session_id, **kwargs):
                artifact = {
                    "artifact_id": f"a{len(self.saved) + 1}",
                    "session_id": session_id,
                    **kwargs,
                }
                self.saved.append(artifact)
                return artifact

        manager = ArtifactManager()
        events = _persist_artifacts_if_present(manager, "s1", [
            {
                "tool": "analyze_stock", "status": "done", "message_id": 201,
                "content": "[analyze_stock] success: {'symbol': '000001'}",
            },
            {
                "tool": "analyze_stock", "status": "done", "message_id": 202,
                "content": "[analyze_stock] success: {'symbol': '000001'}",
            },
            {
                "tool": "analyze_stock", "status": "done", "message_id": 203,
                "content": "[analyze_stock] success: {'symbol': '600036'}",
            },
        ], memory=object())

        assert [event["artifact"]["message_id"] for event in events] == [201, 202, 203]
        assert [artifact["message_id"] for artifact in manager.saved] == [201, 202, 203]

    def test_ignores_unstructured_react_summary(self):
        """ReAct 自然语言摘要不能被误当作报告成果。"""
        assert _extract_stock_reports([{
            "tool": "analyze_stock",
            "status": "done",
            "content": "平安银行综合评分较高，建议关注。",
        }]) == []


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
    async def test_stream_replaces_draft_session_id_in_first_session_event(self, client):
        async with client.stream("POST", "/api/v1/chat/stream", json={
            "message": "echo 测试", "session_id": "draft-browser-1",
        }) as response:
            events = parse_sse_events((await response.aread()).decode())

        session_event = next(
            event for event in events if event["type"] in ("session_title", "plan")
        )
        assert session_event["session_id"] != "draft-browser-1"

    @pytest.mark.asyncio
    async def test_stream_emits_local_session_title_after_start(self, client):
        async with client.stream("POST", "/api/v1/chat/stream",
                                 json={"message": "echo 测试"}) as resp:
            text = (await resp.aread()).decode()

        events = parse_sse_events(text)
        local_title = next(event for event in events
                           if event["type"] == "session_title")
        assert local_title["session_id"]
        assert local_title["title"] == "echo 测试"
        assert [event["type"] for event in events].index("session_title") \
            < [event["type"] for event in events].index("plan")

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
    async def test_stream_persists_stock_report_before_done(self, tmp_path):
        sessions = SessionManager(SessionStore(tmp_path / "stock_report.db"))
        app_stock = create_app(core=make_symbol_core(), sessions=sessions, push=False)
        async with (
            AsyncClient(transport=ASGITransport(app=app_stock),
                        base_url="http://test") as c,
            c.stream("POST", "/api/v1/chat/stream", json={
                "message": "分析 000001 的估值",
            }) as resp,
        ):
            events = parse_sse_events((await resp.aread()).decode())
            plan_event = next(event for event in events
                              if event["type"] == "plan")
            history = await c.get(
                f"/api/v1/sessions/{plan_event['session_id']}/messages")

        artifact_event = next(event for event in events
                              if event["type"] == "artifact")
        assert artifact_event["persisted"] is True
        assert artifact_event["artifact"]["payload"]["symbol"] == "000001"
        assert artifact_event["artifact"]["payload"]["commentary"] == \
            "基本面稳健\n\n关注净息差变化"
        tool_messages = [message for message in history.json()["messages"]
                         if message["role"] == "tool"]
        assert artifact_event["artifact"]["message_id"] == \
            tool_messages[-1]["message_id"]
        assert [event["type"] for event in events].index("artifact") \
            < [event["type"] for event in events].index("done")
        assert history.status_code == 200
        assert history.json()["artifacts"] == [artifact_event["artifact"]]

    @pytest.mark.asyncio
    async def test_stream_plan_emits_every_report_artifact(self, tmp_path):
        """plan 的多个报告步骤应逐个保存并逐个发送 artifact 事件。"""
        sessions = SessionManager(SessionStore(tmp_path / "multi_report.db"))
        app_stock = create_app(
            core=make_multi_symbol_core(), sessions=sessions, push=False)
        async with (
            AsyncClient(transport=ASGITransport(app=app_stock),
                        base_url="http://test") as c,
            c.stream("POST", "/api/v1/chat/stream", json={
                "message": "连续分析 000001 和 600036",
            }) as resp,
        ):
            events = parse_sse_events((await resp.aread()).decode())

        artifacts = [event["artifact"] for event in events
                     if event["type"] == "artifact"]
        assert [artifact["symbol"] for artifact in artifacts] == ["000001", "600036"]
        assert len({artifact["message_id"] for artifact in artifacts}) == 2

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_stream_emits_unpersisted_artifact_when_store_fails(
            self, tmp_path, monkeypatch):
        sessions = SessionManager(SessionStore(tmp_path / "broken_artifact.db"))

        def fail_save(*args, **kwargs):
            raise OSError("磁盘不可写")

        monkeypatch.setattr(sessions, "save_artifact", fail_save)
        app_stock = create_app(core=make_symbol_core(), sessions=sessions, push=False)
        async with (
            AsyncClient(transport=ASGITransport(app=app_stock),
                        base_url="http://test") as c,
            c.stream("POST", "/api/v1/chat/stream", json={
                "message": "分析 000001 的估值",
            }) as resp,
        ):
            events = parse_sse_events((await resp.aread()).decode())

        artifact_event = next(event for event in events
                              if event["type"] == "artifact")
        assert artifact_event["persisted"] is False
        assert artifact_event["artifact"]["payload"]["symbol"] == "000001"
        assert events[-1]["type"] == "done"

    @pytest.mark.asyncio
    async def test_stream_emits_refined_title_after_text(self, tmp_path):
        sessions = SessionManager(SessionStore(tmp_path / "refined_title.db"))
        app_chat = create_app(
            core=make_titled_chat_core(TitleAwareModel(title="宁德时代成长展望")),
            sessions=sessions,
            push=False,
        )
        async with (
            AsyncClient(transport=ASGITransport(app=app_chat),
                        base_url="http://test") as c,
            c.stream("POST", "/api/v1/chat/stream",
                     json={"message": "你好"}) as resp,
        ):
            events = parse_sse_events((await resp.aread()).decode())

        event_types = [event["type"] for event in events]
        title_events = [event for event in events
                        if event["type"] == "session_title"]
        assert [event["title"] for event in title_events] == [
            "你好", "宁德时代成长展望"]
        assert event_types.index("text") < len(events) - 2
        assert events[-2] == title_events[-1]
        assert events[-1]["type"] == "done"

    @pytest.mark.asyncio
    async def test_stream_does_not_overwrite_manual_title(self, tmp_path):
        sessions = SessionManager(SessionStore(tmp_path / "manual_title.db"))
        app_chat = create_app(
            core=make_titled_chat_core(TitleAwareModel(title="模型试图覆盖")),
            sessions=sessions,
            push=False,
        )
        async with AsyncClient(transport=ASGITransport(app=app_chat),
                               base_url="http://test") as c:
            sid, _ = sessions.get_or_create(None, "")
            assert sessions.rename(sid, "用户手动标题")
            async with c.stream("POST", "/api/v1/chat/stream", json={
                "message": "你好", "session_id": sid,
            }) as resp:
                events = parse_sse_events((await resp.aread()).decode())
            listed = await c.get("/api/v1/sessions")

        session = next(item for item in listed.json()["sessions"]
                       if item["session_id"] == sid)
        assert session["title"] == "用户手动标题"
        assert session["title_source"] == "manual"
        assert [event["title"] for event in events
                if event["type"] == "session_title"] == ["用户手动标题"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("model", [
        TitleAwareModel(title_error=True),
        TitleAwareModel(title_delay=1.0),
    ], ids=["exception", "timeout"])
    async def test_stream_title_failure_still_finishes(self, tmp_path, model):
        sessions = SessionManager(SessionStore(tmp_path / f"title_{id(model)}.db"))
        app_chat = create_app(
            core=make_titled_chat_core(model), sessions=sessions, push=False)
        async with (
            AsyncClient(transport=ASGITransport(app=app_chat),
                        base_url="http://test") as c,
            c.stream("POST", "/api/v1/chat/stream",
                     json={"message": "你好"}) as resp,
        ):
            events = parse_sse_events((await resp.aread()).decode())

        assert any(event["type"] == "text" for event in events)
        assert events[-1]["type"] == "done"

    @pytest.mark.asyncio
    async def test_stream_title_metadata_read_failure_still_returns_result(
            self, tmp_path, monkeypatch):
        sessions = SessionManager(SessionStore(tmp_path / "title_read_failure.db"))
        sid, memory = sessions.get_or_create(None, "已有会话")
        memory.add_message("user", "已有消息")
        list_sessions = sessions.list_sessions
        read_count = 0

        def fail_second_read():
            nonlocal read_count
            read_count += 1
            if read_count == 2:
                raise OSError("标题元数据不可读")
            return list_sessions()

        monkeypatch.setattr(sessions, "list_sessions", fail_second_read)
        app_plan = create_app(core=make_core(), sessions=sessions, push=False)
        async with (
            AsyncClient(transport=ASGITransport(app=app_plan),
                        base_url="http://test") as c,
            c.stream("POST", "/api/v1/chat/stream", json={
                "message": "echo 测试", "session_id": sid,
            }) as resp,
        ):
            events = parse_sse_events((await resp.aread()).decode())

        title_events = [event for event in events
                        if event["type"] == "session_title"]
        assert len(title_events) == 1
        assert title_events[0]["session_id"]
        assert title_events[0]["title"] == "echo 测试"
        assert any(event["type"] == "result" for event in events)
        assert events[-1]["type"] == "done"
        assert not any(event["type"] == "error" for event in events)

    @pytest.mark.asyncio
    async def test_stream_local_title_write_failure_still_returns_result(
            self, tmp_path, monkeypatch):
        sessions = SessionManager(SessionStore(tmp_path / "title_write_failure.db"))
        sid, _ = sessions.get_or_create(None)

        def fail_title_update(*args, **kwargs):
            raise OSError("标题元数据不可写")

        monkeypatch.setattr(sessions, "maybe_update_title", fail_title_update)
        app_chat = create_app(
            core=make_titled_chat_core(TitleAwareModel(title="不应出现的润色标题")),
            sessions=sessions,
            push=False,
        )
        async with (
            AsyncClient(transport=ASGITransport(app=app_chat),
                        base_url="http://test") as c,
            c.stream("POST", "/api/v1/chat/stream", json={
                "message": "你好", "session_id": sid,
            }) as resp,
        ):
            events = parse_sse_events((await resp.aread()).decode())

        title_events = [event for event in events
                        if event["type"] == "session_title"]
        assert title_events == [{
            "type": "session_title", "session_id": sid, "title": "你好"}]
        assert any(event["type"] == "text" for event in events)
        assert events[-1]["type"] == "done"
        assert not any(event["type"] == "error" for event in events)

    @pytest.mark.asyncio
    async def test_stream_emits_error_event_on_failure(self, tmp_path):
        store = SessionStore(tmp_path / "sessions_err2.db")
        sessions = SessionManager(store, facts_path=tmp_path / "facts_err2.json")
        app_err = create_app(core=make_broken_core(), sessions=sessions, push=False)
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
        app_chat = create_app(core=make_chat_core(), sessions=sessions, push=False)
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
        assert '"type": "text_delta"' in text
        assert '"type": "text"' in text
        assert "你好呀" in text

    @pytest.mark.asyncio
    async def test_stream_chat_mode_emits_deepseek_reasoning(self, tmp_path):
        """OpenAI 兼容的 reasoning_content 必须透传为 SSE thinking 事件。"""
        model = FakeChatModel(responses=[SimpleNamespace(
            content="这是正文。",
            additional_kwargs={"reasoning_content": "正在核对数据。"},
        )])
        sessions = SessionManager(SessionStore(tmp_path / "s_reasoning.db"))
        app_chat = create_app(core=make_chat_core(model), sessions=sessions, push=False)
        transport = ASGITransport(app=app_chat)
        async with (
            AsyncClient(transport=transport, base_url="http://test") as client,
            client.stream("POST", "/api/v1/chat/stream", json={"message": "你好"}) as resp,
        ):
            events = parse_sse_events((await resp.aread()).decode())

        assert any(
            event == {"type": "thinking", "content": "正在核对数据。"}
            for event in events
        )
        assert any(
            event.get("type") == "text"
            and event.get("thinking") == "正在核对数据。"
            and event.get("content") == "这是正文。"
            for event in events
        )

    @pytest.mark.asyncio
    async def test_stream_chat_mode_joins_reasoning_deltas_without_blank_lines(
            self, tmp_path):
        class ReasoningDeltaModel:
            async def astream(self, _messages):
                yield SimpleNamespace(
                    content="", additional_kwargs={"reasoning_content": "正"})
                yield SimpleNamespace(
                    content="", additional_kwargs={"reasoning_content": "在"})
                yield SimpleNamespace(content="正文", additional_kwargs={})

            async def ainvoke(self, _messages):
                return SimpleNamespace(content="标题")

        sessions = SessionManager(SessionStore(tmp_path / "reasoning_delta.db"))
        app_chat = create_app(
            core=make_chat_core(ReasoningDeltaModel()), sessions=sessions, push=False)
        transport = ASGITransport(app=app_chat)
        async with (
            AsyncClient(transport=transport, base_url="http://test") as client,
            client.stream("POST", "/api/v1/chat/stream", json={"message": "你好"}) as resp,
        ):
            events = parse_sse_events((await resp.aread()).decode())

        final_text = next(event for event in events if event["type"] == "text")
        assert final_text["thinking"] == "正在"

    @pytest.mark.asyncio
    async def test_stream_agent_mode_emits_tool_events(self, tmp_path):
        sessions = SessionManager(SessionStore(tmp_path / "s_agent2.db"))
        app_agent = create_app(core=make_agent_core(), sessions=sessions, push=False)
        transport = ASGITransport(app=app_agent)
        async with (
            AsyncClient(transport=transport, base_url="http://test") as c,
            c.stream("POST", "/api/v1/chat/stream",
                     json={"message": "对比茅台和宁德时代"}) as resp,
        ):
            body = b""
            async for chunk in resp.aiter_bytes():
                body += chunk

        text = body.decode()
        assert '"type": "tool_call"' in text
        assert '"type": "tool_result"' in text
        assert '"type": "text"' in text
        assert "对比结论" in text
        assert '"type": "done"' in text

    @pytest.mark.asyncio
    async def test_stream_agent_mode_emits_every_report_artifact(self, tmp_path):
        """ReAct 的多个成功报告工具结果应各自产生成果事件。"""
        sessions = SessionManager(SessionStore(tmp_path / "s_agent_multi.db"))
        app_agent = create_app(
            core=make_multi_report_agent_core(), sessions=sessions, push=False)
        async with (
            AsyncClient(transport=ASGITransport(app=app_agent),
                        base_url="http://test") as c,
            c.stream("POST", "/api/v1/chat/stream",
                     json={"message": "重复分析平安银行"}) as resp,
        ):
            events = parse_sse_events((await resp.aread()).decode())

        artifacts = [event["artifact"] for event in events
                     if event["type"] == "artifact"]
        visible_tool_results = [event for event in events
                                if event["type"] == "tool_result"]
        assert all("raw_output" not in event for event in visible_tool_results)
        assert all(len(event.get("content", "")) <= 500
                   for event in visible_tool_results)
        assert len(artifacts) == 2, events
        assert {artifact["symbol"] for artifact in artifacts} == {"000001"}
        assert len({artifact["message_id"] for artifact in artifacts}) == 2

    @pytest.mark.asyncio
    async def test_stream_agent_mode_without_model_falls_back(self, tmp_path):
        """agent 模式但 model 为 None：降级 plan 单步并正常完成"""

        sessions = SessionManager(SessionStore(tmp_path / "s_agent3.db"))
        core = make_agent_core()
        core.model = None
        app_fallback = create_app(core=core, sessions=sessions, push=False)
        transport = ASGITransport(app=app_fallback)
        async with (
            AsyncClient(transport=transport, base_url="http://test") as c,
            c.stream("POST", "/api/v1/chat/stream",
                     json={"message": "对比茅台和宁德时代"}) as resp,
        ):
            body = b""
            async for chunk in resp.aiter_bytes():
                body += chunk

        text = body.decode()
        assert '"type": "result"' in text
        assert '"type": "done"' in text
        assert '"type": "error"' not in text
        # 降级后无工具匹配（"对比茅台和宁德时代" 与 echo 描述无关键词交集）→ 0/1 步完成
        assert "完成: 0/1 步骤" in text
        session_id = next(event["session_id"] for event in parse_sse_events(text)
                           if event.get("type") == "plan")
        messages = sessions.get_messages(session_id)
        assistant = [item for item in messages or []
                     if item["role"] == "assistant"]
        assert assistant
        assert assistant[-1]["content"]
        assert "思考中..." in assistant[-1]["content"]

    @pytest.mark.asyncio
    async def test_stream_agent_fallback_emits_every_report_artifact(
            self, tmp_path, monkeypatch):
        """agent 降级返回多个报告结果时也必须逐项发送成果。"""
        async def fake_fallback(executor, memory, goal):
            return "降级完成", {"goal": goal, "mode": "plan", "steps": []}, [
                {
                    "tool": "analyze_stock", "status": "done", "message_id": 701,
                    "content": "[analyze_stock] success: {'symbol': '000001'}",
                },
                {
                    "tool": "analyze_stock", "status": "done", "message_id": 702,
                    "content": "[analyze_stock] success: {'symbol': '600036'}",
                },
            ]

        monkeypatch.setattr("api.app._agent_fallback", fake_fallback)
        sessions = SessionManager(SessionStore(tmp_path / "s_agent_fallback_multi.db"))
        core = make_agent_core()
        core.model = None
        app_fallback = create_app(core=core, sessions=sessions, push=False)
        async with (
            AsyncClient(transport=ASGITransport(app=app_fallback),
                        base_url="http://test") as c,
            c.stream("POST", "/api/v1/chat/stream",
                     json={"message": "对比两只股票"}) as resp,
        ):
            events = parse_sse_events((await resp.aread()).decode())

        artifacts = [event["artifact"] for event in events
                     if event["type"] == "artifact"]
        assert [artifact["message_id"] for artifact in artifacts] == [701, 702]


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
    async def test_analyze_adds_commentary_fallback_when_llm_empty(self, tmp_path, mocker):
        """API 分析路径也应在 LLM 空输出时返回量化兜底摘要"""
        mocker.patch("utils.symbols.resolve_name", return_value="平安银行")

        class EmptyCommentaryPipeline:
            def run(self, symbol, name, market="a-shares"):
                from data.schemas import AnalysisContext, AnalysisResult
                results = [AnalysisResult(
                    dimension="financial", status="ok", summary="财务健康",
                    score=8.0, score_detail="财务稳健",
                )]
                return results, {"bulk": ""}, AnalysisContext(
                    symbol=symbol, name=name, market=market,
                )

        from typing import cast
        core = AgentCore(
            registry=make_core().registry,
            pipeline=cast(Any, EmptyCommentaryPipeline()),
            index_pipeline=cast(Any, FakeIndexPipeline()),
            llm=cast(Any, FakeLLM()),
        )
        sessions = SessionManager(SessionStore(tmp_path / "sessions_analyze_fb.db"))
        app_fb = create_app(core=core, sessions=sessions, push=False)
        async with AsyncClient(
            transport=ASGITransport(app=app_fb), base_url="http://test",
        ) as c:
            resp = await c.post("/api/v1/analyze", json={"symbol": "000001"})

        assert resp.status_code == 200
        commentary = resp.json()["commentary"]
        assert "AI 解读当前不可用" in commentary
        assert "最终综合得分为 8.0/10" in commentary

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
        app_err = create_app(core=core, sessions=sessions, push=False)
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
    async def test_empty_session_creation_and_clear_operations_are_not_registered(self, client):
        create_resp = await client.post("/api/v1/sessions")
        assert create_resp.status_code == 405

        clear_resp = await client.post("/api/v1/sessions/draft-browser-1/clear")
        assert clear_resp.status_code == 405

        schema = await client.get("/openapi.json")
        paths = schema.json()["paths"]
        assert "post" not in paths["/api/v1/sessions"]
        assert "/api/v1/sessions/{session_id}/clear" not in paths

    @pytest.mark.asyncio
    async def test_real_message_session_can_be_read_renamed_and_deleted(self, client):
        created = await client.post("/api/v1/chat", json={"message": "echo 测试"})
        assert created.status_code == 200
        sid = created.json()["session_id"]

        history = await client.get(f"/api/v1/sessions/{sid}/messages")
        assert history.status_code == 200
        assert history.json()["messages"]

        renamed = await client.patch(
            f"/api/v1/sessions/{sid}", json={"title": "我的会话"})
        assert renamed.status_code == 200

        del_resp = await client.delete(f"/api/v1/sessions/{sid}")
        assert del_resp.status_code == 200

        del_again = await client.delete(f"/api/v1/sessions/{sid}")
        assert del_again.status_code == 404

    @pytest.mark.asyncio
    async def test_draft_session_detail_rename_and_delete_return_404(self, client):
        detail = await client.get("/api/v1/sessions/draft-browser-1/messages")
        renamed = await client.patch(
            "/api/v1/sessions/draft-browser-1", json={"title": "无效会话"})
        deleted = await client.delete("/api/v1/sessions/draft-browser-1")

        assert detail.status_code == 404
        assert renamed.status_code == 404
        assert deleted.status_code == 404

    @pytest.mark.asyncio
    async def test_draft_history_is_not_readable_renamable_or_deletable(self, tmp_path):
        store = SessionStore(tmp_path / "draft_history.db")
        store.create_session("draft-legacy", "历史草稿")
        store.append_message("draft-legacy", "user", "旧消息")
        sessions = SessionManager(store, facts_path=tmp_path / "facts.json")
        app_history = create_app(core=make_core(), sessions=sessions, push=False)

        async with AsyncClient(transport=ASGITransport(app=app_history),
                               base_url="http://test") as client:
            detail = await client.get("/api/v1/sessions/draft-legacy/messages")
            renamed = await client.patch(
                "/api/v1/sessions/draft-legacy", json={"title": "不可编辑"})
            deleted = await client.delete("/api/v1/sessions/draft-legacy")

        assert detail.status_code == 404
        assert renamed.status_code == 404
        assert deleted.status_code == 404
        assert store.session_exists("draft-legacy") is True

    @pytest.mark.asyncio
    async def test_patch_session_title_trims_and_marks_manual(self, client):
        created = await client.post("/api/v1/chat", json={"message": "echo 测试"})
        sid = created.json()["session_id"]

        resp = await client.patch(
            f"/api/v1/sessions/{sid}", json={"title": "  我的观察列表  "})

        assert resp.status_code == 200
        assert resp.json() == {
            "session_id": sid,
            "title": "我的观察列表",
            "title_source": "manual",
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("title", ["   ", "超" * 21], ids=["empty", "overlong"])
    async def test_patch_session_title_rejects_invalid_length(self, client, title):
        created = await client.post("/api/v1/chat", json={"message": "echo 测试"})
        sid = created.json()["session_id"]

        resp = await client.patch(
            f"/api/v1/sessions/{sid}", json={"title": title})

        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_patch_unknown_session_returns_404(self, client):
        resp = await client.patch(
            "/api/v1/sessions/nope", json={"title": "有效标题"})
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
        assert resp.json()["artifacts"] == []

    @pytest.mark.asyncio
    async def test_get_messages_decodes_assistant_thinking_blocks(self, tmp_path):
        sessions = SessionManager(SessionStore(tmp_path / "thinking_history.db"))
        sid, memory = sessions.get_or_create(None, "hello")
        message_id = memory.add_message(
            "assistant",
            "[{\"type\":\"thinking\",\"thinking\":\"先判断意图\","
            "\"thinking_duration_seconds\":1.4},"
            "{\"type\":\"text\",\"text\":\"你好，有什么可以帮你？\"}]",
        )
        app_history = create_app(core=make_core(), sessions=sessions, push=False)

        async with AsyncClient(transport=ASGITransport(app=app_history),
                               base_url="http://test") as c:
            resp = await c.get(f"/api/v1/sessions/{sid}/messages")

        assert resp.status_code == 200
        assistant = next(
            message for message in resp.json()["messages"]
            if message["message_id"] == message_id
        )
        assert assistant["content"] == "你好，有什么可以帮你？"
        assert assistant["thinking"] == "先判断意图"
        assert assistant["thinking_duration_seconds"] == 1.4
        assert "text" not in assistant

    @pytest.mark.asyncio
    async def test_get_messages_returns_artifacts(self, tmp_path):
        sessions = SessionManager(SessionStore(tmp_path / "history_artifacts.db"))
        sid, _ = sessions.get_or_create(None, "分析 000001")
        artifact = sessions.save_artifact(
            sid, kind="stock_report", symbol="000001",
            payload={"symbol": "000001"},
        )
        app_history = create_app(core=make_core(), sessions=sessions, push=False)

        async with AsyncClient(transport=ASGITransport(app=app_history),
                               base_url="http://test") as c:
            resp = await c.get(f"/api/v1/sessions/{sid}/messages")

        assert resp.status_code == 200
        assert resp.json() == {"messages": [], "artifacts": [artifact]}

    @pytest.mark.asyncio
    async def test_get_artifact_validates_session_ownership(self, tmp_path):
        sessions = SessionManager(SessionStore(tmp_path / "artifact_routes.db"))
        sid, _ = sessions.get_or_create(None, "分析 000001")
        other_sid, _ = sessions.get_or_create(None, "分析 600519")
        artifact = sessions.save_artifact(
            sid, kind="stock_report", symbol="000001",
            payload={"symbol": "000001"},
        )
        app_routes = create_app(core=make_core(), sessions=sessions, push=False)
        async with AsyncClient(transport=ASGITransport(app=app_routes),
                               base_url="http://test") as c:
            success = await c.get(
                f"/api/v1/sessions/{sid}/artifacts/{artifact['artifact_id']}")
            crossed = await c.get(
                f"/api/v1/sessions/{other_sid}/artifacts/{artifact['artifact_id']}")
            missing_session = await c.get(
                f"/api/v1/sessions/nope/artifacts/{artifact['artifact_id']}")

        assert success.status_code == 200
        assert success.json() == {"artifact": artifact}
        assert crossed.status_code == 404
        assert missing_session.status_code == 404

    @pytest.mark.asyncio
    async def test_get_messages_unknown_session_returns_404(self, client):
        resp = await client.get("/api/v1/sessions/nope/messages")
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

    @pytest.mark.asyncio
    async def test_messages_returns_503(self, empty_client):
        resp = await empty_client.get("/api/v1/sessions/any/messages")
        assert resp.status_code == 503


class TestIndustryMappingEndpoint:
    def test_update_symbol(self, mocker, tmp_path):
        from fastapi.testclient import TestClient

        from api.app import create_app

        mocker.patch("data.industry_mapping_builder.update_symbol",
                     return_value={"symbol": "600097", "sw_level1": "农林牧渔",
                                   "sw_level2": "渔业", "style_category": "必选消费",
                                   "action": "updated"})
        sessions = SessionManager(SessionStore(tmp_path / "mapping_sessions.db"))
        app = create_app(core=make_core(), sessions=sessions, push=False)
        client = TestClient(app)
        resp = client.post("/api/v1/industry-mapping/update",
                           json={"symbol": "600097"})
        assert resp.status_code == 200
        assert resp.json()["sw_level1"] == "农林牧渔"

    def test_update_missing_symbol(self, mocker, tmp_path):
        from fastapi.testclient import TestClient

        from api.app import create_app

        mocker.patch("data.industry_mapping_builder.update_symbol",
                     side_effect=IndustryMappingError("个股页无行业区块，可能未分类"))
        sessions = SessionManager(SessionStore(tmp_path / "missing_sessions.db"))
        app = create_app(core=make_core(), sessions=sessions, push=False)
        client = TestClient(app)
        resp = client.post("/api/v1/industry-mapping/update",
                           json={"symbol": "600097"})
        assert resp.status_code == 404
        assert "未分类" in resp.json()["detail"]

    def test_update_invalid_symbol(self, mocker, tmp_path):
        from fastapi.testclient import TestClient

        from api.app import create_app

        sessions = SessionManager(SessionStore(tmp_path / "invalid_sessions.db"))
        app = create_app(core=make_core(), sessions=sessions, push=False)
        client = TestClient(app)
        resp = client.post("/api/v1/industry-mapping/update",
                           json={"symbol": "abc"})
        assert resp.status_code == 422

    def test_get_symbol(self, mocker, tmp_path):
        from fastapi.testclient import TestClient

        from api.app import create_app

        mocker.patch("data.industry_classifier.IndustryClassifier.lookup",
                     return_value=type("C", (), {"sw_level1": "农林牧渔",
                                                 "sw_level2": "渔业",
                                                 "style_category": "必选消费",
                                                 "mapping_status": "verified"})())
        sessions = SessionManager(SessionStore(tmp_path / "lookup_sessions.db"))
        app = create_app(core=make_core(), sessions=sessions, push=False)
        client = TestClient(app)
        resp = client.get("/api/v1/industry-mapping/600097")
        assert resp.status_code == 200
        assert resp.json()["sw_level1"] == "农林牧渔"
        assert resp.json()["mapping_status"] == "verified"
