"""多期经营趋势只生成事实，不改变评分或增加模型调用。"""
from datetime import date

import pytest

from analysis.financial_trends import build_financial_trends
from data.schemas import FinancialData


def period(day, **values):
    ending = date.fromisoformat(day)
    defaults = {"symbol": "688326", "fiscal_quarter": ending,
                    "period_start": date(ending.year, 1, 1), "period_basis": "cumulative",
                    "reporting_basis": "同花顺报告期原币", "report_currency": "CNY"}
    return FinancialData(**(defaults | values))


def text(rows):
    return "；".join(build_financial_trends(rows, "688326")["highlights"])


def test_same_period_three_year_trend_not_cumulative_seasonality():
    rows = [period("2026-06-30", revenue=300), period("2025-06-30", revenue=200),
            period("2024-06-30", revenue=100), period("2026-03-31", revenue=90)]
    actual = text(rows)
    assert "连续增长" in actual and "2024-06-30" in actual
    assert "2026-03-31" not in actual


def test_recent_period_yoy_acceleration_is_not_single_quarter():
    rows = [period(f"{year}-{md}", revenue=value) for year, md, value in [
        (2025, "03-31", 100), (2025, "06-30", 200), (2025, "09-30", 300),
        (2026, "03-31", 105), (2026, "06-30", 216), (2026, "09-30", 336)]]
    actual = text(rows)
    assert "累计营收同比增速连续提高" in actual
    assert "5.00%" in actual and "12.00%" in actual
    assert "单季度" not in actual


@pytest.mark.parametrize(("before", "after", "label"), [
    (-10, 5, "扭亏为盈"), (10, -5, "由盈转亏"),
    (-10, -15, "亏损扩大"), (-10, -5, "亏损收窄"), (0, 5, "零基数"),
])
def test_profit_sign_changes_do_not_produce_growth_rate(before, after, label):
    actual = text([period("2025-06-30", net_profit=before), period("2026-06-30", net_profit=after)])
    assert label in actual and "%" not in actual
    assert str(abs(before)) in actual and str(abs(after)) in actual


def test_revenue_profit_divergence_is_fact_not_invented_cause():
    actual = text([period("2025-06-30", revenue=100, net_profit=20),
                   period("2026-06-30", revenue=120, net_profit=10)])
    assert "营收增长而净利润下降" in actual
    assert "20.00%" in actual and "50.00%" in actual
    assert "成本" not in actual


def test_deducted_profit_and_cashflow_are_available_independently():
    rows = [period("2025-06-30", deducted_net_profit=10, operating_cash_flow=10,
                   cash_flow_basis="reported"),
            period("2026-06-30", deducted_net_profit=20, operating_cash_flow=-5,
                   cash_flow_basis="reported")]
    assert "扣非净利润" in text(rows)
    assert "经营现金流" in text(rows)


def test_unknown_legacy_period_basis_is_not_guessed():
    rows = [FinancialData(symbol="688326", fiscal_quarter=date(y, 6, 30), revenue=v)
            for y, v in ((2025, 100), (2026, 200))]
    actual = build_financial_trends(rows, "688326")
    assert actual["highlights"] == []
    assert "口径" in actual["note"]


@pytest.mark.parametrize("changed", [
    {"period_basis": "single"}, {"report_currency": "HKD"},
    {"reporting_basis": "其他来源"}, {"period_start": date(2026, 4, 1)},
])
def test_incompatible_periods_are_not_compared(changed):
    rows = [period("2025-06-30", revenue=100), period("2026-06-30", revenue=200, **changed)]
    assert build_financial_trends(rows, "688326")["highlights"] == []


def test_missing_year_does_not_claim_continuous_improvement():
    rows = [period("2023-06-30", revenue=100), period("2025-06-30", revenue=200),
            period("2026-06-30", revenue=300)]
    assert "连续" not in text(rows)


def test_roe_and_margin_do_not_compare_different_cumulative_lengths():
    rows = [period("2026-03-31", roe=.01, gross_margin=.1),
            period("2026-06-30", roe=.1, gross_margin=.2)]
    assert text(rows) == ""


def test_estimated_cashflow_never_mixes_with_reported():
    rows = [period("2025-06-30", operating_cash_flow=100, cash_flow_basis="reported"),
            period("2026-06-30", operating_cash_flow=10, cash_flow_basis="estimated")]
    actual = build_financial_trends(rows, "688326")
    assert actual["highlights"] == []
    assert "推算" in actual["note"]


def test_unknown_currency_discloses_same_source_original_unit_limitation():
    rows = [period("2025-06-30", revenue=100, report_currency=""),
            period("2026-06-30", revenue=200, report_currency="")]
    actual = build_financial_trends(rows, "688326")
    assert "币种未记录" in actual["note"]
    assert actual["highlights"] == []
    assert "人民币" not in text(rows)


def test_conflicting_duplicate_period_and_nonfinite_or_wrong_symbol_are_not_used():
    rows = [period("2025-06-30", revenue=100), period("2026-06-30", revenue=200),
            period("2026-06-30", revenue=300)]
    assert text(rows) == ""
    for value in (None, float("nan"), float("inf")):
        assert text([period("2025-06-30", revenue=100), period("2026-06-30", revenue=value)]) == ""
    assert text([period("2025-06-30", revenue=100),
                 period("2026-06-30", symbol="600000", revenue=200)]) == ""


def test_highlights_are_bounded_stable_and_input_is_not_mutated():
    rows = [period(f"{y}-06-30", revenue=v, net_profit=v, deducted_net_profit=v,
                   gross_margin=v / 1000, roe=v / 2000) for y, v in [(2024, 100), (2025, 120), (2026, 150)]]
    before = [row.model_dump() for row in rows]
    result = build_financial_trends(rows, "688326")
    assert 1 <= len(result["highlights"]) <= 3
    assert result == build_financial_trends(list(reversed(rows)), "688326")
    assert before == [row.model_dump() for row in rows]


def test_hk_non_calendar_fiscal_year_uses_actual_start_and_does_not_require_quarters():
    rows = [period(f"{y}-09-30", period_start=date(y, 4, 1), report_currency="HKD",
                   reporting_basis="港股报告期", revenue=v) for y, v in [(2024, 100), (2025, 120), (2026, 150)]]
    assert "连续增长" in text(rows)


def test_analyzer_attaches_templates_without_changing_scores_or_input():
    from analysis.financial import FinancialAnalyzer
    from report.scoring import compute_score_summary
    from tests.analysis.test_financial import make_ctx

    rows = [period(f"{y}-06-30", revenue=v, net_profit=v / 10, roe=.1,
                   total_assets=1000, total_equity=600) for y, v in [(2024, 100), (2025, 120), (2026, 150)]]
    before = [row.model_dump() for row in rows]
    cfg = {"financial": {"roe": {"tiers": [{"min": 0, "score": 3}]}}}
    result = FinancialAnalyzer().analyze(make_ctx(symbol="688326", name="测试", financial_data=rows), cfg)
    legacy = [row.model_copy(update={"period_basis": "unknown", "reporting_basis": ""}) for row in rows]
    reference = FinancialAnalyzer().analyze(make_ctx(symbol="688326", name="测试", financial_data=legacy), cfg)
    assert result.metrics["operating_trends"]
    assert (result.score, result.status, result.score_detail, result.risk_flags) == (
        reference.score, reference.status, reference.score_detail, reference.risk_flags)
    assert compute_score_summary([result]) == compute_score_summary([reference])
    assert before == [row.model_dump() for row in rows]


@pytest.mark.parametrize("market", ["A 股", "港股"])
def test_markdown_keeps_short_fixed_template_highlights(market):
    from analysis.financial import FinancialAnalyzer
    from report.builder import ReportBuilder
    from tests.analysis.test_financial import make_ctx

    rows = [period("2025-06-30", net_profit=-3000000), period("2026-06-30", net_profit=5000000)]
    result = FinancialAnalyzer().analyze(make_ctx(symbol="688326", name="测试", financial_data=rows))
    output = ReportBuilder().build("688326", "测试", [result], {}, no_llm=True, market=market)
    assert "**经营趋势：**" in output
    assert "扭亏为盈" in output and "300.00 万" in output
    assert output.count(result.metrics["operating_trends"][0]) == 1


def test_old_cache_and_new_metadata_roundtrip():
    legacy = FinancialData.model_validate({"symbol": "688326", "fiscal_quarter": "2026-06-30", "revenue": 100})
    assert legacy.period_basis == "unknown"
    row = period("2026-06-30", cash_flow_basis="reported", cash_flow_period_start=date(2026, 1, 1))
    assert FinancialData.model_validate_json(row.model_dump_json()) == row


def test_growth_overflow_is_not_a_trend_fact():
    rows = [period("2025-06-30", revenue=1e-308), period("2026-06-30", revenue=1e308)]
    assert "inf" not in text(rows).lower()


@pytest.mark.parametrize(("provider", "market"), [("openai", "a-shares"), ("claude", "a-shares"), ("openai", "hk")])
def test_existing_single_ai_call_consumes_fixed_facts(provider, market, tmp_path):
    from analysis.financial import FinancialAnalyzer
    from core.pipeline import Pipeline
    from core.registry import Registry
    from tests.analysis.test_financial import make_ctx
    from tests.core.test_pipeline import CountingLLM
    from utils.config import Config

    config = Config(config_dir=tmp_path)
    config.data["llm"]["provider"] = provider
    registry, backend = Registry(), CountingLLM()
    registry.register_llm_backend(backend, provider=provider)
    pipeline = Pipeline(registry, config)
    rows = [period(f"{y}-06-30", revenue=v, net_profit=10) for y, v in [(2024, 100), (2025, 120), (2026, 150)]]
    ctx = make_ctx(symbol="688326", name="测试", market=market, financial_data=rows)
    result = FinancialAnalyzer().analyze(ctx, {"financial": {}})
    pipeline._generate_commentary("688326", "测试", [result], context=ctx)
    assert len(backend.calls) == 1
    assert result.metrics["operating_trends"][0] in backend.calls[0]
    assert "不重复铺陈" in backend.calls[0]


def test_metadata_survives_existing_pipeline_cache(tmp_path):
    from core.pipeline import Pipeline
    from core.registry import Registry
    from utils.config import Config

    pipeline = Pipeline(Registry(), Config(config_dir=tmp_path))
    rows = [period(f"{y}-06-30", revenue=v) for y, v in [(2025, 100), (2026, 150)]]
    pipeline._set_cache("688326", "financial", rows)
    restored = pipeline._get_cached("688326", "financial")
    assert restored and build_financial_trends(restored, "688326") == build_financial_trends(rows, "688326")


def test_recent_yoy_sequence_never_crosses_source_or_currency():
    rows = [period(f"{year}-{md}", revenue=value, reporting_basis=source, report_currency=currency)
            for md, before, after, source, currency in [
                ("03-31", 100, 105, "甲源", "CNY"), ("06-30", 200, 216, "乙源", "HKD"),
                ("09-30", 300, 336, "丙源", "USD")]
            for year, value in [(2025, before), (2026, after)]]
    assert "同比增速连续" not in text(rows)


def test_legacy_unknown_basis_cannot_leak_yoy_through_old_summary():
    from analysis.financial import FinancialAnalyzer
    from tests.analysis.test_financial import make_ctx

    rows = [FinancialData(symbol="688326", fiscal_quarter=date(y, 6, 30), revenue=v)
            for y, v in ((2025, 100), (2026, 200))]
    result = FinancialAnalyzer().analyze(make_ctx(symbol="688326", name="测试", financial_data=rows))
    assert "revenue_growth_yoy" not in result.metrics
    assert "增长" not in result.summary


def test_leap_day_real_entry_and_ratio_overflow_are_safe():
    from analysis.financial import FinancialAnalyzer
    from tests.analysis.test_financial import make_ctx

    rows = [period("2024-02-29", roe=1e308), period("2023-02-28", roe=-1e308)]
    result = FinancialAnalyzer().analyze(make_ctx(symbol="688326", name="测试", financial_data=rows))
    assert "inf" not in result.summary.lower()
    assert "inf" not in text([period("2025-06-30", roe=-1e308), period("2026-06-30", roe=1e308)]).lower()


@pytest.mark.parametrize("currency", [None, float("nan"), "nan", "--", " ", "None", "XXX", "未披露", "未知币"])
def test_placeholder_currency_is_unknown(currency):
    rows = [period("2025-06-30", revenue=100, report_currency=currency),
            period("2026-06-30", revenue=200, report_currency=currency)]
    assert not rows[0].report_currency
    assert build_financial_trends(rows, "688326")["highlights"] == []


def test_each_metric_and_divergence_are_not_repeated():
    rows = [period(f"{year}-{md}", revenue=value) for year, md, value in [
        (2024, "09-30", 200), (2025, "03-31", 100), (2025, "06-30", 200),
        (2025, "09-30", 300), (2026, "03-31", 105), (2026, "06-30", 216), (2026, "09-30", 336)]]
    assert len(build_financial_trends(rows, "688326")["highlights"]) == 1
    rows = [period("2025-06-30", revenue=100, net_profit=20), period("2026-06-30", revenue=120, net_profit=10)]
    assert len(build_financial_trends(rows, "688326")["highlights"]) == 1
