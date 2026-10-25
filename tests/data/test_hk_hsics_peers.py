"""港股恒生二级同行池单元测试。"""

import threading
import time

import pytest
import requests

from data import hk_hsics_peers as peers
from data.cache import CacheManager
from data.hk_hsics_industry import HsicsMember


def member(symbol: str, sector: str, industry: str, status: str = "mapped") -> HsicsMember:
    return HsicsMember(
        symbol=symbol, sector=sector, industry=industry, status=status, in_hsci=True)


MEMBERS = {
    "00700.HK": member("00700.HK", "软件服务", "Information Technology"),
    "00268.HK": member("00268.HK", "软件服务", "Information Technology"),
    "00909.HK": member("00909.HK", "软件服务", "Information Technology"),
    "00005.HK": member("00005.HK", "银行", "Financials"),
    "09988.HK": member("09988.HK", "专业零售", "Consumer Discretionary", "missing"),
}
CONNECT = {"00700.HK", "00268.HK", "00909.HK", "00005.HK", "09988.HK"}


def pool_row(symbol: str, name: str, pe, pb, type_name: str = "软件服务") -> dict:
    return {
        "CORRE_SECUCODE": symbol,
        "CORRE_SECURITY_NAME": name,
        "PE_TTM": pe,
        "PB_MQR": pb,
        "TYPE_NAME": type_name,
        "TYPE_ID": "403010002",
        "REPORT_DATE": "2026-10-23 00:00:00",
    }


def test_build_industry_data_filters_connect_mapped_same_sector():
    rows = [
        pool_row("00700.HK", "腾讯控股", 13.15, 2.68),
        pool_row("00268.HK", "金蝶国际", 30.0, 4.0),
        pool_row("00909.HK", "明源云", -5.0, None),
        pool_row("00005.HK", "汇丰控股", 12.0, 1.8, type_name="软件服务"),
        pool_row("01111.HK", "非港股通", 8.0, 1.2),
        pool_row("09988.HK", "阿里巴巴", 22.0, 1.6),
    ]

    data = peers.build_industry_data("00700.HK", rows, MEMBERS, CONNECT)

    assert data is not None
    assert data.industry == "软件服务"
    assert data.sector == "Information Technology"
    assert data.peer_scope == "港股通·恒生HSICS二级"
    assert data.peer_industry == "软件服务"
    assert data.peers == ["00268.HK", "00909.HK"]
    assert data.peer_names == {"00268.HK": "金蝶国际", "00909.HK": "明源云"}
    assert data._target_pe == 13.15
    assert data._target_pb == 2.68
    assert data._data_date == "2026-10-23"
    assert set(data._peer_valuations) == {"00268.HK", "00909.HK"}
    assert data._peer_valuations["00909.HK"].pe_ttm == -5.0


def test_build_industry_data_requires_target_in_connect():
    rows = [pool_row("00700.HK", "腾讯控股", 13.15, 2.68)]
    assert peers.build_industry_data(
        "00700.HK", rows, MEMBERS, CONNECT - {"00700.HK"}) is None


def test_build_industry_data_returns_none_for_non_member_or_missing_mapping():
    missing_quote = peers.build_industry_data("00700.HK", [], MEMBERS, CONNECT)
    assert missing_quote is not None
    assert missing_quote.comparison_ready
    assert missing_quote.comparison_snapshot["errors"]
    assert peers.build_industry_data("01111.HK", [], MEMBERS, CONNECT) is None
    assert peers.build_industry_data("09988.HK", [
        pool_row("09988.HK", "阿里巴巴", 22.0, 1.6, "专业零售"),
    ], MEMBERS, CONNECT) is None
    wrong_quote = peers.build_industry_data("00700.HK", [
        pool_row("00700.HK", "腾讯控股", 13.15, 2.68, "其他行业"),
    ], MEMBERS, CONNECT)
    assert wrong_quote is not None
    assert wrong_quote.comparison_snapshot["target"]["pe_ttm"] is None


def test_fetch_sector_pool_paginates_and_returns_report_date(mocker):
    response_one = mocker.Mock()
    response_one.json.return_value = {
        "success": True,
        "result": {"count": 2, "pages": 2, "data": [pool_row("00700.HK", "腾讯", 13, 2)]},
    }
    response_two = mocker.Mock()
    response_two.json.return_value = {
        "success": True,
        "result": {"count": 2, "pages": 2, "data": [pool_row("00268.HK", "金蝶", 30, 4)]},
    }
    session = mocker.Mock()
    session.get.side_effect = [response_one, response_two]

    rows, report_date = peers.fetch_sector_pool(session, "00700.HK")

    assert len(rows) == 2
    assert report_date == "2026-10-23"
    assert session.get.call_count == 2
    assert session.get.call_args_list[1].kwargs["params"]["pageNumber"] == "2"


def test_fetch_sector_pool_keeps_dates_and_build_uses_latest_target_date(mocker):
    old = pool_row("00700.HK", "腾讯", 13, 2)
    old["REPORT_DATE"] = "2026-10-22 00:00:00"
    latest = pool_row("00700.HK", "腾讯", 13, 2)
    response = mocker.Mock()
    response.json.return_value = {
        "success": True,
        "result": {"count": 2, "pages": 1, "data": [old, latest]},
    }
    session = mocker.Mock()
    session.get.return_value = response

    rows, report_date = peers.fetch_sector_pool(session, "00700.HK")

    assert len(rows) == 2
    assert report_date == "2026-10-23"
    data = peers.build_industry_data("00700.HK", rows, MEMBERS, CONNECT)
    assert data is not None
    assert data.comparison_snapshot["target"]["as_of"] == "2026-10-23"


def test_cli_registry_registers_hk_industry_adapter():
    from stock_robot.cli import _get_registry

    sources = _get_registry().get_data_sources("hk", "industry")

    assert any(isinstance(source, peers.HKIndustryAdapter) for source in sources)


def test_adapter_supports_only_hk_industry():
    adapter = peers.HKIndustryAdapter()

    assert adapter.supports("hk", "industry") is True
    assert adapter.supports("a-shares", "industry") is False
    assert adapter.supports("hk", "valuation") is False


def test_hk_snapshot_is_public_and_cache_refresh_failure_preserves_success(monkeypatch, mocker, tmp_path):
    monkeypatch.setattr(peers, "load_hsics_members", lambda: MEMBERS)
    monkeypatch.setattr(peers, "load_connect_symbols", lambda: {s.removesuffix(".HK") for s in CONNECT})
    rows = [pool_row("00700.HK", "腾讯", 13, 2), pool_row("00268.HK", "金蝶", 30, 4),
            pool_row("00909.HK", "明源云", -5, None)]
    fetch = mocker.patch.object(peers, "fetch_sector_pool", return_value=(rows, "2026-10-23"))
    cache = CacheManager(tmp_path / "cache.db")
    adapter = peers.HKIndustryAdapter()
    first = adapter.fetch("00700.HK", comparison_cache=cache)[0]
    again = adapter.fetch("00700.HK", comparison_cache=cache)[0]
    assert first.comparison_ready
    assert again.comparison_snapshot["target"]["pe_ttm"] == 13
    assert again.comparison_snapshot["cache_status"] == "cache"
    assert fetch.call_count == 1
    fetch.side_effect = requests.Timeout("模拟刷新失败")
    stale = adapter.fetch("00700.HK", comparison_cache=cache, refresh_cache=True)[0]
    assert stale.comparison_snapshot["as_of"] == "2026-10-23"
    assert stale.comparison_snapshot["cache_status"] == "stale"
    assert stale.peers == ["00268.HK", "00909.HK"]
    assert stale.comparison_snapshot["errors"]
    assert stale.comparison_snapshot["peers"][0]["as_of"] == "2026-10-23"


def test_hk_cache_rechecks_current_connect_qualification(monkeypatch, mocker, tmp_path):
    monkeypatch.setattr(peers, "load_hsics_members", lambda: MEMBERS)
    monkeypatch.setattr(peers, "load_connect_symbols", lambda: {s.removesuffix(".HK") for s in CONNECT})
    rows = [pool_row("00700.HK", "腾讯", 13, 2), pool_row("00268.HK", "金蝶", 30, 4)]
    fetch = mocker.patch.object(peers, "fetch_sector_pool", return_value=(rows, "2026-10-23"))
    cache = CacheManager(tmp_path / "cache.db")
    adapter = peers.HKIndustryAdapter()
    adapter.fetch("00700.HK", comparison_cache=cache)
    monkeypatch.setattr(peers, "load_connect_symbols", lambda: {"00700"})
    fetch.side_effect = requests.Timeout("当前源不可用")
    data = adapter.fetch("00700.HK", comparison_cache=cache)[0]
    assert data.peers == []
    assert data.comparison_snapshot["peers"] == []


def test_hk_unqualified_target_does_not_request_pool(monkeypatch, mocker):
    monkeypatch.setattr(peers, "load_hsics_members", lambda: MEMBERS)
    monkeypatch.setattr(peers, "load_connect_symbols", set)
    fetch = mocker.patch.object(peers, "fetch_sector_pool")
    assert peers.HKIndustryAdapter().fetch("00700.HK") == []
    fetch.assert_not_called()


def test_hk_deadline_stops_before_network(mocker):
    session = mocker.Mock()
    with pytest.raises(peers.HsicsPeerError, match="预算"):
        peers.fetch_sector_pool(session, "00700.HK", deadline=time.monotonic() - 1)
    session.get.assert_not_called()


def test_hk_rows_keep_individual_dates_and_exclude_other_days(monkeypatch):
    from data.peer_comparison import PeerComparisonService
    from data.schemas import AnalysisContext

    members = {f"{i:05}.HK": member(f"{i:05}.HK", "软件服务", "Information Technology") for i in range(1, 9)}
    rows = [pool_row(s, s, 10 + i, 2) for i, s in enumerate(members)]
    rows[-2]["REPORT_DATE"] = "2026-10-22 00:00:00"
    rows[-1]["REPORT_DATE"] = ""
    data = peers.build_industry_data("00001.HK", rows, members, set(members))
    assert data is not None
    monkeypatch.setattr("data.peer_comparison.fetch_financial_rows", lambda *a, **k: ([], []))
    ctx = PeerComparisonService().enrich(AnalysisContext(symbol="00001.HK", name="目标", market="hk", industry_data=data))
    assert ctx.enriched_industry is not None
    valuation = ctx.enriched_industry.comparison["valuation"]
    assert valuation["metrics"][0]["sample_count"] == 5
    by_code = {p["symbol"]: p for p in valuation["peers"]}
    assert by_code["00007.HK"]["as_of"] == "2026-10-22"
    assert by_code["00007.HK"]["pe_ttm"] == 16
    assert by_code["00007.HK"]["reason"]
    assert by_code["00008.HK"]["as_of"] == ""
    assert by_code["00008.HK"]["reason"]


def test_hk_target_date_is_anchor_even_when_peer_has_newer_date(mocker):
    old = pool_row("00700.HK", "目标", 13, 2)
    old["REPORT_DATE"] = "2026-10-22 00:00:00"
    new_peer = pool_row("00268.HK", "同行", 20, 3)
    response = mocker.Mock()
    response.json.return_value = {"success": True, "result": {"count": 2, "pages": 1, "data": [old, new_peer]}}
    session = mocker.Mock()
    session.get.return_value = response
    rows, as_of = peers.fetch_sector_pool(session, "00700.HK")
    assert len(rows) == 2
    assert as_of == "2026-10-22"
    data = peers.build_industry_data("00700.HK", rows, MEMBERS, CONNECT)
    assert data is not None
    assert data.comparison_snapshot["as_of"] == "2026-10-22"
    assert data.comparison_snapshot["peers"][0]["as_of"] == "2026-10-23"
    assert data.comparison_snapshot["peers"][0]["reason"]


def test_hk_invalid_target_date_disables_valuation_statistics(monkeypatch):
    from data.peer_comparison import PeerComparisonService
    from data.schemas import AnalysisContext

    members = {f"{i:05}.HK": member(f"{i:05}.HK", "软件服务", "Information Technology") for i in range(1, 8)}
    rows = [pool_row(s, s, 10 + i, 2) for i, s in enumerate(members)]
    rows[0]["REPORT_DATE"] = "bad-date"
    data = peers.build_industry_data("00001.HK", rows, members, set(members))
    assert data is not None
    monkeypatch.setattr("data.peer_comparison.fetch_financial_rows", lambda *a, **k: ([], []))
    ctx = PeerComparisonService().enrich(AnalysisContext(symbol="00001.HK", name="目标", market="hk", industry_data=data))
    assert ctx.enriched_industry is not None
    assert ctx.enriched_industry.industry_median_pe is None
    assert ctx.enriched_industry.target_pe_premium is None
    valuation = ctx.enriched_industry.comparison["valuation"]
    assert valuation["as_of"] == ""
    assert valuation["metrics"][0]["percentile"] is None


def test_hk_prefers_peer_row_matching_target_date_to_newer_reference():
    target = pool_row("00700.HK", "目标", 13, 2)
    target["REPORT_DATE"] = "2026-10-22 00:00:00"
    matching_peer = pool_row("00268.HK", "同行", 20, 3)
    matching_peer["REPORT_DATE"] = "2026-10-22 00:00:00"
    newer_peer = pool_row("00268.HK", "同行", 40, 5)
    for peer_rows in ([newer_peer, matching_peer], [matching_peer, newer_peer]):
        data = peers.build_industry_data("00700.HK", [target, *peer_rows], MEMBERS, CONNECT)
        assert data is not None
        row = data.comparison_snapshot["peers"][0]
        assert row["pe_ttm"] == 20
        assert row["as_of"] == "2026-10-22"
        assert not row["reason"]


def test_hk_old_cache_without_row_dates_cannot_supply_new_comparison(monkeypatch, mocker, tmp_path):
    from data.peer_comparison_fetch import write_snapshot

    cache = CacheManager(tmp_path / "cache.db")
    old = peers.build_industry_data("00700.HK", [pool_row("00700.HK", "腾讯", 13, 2),
                                   pool_row("00268.HK", "金蝶", 30, 4)], MEMBERS, CONNECT)
    assert old is not None
    for row in old.comparison_snapshot["peers"]:
        row.pop("as_of")
    write_snapshot(cache, "peer_hk_industry", "00700.HK", old.model_dump(mode="json"), 3600)
    monkeypatch.setattr(peers, "load_hsics_members", lambda: MEMBERS)
    monkeypatch.setattr(peers, "load_connect_symbols", lambda: {s.removesuffix(".HK") for s in CONNECT})
    fetch = mocker.patch.object(peers, "fetch_sector_pool", side_effect=requests.Timeout("新源失败"))
    data = peers.HKIndustryAdapter().fetch("00700.HK", comparison_cache=cache)[0]
    assert data.comparison_ready
    assert data.comparison_snapshot["target"]["pe_ttm"] is None
    assert data.comparison_snapshot["errors"]
    fetch.assert_called_once()


def test_hk_qualified_pool_is_complete_when_quote_rows_or_target_are_missing():
    members = {f"{i:05}.HK": member(f"{i:05}.HK", "软件服务", "Information Technology") for i in range(1, 8)}
    data = peers.build_industry_data("00001.HK", [pool_row(s, s, 12, 2) for s in list(members)[1:-1]], members, set(members))
    assert data is not None
    assert data.comparison_ready
    assert set(data.peers) == set(members) - {"00001.HK"}
    by_code = {p["symbol"]: p for p in data.comparison_snapshot["peers"]}
    assert by_code["00007.HK"]["pe_ttm"] is None
    assert by_code["00007.HK"]["reason"]
    assert data.comparison_snapshot["errors"]


def test_hk_cache_only_keeps_qualified_business_pool_without_network(monkeypatch, mocker):
    monkeypatch.setattr(peers, "load_hsics_members", lambda: MEMBERS)
    monkeypatch.setattr(peers, "load_connect_symbols", lambda: {s.removesuffix(".HK") for s in CONNECT})
    fetch = mocker.patch.object(peers, "fetch_sector_pool", side_effect=AssertionError("熔断不得联网"))
    data = peers.HKIndustryAdapter().fetch("00700.HK", comparison_cache_only=True)[0]
    assert data.comparison_ready
    assert data.peers == ["00268.HK", "00909.HK"]
    assert data._comparison_deadline <= time.monotonic()
    fetch.assert_not_called()


@pytest.mark.parametrize("payload", [None, [], {"success": True, "result": []},
                                    {"success": True, "result": {"data": None}}])
def test_hk_invalid_json_shape_is_a_network_contract_error(mocker, payload):
    from data.peer_comparison_fetch import ComparisonFetchError

    session = mocker.Mock()
    session.get.return_value.json.return_value = payload
    with pytest.raises(ComparisonFetchError):
        peers.fetch_sector_pool(session, "00700.HK")


def test_hk_serial_request_obeys_wall_deadline_before_socket_idle(mocker):
    from data.peer_comparison_fetch import ComparisonFetchError

    release, finished = threading.Event(), threading.Event()
    session = mocker.Mock()
    response = mocker.Mock()

    def slow_get(*a, **k):
        release.wait(2)
        finished.set()
        return response

    session.get.side_effect = slow_get
    started = time.monotonic()
    try:
        with pytest.raises(ComparisonFetchError, match="预算"):
            peers.fetch_sector_pool(session, "00700.HK", deadline=time.monotonic() + 0.02)
        assert time.monotonic() - started < 0.5
    finally:
        release.set()
    assert finished.wait(1)


def test_hk_future_target_cannot_replace_real_target_or_rank(monkeypatch):
    from data.peer_comparison import PeerComparisonService
    from data.schemas import AnalysisContext

    members = {f"{i:05}.HK": member(f"{i:05}.HK", "软件服务", "Information Technology") for i in range(1, 8)}
    rows = [pool_row(s, s, 10 + i, 2) for i, s in enumerate(members)]
    future_target = {**rows[0], "REPORT_DATE": "2099-12-31", "PE_TTM": 999}
    data = peers.build_industry_data("00001.HK", [future_target, *rows], members, set(members))
    assert data is not None
    assert data.comparison_snapshot["target"]["as_of"] == "2026-10-23"
    assert data.comparison_snapshot["target"]["pe_ttm"] == 10
    for row in rows:
        row["REPORT_DATE"] = "2099-12-31"
    data = peers.build_industry_data("00001.HK", rows, members, set(members))
    assert data is not None
    monkeypatch.setattr("data.peer_comparison.fetch_financial_rows", lambda *a, **k: ([], []))
    ctx = PeerComparisonService().enrich(AnalysisContext(symbol="00001.HK", name="目标", market="hk", industry_data=data))
    assert ctx.enriched_industry is not None
    assert ctx.enriched_industry.industry_median_pe is None
    assert ctx.enriched_industry.target_pe_percentile is None
    assert data.comparison_snapshot["errors"]


@pytest.mark.parametrize("bad_date", ["", "2099-12-31", "bad-date"])
def test_hk_invalid_date_refresh_keeps_previous_success_cache(monkeypatch, mocker, tmp_path, bad_date):
    from data.peer_comparison_fetch import read_snapshot

    members = {f"{i:05}.HK": member(f"{i:05}.HK", "软件服务", "Information Technology") for i in range(1, 8)}
    monkeypatch.setattr(peers, "load_hsics_members", lambda: members)
    monkeypatch.setattr(peers, "load_connect_symbols", lambda: {s.removesuffix(".HK") for s in members})
    rows = [pool_row(s, s, 10 + i, 2) for i, s in enumerate(members)]
    fetch = mocker.patch.object(peers, "fetch_sector_pool", return_value=(rows, "2026-10-23"))
    cache = CacheManager(tmp_path / "cache.db")
    adapter = peers.HKIndustryAdapter()
    first = adapter.fetch("00001.HK", comparison_cache=cache)[0]
    original, _ = read_snapshot(cache, "peer_hk_industry_v2", "00001.HK", 3600)
    bad_rows = [dict(row) for row in rows]
    bad_rows[-1]["REPORT_DATE"] = bad_date
    fetch.return_value = (bad_rows, "2026-10-23")
    refreshed = adapter.fetch("00001.HK", comparison_cache=cache, refresh_cache=True)[0]
    after, _ = read_snapshot(cache, "peer_hk_industry_v2", "00001.HK", 3600)
    assert after == original
    assert refreshed.comparison_snapshot["cache_status"] == "stale"
    assert refreshed.comparison_snapshot["errors"]
    assert refreshed.comparison_snapshot["peers"] == first.comparison_snapshot["peers"]
