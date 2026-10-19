"""港股仅重试解读沿用真实分析事实，不重新采集。"""
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from api.app import create_app
from data.schemas import (
    AnalysisContext,
    AnalysisResult,
    DimensionSufficiency,
    SufficiencyLevel,
)
from report.commentary import CommentaryContext, commentary_store


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["ok", "empty", "timeout"])
async def test_hk_retry_unscored_facts_uses_same_context(tmp_path, monkeypatch, outcome):
    monkeypatch.chdir(tmp_path)
    ctx = AnalysisContext(symbol="00700.HK", name="腾讯控股", market="hk")
    ctx.sufficiency.financial = DimensionSufficiency(level=SufficiencyLevel.PARTIAL, sample_count=5)
    results = [AnalysisResult(dimension="financial", status="partial", summary="报告期数据", metrics={"revenue": 396431000000})]
    output = tmp_path / "reports" / "stock" / "00700.HK" / "2026-10" / "original.md"
    output.parent.mkdir(parents=True)
    output.write_text("旧报告不可覆盖", encoding="utf-8")
    record = CommentaryContext(ctx.symbol, ctx.name, results, {}, ctx, None, output_path=output)
    token = commentary_store.remember(record)
    calls = []

    def generate(*args, context=None):
        calls.append(context)
        if outcome == "timeout":
            raise TimeoutError("模型超时")
        return {"bulk": "港股营业收入观察"} if outcome == "ok" else {}

    core = SimpleNamespace(pipeline=SimpleNamespace(_generate_commentary=generate), llm=object())
    async with AsyncClient(transport=ASGITransport(app=create_app(core=core, push=False)), base_url="http://test") as client:
        response = await client.post("/api/v1/commentary/retry", json={"report_token": token})
    assert calls == [ctx]
    assert response.status_code == (200 if outcome == "ok" else 502)
    if outcome == "ok":
        assert response.json()["commentary_status"] == "ok"
        assert response.json()["score"]["final"] is None
        assert response.json()["signal"] is None
        assert "市场：港股" in output.read_text(encoding="utf-8")
    else:
        assert output.read_text(encoding="utf-8") == "旧报告不可覆盖"
