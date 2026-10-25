"""年度 ROE 必须有完整财政年证据，补取复用既有缓存和预算。"""
import time
from datetime import date
from unittest.mock import Mock

import pytest

from data.cache import CacheManager
from data.financial_scoring_facts import FinancialScoringFactsService
from data.schemas import AnalysisContext, EnrichedIndustry, FinancialData, IndustryData


def financials(ctx: AnalysisContext) -> list[FinancialData]:
    assert ctx.financial_data is not None
    return ctx.financial_data


def context(**values):
    defaults = {"symbol": "09988.HK", "fiscal_quarter": date(2026, 3, 31),
                    "period_start": date(2025, 4, 1), "period_basis": "cumulative",
                    "reporting_basis": "合并报告期", "financial_source": "东方财富港股财报"}
    return AnalysisContext(symbol="09988.HK", name="样例", market="hk",
                           financial_data=[FinancialData.model_validate(defaults | values)])


def test_explicit_fiscal_year_wins_and_leaves_latest_snapshot(mocker):
    fetch = mocker.patch("data.financial_scoring_facts.fetch_financial_rows")
    ctx = context(roe=.12)
    financials(ctx).append(FinancialData(symbol=ctx.symbol, fiscal_quarter=date(2026, 9, 30), roe=.08))
    FinancialScoringFactsService().enrich(ctx)
    assert ctx.financial_scoring_facts["annual_roe"]["value"] == .12
    assert ctx.financial_scoring_facts["annual_roe"]["period_end"] == "2026-03-31"
    assert financials(ctx)[-1].roe == .08
    fetch.assert_not_called()


def test_comparison_annual_target_reuses_percent_units_without_peer_requirement(mocker):
    fetch = mocker.patch("data.financial_scoring_facts.fetch_financial_rows")
    ctx = context()
    ctx.enriched_industry = EnrichedIndustry(comparison={"periods": {"annual": {
        "start": "2025-04-01", "end": "2026-03-31", "source": "东财主要财务指标",
        "basis": "累计·平均权益ROE（未年化）", "reason": "", "metrics": [
            {"key": "roe", "unit": "percent", "target": 12, "sample_count": 0, "status": "insufficient"}]}}})
    FinancialScoringFactsService().enrich(ctx)
    assert ctx.financial_scoring_facts["annual_roe"]["value"] == .12
    fetch.assert_not_called()


@pytest.mark.parametrize("values", [{"period_start": None}, {"period_basis": "unknown"},
    {"period_start": date(2025, 10, 1)}, {"fiscal_quarter": date(2030, 3, 31)},
    {"financial_source": "", "reporting_basis": ""}])
def test_unknown_future_and_nonannual_not_accepted(mocker, values):
    mocker.patch("data.financial_scoring_facts.fetch_financial_rows", return_value=([], []))
    ctx = context(roe=.12, **values)
    FinancialScoringFactsService().enrich(ctx)
    assert ctx.financial_scoring_facts["annual_roe"]["value"] is None
    assert ctx.financial_scoring_facts["annual_roe"]["reason"]


def test_conflicting_duplicate_year_skipped(mocker):
    mocker.patch("data.financial_scoring_facts.fetch_financial_rows", return_value=([], []))
    ctx = context(roe=.12)
    financials(ctx).append(financials(ctx)[0].model_copy(update={"roe": .2}))
    FinancialScoringFactsService().enrich(ctx)
    assert ctx.financial_scoring_facts["annual_roe"]["value"] is None


def test_fallback_keeps_cache_refresh_and_original_deadline(mocker):
    fetch = mocker.patch("data.financial_scoring_facts.fetch_financial_rows", return_value=([
        {"symbol": "09988.HK", "start": "2025-04-01", "end": "2026-03-31", "annual": True,
             "period_basis": "cumulative", "basis": "累计·平均权益ROE（未年化）", "roe": 12, "reason": ""}], []))
    ctx = context()
    ctx.industry_data = IndustryData(symbol=ctx.symbol, industry="软件服务", sector="Information Technology")
    ctx.industry_data._comparison_deadline = time.monotonic() - 1
    cache = Mock()
    FinancialScoringFactsService(cache).enrich(ctx, refresh=True)
    assert ctx.financial_scoring_facts["annual_roe"]["value"] == .12
    assert fetch.call_args.args == ([ctx.symbol], "hk")
    assert fetch.call_args.kwargs["cache"] is cache
    assert fetch.call_args.kwargs["refresh"] is True
    assert fetch.call_args.kwargs["deadline"] == ctx.industry_data._comparison_deadline


def test_legacy_fake_financials_do_not_start_network(mocker):
    fetch = mocker.patch("data.financial_scoring_facts.fetch_financial_rows")
    ctx = AnalysisContext(symbol="600519", name="样例", financial_data=[
        FinancialData(symbol="600519", fiscal_quarter=date(2025, 12, 31), roe=.3)])
    FinancialScoringFactsService().enrich(ctx)
    fetch.assert_not_called()


def test_target_only_main_indicator_cache_reused_even_after_deadline(mocker, tmp_path):
    response = Mock()
    response.json.return_value = {"success": True, "result": {"pages": 1, "count": 1, "data": [
        {"SECUCODE": "09988.HK", "REPORT_DATE": "2026-03-31", "START_DATE": "2025-04-01",
         "DATE_TYPE_CODE": "001", "ROE_AVG": 12}]}}
    request = mocker.patch("data.peer_comparison_fetch._response", return_value=response)
    cache = CacheManager(tmp_path / "cache.db")
    first, second = context(), context()
    service = FinancialScoringFactsService(cache)
    service.enrich(first)
    second.industry_data = IndustryData(symbol=second.symbol, industry="软件服务", sector="Information Technology")
    second.industry_data._comparison_deadline = time.monotonic() - 1
    service.enrich(second)
    assert first.financial_scoring_facts == second.financial_scoring_facts
    assert first.financial_scoring_facts["annual_roe"]["value"] == .12
    assert request.call_count == 1


def test_latest_complete_year_selected_and_interim_never_annualized(mocker):
    mocker.patch("data.financial_scoring_facts.fetch_financial_rows", return_value=([], []))
    ctx = context(roe=.12)
    financials(ctx).extend([
        financials(ctx)[0].model_copy(update={"period_start": date(2024, 4, 1),
            "fiscal_quarter": date(2025, 3, 31), "roe": .9}),
        financials(ctx)[0].model_copy(update={"period_start": date(2026, 4, 1),
            "fiscal_quarter": date(2026, 9, 30), "roe": .1})])
    FinancialScoringFactsService().enrich(ctx)
    assert ctx.financial_scoring_facts["annual_roe"]["value"] == .12


def test_target_fetch_failure_is_isolated_without_fake_zero(mocker, caplog):
    mocker.patch("data.financial_scoring_facts.fetch_financial_rows", side_effect=RuntimeError("down"))
    ctx = context()
    FinancialScoringFactsService().enrich(ctx)
    assert ctx.financial_scoring_facts["annual_roe"]["value"] is None
    assert "RuntimeError" in ctx.financial_scoring_facts["annual_roe"]["reason"]
    assert "年度 ROE 补取失败" in caplog.text


def test_same_end_with_conflicting_fiscal_year_starts_is_not_arbitrarily_selected(mocker):
    mocker.patch("data.financial_scoring_facts.fetch_financial_rows", return_value=([], []))
    ctx = context(roe=.12)
    financials(ctx).append(financials(ctx)[0].model_copy(update={"period_start": date(2025, 3, 31), "roe": .3}))
    FinancialScoringFactsService().enrich(ctx)
    assert ctx.financial_scoring_facts["annual_roe"]["value"] is None


def annual_comparison(value: float | None = 18, end="2025-12-31", start="2025-01-01"):
    return EnrichedIndustry(comparison={"periods": {"annual": {
        "start": start, "end": end, "source": "东财主要财务指标", "basis": "累计·平均权益ROE（未年化）",
        "reason": "", "metrics": [{"key": "roe", "unit": "percent", "target": value}]}}})


def test_newer_comparison_year_wins_over_older_reported_roe(mocker):
    fetch = mocker.patch("data.financial_scoring_facts.fetch_financial_rows")
    ctx = context(roe=.03, period_start=date(2024, 1, 1), fiscal_quarter=date(2024, 12, 31))
    ctx.enriched_industry = annual_comparison()
    FinancialScoringFactsService().enrich(ctx)
    fact = ctx.financial_scoring_facts["annual_roe"]
    assert fact["period_end"] == "2025-12-31"
    assert fact["value"] == .18
    fetch.assert_not_called()


def test_same_year_reported_roe_retains_source_priority(mocker):
    fetch = mocker.patch("data.financial_scoring_facts.fetch_financial_rows")
    ctx = context(roe=.13, period_start=date(2025, 1, 1), fiscal_quarter=date(2025, 12, 31))
    ctx.enriched_industry = annual_comparison()
    FinancialScoringFactsService().enrich(ctx)
    assert ctx.financial_scoring_facts["annual_roe"]["value"] == .13
    fetch.assert_not_called()


@pytest.mark.parametrize("newer_source", ["reported", "comparison", "fetched"])
def test_known_newer_year_without_roe_does_not_score_an_older_year(mocker, newer_source):
    rows = []
    fetch = mocker.patch("data.financial_scoring_facts.fetch_financial_rows", return_value=(rows, []))
    ctx = context(roe=.03, period_start=date(2024, 1, 1), fiscal_quarter=date(2024, 12, 31))
    if newer_source == "reported":
        financials(ctx).append(financials(ctx)[0].model_copy(update={
            "roe": None, "period_start": date(2025, 1, 1), "fiscal_quarter": date(2025, 12, 31)}))
    else:
        ctx.enriched_industry = annual_comparison(None)
        if newer_source == "fetched":
            rows.extend([
                {"symbol": ctx.symbol, "start": "2025-01-01", "end": "2025-12-31", "annual": True,
                 "period_basis": "cumulative", "basis": "累计·平均权益ROE（未年化）", "roe": None, "reason": ""},
                {"symbol": ctx.symbol, "start": "2024-01-01", "end": "2024-12-31", "annual": True,
                 "period_basis": "cumulative", "basis": "累计·平均权益ROE（未年化）", "roe": 3, "reason": ""}])
    FinancialScoringFactsService().enrich(ctx)
    fact = ctx.financial_scoring_facts["annual_roe"]
    assert fact["value"] is None
    assert fact["period_end"] == "2025-12-31"
    assert "2024-12-31" in fact["reason"] and "仅供参考" in fact["reason"]
    assert fetch.call_count == 1


@pytest.mark.parametrize("source", ["同花顺财报", "新浪财报", "东方财富财报"])
def test_validated_a_reported_roe_describes_weighted_equity_basis(mocker, source):
    mocker.patch("data.financial_scoring_facts.fetch_financial_rows")
    ctx = AnalysisContext(symbol="600519", name="样例", financial_data=[FinancialData(
        symbol="600519", fiscal_quarter=date(2025, 12, 31), period_start=date(2025, 1, 1),
        period_basis="cumulative", reporting_basis=f"{source}-合并累计报告期", financial_source=source, roe=.15)])
    FinancialScoringFactsService().enrich(ctx)
    assert "加权权益ROE" in ctx.financial_scoring_facts["annual_roe"]["basis"]


def test_unknown_reported_roe_basis_is_not_labeled_average_or_weighted(mocker):
    mocker.patch("data.financial_scoring_facts.fetch_financial_rows")
    ctx = context(roe=.12, financial_source="未知测试来源")
    FinancialScoringFactsService().enrich(ctx)
    assert "权益口径未记录" in ctx.financial_scoring_facts["annual_roe"]["basis"]
