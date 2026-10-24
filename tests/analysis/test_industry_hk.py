"""港股恒生 HSICS 行业分析四态测试。"""
import analysis.industry as industry_module
from analysis.industry import IndustryAnalyzer
from data.hk_hsics_industry import HsicsMember
from data.schemas import AnalysisContext, EnrichedIndustry, IndustryData

MAPPED = {"00700.HK": HsicsMember(
    "00700.HK", "软件服务", "Information Technology", "mapped", True)}


def mapped_context() -> AnalysisContext:
    context = AnalysisContext(symbol="00700.HK", name="腾讯", market="hk")
    context.industry_data = IndustryData(
        symbol="00700.HK",
        industry="软件服务",
        sector="Information Technology",
        peers=["00268.HK"],
        peer_names={"00268.HK": "金蝶国际"},
        peer_scope="港股通·恒生HSICS二级",
        peer_industry="软件服务",
    )
    context.industry_data._data_date = "2026-10-23"
    context.industry_data._target_pe = 13.15
    context.industry_data._target_pb = 2.68
    context.enriched_industry = EnrichedIndustry(
        peer_count=5,
        industry_median_pe=30.0,
        industry_median_pb=3.0,
        target_pe_premium=-56.2,
        target_pe_percentile=20.0,
        target_pb_percentile=40.0,
        peer_pe_sample=5,
        peer_pb_sample=5,
    )
    return context


def test_hk_industry_unavailable_when_snapshot_missing(monkeypatch):
    monkeypatch.setattr(industry_module, "load_connect_symbols", lambda: {"00700"})
    monkeypatch.setattr(industry_module, "load_hsics_members", dict)

    result = IndustryAnalyzer().analyze(
        AnalysisContext(symbol="00700.HK", name="腾讯", market="hk"))

    assert result.status == "unavailable"
    assert "快照" in result.summary


def test_hk_industry_rejects_non_connect(monkeypatch):
    monkeypatch.setattr(industry_module, "load_connect_symbols", lambda: {"00001"})
    monkeypatch.setattr(industry_module, "load_hsics_members", lambda: MAPPED)

    result = IndustryAnalyzer().analyze(mapped_context())

    assert result.status == "unavailable"
    assert "非港股通" in result.summary


def test_hk_industry_reports_pending_mapping(monkeypatch):
    monkeypatch.setattr(industry_module, "load_connect_symbols", lambda: {"00700"})
    monkeypatch.setattr(industry_module, "load_hsics_members", lambda: {
        "00700.HK": HsicsMember(
            "00700.HK", "软件服务", "Information Technology", "missing", True),
    })

    result = IndustryAnalyzer().analyze(mapped_context())

    assert result.status == "unavailable"
    assert "待补齐" in result.summary


def test_hk_industry_unavailable_when_pool_data_missing(monkeypatch):
    monkeypatch.setattr(industry_module, "load_connect_symbols", lambda: {"00700"})
    monkeypatch.setattr(industry_module, "load_hsics_members", lambda: MAPPED)
    context = AnalysisContext(symbol="00700.HK", name="腾讯", market="hk")

    result = IndustryAnalyzer().analyze(context)

    assert result.status == "unavailable"
    assert "行业数据不可用" in result.summary


def test_hk_industry_compares_mapped_connect(monkeypatch):
    monkeypatch.setattr(industry_module, "load_connect_symbols", lambda: {"00700"})
    monkeypatch.setattr(industry_module, "load_hsics_members", lambda: MAPPED)

    result = IndustryAnalyzer().analyze(mapped_context())

    assert result.status == "ok"
    assert result.score is None
    assert "恒生" in result.summary and "软件服务" in result.summary
    assert result.metrics["classification_scheme"] == "恒生HSICS"
    assert result.metrics["industry_data_date"] == "2026-10-23"
    assert result.metrics["peer_count"] == 5
    assert result.metrics["industry_median_pe"] == 30.0
    assert result.metrics["target_pe"] == 13.15
    assert result.metrics["target_pb"] == 2.68
    assert "peer_display_note" not in result.metrics
    assert "东财F10" in result.industry_note


def test_hk_industry_displays_only_first_ten_peers_but_keeps_full_pool_metrics(monkeypatch):
    monkeypatch.setattr(industry_module, "load_connect_symbols", lambda: {"00700"})
    monkeypatch.setattr(industry_module, "load_hsics_members", lambda: MAPPED)
    context = mapped_context()
    peers = [f"{index:05d}.HK" for index in range(1, 13)]
    industry_data = context.industry_data
    assert industry_data is not None
    industry_data.peers = peers
    industry_data.peer_names = {symbol: f"同行{index}" for index, symbol in enumerate(peers, 1)}
    context.enriched_industry = EnrichedIndustry(
        peer_count=12, industry_median_pe=30.0, industry_median_pb=3.0,
        target_pe_premium=-56.2, target_pe_percentile=20.0, target_pb_percentile=40.0,
        peer_pe_sample=12, peer_pb_sample=12,
    )

    result = IndustryAnalyzer().analyze(context)

    assert result.metrics["peers"] == peers[:10]
    assert result.metrics["peer_names"] == {symbol: f"同行{index}" for index, symbol in enumerate(peers[:10], 1)}
    assert result.metrics["peer_total"] == 12
    assert result.metrics["peer_display_note"] == "仅展示前10家，统计基于全部12家港股通同行"
    assert result.metrics["peer_count"] == 12
    assert result.metrics["industry_median_pe"] == 30.0


def test_hk_industry_partial_when_peers_insufficient(monkeypatch):
    monkeypatch.setattr(industry_module, "load_connect_symbols", lambda: {"00700"})
    monkeypatch.setattr(industry_module, "load_hsics_members", lambda: MAPPED)
    context = mapped_context()
    context.enriched_industry = EnrichedIndustry(
        peer_count=2, industry_median_pe=30.0, industry_median_pb=3.0,
        peer_pe_sample=2, peer_pb_sample=2,
    )

    result = IndustryAnalyzer().analyze(context)

    assert result.status == "partial"
    assert "不足" in result.summary
    assert result.score is None
