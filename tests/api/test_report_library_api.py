"""报告库 HTTP API 测试。"""
import pytest
from httpx import ASGITransport, AsyncClient

from api.app import create_app
from tests.api.test_report_library import make_reports


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    make_reports(tmp_path / "reports")
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as item:
        yield item


@pytest.mark.asyncio
async def test_list_reports_endpoint(client):
    resp = await client.get("/api/v1/reports")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 4
    assert payload["reports"][0]["type"] == "stock"
    assert any(report["type"] == "backtest" and report["has_equity_curve"]
               for report in payload["reports"])


@pytest.mark.asyncio
async def test_report_detail_and_download(client):
    listing = (await client.get("/api/v1/reports", params={"type": "backtest"})).json()
    report_id = listing["reports"][0]["id"]

    detail = await client.get(f"/api/v1/reports/{report_id}")
    download = await client.get(f"/api/v1/reports/{report_id}/download")

    assert detail.status_code == 200
    assert detail.json()["markdown"].startswith("# 回测报告")
    assert detail.json()["equity_curve"]["rows"][0]["策略净值"] == "1.0"
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    assert download.text.startswith("# 回测报告")


@pytest.mark.asyncio
async def test_report_errors_are_http_statuses(client):
    missing = await client.get("/api/v1/reports/not-a-valid-report-id")

    assert missing.status_code == 404
    assert missing.json()["detail"] == "报告不存在"
