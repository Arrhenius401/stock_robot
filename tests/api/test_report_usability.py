"""新增质量信息与插件下载契约、AI重试的入口回归。"""
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from api.app import create_app
from api.sessions import SessionManager, SessionStore
from data.schemas import (
    AnalysisContext,
    AnalysisResult,
    AnalysisTarget,
    DimensionSufficiency,
    SufficiencyLevel,
)
from report.commentary import CommentaryContext, CommentaryStore, commentary_store


def test_payload_does_not_trust_score_when_no_quality():
    from report.payload import build_stock_payload
    ctx = AnalysisContext(symbol="000001", name="样本")
    results = [AnalysisResult(dimension="financial", status="ok", summary="财务", score=8)]
    payload = build_stock_payload(ctx.symbol, ctx.name, results, {"bulk": "AI"}, ctx)
    assert payload["score"]["base"] is None
    assert payload["score"]["final"] is None
    assert payload["commentary_status"] == "insufficient"


@pytest.mark.parametrize("bulk", ["", "（LLM 分析暂时不可用：超时）", "AI 解读不可用"])
def test_payload_recognizes_actual_llm_failure(bulk):
    from report.payload import build_stock_payload
    ctx = AnalysisContext(symbol="000001", name="样本")
    ctx.sufficiency.financial = DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=6)
    results = [AnalysisResult(dimension="financial", status="ok", summary="财务", score=8)]
    payload = build_stock_payload(ctx.symbol, ctx.name, results, {"bulk": bulk}, ctx)
    assert payload["commentary_status"] == "failed"


def test_conversation_adapter_uses_actual_model_availability(tmp_path, monkeypatch):
    from agent.pipeline_tools import _wrap_pipeline
    from utils.config import Config
    monkeypatch.chdir(tmp_path)
    ctx = AnalysisContext(symbol="000001", name="样本")
    ctx.sufficiency.financial = DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=6)
    results = [AnalysisResult(dimension="financial", status="ok", summary="财务", score=8)]
    pipeline = SimpleNamespace(
        run=lambda *_: (results, {}, ctx), _llm_enabled=True,
        _registry=SimpleNamespace(get_llm_backend=lambda _: None), _config=Config(config_dir=tmp_path),
    )
    report = _wrap_pipeline(pipeline).run(targets=[AnalysisTarget(
        target_type="stock", symbol="000001", name="样本", market="a-shares",
    )]).reports[0]
    assert report.payload["commentary_status"] == "config"
    assert report.payload["data_quality"]["has_scored_data"]
    assert "commentary_retry_id" not in report.payload


def test_commentary_store_expiry_and_capacity():
    store = CommentaryStore(ttl_seconds=10, capacity=1)
    ctx = AnalysisContext(symbol="000001", name="样本")
    first = CommentaryContext("000001", "样本", [], {}, ctx, None)
    token = store.remember(first)
    second = CommentaryContext("000001", "样本", [], {}, ctx, None)
    next_token = store.remember(second)
    assert store.get(token) is None
    second.created_at -= 11
    assert store.get(next_token) is None


@pytest.mark.asyncio
async def test_unavailable_report_download_keeps_sections_and_no_signal(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("utils.symbols.resolve_name", lambda _: "样本")
    ctx = AnalysisContext(symbol="000001", name="样本")
    results = [AnalysisResult(dimension="financial", status="unavailable", summary="未获取")]
    pipeline = SimpleNamespace(run=lambda *_: (results, {}, ctx))
    core = SimpleNamespace(pipeline=pipeline, llm=None)
    app = create_app(core=core, push=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/analyze", json={"symbol": "000001"})
        assert response.status_code == 200
        report = response.json()
        assert report["score"]["final"] is None
        assert report["signal"] is None
        assert report["commentary_status"] == "insufficient"
        assert len(report["data_quality"]["dimensions"]) == 5
        listing = (await client.get("/api/v1/reports", params={"type": "stock", "query": "000001"})).json()
        download = await client.get(f"/api/v1/reports/{listing['reports'][0]['id']}/download")
        assert download.status_code == 200
        assert "待验证事项" in download.text
        assert "数据覆盖与时效" in download.text
        assert "操作信号：" not in download.text
        assert "text/markdown" in download.headers["content-type"]


@pytest.mark.asyncio
async def test_retry_expired_report_does_not_run_pipeline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    core = SimpleNamespace(pipeline=object(), llm=object())
    async with AsyncClient(transport=ASGITransport(app=create_app(core=core, push=False)),
                           base_url="http://test") as client:
        response = await client.post("/api/v1/commentary/retry", json={"report_token": "expired"})
        assert response.status_code == 410


@pytest.mark.asyncio
async def test_retry_insufficient_report_never_calls_model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = AnalysisContext(symbol="000001", name="样本")
    token = commentary_store.remember(CommentaryContext("000001", "样本", [], {}, ctx, None))
    core = SimpleNamespace(pipeline=object(), llm=object())
    async with AsyncClient(transport=ASGITransport(app=create_app(core=core, push=False)),
                           base_url="http://test") as client:
        response = await client.post("/api/v1/commentary/retry", json={"report_token": token})
        assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["ok", "empty", "fallback", "exception"])
async def test_retry_only_calls_model_and_preserves_report(tmp_path, monkeypatch, case):
    success = case == "ok"
    monkeypatch.chdir(tmp_path)
    ctx = AnalysisContext(symbol="000001", name="样本")
    ctx.sufficiency.financial = DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=6)
    results = [AnalysisResult(dimension="financial", status="ok", summary="财务指标", score=8)]
    output_path = tmp_path / "original.md"
    output_path.write_text("原报告", encoding="utf-8")
    record = CommentaryContext("000001", "样本", results, {}, ctx, None, output_path=output_path)
    token = commentary_store.remember(record)
    calls = []

    def generate(*args):
        calls.append(args)
        if case == "exception":
            raise TimeoutError("模型请求超时")
        return {"bulk": "恢复后的AI解读"} if success else {"bulk": "（LLM 分析暂时不可用：请求超时）"} if case == "fallback" else {}

    # 不提供run/collect，任何重新采集都会直接失败。
    core = SimpleNamespace(pipeline=SimpleNamespace(_generate_commentary=generate), llm=object())
    sessions = SessionManager(SessionStore(tmp_path / "sessions.db"))
    sid, _ = sessions.get_or_create_for_message(None, "报告")
    artifact = sessions.save_artifact(sid, kind="stock_report", symbol="000001",
                                      payload={"commentary_retry_id": token, "commentary": "原报告"})
    async with AsyncClient(transport=ASGITransport(app=create_app(core=core, sessions=sessions, push=False)),
                           base_url="http://test") as client:
        response = await client.post("/api/v1/commentary/retry", json={
            "report_token": token, "artifact_id": artifact["artifact_id"], "session_id": sid,
        })
        assert len(calls) == 1
        persisted = sessions.get_artifact(artifact["artifact_id"])
        assert persisted is not None
        if success:
            assert response.status_code == 200
            assert response.json()["commentary_status"] == "ok"
            assert response.json()["dimensions"]["financial"]["score"] == 8
            assert persisted["payload"]["commentary"] == "恢复后的AI解读"
            assert "恢复后的AI解读" in output_path.read_text(encoding="utf-8")
        else:
            assert response.status_code == 502
            assert output_path.read_text(encoding="utf-8") == "原报告"
            assert persisted["payload"]["commentary"] == "原报告"


@pytest.mark.asyncio
async def test_retry_wrong_artifact_batch_does_not_call_model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = AnalysisContext(symbol="000001", name="样本")
    token = commentary_store.remember(CommentaryContext("000001", "样本", [], {}, ctx, None))
    core = SimpleNamespace(pipeline=object(), llm=object())
    sessions = SessionManager(SessionStore(tmp_path / "sessions.db"))
    async with AsyncClient(transport=ASGITransport(app=create_app(core=core, sessions=sessions, push=False)),
                           base_url="http://test") as client:
        response = await client.post("/api/v1/commentary/retry", json={
            "report_token": token, "artifact_id": "other", "session_id": "other",
        })
        assert response.status_code == 404


def test_conversation_extraction_preserves_quality_and_retry():
    from api.app import _extract_stock_reports
    additions = {"data_quality": {"status": "partial"}, "pending_verifications": ["核实截止日"],
                 "signal": None, "commentary_status": "failed", "commentary_retry_id": "token"}
    report = {"symbol": "000001", **additions}
    extracted = _extract_stock_reports([{"tool": "analyze_stock", "status": "done", "content": report}])
    assert len(extracted) == 1
    assert {key: extracted[0][1][key] for key in additions} == additions
