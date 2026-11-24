"""营收协议审核状态门禁。"""
def test_operating_revenue_flag_matches_reviewed_protocol():
    from data.us_operating import REVENUE_PROTOCOL_VERIFIED, build_operating

    def quarter(start, end, revenue, profit):
        return {"SECURITY_CODE": "AAPL", "REPORT_DATE": end, "START_DATE": start,
                "DATE_TYPE": "单季报", "CURRENCY_ABBR": "USD",
                "OPERATE_INCOME": revenue, "PARENT_HOLDER_NETPROFIT": profit,
                "NOTICE_DATE": end}

    rows = [
        quarter("2025-10-01", "2025-12-31", 100, 10),
        quarter("2026-01-01", "2026-03-31", 110, 11),
        quarter("2026-04-01", "2026-06-30", 120, 12),
        quarter("2026-07-01", "2026-09-30", 130, 13),
    ]
    result = build_operating(rows, "AAPL", "RPT_USF10_FN_GMAININDICATOR")
    assert result is not None
    assert result["revenue_verified"] is REVENUE_PROTOCOL_VERIFIED


def test_unverified_revenue_never_enters_industry_score():
    from analysis.industry_scoring import score_industry_comparison
    from data.enrichers.us_industry_enricher import UsIndustryEnricher
    from data.schemas import AnalysisContext, IndustryData
    from data.us_operating import GENERAL, build_operating
    from tests.data.test_us_operating import rows
    operating = build_operating(rows(), "TEST", GENERAL)
    assert operating is not None
    operating["revenue_verified"] = False
    quotes = {f"P{i}": {"symbol": f"P{i}", "basis_verified": True, "market_cap": 100, "pe": 10, "pb": 2} for i in range(5)}
    context = AnalysisContext(symbol="TEST", name="测试", market="us", industry_data=IndustryData(
        symbol="TEST", industry="软件", sector="", peers=list(quotes), comparison_snapshot={
            "target": {"basis_verified": True, "market_cap": 100, "pe": 10, "pb": 2},
            "quotes": quotes, "operating": {"TEST": operating, **dict.fromkeys(quotes, operating)}}))
    UsIndustryEnricher().enrich(context)
    assert context.enriched_industry is not None
    assert score_industry_comparison(context.enriched_industry.comparison)[0] is None


def test_revenue_protocol_flags_are_enabled():
    from data.us_operating import (
        REVENUE_PROTOCOL_VERIFIED_BANK,
        REVENUE_PROTOCOL_VERIFIED_GENERAL,
    )
    assert REVENUE_PROTOCOL_VERIFIED_GENERAL is True
    assert REVENUE_PROTOCOL_VERIFIED_BANK is True


def test_operating_rows_mark_revenue_verified():
    from data.us_operating import build_operating

    def quarter(start, end, revenue, profit):
        return {"SECURITY_CODE": "AAPL", "REPORT_DATE": end, "START_DATE": start,
                "DATE_TYPE": "单季报", "CURRENCY_ABBR": "USD",
                "OPERATE_INCOME": revenue, "PARENT_HOLDER_NETPROFIT": profit,
                "NOTICE_DATE": end}

    rows = [
        quarter("2025-10-01", "2025-12-31", 100, 10),
        quarter("2026-01-01", "2026-03-31", 110, 11),
        quarter("2026-04-01", "2026-06-30", 120, 12),
        quarter("2026-07-01", "2026-09-30", 130, 13),
    ]
    result = build_operating(rows, "AAPL", "RPT_USF10_FN_GMAININDICATOR")
    assert result is not None
    assert result["revenue_verified"] is True
    assert "SEC" in result["revenue_basis"]
