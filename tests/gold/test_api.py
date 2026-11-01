"""黄金入口直达确定性服务，并持久化会话成果。"""
import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from agent.tools import ToolRegistry
from api.app import _persist_artifacts_if_present, create_app
from api.sessions import SessionManager, SessionStore
from gold.tool import AnalyzeGoldTool


@pytest.fixture
def connected_app(tmp_path, mocker):
    service = mocker.Mock()
    service.run.return_value = {"report_type": "gold", "symbol": "GOLD", "name": "黄金综合研报",
                                "markdown": "# 黄金综合研报\n\n长期：看多", "long_term": {"direction": "看多"},
                                "markets": {}, "status": "ok"}
    core = SimpleNamespace(registry=ToolRegistry(), llm=None, model=None)
    manager = SessionManager(SessionStore(tmp_path / "sessions.db"))
    return create_app(core=core, sessions=manager, push=False, gold_service=service), service, manager


@pytest.mark.asyncio
async def test_gold_api_and_alias_share_service(connected_app):
    app, service, _ = connected_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/gold", json={"refresh_cache": True})
        assert response.status_code == 200
        assert response.json()["long_term"]["direction"] == "看多"
        service.run.assert_called_with(refresh_cache=True)
        response = await client.post("/api/v1/analyze", json={"symbol": "黄金"})
        assert response.status_code == 200
        assert response.json()["symbol"] == "GOLD"
        response = await client.post("/api/v1/analyze", json={"symbol": "山东黄金"})
        assert response.status_code == 422
        assert service.run.call_count == 2
        service.run.side_effect = RuntimeError("内部错误")
        failed = await client.post("/api/v1/analyze", json={"symbol": "黄金"})
        assert failed.status_code == 500
        assert "请重试" in failed.json()["detail"]


@pytest.mark.asyncio
async def test_stream_persists_report_and_recoverable_messages(connected_app):
    app, service, manager = connected_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/chat/stream", json={"message": "请分析一下黄金"})
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert events[0]["type"] == "start"
        plan = next(event for event in events if event["type"] == "plan")
        assert plan["mode"] == "gold"
        sid = plan["session_id"]
        assert any(event["type"] == "text" and "看多" in event["content"] for event in events)
        assert next(event for event in events if event["type"] == "artifact")["artifact"]["kind"] == "gold_report"
        assert events[-1]["type"] == "done"
        detail = manager.get_session_detail(sid)
        assert detail is not None
        assert detail["artifacts"][0]["payload"]["long_term"]["direction"] == "看多"
        assert len(detail["messages"]) == 2
        assert detail["artifacts"][0]["message_id"] == detail["messages"][-1]["message_id"]
        assert service.run.call_count == 1


@pytest.mark.asyncio
async def test_gold_chat_rest_and_stream_failure(connected_app):
    app, service, manager = connected_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.post("/api/v1/chat", json={"message": "GOLD"})
        assert result.status_code == 200
        assert "看多" in result.json()["response"]
        assert manager.get_session_detail(result.json()["session_id"])["artifacts"][0]["kind"] == "gold_report"
        service.run.side_effect = RuntimeError("测试采集失败")
        response = await client.post("/api/v1/chat/stream", json={"message": "黄金"})
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
        assert any(event["type"] == "error" and "测试采集失败" in event["message"] for event in events)
        assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_agent_tool_and_legacy_plan_output_remain_archivable(connected_app):
    _, service, manager = connected_app
    tool = AnalyzeGoldTool(service)
    result = await tool.execute()
    assert result.status == "success"
    assert result.data["symbol"] == "GOLD"
    sid, memory = manager.get_or_create_for_message(None, "黄金分析")
    tool_message = "[analyze_gold] success: " + repr(result.data)
    message_id = memory.add_message("tool", tool_message)
    artifacts = _persist_artifacts_if_present(manager, sid, [{"tool": "analyze_gold", "status": "done",
        "content": tool_message, "message_id": message_id}], memory=memory)
    assert artifacts[0]["persisted"] is True
    assert artifacts[0]["artifact"]["kind"] == "gold_report"
    service.run.side_effect = RuntimeError("源故障")
    failed = await tool.execute()
    assert failed.status == "error"
    assert failed.error == "源故障"
