"""港股关键词新闻只做可追溯检索，不推断情绪。"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from data.hk_news import current_news, fetch_news, normalize_news, search_news
from data.schemas import HKNewsItem, HKNewsLink, HKNewsSnapshot

NOW = datetime(2026, 10, 23, 19, tzinfo=timezone(timedelta(hours=8)))


def row(title="新闻", stamp="2026-10-22 10:00:00", code="2026102212345", media="媒体"):
    return {"title": title, "date": stamp, "code": code, "mediaName": media}


def test_date_window_and_invalid_future():
    rows = [row(stamp=value, code=str(i)) for i, value in enumerate([
        "2026-09-24 00:00:00", "2026-09-23 23:59:59", "2026-10-23 19:00:01", "", "bad",
        "2026-10-23 18:00:00",
    ])]
    items = normalize_news(rows, NOW)
    assert len(items) == 2
    assert items[0].published_at > items[1].published_at


def test_exact_dedup_preserves_sources_and_different_days():
    items = normalize_news([
        row("<em>融资</em>&nbsp;新闻", code="1"),
        row("融资 新闻", code="2", media="其他媒体"),
        row("融资 新闻", stamp="2026-10-21 10:00:00", code="3"),
        row("融资 新闻！", code="4"),
    ], NOW)
    assert len(items) == 3
    assert len(items[0].links) == 2
    assert items[0].title == "融资 新闻"


def test_invalid_code_and_empty_title():
    assert normalize_news([row(code="javascript:bad"), row(title="  ")], NOW) == []


def test_cache_view_refilters_and_does_not_mutate():
    old = HKNewsItem(title="旧闻", published_at=NOW - timedelta(days=31),
                     links=[HKNewsLink(media="媒体", url="https://finance.eastmoney.com/a/1.html")])
    snap = HKNewsSnapshot(fetched_at=NOW, items=[old])
    assert current_news(snap, NOW).items == []
    assert snap.items == [old]
    assert HKNewsSnapshot.model_validate_json(snap.model_dump_json()) == snap


def test_query_budget_and_partial():
    def query(keyword):
        if keyword == "00700":
            return [row()]
        raise TimeoutError("测试超时")

    with patch("data.hk_news.search_news", side_effect=query) as call:
        snap = fetch_news("00700.HK", "腾讯控股", now=NOW)
    assert call.call_count == 2
    assert snap.queries == ["00700", "腾讯控股"]
    assert snap.query_errors == ["腾讯控股"]
    assert len(snap.items) == 1


def test_all_failed_raises_and_empty_is_success():
    with patch("data.hk_news.search_news", side_effect=TimeoutError), pytest.raises(RuntimeError):
        fetch_news("00700.HK", "腾讯控股", now=NOW)
    with patch("data.hk_news.search_news", return_value=[]):
        snap = fetch_news("00700.HK", "腾讯控股", now=NOW)
    assert snap.items == [] and snap.query_errors == []


def test_missing_name_does_not_invent_extra_lookup():
    with patch("data.hk_news.search_news", return_value=[]) as call:
        snap = fetch_news("00700.HK", "00700.HK", now=NOW)
    assert call.call_count == 1
    assert snap.query_errors


def test_http_parameters_and_payload_validation():
    class Response:
        text = 'hkNews({"bizCode":"","result":{"cmsArticleWebOld":[]}});'

        def raise_for_status(self):
            return None

    with patch("data.hk_news.requests.get", return_value=Response()) as call:
        assert search_news("00700") == []
    import json
    kwargs = call.call_args.kwargs
    params = json.loads(kwargs["params"]["param"])["param"]["cmsArticleWebOld"]
    assert params["sort"] == "time" and params["pageIndex"] == 1 and params["pageSize"] == 100
    assert kwargs["timeout"] == 12
    Response.text = 'hkNews({"bizCode":"B0001","result":{"cmsArticleWebOld":[]}});'
    with patch("data.hk_news.requests.get", return_value=Response()), pytest.raises(ValueError):
        search_news("00700")


def test_fetch_keeps_two_hundred_candidates_for_later_filtering():
    def query(keyword):
        prefix = "1" if keyword == "00700" else "2"
        return [row(title=f"{keyword}事件{i}", code=f"{prefix}{i}") for i in range(100)]

    with patch("data.hk_news.search_news", side_effect=query):
        snapshot = fetch_news("00700.HK", "腾讯控股", now=NOW)
    assert snapshot.candidate_count == 200
    assert snapshot.limit_reached
    assert len(snapshot.items) == 200


def test_current_view_rejects_unsafe_links_and_naive_dates():
    bad = HKNewsItem(title="测试", published_at=NOW, links=[HKNewsLink(media="媒体", url="javascript:alert(1)")])
    naive = HKNewsItem(title="日期未知", published_at=NOW.replace(tzinfo=None),
                       links=[HKNewsLink(media="媒体", url="https://example.com/news")])
    assert current_news(HKNewsSnapshot(fetched_at=NOW, items=[bad, naive]), NOW).items == []


@pytest.mark.parametrize("text", [
    "error page", 'hkNews({"result":{}})', 'hkNews({"result":{"cmsArticleWebOld":null}})',
    'hkNews({"result":{"cmsArticleWebOld":[null]}})',
])
def test_malformed_response_is_failure_not_successful_empty(text):
    class Response:
        text: str = ""

        def raise_for_status(self):
            return None

    response = Response()
    response.text = text
    with patch("data.hk_news.requests.get", return_value=response), pytest.raises(ValueError):
        search_news("00700")
