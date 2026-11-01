"""港股恒生二级行业充实器测试。"""
import pytest

from data.enrichers.hk_industry_enricher import HKIndustryEnricher
from data.enrichers.industry_enricher import IndustryEnricher
from data.hk_hsics_industry import HsicsMember
from data.hk_hsics_peers import build_industry_data
from data.schemas import AnalysisContext, SufficiencyLevel


def context_with_peers(count: int) -> AnalysisContext:
    peers = [f"0000{index}.HK" for index in range(1, count + 1)]
    members = {
        "00700.HK": HsicsMember("00700.HK", "软件服务", "Information Technology", "mapped", True),
        **{
            symbol: HsicsMember(symbol, "软件服务", "Information Technology", "mapped", True)
            for symbol in peers
        },
    }
    connect = {"00700.HK", *peers}
    rows = [{
        "CORRE_SECUCODE": "00700.HK", "CORRE_SECURITY_NAME": "腾讯",
        "PE_TTM": 13.15, "PB_MQR": 2.68,
        "TYPE_NAME": "软件服务", "REPORT_DATE": "2026-10-23 00:00:00",
    }]
    for index, symbol in enumerate(peers, 1):
        rows.append({
            "CORRE_SECUCODE": symbol, "CORRE_SECURITY_NAME": f"同行{index}",
            "PE_TTM": index * 10, "PB_MQR": float(index),
            "TYPE_NAME": "软件服务", "REPORT_DATE": "2026-10-23 00:00:00",
        })
    context = AnalysisContext(symbol="00700.HK", name="腾讯", market="hk")
    context.industry_data = build_industry_data("00700.HK", rows, members, connect)
    return context


def build_context() -> AnalysisContext:
    return context_with_peers(5)


@pytest.mark.parametrize(("count", "expected"), [
    (2, SufficiencyLevel.INSUFFICIENT),
    (3, SufficiencyLevel.PARTIAL),
    (8, SufficiencyLevel.SUFFICIENT),
])
def test_hk_enricher_peer_count_boundaries(count, expected):
    result = HKIndustryEnricher().enrich(context_with_peers(count))

    assert result.sufficiency.industry.level == expected


def test_hk_enricher_computes_medians_premium_and_percentiles():
    context = build_context()

    result = HKIndustryEnricher().enrich(context)

    enriched = result.enriched_industry
    assert enriched is not None
    assert enriched.peer_count == 5
    assert enriched.industry_median_pe == 30.0
    assert enriched.industry_median_pb == 3.0
    assert enriched.target_pe_premium == -56.2
    assert enriched.target_pe_percentile == 20.0
    assert enriched.target_pb_percentile == 40.0
    assert enriched.peer_pe_sample == 5
    assert enriched.peer_pb_sample == 5
    assert result.sufficiency.industry.level == SufficiencyLevel.PARTIAL


def test_hk_enricher_excludes_nonpositive_and_missing_values():
    context = build_context()
    data = context.industry_data
    assert data is not None
    data._peer_valuations["00001.HK"].pe_ttm = -10.0
    data._peer_valuations["00002.HK"].pb = None

    result = HKIndustryEnricher().enrich(context)

    enriched = result.enriched_industry
    assert enriched is not None
    assert enriched.peer_pe_sample == 4
    assert enriched.peer_pb_sample == 4
    assert enriched.industry_median_pe == 35.0


def test_a_share_industry_enricher_does_not_touch_hk_context():
    context = build_context()

    result = IndustryEnricher().enrich(context)

    assert result.enriched_industry is None
