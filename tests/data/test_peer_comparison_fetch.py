"""用裁切原始响应核对源契约，并注入分页、预算和缓存故障。"""
import json
import threading
import time
from pathlib import Path

import pytest
import requests

from data import peer_comparison_fetch as fetch
from data.cache import CacheManager
from data.peer_comparison_rules import normalize_financial_rows

RESOURCES = Path(__file__).parents[1] / "resources" / "industry"


def a_row(symbol="600519", end="2025-12-31"):
    return {"SECURITY_CODE": symbol, "REPORT_DATE": end, "REPORT_TYPE": "年报",
            "TOTALOPERATEREVETZ": 12.5, "XSJLL": -3, "ROEJQ": 20}


class Response:
    def __init__(self, rows, count=None, pages=1):
        self.payload = {"success": True, "result": {"count": len(rows) if count is None else count,
                                                     "pages": pages, "data": rows}}

    def json(self):
        return self.payload


def test_real_cropped_profile_and_members_preserve_exact_level_and_exchange():
    profile = fetch.parse_a_profile((RESOURCES / "legulegu-600519-classification.html").read_text(encoding="utf-8"))
    assert profile == {"code": "851251.SI", "level3": "白酒Ⅲ", "level2": "白酒Ⅱ", "level1": "食品饮料"}
    members = fetch.parse_a_members((RESOURCES / "legulegu-851251-members.html").read_text(encoding="utf-8"), profile["code"])
    assert len(members) == 2
    assert members[0]["symbol"] == "603589"
    assert members[0]["exchange"] == "SH"
    assert members[0]["market_cap"] == pytest.approx(105.568092e8)
    assert "level2" not in members[0]


def test_profile_rejects_level_two_as_precise_peer_pool():
    with pytest.raises(fetch.ComparisonFetchError):
        fetch.parse_a_profile('<a href="/stockdata/sw-industry-2021?industryCode=801125.SI">II白酒Ⅱ</a>')


def test_members_do_not_drop_st_or_missing_market_cap():
    text = '<tr data-stock-code="900001.BJ"><td>1</td><td>900001.BJ</td><td>ST样例</td><td><a href="?industryCode=851251.SI">行业</a></td></tr>'
    rows = fetch.parse_a_members(text + text, "851251.SI")
    assert len(rows) == 1
    assert rows[0]["market_cap"] is None


def test_real_quote_parser_rejects_wrong_exchange_and_preserves_trade_date():
    text = (RESOURCES / "tencent-a-batch-quotes.txt").read_text(encoding="utf-8")
    rows = fetch.parse_tencent_quotes(text, {"600519": "SH", "000858": "SZ", "000568": "SH"})
    assert len(rows) == 2
    assert rows[0]["as_of"] == "2026-10-23"
    assert rows[0]["pb"] == 6.64
    assert rows[0]["pe_ttm"] == 20.49
    assert rows[0]["pe_ttm"] not in (18.74, 20.27)


@pytest.mark.parametrize("market", ["a", "hk"])
def test_real_indicator_samples_keep_source_percentage_units_and_periods(market):
    raw = json.loads((RESOURCES / f"eastmoney-{market}-main-indicators.json").read_text(encoding="utf-8"))
    rows = normalize_financial_rows(raw, "hk" if market == "hk" else "a-shares")
    assert len(rows) == len(raw)
    key = "OPERATE_INCOME_YOY" if market == "hk" else "TOTALOPERATEREVETZ"
    assert rows[0]["revenue_growth_yoy"] == raw[0][key]
    assert all(r["start"] and r["end"] for r in rows)


def test_financial_paginates_and_checks_count(monkeypatch):
    calls = []

    def response(url, deadline, *, params):
        page = int(params["pageNumber"])
        calls.append(page)
        return Response([a_row(end="2025-12-31" if page == 1 else "2024-12-31")], 2, 2)

    monkeypatch.setattr(fetch, "_response", response)
    result = fetch.fetch_financial_rows(["600519"], "a-shares", start="2024-01-01", deadline=time.monotonic() + 5)
    rows, errors = result.rows, result.errors
    assert len(rows) == 2
    assert calls == [1, 2]
    assert not errors


@pytest.mark.parametrize("response", [Response([a_row()], 2), Response([a_row("000858")])])
def test_incomplete_or_foreign_batch_is_rejected(monkeypatch, response):
    monkeypatch.setattr(fetch, "_response", lambda *a, **k: response)
    result = fetch.fetch_financial_rows(["600519"], "a-shares", start="2024-01-01", deadline=time.monotonic() + 5)
    rows, errors = result.rows, result.errors
    assert rows == []
    assert errors


def test_partial_coverage_is_reported_without_zero_filling(monkeypatch):
    monkeypatch.setattr(fetch, "_response", lambda *a, **k: Response([a_row()]))
    result = fetch.fetch_financial_rows(["600519", "000858"], "a-shares", start="2024-01-01", deadline=time.monotonic() + 5)
    rows, errors = result.rows, result.errors
    assert len(rows) == 1
    assert any("1/2" in e for e in errors)
    assert result.record is not None
    assert (result.record.result_state, result.record.cache_status) == ("partial", "live")


def test_cache_hit_no_request_refresh_failure_preserves_success(monkeypatch, tmp_path):
    cache = CacheManager(tmp_path / "cache.db")
    monkeypatch.setattr(fetch, "_response", lambda *a, **k: Response([a_row()]))
    args = {"market": "a-shares", "start": "2024-01-01", "deadline": time.monotonic() + 5, "cache": cache}
    result = fetch.fetch_financial_rows(["600519"], **args)
    expected, errors = result.rows, result.errors
    assert not errors
    monkeypatch.setattr(fetch, "_response", lambda *a, **k: pytest.fail("新鲜缓存不应联网"))
    assert fetch.fetch_financial_rows(["600519"], **args).rows == expected

    def fail(*args, **kwargs):
        raise requests.Timeout("模拟超时")

    monkeypatch.setattr(fetch, "_response", fail)
    result = fetch.fetch_financial_rows(["600519"], **args, refresh=True)
    restored, errors = result.rows, result.errors
    assert restored == expected
    assert any("原报告期" in e for e in errors)
    assert fetch.fetch_financial_rows(["600519"], **args).rows == expected


def test_batch_size_and_maximum_concurrency(monkeypatch):
    symbols = [f"{i:06}" for i in range(101)]
    lock = threading.Lock()
    active, maximum = 0, 0
    sizes = []

    def response(url, deadline, *, params):
        nonlocal active, maximum
        import re
        codes = re.findall(r'"(\d{6})"', params["filter"])
        with lock:
            sizes.append(len(codes))
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.01)
        with lock:
            active -= 1
        return Response([a_row(s) for s in codes])

    monkeypatch.setattr(fetch, "_response", response)
    result = fetch.fetch_financial_rows(symbols, "a-shares", start="2024-01-01", deadline=time.monotonic() + 5)
    rows, errors = result.rows, result.errors
    assert len(rows) == 101
    assert not errors
    assert sorted(sizes) == [1, 50, 50]
    assert maximum <= 2


def test_budget_exhaustion_stops_new_requests(monkeypatch):
    monkeypatch.setattr(fetch, "_response", lambda *a, **k: pytest.fail("预算到期不得发请求"))
    result = fetch.fetch_financial_rows(["600519"], "a-shares", start="2024-01-01", deadline=time.monotonic() - 1)
    rows, errors = result.rows, result.errors
    assert not rows
    assert errors


def test_duplicate_conflicting_values_cannot_silently_rank():
    rows = normalize_financial_rows([a_row(), {**a_row(), "ROEJQ": 10}], "a-shares")
    assert len(rows) == 1
    assert "冲突" in rows[0]["reason"]


def test_expired_success_is_available_after_freshness_check(tmp_path):
    cache = CacheManager(tmp_path / "cache.db")
    cache.put("peer_financial", "key", "v1", json.dumps({"saved_at": time.time() - 1000, "payload": [a_row()]}), ttl_seconds=1)
    old, fresh = fetch.read_snapshot(cache, "peer_financial", "key", 1)
    assert not fresh
    assert old == [a_row()]
    assert cache.get_stale("peer_financial", "key", "v1") is not None


def test_a_helper_persists_full_snapshot_and_refresh_failure_keeps_original(monkeypatch, tmp_path):
    cache = CacheManager(tmp_path / "cache.db")
    profile = (RESOURCES / "legulegu-600519-classification.html").read_text(encoding="utf-8")
    quotes = (RESOURCES / "tencent-a-batch-quotes.txt").read_text(encoding="utf-8")
    members = ''.join(f'<tr data-stock-code="{code}.{exchange}"><td>1</td><td>{code}</td><td>{code}</td>'
                      '<td><a href="?industryCode=851251.SI">白酒Ⅲ</a></td></tr>'
                      for code, exchange in (("600519", "SH"), ("000858", "SZ"), ("000568", "SZ")))
    calls = []

    class TextResponse:
        def __init__(self, text):
            self.text = text

    def response(url, deadline, **kwargs):
        calls.append(url)
        return TextResponse(profile if "/s/" in url else members if "index-composition" in url else quotes)

    monkeypatch.setattr(fetch, "_response", response)
    data = fetch.fetch_a_industry("600519", cache=cache)[0]
    assert data.comparison_ready
    assert data.peer_scope == "申万三级"
    assert data.resolved_sw_level2 == "白酒Ⅱ"
    assert set(data.peers) == {"000858", "000568"}
    assert data.comparison_snapshot["target"]["pe_ttm"] == 20.49
    assert len(calls) == 3
    again = fetch.fetch_a_industry("600519", cache=cache)[0]
    assert len(calls) == 3
    assert again.comparison_snapshot["cache_status"] == "cache"

    def fail(*a, **k):
        raise requests.Timeout("模拟刷新失败")

    monkeypatch.setattr(fetch, "_response", fail)
    stale = fetch.fetch_a_industry("600519", cache=cache, refresh=True)[0]
    assert stale.comparison_snapshot["target"] == data.comparison_snapshot["target"]
    assert stale.comparison_snapshot["cache_status"] == "stale"
    assert stale.comparison_snapshot["errors"]
    assert fetch.fetch_a_industry("600519", cache=cache)[0].comparison_snapshot["cache_status"] == "cache"
    frozen = fetch.fetch_a_industry("600519", cache=cache, refresh=True, comparison_cache_only=True)[0]
    assert frozen.comparison_snapshot["target"]["pe_ttm"] == 20.49
    assert frozen._comparison_deadline is not None
    assert frozen._comparison_deadline <= time.monotonic()


def test_a_unknown_classification_returns_explicit_unverified_state(monkeypatch):
    def fail(*a, **k):
        raise requests.Timeout("三级未知")

    monkeypatch.setattr(fetch, "_response", fail)
    data = fetch.fetch_a_industry("600519")[0]
    assert not data.comparison_ready
    assert data.industry == "未知"
    assert data.comparison_snapshot["errors"]


def test_same_pool_cache_is_rebased_for_each_target_without_second_quote_request(monkeypatch, tmp_path):
    cache = CacheManager(tmp_path / "cache.db")
    text = (RESOURCES / "tencent-a-batch-quotes.txt").read_text(encoding="utf-8")
    calls = []

    class TextResponse:
        encoding = ""

        def __init__(self):
            self.text = text

    def response(*a, **k):
        calls.append(True)
        return TextResponse()

    monkeypatch.setattr(fetch, "_response", response)
    members = [{"symbol": s, "exchange": x} for s, x in (("600519", "SH"), ("000858", "SZ"), ("000568", "SZ"))]
    first = fetch._quote_snapshot(members, "600519", cache, False, time.monotonic() + 5)
    second = fetch._quote_snapshot(members, "000858", cache, False, time.monotonic() + 5)
    assert first["target"]["symbol"] == "600519"
    assert second["target"]["symbol"] == "000858"
    assert second["target"]["pe_ttm"] == 21.91
    assert {p["symbol"] for p in second["peers"]} == {"600519", "000568"}
    assert second["cache_status"] == "cache"
    assert len(calls) == 1

    def fail(*a, **k):
        raise requests.Timeout("刷新失败")

    monkeypatch.setattr(fetch, "_response", fail)
    stale = fetch._quote_snapshot(members, "000568", cache, True, time.monotonic() + 5)
    assert stale["target"]["symbol"] == "000568"
    assert stale["target"]["pe_ttm"] == 13.72
    assert {p["symbol"] for p in stale["peers"]} == {"600519", "000858"}


def test_a_quote_cache_preserves_each_trade_date_when_target_changes(monkeypatch, tmp_path):
    text = (RESOURCES / "tencent-a-batch-quotes.txt").read_text(encoding="utf-8").replace("20261023161458", "20261022161458")

    class TextResponse:
        encoding = ""

        def __init__(self):
            self.text = text

    monkeypatch.setattr(fetch, "_response", lambda *a, **k: TextResponse())
    members = [{"symbol": s, "exchange": x} for s, x in (("600519", "SH"), ("000858", "SZ"), ("000568", "SZ"))]
    cache = CacheManager(tmp_path / "cache.db")
    first = fetch._quote_snapshot(members, "600519", cache, False, time.monotonic() + 5)
    assert first["as_of"] == "2026-10-22"
    assert all(p["reason"] for p in first["peers"])
    second = fetch._quote_snapshot(members, "000858", cache, False, time.monotonic() + 5)
    assert second["as_of"] == "2026-10-23"
    by_code = {p["symbol"]: p for p in second["peers"]}
    assert by_code["600519"]["as_of"] == "2026-10-22"
    assert by_code["600519"]["reason"]
    assert not by_code["000568"]["reason"]


@pytest.mark.parametrize("payload", [None, [], {"success": True, "result": []}])
def test_invalid_json_shapes_become_fetch_errors_and_keep_success_cache(monkeypatch, tmp_path, payload):
    cache = CacheManager(tmp_path / "cache.db")
    monkeypatch.setattr(fetch, "_response", lambda *a, **k: Response([a_row()]))
    args = {"market": "a-shares", "start": "2024-01-01", "deadline": time.monotonic() + 5, "cache": cache}
    result = fetch.fetch_financial_rows(["600519"], **args)
    expected, _ = result.rows, result.errors
    bad = Response([])
    bad.payload = payload
    monkeypatch.setattr(fetch, "_response", lambda *a, **k: bad)
    result = fetch.fetch_financial_rows(["600519"], **args, refresh=True)
    restored, errors = result.rows, result.errors
    assert restored == expected
    assert errors


@pytest.mark.parametrize("bad_row", [{**a_row(), "REPORT_TYPE": "未知报表"},
                                    a_row(end="2099-12-31")])
def test_unknown_or_future_period_does_not_replace_success_cache(monkeypatch, tmp_path, bad_row):
    cache = CacheManager(tmp_path / "cache.db")
    monkeypatch.setattr(fetch, "_response", lambda *a, **k: Response([a_row()]))
    args = {"market": "a-shares", "start": "2024-01-01", "deadline": time.monotonic() + 5, "cache": cache}
    result = fetch.fetch_financial_rows(["600519"], **args)
    expected, _ = result.rows, result.errors
    monkeypatch.setattr(fetch, "_response", lambda *a, **k: Response([bad_row]))
    result = fetch.fetch_financial_rows(["600519"], **args, refresh=True)
    restored, errors = result.rows, result.errors
    assert restored == expected
    assert errors


def test_a_cache_only_does_not_request_network_even_when_refresh_is_true(monkeypatch, tmp_path):
    cache = CacheManager(tmp_path / "cache.db")
    monkeypatch.setattr(fetch, "_response", lambda *a, **k: pytest.fail("熔断仅缓存模式不得联网"))
    data = fetch.fetch_a_industry("600519", cache=cache, refresh=True, comparison_cache_only=True)[0]
    assert not data.comparison_ready
    assert data.comparison_snapshot["errors"]


def test_a_classification_wall_deadline_does_not_wait_for_socket_idle_or_cache_late_response(monkeypatch, tmp_path):
    release, finished = threading.Event(), threading.Event()
    profile = (RESOURCES / "legulegu-600519-classification.html").read_text(encoding="utf-8")

    class LateResponse:
        text = profile

        def raise_for_status(self):
            return None

    def slow_get(*a, **k):
        release.wait(2)
        finished.set()
        return LateResponse()

    monkeypatch.setattr(fetch.requests, "get", slow_get)
    monkeypatch.setattr(fetch, "COLLECTION_BUDGET", 0.02)
    cache = CacheManager(tmp_path / "cache.db")
    started = time.monotonic()
    try:
        data = fetch.fetch_a_industry("600519", cache=cache)[0]
        assert time.monotonic() - started < 0.5
        assert not data.comparison_ready
        assert cache.stats()["total_entries"] == 0
    finally:
        release.set()
    assert finished.wait(1)
    assert cache.stats()["total_entries"] == 0


def test_financial_result_records_cache_and_failed_refresh(monkeypatch, tmp_path):
    cache = CacheManager(tmp_path / "cache.db")
    monkeypatch.setattr(fetch, "_response", lambda *a, **k: Response([a_row()]))
    args = {"market": "a-shares", "start": "2024-01-01", "deadline": time.monotonic() + 5, "cache": cache}
    live = fetch.fetch_financial_rows(["600519"], **args)
    assert live.record is not None
    assert live.record.result_state == "complete"
    assert live.record.cache_status == "live"
    cached = fetch.fetch_financial_rows(["600519"], **args)
    assert cached.record is not None
    assert cached.record.cache_status == "cache"
    assert cached.record.collected_at is not None
    monkeypatch.setattr(fetch, "_response", lambda *a, **k: (_ for _ in ()).throw(requests.Timeout()))
    stale = fetch.fetch_financial_rows(["600519"], **args, refresh=True)
    assert stale.rows == live.rows
    assert stale.errors
    assert stale.record is not None
    assert stale.record.result_state == "partial"
    assert stale.record.cache_status == "stale"
    assert stale.record.collected_at == cached.record.collected_at


def test_financial_result_empty_is_failed(monkeypatch):
    monkeypatch.setattr(fetch, "_response", lambda *a, **k: Response([]))
    result = fetch.fetch_financial_rows(["600519"], "a-shares", start="2024-01-01", deadline=time.monotonic() + 5)
    assert not result.rows
    assert result.errors
    assert result.record is not None
    assert result.record.result_state == "failed"
    assert result.record.error_kind == "empty"
