"""五维计分的财务证据、单位及港股适用性回归。"""
from copy import deepcopy
from datetime import date, timedelta

import pytest

from analysis.config_loader import ConfigLoader
from analysis.financial import FinancialAnalyzer
from analysis.scorers.general import GeneralScorer
from analysis.technical import TechnicalAnalyzer
from analysis.valuation_rules import valuation_evidence
from data.schemas import (
    AnalysisContext,
    DimensionSufficiency,
    EnrichedIndustry,
    EnrichedValuation,
    FinancialData,
    IndustryData,
    PriceData,
    SufficiencyLevel,
    ValuationData,
    ValuationHistorySummary,
)


def row(**values):
    defaults = {"symbol": "00700.HK", "fiscal_quarter": date(2025, 12, 31),
                    "period_start": date(2025, 1, 1), "period_basis": "cumulative",
                    "reporting_basis": "同源合并财报", "financial_source": "同源财报",
                    "report_currency": "CNY", "amount_currency_verified": True, "cash_flow_period_start": date(2025, 1, 1),
                    "cash_flow_basis": "reported"}
    return FinancialData.model_validate(defaults | values)


def financials(ctx: AnalysisContext) -> list[FinancialData]:
    assert ctx.financial_data is not None
    return ctx.financial_data


def context(*rows):
    ctx = AnalysisContext(symbol="00700.HK", name="样例", market="hk", financial_data=list(rows))
    ctx.sufficiency.financial = DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT)
    return ctx


def scorer(annual=None, **row_values):
    ctx = context(row(**row_values))
    if annual is not None:
        ctx.financial_scoring_facts = {"annual_roe": {"value": annual, "period_start": "2025-01-01",
            "period_end": "2025-12-31", "source": "同源财报", "basis": "加权ROE", "reason": ""}}
    return GeneralScorer(ConfigLoader().load("机械设备"), {}), ctx


def test_real_yaml_roe_ratio_and_missing_items_scale_to_ten():
    scoring, ctx = scorer(0.15, roe=0.99)
    score, detail, _ = scoring.score_financial(ctx)
    assert score == 10
    assert "15.00%" in detail and "3/3" in detail
    assert financials(ctx)[0].roe == 0.99


def test_valid_zero_roe_remains_in_denominator():
    scoring, ctx = scorer(0, total_assets=100, total_equity=50)
    assert scoring.score_financial(ctx)[0] == 4


def test_missing_annual_and_single_gross_margin_have_no_score():
    scoring, ctx = scorer(None, roe=0.15, gross_margin=0.4)
    score, detail, _ = scoring.score_financial(ctx)
    assert score is None
    assert "不足两期" in detail


@pytest.mark.parametrize("change", [{"cash_flow_period_start": date(2025, 7, 1)},
    {"report_currency": ""}, {"cash_flow_basis": "unknown"},
    {"field_positions": {"operating_cash_flow": {"currency": "USD"}}}])
def test_cashflow_requires_same_period_currency_and_known_source(change):
    scoring, ctx = scorer(None, net_profit=10, operating_cash_flow=20, **change)
    assert scoring.score_financial(ctx)[0] is None


def test_cashflow_reported_same_period_and_currency_is_usable():
    scoring, ctx = scorer(None, net_profit=10, operating_cash_flow=20)
    assert scoring.score_financial(ctx)[0] == 10


def test_negative_profit_is_valid_zero_only_with_cashflow_evidence():
    scoring, ctx = scorer(None, net_profit=-10, operating_cash_flow=20)
    assert scoring.score_financial(ctx)[0] == 0
    financials(ctx)[0].cash_flow_period_start = None
    assert scoring.score_financial(ctx)[0] is None


def test_gross_margin_only_compares_same_report_type_and_basis():
    ctx = context(row(gross_margin=0.40, gross_margin_basis="披露毛利"),
        row(fiscal_quarter=date(2025, 6, 30), gross_margin=0.1, gross_margin_basis="披露毛利"),
        row(fiscal_quarter=date(2024, 12, 31), period_start=date(2024, 1, 1),
            gross_margin=0.41, gross_margin_basis="披露毛利"))
    scoring = GeneralScorer(ConfigLoader().load("机械设备"), {})
    score, detail, _ = scoring.score_financial(ctx)
    assert score == 10
    assert "2期" in detail
    financials(ctx)[-1].gross_margin_basis = "另一种毛利"
    assert scoring.score_financial(ctx)[0] is None


@pytest.mark.parametrize(("sector", "industry", "style", "strategy"), [
    ("银行", "Financials", "大金融", "BankScorer"),
    ("保险", "Financials", "大金融", "NonBankFinancialScorer"),
    ("其他金融", "Financials", "大金融", "NonBankFinancialScorer"),
    ("地产", "Properties and Construction", "大金融", "RealEstateScorer"),
    ("建筑", "Properties and Construction", "高端制造", "GeneralScorer"),
    ("软件服务", "Information Technology", "TMT科技", "TechGrowthScorer"),
    ("电讯", "Telecommunications", "TMT科技", "TechGrowthScorer"),
    ("药品及生物科技", "Healthcare", "医药", "PharmaScorer"),
    ("食物饮品", "Consumer Staples", "必选消费", "GeneralScorer"),
    ("汽车", "Consumer Discretionary", "可选消费", "GeneralScorer"),
    ("煤炭", "Energy", "周期资源", "CyclicalScorer"),
    ("公用事业", "Utilities", "公用事业", "GeneralScorer"),
    ("工业工程", "Industrials", "高端制造", "GeneralScorer"),
    ("综合企业", "Conglomerates", "高端制造", "GeneralScorer"),
])
def test_hk_templates_do_not_rewrite_real_classification(sector, industry, style, strategy):
    ctx = context()
    ctx.industry_data = IndustryData(symbol=ctx.symbol, industry=sector, sector=industry)
    config = ConfigLoader().load_for_context(ctx)
    assert config["meta"]["style_category"] == style
    assert config["meta"]["strategy_key"] == strategy
    assert ctx.industry_data.industry == sector and ctx.sw_industry == ""
    assert [config[d]["weight"] for d in ("financial", "technical", "valuation", "industry", "sentiment")] == [.3, .2, .25, .2, .05]
    if sector in ("银行", "保险"):
        assert not config["financial"]["gross_margin_stability"]["enabled"]
        assert config["financial"]["debt_ratio"].get("use_custom_calc") or not config["financial"]["debt_ratio"]["enabled"]


def test_bank_roe_threshold_is_not_scaled_twice():
    assert ConfigLoader().load("银行")["financial"]["roe"]["tiers"][0]["min"] == .13


def test_hk_valuation_uses_curve_and_same_comparison_premium():
    ctx = context()
    ctx.valuation_data = ValuationData(symbol=ctx.symbol, date=date(2026, 10, 23), pe_ttm=999, pb=2)
    ctx.enriched_valuation = EnrichedValuation(pe_percentile=20, pb_percentile=20,
        pe_history_summary=ValuationHistorySummary(source="自身曲线", sample_count=120, reference=20),
        pb_history_summary=ValuationHistorySummary(source="自身曲线", sample_count=120, reference=2))
    ctx.enriched_industry = EnrichedIndustry(target_pe_premium=999, industry_median_pe=1,
        comparison={"valuation": {"metrics": [{"key": "pe_ttm", "target": 20, "median": 25, "sample_count": 5, "status": "ok"}]}})
    assert valuation_evidence(ctx) == pytest.approx((20, 20, -20))
    scoring = GeneralScorer(ConfigLoader().load("机械设备"), {})
    assert scoring.score_valuation(ctx)[0] == 10
    ctx.enriched_industry.comparison = {}
    assert valuation_evidence(ctx)[2] is None


def test_hk_technical_enabled_and_missing_macd_is_renormalized():
    ctx = context()
    ctx.price_data = [PriceData(symbol=ctx.symbol, trade_date=date(2026, 8, 1) + timedelta(days=i),
                     open=10+i, high=11+i, low=9+i, close=10+i, volume=100) for i in range(20)]
    ctx.sufficiency.price = DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT)
    config = ConfigLoader().load("机械设备")
    result = TechnicalAnalyzer().analyze(ctx, config)
    assert result.score == pytest.approx(40 / 7)
    for key in ("ma_structure", "volume_price", "macd"):
        config["technical"][key]["enabled"] = False
    assert GeneralScorer(config, {}).score_technical(ctx)[0] is None


def test_analyzer_exposes_independent_annual_facts():
    scoring, ctx = scorer(.15, roe=.07)
    result = FinancialAnalyzer().analyze(ctx, deepcopy(scoring.config))
    assert result.metrics["roe"] == .07
    assert result.metrics["annual_roe"] == ctx.financial_scoring_facts["annual_roe"]


def test_financial_status_uses_applicable_scoring_coverage():
    scoring, ctx = scorer(.15, total_assets=100, total_equity=50,
                          net_profit=10, operating_cash_flow=20, gross_margin=.4, gross_margin_basis="披露毛利")
    financials(ctx).append(row(fiscal_quarter=date(2024, 12, 31), period_start=date(2024, 1, 1),
                                 gross_margin=.41, gross_margin_basis="披露毛利"))
    ctx.sufficiency.financial = DimensionSufficiency(level=SufficiencyLevel.PARTIAL)
    result = FinancialAnalyzer().analyze(ctx, scoring.config)
    assert result.status == "ok"
    assert result.metrics["scoring_basis"] == {"available_max": 10, "eligible_max": 10, "reasons": []}
    financials(ctx).pop()
    result = FinancialAnalyzer().analyze(ctx, scoring.config)
    assert result.status == "partial"
    assert result.metrics["scoring_basis"]["available_max"] == 8


def test_hk_nonconnect_uses_local_hsics_without_gaining_peers(mocker):
    from data.hk_hsics_industry import HsicsMember

    ctx = context()
    mocker.patch("data.hk_hsics_industry.load_hsics_members", return_value={ctx.symbol:
        HsicsMember(ctx.symbol, "银行", "Financials", "mapped", False)})
    config = ConfigLoader().load_for_context(ctx)
    assert config["meta"]["strategy_key"] == "BankScorer"
    assert ctx.industry_data is None


def test_hk_fallback_is_explicit(mocker):
    mocker.patch("data.hk_hsics_industry.load_hsics_members", return_value={})
    config = ConfigLoader().load_for_context(context())
    assert config["meta"]["scoring_template_fallback"]
    assert "通用规则" in config["meta"]["scoring_template_source"]


def test_hk_invalid_local_hsics_snapshot_falls_back(mocker, caplog):
    from data.hk_hsics_industry import HsicsIndustryError

    mocker.patch("data.hk_hsics_industry.load_hsics_members", side_effect=HsicsIndustryError("bad hash"))
    config = ConfigLoader().load_for_context(context())
    assert config["meta"]["scoring_template_fallback"]
    assert "HSICS" in caplog.text


def test_short_macd_history_is_missing_not_a_bearish_score():
    ctx = context()
    ctx.price_data = [PriceData(symbol=ctx.symbol, trade_date=date(2026, 8, 1) + timedelta(days=i),
                     open=10+i, high=11+i, low=9+i, close=10+i, volume=100) for i in range(26)]
    config = {"technical": {"ma_structure": {"enabled": False}, "volume_price": {"enabled": False}}}
    assert GeneralScorer(config, {}).score_technical(ctx)[0] is None


@pytest.mark.parametrize(("industry", "expected"), [("银行", 10), ("煤炭", 0)])
def test_hk_specialized_valuation_rules_remain(industry, expected):
    ctx = context()
    ctx.valuation_data = ValuationData(symbol=ctx.symbol, date=date(2026, 10, 23), pe_ttm=10, pb=1)
    ctx.enriched_valuation = EnrichedValuation(pe_percentile=0, pb_percentile=0,
        pe_history_summary=ValuationHistorySummary(source="自身曲线", sample_count=120, reference=10),
        pb_history_summary=ValuationHistorySummary(source="自身曲线", sample_count=120, reference=1))
    config = ConfigLoader().load(industry)
    config["valuation"]["pb_percentile"]["enabled"] = industry == "银行"
    result = FinancialAnalyzer._get_scorer(config).score_valuation(ctx)
    assert result[0] == expected
