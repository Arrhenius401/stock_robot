"""东财公告正文解析与缺失边界。"""
import pytest
import requests

from data.a_announcements import extract_art_code, fetch_notice_content
from data.cache import CacheManager


def test_notice_cache_survives_new_cache_manager(monkeypatch, tmp_path):
    path = tmp_path / "notice.sqlite"

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"notice_content": "真实公告正文"}}

    monkeypatch.setattr(requests, "get", lambda *a, **k: Response())
    assert fetch_notice_content("AN202610301831245504", cache=CacheManager(path)) == "真实公告正文"

    def unexpected_request(*args, **kwargs):
        pytest.fail("持久缓存命中不应重新请求")

    monkeypatch.setattr(requests, "get", unexpected_request)
    assert fetch_notice_content("AN202610301831245504", cache=CacheManager(path)) == "真实公告正文"


def test_extract_art_code_from_eastmoney_url():
    assert extract_art_code("https://data.eastmoney.com/notices/detail/600519/AN202610301831245502.html") == "AN202610301831245502"
    assert extract_art_code("https://example.com/no-code") is None


@pytest.mark.parametrize("content", ["  贵州茅台 公告  ", None])
def test_fetch_notice_content(monkeypatch, content, tmp_path):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"notice_content": content}}

    monkeypatch.setattr(requests, "get", lambda *a, **k: Response())
    if content is None:
        with pytest.raises(ValueError, match="正文为空"):
            fetch_notice_content("AN202610301831245503", cache=CacheManager(tmp_path / "cache.db"))
    else:
        assert fetch_notice_content("AN202610301831245502", cache=CacheManager(tmp_path / "cache.db")) == "贵州茅台 公告"
