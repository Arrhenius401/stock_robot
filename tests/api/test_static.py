"""静态 Web UI 冒烟测试 — 页面与关键资源可访问"""
import pytest
from httpx import ASGITransport, AsyncClient

from api.app import create_app


@pytest.fixture
async def client():
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestStaticUI:
    @pytest.mark.asyncio
    async def test_index_html_served(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert "Stock Robot" in resp.text

    @pytest.mark.asyncio
    async def test_js_modules_served(self, client):
        for path in ("/js/app.js", "/js/api.js", "/js/state.js", "/js/markdown.js",
                     "/js/chat.js", "/js/sessions.js", "/js/components.js",
                     "/js/report.js", "/js/indexview.js"):
            resp = await client.get(path)
            assert resp.status_code == 200, path

    @pytest.mark.asyncio
    async def test_css_served(self, client):
        resp = await client.get("/css/app.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers.get("content-type", "")
