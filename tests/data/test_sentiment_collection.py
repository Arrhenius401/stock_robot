"""新闻采集保留真实日期、摘要和链接，不补造公告正文。"""
from datetime import datetime

import pandas as pd

from data.akshare import AkShareAdapter
from data.hk_news import HK_TZ, normalize_news

NOW = datetime(2026, 10, 25, 15, tzinfo=HK_TZ)


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz)


def test_a_window_unknown_future_and_actual_source_fields(mocker):
    rows = [{"新闻标题": f"新闻{i}", "发布时间": stamp, "新闻内容": "实际摘要", "新闻链接": f"https://example.com/{i}",
             "文章来源": "原媒体"} for i, stamp in enumerate([
                 "2026-09-26", "2026-09-25", "2026-10-26", "", "bad", "2026-10-25 15:01:00",
                 "2026-10-25 14:00:00", "2026-10-25T06:30:00+00:00"])]
    mocker.patch("data.akshare.datetime", FrozenDatetime)
    mocker.patch("data.akshare._ak_news", return_value=pd.DataFrame(rows))
    mocker.patch("akshare.stock_individual_notice_report", return_value=pd.DataFrame([
        {"公告标题": "真实公告", "公告日期": "2026-10-25", "网址": "https://notice.example.com/a",
         "内容": "不能伪装成实际已取得正文"}]))
    raw = AkShareAdapter()._fetch_news("600519")[0]._raw_sentiment
    assert raw is not None
    assert {item.title for item in raw.items} == {"新闻0", "新闻6", "新闻7", "真实公告"}
    news = next(item for item in raw.items if item.title == "新闻0")
    assert news.content == "实际摘要" and news.media == "原媒体" and news.url == "https://example.com/0"
    notice = next(item for item in raw.items if item.title == "真实公告")
    assert notice.content == "" and notice.url == "https://notice.example.com/a"


def test_a_exact_dedup_preserves_different_days(mocker):
    mocker.patch("data.akshare.datetime", FrozenDatetime)
    mocker.patch("data.akshare._ak_news", return_value=pd.DataFrame([
        {"新闻标题": "相同标题", "发布时间": day, "新闻链接": url}
        for day, url in [("2026-10-25", "https://example.com/a"),
                         ("2026-10-25", "https://example.com/b"),
                         ("2026-10-24", "https://example.com/c"),
                         ("2026-10-23", "https://example.com/a")]]))
    mocker.patch("akshare.stock_individual_notice_report", return_value=pd.DataFrame())
    raw = AkShareAdapter()._fetch_news("600519")[0]._raw_sentiment
    assert raw is not None
    items = raw.items
    assert len(items) == 2
    assert {item.publish_date.isoformat() for item in items} == {"2026-10-25", "2026-10-24"}


def test_hk_keeps_real_cleaned_summary_media_and_url():
    item = normalize_news([{"title": "<em>腾讯</em>公告", "date": "2026-10-25 10:00:00", "code": "123",
        "content": "<em>腾讯</em>的实际&nbsp;摘要", "mediaName": "原媒体",
        "url": "http://hk.eastmoney.com/a/123.html"}], NOW)[0]
    assert item.content == "腾讯的实际 摘要"
    assert item.links[0].url == "http://hk.eastmoney.com/a/123.html"
    assert item.links[0].media == "原媒体"


def test_a_nan_links_do_not_deduplicate_different_titles(mocker):
    mocker.patch("data.akshare.datetime", FrozenDatetime)
    mocker.patch("data.akshare._ak_news", return_value=pd.DataFrame([
        {"新闻标题": f"独立新闻{i}", "发布时间": "2026-10-25", "新闻链接": float("nan"),
         "标题": float("nan"), "新闻内容": float("nan"), "文章来源": float("nan")}
        for i in range(3)]))
    mocker.patch("akshare.stock_individual_notice_report", return_value=pd.DataFrame())
    raw = AkShareAdapter()._fetch_news("600519")[0]._raw_sentiment
    assert raw is not None
    assert {item.title for item in raw.items} == {"独立新闻0", "独立新闻1", "独立新闻2"}
    assert all(item.url == item.content == item.media == "" for item in raw.items)
