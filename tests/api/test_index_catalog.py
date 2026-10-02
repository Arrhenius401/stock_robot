"""Web策略指数解析与目录接口测试。"""
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from api.app import create_app
from api.sessions import SessionManager, SessionStore
from data.schemas import IndexAnalysisContext
from index.build_single import IndexReportBuilder
from index.pipeline import IndexPipeline, IndexPipelineResult


class CatalogPipeline(IndexPipeline):
    """真实报告模型的入口测试桩，保留请求目标与序列化字段。"""

    def run(self, targets, on_progress=None) -> IndexPipelineResult:
        builder = IndexReportBuilder()
        return IndexPipelineResult(reports=[
            builder.build(IndexAnalysisContext(target=target), []) for target in targets
        ])


@pytest.fixture
def catalog_app(tmp_path, mocker):
    pipeline = CatalogPipeline()
    spy = mocker.spy(pipeline, "run")
    sessions = SessionManager(SessionStore(tmp_path / "sessions.db"))
    core = SimpleNamespace(index_pipeline=pipeline)
    app = create_app(core=core, sessions=sessions, push=False)
    mocker.patch("index.build_single.render_index_report_markdown", return_value="报告")
    mocker.patch("report.formatter.ReportFormatter.save")
    return app, spy


@pytest.mark.asyncio
async def test_catalog_filters_name_and_includes_tracking(catalog_app):
    app, _ = catalog_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/indices", params={"q": "515300"})
    assert resp.status_code == 200
    assert resp.json()["etfs"][0]["index_symbol"] == "930740"
    assert resp.json()["indices"][0]["symbol"] == "930740"


@pytest.mark.asyncio
@pytest.mark.parametrize("query,expected", [("515300", "930740"), ("国证自由现金流", "980092"), ("h30269", "H30269")])
async def test_web_resolves_etf_name_and_alphanumeric_code(catalog_app, query, expected):
    app, spy = catalog_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/index", json={"symbols": [query]})
    assert resp.status_code == 200
    target = spy.call_args.args[0][0]
    assert target.symbol == expected
    assert target.index_style == "strategy"
    if query == "515300":
        assert target.requested_instrument["symbol"] == "515300"
        assert resp.json()["reports"][0]["requested_instrument"]["index_symbol"] == "930740"


@pytest.mark.asyncio
async def test_web_ambiguous_name_requires_selection(catalog_app):
    app, spy = catalog_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/index", json={"symbols": ["自由现金流"]})
    assert resp.status_code == 422
    assert "多个指数" in resp.json()["detail"]
    spy.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("symbols,is_etf", [(["930740", "515300"], False), (["515300", "930740"], True)])
async def test_duplicate_target_keeps_first_instrument_identity(catalog_app, symbols, is_etf):
    app, spy = catalog_app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/index", json={"symbols": symbols})
    assert resp.status_code == 200
    assert len(spy.call_args.args[0]) == 1
    report = resp.json()["reports"][0]
    assert bool(report["requested_instrument"]) is is_etf
    assert any("重复指数" in error for error in resp.json()["errors"])
