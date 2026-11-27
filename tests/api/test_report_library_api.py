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

def test_chat_artifact_preserves_assessment_contract():
    from api.app import _extract_stock_reports

    report = {"symbol": "600519", "report_scope": "limited", "assessment_status": "partial",
              "identity_status": "confirmed", "market_profile": {"market": "a-shares"},
              "price": {"status": "ok"}}
    payload = _extract_stock_reports([{"tool": "analyze_stock", "status": "success", "content": report}])[0][1]
    for key, value in report.items():
        assert payload[key] == value


@pytest.mark.asyncio
async def test_legacy_report_retains_original_without_invented_snapshot(client):
    reports = (await client.get("/api/v1/reports")).json()["reports"]
    legacy = next(report for report in reports if report["legacy"])
    detail = (await client.get(f"/api/v1/reports/{legacy['id']}")).json()
    assert detail["report"]["legacy"] is True
    assert detail["markdown"].replace("\r\n", "\n") == "# 贵州茅台\n\n旧版个股正文"
    assert detail.get("stock_payload") is None
