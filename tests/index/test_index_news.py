"""港美指数仅采集可核查的市场新闻。"""
import pytest

from index.index_news import search_index_news


def row(title, url, stamp="2026-11-17 09:30:00"):
    return {"title": title, "url": url, "date": stamp, "mediaName": "来源"}


def test_spx_news_filters_and_deduplicates(mocker):
    request = mocker.patch("index.index_news.search_news", return_value=[
        row("<em>标普500</em>收盘", "https://example.com/s"),
        row("标普500收盘", "https://example.com/duplicate"),
        row("美股重复链接", "https://example.com/s"),
        row("美国股市观察", "https://example.com/u"),
        row("标普500ETF连涨", "https://example.com/ad"),
        row("沪深指数收盘", "https://example.com/a"),
        row("美股盘中", "javascript:bad"),
        row("纳指回顾", "https://example.com/bad-date", "昨天"),
        row("美股缺链接", ""),
        *(row(f"美股消息 {i}", f"https://example.com/{i}") for i in range(20)),
    ])

    items = search_index_news("SPX", "标普500", "us")

    request.assert_called_once_with("标普500")
    assert len(items) == 10
    assert [item["title"] for item in items[:2]] == ["标普500收盘", "美国股市观察"]
    assert items[0]["source"] == "来源"
    assert items[0]["published_at"].startswith("2026-11-17")
    assert "市场新闻" in items[0]["news_disclaimer"]
    assert all(item["title"].startswith("美股消息") for item in items[2:])


def test_hk_news_empty_and_network_failure(mocker):
    request = mocker.patch("index.index_news.search_news", return_value=[
        row("恒生科技基金净值", "https://example.com/ad"),
    ])
    assert search_index_news("HSI:02083.00", "恒生科技指数", "hk") == []
    request.side_effect = TimeoutError("超时")
    with pytest.raises(TimeoutError):
        search_index_news("HSI:02083.00", "恒生科技指数", "hk")
