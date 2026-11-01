"""同行比较统计、缓存和批量采集的边界回归。"""
import json
from datetime import date

import pytest

from data.base import CollectionResult
from data.collection_state import make_record
from data.peer_comparison import PeerComparisonService
from data.peer_comparison_rules import (
    metric_summary,
    normalize_financial_rows,
    period_comparison,
)
from data.schemas import AnalysisContext, EnrichedIndustry, IndustryData


def test_metric_requires_five_excludes_target_and_keeps_negative_business_values():
    peers = [{"symbol": str(i), "roe": i - 3} for i in range(6)]
    peers.append({"symbol": "target", "roe": 999})
    result = metric_summary("roe", -1, peers, "target")
    assert result["sample_count"] == 6
    assert result["median"] == -0.5
    assert result["percentile"] == pytest.approx(100 * 2.5 / 6)
    assert result["unit"] == "percent"
    assert metric_summary("roe", 0, peers[:4], "target")["median"] is None


def test_valuation_filters_independently_and_uses_midrank():
    peers = [{"symbol": str(i), "pe_ttm": 10, "pb": -1} for i in range(5)]
    peers += [{"symbol": "bad", "pe_ttm": float("inf"), "pb": 2}]
    assert metric_summary("pe_ttm", 10, peers, "target")["percentile"] == 50
    assert metric_summary("pb", 2, peers, "target")["sample_count"] == 1
    assert metric_summary("pe_ttm", -10, peers, "target")["percentile"] is None


def test_hk_matching_requires_start_end_and_report_basis():
    raw = [{"SECUCODE": f"{i:05}.HK", "START_DATE": "2026-01-01",
            "REPORT_DATE": "2026-06-30", "DATE_TYPE_CODE": "002",
            "OPERATE_INCOME_YOY": -12.5, "NET_PROFIT_RATIO": 8, "ROE_AVG": 3}
           for i in range(1, 8)]
    raw[-1]["START_DATE"] = "2026-04-01"
    rows = normalize_financial_rows(raw, "hk")
    result = period_comparison("latest", rows, "00001.HK", [f"{i:05}.HK" for i in range(2, 8)])
    assert result["metrics"][0]["sample_count"] == 5
    assert result["metrics"][0]["target"] == -12.5
    assert result["peers"][-1]["reason"]
    assert result["peers"][-1]["start"] == "2026-04-01"


def test_annual_uses_full_fiscal_year_and_latest_uses_known_target_date():
    raw = [{"SECUCODE": "09988.HK", "START_DATE": "2025-04-01", "REPORT_DATE": "2026-03-31",
            "DATE_TYPE_CODE": "001", "ROE_AVG": 8},
           {"SECUCODE": "09988.HK", "START_DATE": "2026-04-01", "REPORT_DATE": "2026-06-30",
            "DATE_TYPE_CODE": "003", "ROE_AVG": 2}]
    rows = normalize_financial_rows(raw, "hk")
    assert period_comparison("annual", rows, "09988.HK", [])["start"] == "2025-04-01"
    result = period_comparison("latest", rows, "09988.HK", [], expected_latest="2026-09-30")
    assert "未取得" in result["reason"]
    assert result["metrics"][2]["target"] is None
    assert "最新" not in result["label"]


def test_future_financial_period_cannot_anchor_or_rank():
    rows = normalize_financial_rows([{"SECURITY_CODE": "600519", "REPORT_DATE": "2099-12-31",
                                      "REPORT_TYPE": "年报", "ROEJQ": 20}], "a-shares")
    result = period_comparison("latest", rows, "600519", [])
    assert result["end"] == ""
    assert result["metrics"][2]["target"] is None


def test_unverified_pool_does_not_fetch_or_reuse_old_statistics(monkeypatch):
    monkeypatch.setattr("data.peer_comparison.fetch_financial_rows", lambda *a, **k: pytest.fail("旧池不得联网"))
    ctx = AnalysisContext(symbol="600519", name="贵州茅台", industry_data=IndustryData(
        symbol="600519", industry="白酒", sector="食品饮料", peers=["000858"]),
        enriched_industry=EnrichedIndustry(industry_median_pe=20, target_pe_premium=-30))
    PeerComparisonService().enrich(ctx)
    assert ctx.enriched_industry is not None
    assert ctx.enriched_industry.industry_median_pe is None
    assert ctx.enriched_industry.target_pe_premium is None


def test_public_snapshot_survives_model_json_roundtrip():
    data = IndustryData(symbol="600519", industry="白酒Ⅲ", sector="食品饮料",
                        comparison_ready=True, comparison_snapshot={"target": {"pe_ttm": 10}})
    restored = IndustryData.model_validate(json.loads(data.model_dump_json()))
    assert restored.comparison_ready
    assert restored.comparison_snapshot["target"]["pe_ttm"] == 10


def test_a_annual_and_latest_preserve_percent_points():
    raw = [{"SECURITY_CODE": "600519", "REPORT_DATE": "2025-12-31", "REPORT_TYPE": "年报",
            "TOTALOPERATEREVETZ": 12.5, "XSJLL": -3, "ROEJQ": 20},
           {"SECURITY_CODE": "600519", "REPORT_DATE": "2026-09-30", "REPORT_TYPE": "三季报",
            "TOTALOPERATEREVETZ": 0, "XSJLL": 2, "ROEJQ": 10}]
    rows = normalize_financial_rows(raw, "a-shares")
    assert period_comparison("annual", rows, "600519", [])["metrics"][0]["target"] == 12.5
    assert period_comparison("latest", rows, "600519", [])["end"] == "2026-09-30"
    assert rows[1]["start"] == "2026-01-01"


def test_invalid_report_date_does_not_become_comparable():
    rows = normalize_financial_rows([{"SECURITY_CODE": "600519", "REPORT_DATE": "bad", "ROEJQ": 10}], "a-shares")
    assert rows == []


def test_latest_does_not_claim_annual_when_newer_financial_is_known(monkeypatch):
    from data.schemas import FinancialData
    monkeypatch.setattr("data.peer_comparison.fetch_financial_rows", lambda *a, **k: CollectionResult([{
        "symbol": "600519", "start": "2025-01-01", "end": "2025-12-31",
        "basis": "累计·加权ROE", "annual": True, "roe": 20}]))
    ctx = AnalysisContext(symbol="600519", name="贵州茅台", financial_data=[
        FinancialData(symbol="600519", fiscal_quarter=date(2026, 6, 30))],
        industry_data=IndustryData(symbol="600519", industry="白酒Ⅲ", sector="食品饮料",
                                  peer_scope="申万三级", comparison_ready=True))
    PeerComparisonService().enrich(ctx)
    assert ctx.enriched_industry is not None
    assert "未取得" in ctx.enriched_industry.comparison["periods"]["latest"]["reason"]


def test_service_uses_full_pool_same_snapshot_for_valuation_and_compatibility(monkeypatch):
    peers = [{"symbol": str(i), "pe_ttm": i + 5, "pb": i + 1, "as_of": "2026-10-23"} for i in range(8)]
    data = IndustryData(symbol="600519", industry="白酒Ⅲ", sector="食品饮料", peer_scope="申万三级",
                        comparison_ready=True, peers=[p["symbol"] for p in peers] + ["600519"],
                        comparison_snapshot={"target": {"symbol": "600519", "pe_ttm": 17, "pb": -1, "as_of": "2026-10-23"},
                                             "as_of": "2026-10-23", "peers": peers})
    monkeypatch.setattr("data.peer_comparison.fetch_financial_rows", lambda *a, **k: CollectionResult([], record=make_record("测试源", "failed"), errors=["经营源失败"]))
    ctx = PeerComparisonService().enrich(AnalysisContext(symbol="600519", name="目标", industry_data=data))
    assert ctx.collection_records["peer_financial"].source == "测试源"
    result = ctx.enriched_industry
    assert result is not None
    assert result.peer_count == 8
    assert result.peer_pe_sample == 8
    assert result.industry_median_pe == 8.5
    assert result.target_pe_premium == 100
    assert result.target_pe_percentile == 100
    assert result.industry_median_pb == 4.5
    assert result.target_pb_percentile is None
    assert result.comparison["errors"] == ["经营源失败"]


def test_bank_net_margin_keeps_raw_values_but_does_not_compare(monkeypatch):
    symbols = [f"{i:05}.HK" for i in range(1, 8)]
    rows = normalize_financial_rows([
        {"SECUCODE": symbol, "START_DATE": "2025-01-01", "REPORT_DATE": "2025-12-31",
         "DATE_TYPE_CODE": "001", "NET_PROFIT_RATIO": 25, "ROE_AVG": 12, "OPERATE_INCOME_YOY": 4}
        for symbol in symbols], "hk")
    monkeypatch.setattr("data.peer_comparison.fetch_financial_rows", lambda *a, **k: CollectionResult(rows))
    data = IndustryData(symbol=symbols[0], industry="银行", sector="Financials", peer_scope="港股通·恒生HSICS二级",
                        comparison_ready=True, peers=symbols[1:])
    ctx = PeerComparisonService().enrich(AnalysisContext(symbol=symbols[0], name="银行", market="hk", industry_data=data))
    assert ctx.enriched_industry is not None
    metrics = ctx.enriched_industry.comparison["periods"]["annual"]["metrics"]
    assert metrics[1]["target"] == 25
    assert metrics[1]["median"] is None
    assert "未验证" in metrics[1]["reason"]
    assert metrics[2]["median"] == 12


def test_single_quarter_cannot_be_replaced_with_cumulative_peer_ratios():
    rows = normalize_financial_rows([{"SECURITY_CODE": "600519", "REPORT_DATE": "2026-09-30",
                                      "REPORT_TYPE": "三季报", "ROEJQ": 20}], "a-shares")
    result = period_comparison("latest", rows, "600519", [], expected_latest="2026-09-30", expected_basis="single")
    assert result["metrics"][2]["target"] is None
    assert "单季" in result["reason"]


def test_service_checks_target_identity_and_per_row_dates_even_for_ready_snapshots(monkeypatch):
    monkeypatch.setattr("data.peer_comparison.fetch_financial_rows", lambda *a, **k: CollectionResult([]))
    peers = [{"symbol": str(i), "pe_ttm": i + 5, "pb": i + 1, "as_of": "2026-10-23"} for i in range(5)]
    data = IndustryData(symbol="600519", industry="白酒Ⅲ", sector="食品饮料", peer_scope="申万三级",
                        comparison_ready=True, peers=[p["symbol"] for p in peers],
                        comparison_snapshot={"target": {"symbol": "000858", "pe_ttm": 17, "as_of": "2026-10-23"},
                                             "as_of": "2026-10-23", "peers": peers})
    ctx = PeerComparisonService().enrich(AnalysisContext(symbol="600519", name="目标", industry_data=data))
    assert ctx.enriched_industry is not None
    assert ctx.enriched_industry.industry_median_pe is None
    assert any("身份" in e for e in ctx.enriched_industry.comparison["errors"])
    data.comparison_snapshot["target"]["symbol"] = "600519"
    data.comparison_snapshot["peers"][0]["as_of"] = ""
    ctx = PeerComparisonService().enrich(ctx)
    assert ctx.enriched_industry is not None
    assert ctx.enriched_industry.peer_pe_sample == 4
    assert ctx.enriched_industry.industry_median_pe is None
    assert ctx.enriched_industry.comparison["valuation"]["peers"][0]["pe_ttm"] == 5


def test_service_rejects_future_valuation_from_preexisting_public_cache(monkeypatch):
    monkeypatch.setattr("data.peer_comparison.fetch_financial_rows", lambda *a, **k: CollectionResult([]))
    peers = [{"symbol": str(i), "pe_ttm": i + 5, "pb": i + 1, "as_of": "2099-12-31"} for i in range(5)]
    data = IndustryData(symbol="600519", industry="白酒Ⅲ", sector="食品饮料", peer_scope="申万三级",
                        comparison_ready=True, peers=[p["symbol"] for p in peers],
                        comparison_snapshot={"target": {"symbol": "600519", "pe_ttm": 17, "as_of": "2099-12-31"},
                                             "as_of": "2099-12-31", "peers": peers})
    ctx = PeerComparisonService().enrich(AnalysisContext(symbol="600519", name="目标", industry_data=data))
    assert ctx.enriched_industry is not None
    assert ctx.enriched_industry.industry_median_pe is None
    assert ctx.enriched_industry.target_pe_premium is None
    assert ctx.enriched_industry.target_pe_percentile is None
