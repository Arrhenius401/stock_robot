"""HKEX 协议合成样例，离线覆盖元数据与正文降级。"""
import json

from data import hk_announcements as announcements


def test_metadata_survives_pdf_failure(monkeypatch):
    assert announcements.parse_stock_id('callback({"stockInfo":[{"stockId":7609}]});') == 7609
    raw = {"result": json.dumps([{"TITLE": "季度业绩", "LONG_TEXT": "公告及通告",
        "DATE_TIME": "18/11/2026 16:30", "FILE_LINK": "/a.pdf"}])}
    item = announcements.parse_announcements(raw)[0]
    assert item["title"] == "季度业绩"
    assert item["type"] == "公告及通告"
    assert item["published_at"].isoformat() == "2026-11-18T16:30:00+08:00"
    assert item["url"] == "https://www1.hkexnews.hk/a.pdf"
    def fail(*args, **kwargs):
        raise OSError("离线")
    monkeypatch.setattr(announcements.requests, "get", fail)
    assert announcements.fetch_announcement_text(item["url"]) == ""


def test_adapter_keeps_announcements_when_news_fails(monkeypatch):
    from datetime import datetime

    from data.akshare import AkShareAdapter
    from data.hk_news import HK_TZ

    def fail(*args, **kwargs):
        raise RuntimeError("离线新闻失败")
    monkeypatch.setattr("data.hk_news.fetch_news", fail)
    monkeypatch.setattr(announcements, "fetch_announcements", lambda *args: [{
        "title": "业绩", "type": "公告及通告", "published_at": datetime.now(HK_TZ),
        "url": "https://www1.hkexnews.hk/a.pdf"}])
    snapshot = AkShareAdapter()._fetch_news("00700.HK", market="hk")[0].hk_news
    assert snapshot is not None
    assert snapshot.items[0].source == "announcement"
    assert snapshot.items[0].content == ""
    assert snapshot.query_errors


def test_adapter_keeps_news_when_announcements_fail(monkeypatch):
    from datetime import datetime

    from data.akshare import AkShareAdapter
    from data.hk_news import HK_TZ
    from data.schemas import HKNewsItem, HKNewsSnapshot

    now = datetime.now(HK_TZ)
    monkeypatch.setattr("data.hk_news.fetch_news", lambda *args: HKNewsSnapshot(
        fetched_at=now, items=[HKNewsItem(title="新闻", published_at=now)]))
    def fail(*args):
        raise RuntimeError("离线公告失败")
    monkeypatch.setattr(announcements, "fetch_announcements", fail)
    snapshot = AkShareAdapter()._fetch_news("00700.HK", market="hk")[0].hk_news
    assert snapshot is not None
    assert snapshot.items[0].source == "news"
    assert snapshot.query_errors == ["HKEXnews公告查询失败"]


def test_announcement_window_survives_downstream_filter():
    from datetime import datetime, timedelta

    from data.hk_news import HK_TZ, current_news
    from data.schemas import HKNewsItem, HKNewsLink, HKNewsSnapshot

    now = datetime.now(HK_TZ)
    link = HKNewsLink(media="HKEXnews", url="https://www1.hkexnews.hk/a.pdf")
    items = [HKNewsItem(title=source, source=source, published_at=now - timedelta(days=35), links=[link])
             for source in ("news", "announcement")]
    result = current_news(HKNewsSnapshot(fetched_at=now, items=items), now)
    assert [item.source for item in result.items] == ["announcement"]
