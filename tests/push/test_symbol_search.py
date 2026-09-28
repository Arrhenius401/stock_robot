"""订阅标的候选的排序、歧义与缓存回退。"""
import json
import threading
import time

import pandas as pd
import pytest

from push.symbol_search import StockNameCatalog, search_symbols


def _snapshot(path, stocks, fetched_at=None):
    path.write_text(json.dumps({"fetched_at": fetched_at or time.time(), "stocks": stocks}), encoding="utf-8")


@pytest.mark.parametrize(("query", "symbol"), [
    ("hsi", "HSI"), ("恒指", "HSI"), ("国企指数", "HSCEI"),
    ("标普", "SPX"), ("纳指", "IXIC"), ("道指", "DJI"),
])
def test_overseas_index_aliases(tmp_path, query, symbol):
    _snapshot(tmp_path / "push_stock_names.json", {"600519": "贵州茅台"})
    result = search_symbols(query, 8, tmp_path)
    assert result["candidates"][0]["symbol"] == symbol
    assert result["candidates"][0]["kind"] == "index"
    assert result["candidates"][0]["index_style"] == "overseas"
    assert result["stocks_available"] is True


def test_ambiguous_code_returns_stock_and_index(tmp_path):
    _snapshot(tmp_path / "push_stock_names.json", {"000001": "平安银行", "600519": "贵州茅台"})
    result = search_symbols("000001", 8, tmp_path)
    assert {(item["symbol"], item["kind"]) for item in result["candidates"]} == {
        ("000001", "stock"), ("000001", "index"),
    }
    assert {item["display_name"] for item in result["candidates"]} == {"平安银行", "上证指数"}


def test_name_and_code_ranking_and_limit(tmp_path):
    _snapshot(tmp_path / "push_stock_names.json", {
        "600519": "贵州茅台", "600520": "贵州电子", "300001": "茅台科技",
    })
    result = search_symbols("6005", 1, tmp_path)
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["symbol"] == "600519"
    result = search_symbols("茅台", 8, tmp_path)
    assert [item["symbol"] for item in result["candidates"]] == ["300001", "600519"]


def test_network_failure_without_snapshot_returns_code_fallback_once(tmp_path, mocker):
    fetch = mocker.patch("utils.symbols._ak_code_name", side_effect=OSError("offline"))
    result = search_symbols("000001", 8, tmp_path)
    assert result["stocks_available"] is False
    assert {(item["kind"], item["display_name"]) for item in result["candidates"]} == {
        ("index", "上证指数"), ("stock", ""),
    }
    assert search_symbols("UNKNOWN", 8, tmp_path)["candidates"] == []
    assert fetch.call_count == 1


def test_successful_fetch_is_cached_and_persisted(tmp_path, mocker):
    fetch = mocker.patch("utils.symbols._ak_code_name", return_value=pd.DataFrame([
        {"code": "600519", "name": "贵州茅台"},
    ]))
    assert search_symbols("贵州", 8, tmp_path)["candidates"][0]["display_name"] == "贵州茅台"
    assert search_symbols("600519", 8, tmp_path)["stocks_available"] is True
    assert fetch.call_count == 1
    assert StockNameCatalog(tmp_path / "push_stock_names.json").names() == {"600519": "贵州茅台"}


def test_snapshot_returns_immediately_while_refresh_is_slow(tmp_path, mocker):
    path = tmp_path / "push_stock_names.json"
    _snapshot(path, {"600519": "贵州茅台"}, fetched_at=1)
    release = threading.Event()
    started = threading.Event()

    def slow_fetch():
        started.set()
        release.wait(timeout=3)
        return pd.DataFrame([{"code": "600519", "name": "新名称"}])

    mocker.patch("utils.symbols._ak_code_name", side_effect=slow_fetch)
    try:
        began = time.monotonic()
        result = search_symbols("贵州", 8, tmp_path)
        assert time.monotonic() - began < 1.0
        assert result["candidates"][0]["display_name"] == "贵州茅台"
        assert started.wait(timeout=1)
    finally:
        release.set()


def test_cold_start_wait_is_bounded(tmp_path, mocker):
    release = threading.Event()
    mocker.patch("utils.symbols._ak_code_name", side_effect=lambda: release.wait(timeout=3))
    try:
        began = time.monotonic()
        result = search_symbols("600519", 8, tmp_path)
        assert time.monotonic() - began < 1.2
        assert result["candidates"] == [{"symbol": "600519", "display_name": "",
                                         "kind": "stock", "index_style": None, "market": "a-shares"}]
        assert result["stocks_available"] is False
    finally:
        release.set()
