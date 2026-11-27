from datetime import date


def test_financial_metrics_expose_eps_and_common_equity():
    ctx = AnalysisContext(symbol="600519", name="贵州茅台", market="a-shares")
    ctx.financial_data = [FinancialData(
        symbol="600519", fiscal_quarter=date(2026, 3, 31), revenue=100,
        net_profit=20, total_equity=250, common_equity=240,
        operating_cash_flow=30, basic_eps=2.5, roe=0.1, gross_margin=0.8,
    )]
    result = FinancialAnalyzer().analyze(ctx)
    assert result.metrics["basic_eps"] == 2.5
    assert result.metrics["common_equity"] == 240

import pytest

from analysis.financial import FinancialAnalyzer
from data.schemas import (
    AnalysisContext,
    DataSufficiency,
    DimensionSufficiency,
    FinancialData,
    SufficiencyLevel,
    ValuationData,
)


def make_ctx(**kwargs) -> AnalysisContext:
    """构造充足度标记为 SUFFICIENT 的上下文，让分析器按自身数据逻辑判定状态"""
    ctx = AnalysisContext(**kwargs)
    ctx.sufficiency = DataSufficiency(
        price=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=100, score_weight=1.0),
        financial=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=4, score_weight=1.0),
        valuation=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=100, score_weight=1.0),
        industry=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=10, score_weight=1.0),
        sentiment=DimensionSufficiency(level=SufficiencyLevel.SUFFICIENT, sample_count=10, score_weight=1.0),
    )
    return ctx


def known_periods(rows):
    """合成同比用例显式提供口径，不给真实旧缓存猜币种。"""
    rows = [row.model_copy(update={"period_start": date(row.fiscal_quarter.year, 1, 1),
                                  "period_basis": "cumulative", "report_currency": "CNY",
                                  "reporting_basis": "测试同源财报"}) for row in rows]
    from data.ths_financial import attach_positions

    for row in rows:
        row.financial_source = "测试同源财报"
        row.field_precision = {field: "source_native" for field in (
            "revenue", "net_profit", "total_assets", "total_equity", "operating_cash_flow", "gross_margin")
            if getattr(row, field) is not None}
        attach_positions(row)
        for pos in row.field_positions.values():
            pos.update(accounting="CAS", revision="测试同一重述包")
    return rows



class TestFinancialAnalyzer:
    def test_dimension_is_financial(self):
        a = FinancialAnalyzer()
        assert a.dimension == "financial"

    def test_full_data_analysis(self):
        financials = [
            FinancialData(symbol="000001", fiscal_quarter=date(2025,12,31), revenue=45e9, net_profit=8.5e9, total_assets=500e9, total_equity=45e9, operating_cash_flow=12e9, roe=0.189, gross_margin=None),
            FinancialData(symbol="000001", fiscal_quarter=date(2025,9,30), revenue=33e9, net_profit=6.2e9, total_assets=490e9, total_equity=44e9, operating_cash_flow=9e9, roe=0.141, gross_margin=None),
            FinancialData(symbol="000001", fiscal_quarter=date(2025,6,30), revenue=22e9, net_profit=4.1e9, total_assets=485e9, total_equity=43.5e9, operating_cash_flow=6e9, roe=0.094, gross_margin=None),
            FinancialData(symbol="000001", fiscal_quarter=date(2025,3,31), revenue=11e9, net_profit=2.0e9, total_assets=475e9, total_equity=43e9, operating_cash_flow=3e9, roe=0.047, gross_margin=None),
            FinancialData(symbol="000001", fiscal_quarter=date(2024,12,31), revenue=42e9, net_profit=7.8e9, total_assets=460e9, total_equity=41e9, operating_cash_flow=11e9, roe=0.190, gross_margin=None),
        ]
        ctx = make_ctx(symbol="000001", name="平安银行", financial_data=known_periods(financials))
        result = FinancialAnalyzer().analyze(ctx)
        assert result.status == "ok"
        assert "revenue_growth_yoy" in result.metrics
        assert "roe_trend" in result.metrics
        assert len(result.metrics["roe_trend"]) > 0

    def test_no_financial_data_returns_unavailable(self):
        ctx = AnalysisContext(symbol="000001", name="测试")
        result = FinancialAnalyzer().analyze(ctx)
        assert result.status == "unavailable"

    def test_single_quarter_returns_partial(self):
        ctx = make_ctx(symbol="000001", name="测试", financial_data=[
            FinancialData(symbol="000001", fiscal_quarter=date(2025,12,31), revenue=45e9, net_profit=8.5e9, total_assets=500e9, total_equity=45e9, operating_cash_flow=12e9)
        ])
        result = FinancialAnalyzer().analyze(ctx)
        assert result.status == "partial"

    def test_analyze_tolerates_none_revenue(self):
        financials = [
            FinancialData(symbol="600350", fiscal_quarter=date(2025, 12, 31),
                          revenue=None, net_profit=8.5e8, total_assets=None,
                          total_equity=45e8, operating_cash_flow=None, roe=0.18),
            FinancialData(symbol="600350", fiscal_quarter=date(2024, 12, 31),
                          revenue=None, net_profit=7.0e8, total_assets=None,
                          total_equity=43e8, operating_cash_flow=None, roe=0.16),
        ]
        ctx = make_ctx(symbol="600350", name="山东高速", financial_data=known_periods(financials))
        result = FinancialAnalyzer().analyze(ctx)
        assert result.status in ("ok", "partial")
        assert "revenue_growth_yoy" not in result.metrics
        assert "profit_growth_yoy" in result.metrics

    def test_yoy_uses_same_period_last_year_and_labels_per_share_cashflow(self):
        financials = [
            FinancialData(symbol="002714", fiscal_quarter=date(2026, 6, 30), revenue=594.10e8,
                          net_profit=-60.78e8, operating_cash_flow_per_share=-0.39, roe=-0.0748),
            FinancialData(symbol="002714", fiscal_quarter=date(2026, 3, 31), revenue=298.94e8),
            FinancialData(symbol="002714", fiscal_quarter=date(2025, 12, 31), revenue=1441.45e8),
            FinancialData(symbol="002714", fiscal_quarter=date(2025, 9, 30), revenue=1117.90e8),
            FinancialData(symbol="002714", fiscal_quarter=date(2025, 6, 30), revenue=764.63e8),
        ]
        result = FinancialAnalyzer().analyze(make_ctx(symbol="002714", name="牧原股份", financial_data=known_periods(financials)))
        assert result.metrics["revenue_growth_yoy"] == pytest.approx(594.10 / 764.63 - 1)
        assert result.metrics["operating_cash_flow_per_share"] == pytest.approx(-0.39)
        assert result.metrics["operating_cash_flow"] is None


def test_derived_margin_evidence_reaches_report_metrics():
    row = FinancialData(symbol="AAPL", fiscal_quarter=date(2026, 9, 30),
                        period_start=date(2026, 6, 28), period_basis="single", report_currency="USD",
                        revenue=100, net_profit=20, net_margin=.2,
                        net_margin_basis="计算值：单季净利润÷营业收入；仅展示",
                        financial_source="东方财富美股F10")
    result = FinancialAnalyzer().analyze(make_ctx(symbol="AAPL", name="苹果", market="us", financial_data=[row]))
    assert "仅展示" in result.metrics["net_margin_basis"]
    assert "2026-06-28" in result.metrics["net_margin_basis"]
    assert "东方财富美股F10" in result.metrics["net_margin_basis"]
    from report.table_notes import prepare_table
    assert any("仅展示" in note for note in prepare_table("financial", result.metrics)["notes"])


def test_gross_margin_periods_group_52_53_week_fiscal_quarters():
    from analysis.financial_scoring import gross_margin_periods

    rows = [
        FinancialData(symbol="AAPL", fiscal_quarter=date(2026, 9, 30),
                      period_start=date(2026, 6, 28), period_basis="single",
                      reporting_basis="东方财富美股F10-单季报（会计准则未核实）",
                      gross_margin=.48, gross_margin_basis="毛利/营业收入（单季）",
                      financial_source="东方财富美股F10",
                      field_sources={"gross_margin": "东方财富美股F10"}),
        FinancialData(symbol="AAPL", fiscal_quarter=date(2025, 9, 30),
                      period_start=date(2025, 6, 29), period_basis="single",
                      reporting_basis="东方财富美股F10-单季报（会计准则未核实）",
                      gross_margin=.47, gross_margin_basis="毛利/营业收入（单季）",
                      financial_source="东方财富美股F10",
                      field_sources={"gross_margin": "东方财富美股F10"}),
        FinancialData(symbol="AAPL", fiscal_quarter=date(2024, 9, 30),
                      period_start=date(2024, 6, 30), period_basis="single",
                      reporting_basis="东方财富美股F10-单季报（会计准则未核实）",
                      gross_margin=.46, gross_margin_basis="毛利/营业收入（单季）",
                      financial_source="东方财富美股F10",
                      field_sources={"gross_margin": "东方财富美股F10"}),
    ]
    assert gross_margin_periods(rows) == pytest.approx([.48, .47, .46])


def test_hk_missing_capex_flow_limitation_is_visible():
    row = FinancialData(symbol="00700.HK", fiscal_quarter=date(2026, 9, 30),
                        period_start=date(2026, 1, 1), period_basis="cumulative",
                        reporting_basis="东方财富港股财报-报告期-国际会计准则",
                        report_currency="HKD", amount_currency_verified=True,
                        revenue=100, net_profit=20, operating_cash_flow=30,
                        financial_source="东方财富港股财报")
    result = FinancialAnalyzer().analyze(make_ctx(symbol="00700.HK", name="腾讯",
                                                  market="hk", financial_data=[row]))
    assert "汇总项" in result.metrics["cash_flow_limitation"]
    from report.table_notes import prepare_table
    assert any("汇总项" in note for note in prepare_table("financial", result.metrics)["notes"])


def test_dividend_yield_follows_net_margin_with_source():
    row = FinancialData(symbol="600519", fiscal_quarter=date(2026, 9, 30),
                        period_start=date(2026, 1, 1), period_basis="cumulative",
                        reporting_basis="同花顺财务摘要-按报告期", report_currency="CNY",
                        revenue=100, net_profit=20, net_margin=.2,
                        financial_source="同花顺财报")
    valuation = ValuationData(
        symbol="600519", date=date(2026, 11, 24), pe_ttm=20, pb=6,
        dividend_yield=2.31, dividend_yield_as_of=date(2026, 6, 25),
        dividend_yield_source="东方财富分红送配",
        dividend_yield_basis="最近已实施现金分红方案股息率（东财，方案口径）",
    )
    result = FinancialAnalyzer().analyze(make_ctx(symbol="600519", name="贵州茅台",
                                                  market="a-shares", financial_data=[row],
                                                  valuation_data=valuation))
    keys = list(result.metrics)
    assert keys.index("dividend_yield") == keys.index("net_margin") + 1
    assert result.metrics["dividend_yield"] == pytest.approx(2.31)
    from report.table_notes import prepare_table
    view = prepare_table("financial", result.metrics)
    assert any(label.startswith("股息率") for label in view["metrics"])
    assert any("股息率来源" in note and "东方财富分红送配" in note for note in view["notes"])


def test_hk_cashflow_annual_fallback_and_unverified_balance(verified_fx):
    from analysis.financial_scoring import debt_ratio
    annual = FinancialData(
        symbol='00700.HK', fiscal_quarter=date(2025, 12, 31),
        period_start=date(2025, 1, 1), period_basis='cumulative',
        report_currency='HKD', amount_currency_verified=True, amount_unit='currency',
        net_profit=20, total_assets=500, total_equity=300, common_equity=250,
        capital_expenditure=30, free_cash_flow=50,
        cash_flow_basis='reported', cash_flow_period_start=date(2025, 1, 1),
        field_precision={key: 'source_native' for key in
                         ('net_profit', 'total_assets', 'total_equity', 'common_equity',
                          'capital_expenditure', 'free_cash_flow')},
        field_sources={'total_assets': '测试源', 'total_equity': '测试源'},
    )
    ctx = make_ctx(symbol='00700.HK', name='腾讯', market='hk')
    ctx.financial_data = [annual, annual.model_copy(update={
        'fiscal_quarter': date(2026, 6, 30), 'period_start': date(2026, 1, 1),
        'cash_flow_period_start': date(2026, 1, 1),
        'capital_expenditure': None, 'free_cash_flow': None})]
    metrics = FinancialAnalyzer().analyze(ctx).metrics
    assert metrics['capital_expenditure_latest'] == 30
    assert metrics['free_cash_flow_latest'] == 50
    assert '非 TTM' in metrics['cash_flow_latest_period']
    assert not isinstance(metrics['roe'], (float, int))
    assert debt_ratio(annual) is None


def test_hk_verified_balance_allows_ratios(verified_fx):
    from analysis.financial_scoring import debt_ratio
    annual = FinancialData(
        symbol='00388.HK', fiscal_quarter=date(2025, 12, 31),
        period_start=date(2025, 1, 1), period_basis='cumulative',
        report_currency='HKD', amount_currency_verified=True, amount_unit='currency',
        net_profit=20, total_assets=500, total_equity=300, common_equity=250,
        field_precision={key: 'source_native' for key in ('net_profit', 'common_equity')},
        field_sources={'total_assets': '测试源', 'total_equity': '测试源'},
        field_positions={key: {'currency': 'HKD', 'basis': 'instant'}
                         for key in ('total_assets', 'total_equity', 'common_equity')},
    )
    ctx = make_ctx(symbol='00388.HK', name='香港交易所', market='hk', financial_data=[annual])
    assert FinancialAnalyzer().analyze(ctx).metrics['roe'] == pytest.approx(.08)
    assert debt_ratio(annual) == pytest.approx(40)
    annual.field_positions['common_equity']['currency'] = 'USD'
    assert FinancialAnalyzer().analyze(ctx).metrics['roe'] is None
