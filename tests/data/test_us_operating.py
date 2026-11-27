"""连续单季TTM、缺项及独立经营分门槛。"""
from datetime import date, timedelta

import pytest

from analysis.industry_scoring import score_industry_comparison
from data.enrichers.us_industry_enricher import UsIndustryEnricher
from data.schemas import AnalysisContext, IndustryData
from data.us_operating import GENERAL, build_operating, load_operating


def rows():
    starts = ['2024-10-01', '2025-01-01', '2025-04-01', '2025-07-01',
              '2025-10-01', '2026-01-01', '2026-04-01', '2026-07-01']
    ends = ['2024-12-31', '2025-03-31', '2025-06-30', '2025-09-30',
            '2025-12-31', '2026-03-31', '2026-06-30', '2026-09-30']
    return [{'SECURITY_CODE': 'TEST', 'DATE_TYPE': '单季报', 'START_DATE': s, 'REPORT_DATE': e,
             'CURRENCY_ABBR': 'USD', 'OPERATE_INCOME': 100 if i < 4 else 120,
             'PARENT_HOLDER_NETPROFIT': 12, 'NOTICE_DATE': None} for i, (s, e) in enumerate(zip(starts, ends, strict=True))]


def test_ttm_actual_period_growth_and_margin():
    value = build_operating(rows(), 'TEST', GENERAL)
    assert value is not None
    assert value['revenue'] == 480
    assert value['revenue_growth_yoy'] == pytest.approx(20)
    assert value['net_margin'] == 10
    assert value['start'] == '2025-10-01'
    assert value['notice_dates'] == [None]*4


@pytest.mark.parametrize('failure', ['gap', 'duplicate', 'currency', 'identity', 'missing'])
def test_invalid_data_never_fills_zero(failure):
    data = rows()
    if failure == 'gap':
        data[-1]['START_DATE'] = '2026-07-02'
    elif failure == 'duplicate':
        data[-2] = data[-1]
    elif failure == 'currency':
        data[-1]['CURRENCY_ABBR'] = 'EUR'
    elif failure == 'identity':
        data[-1]['SECURITY_CODE'] = 'OTHER'
    else:
        data[-1]['OPERATE_INCOME'] = None
    assert build_operating(data, 'TEST', GENERAL) is None


def test_failure_and_brk_are_missing(mocker):
    mocker.patch('data.us_operating.fetch_operating', side_effect=ValueError('失败'))
    assert load_operating(['TEST']) == {'TEST': None}


@pytest.mark.parametrize('count,scored', [(5, True), (4, False), (2, False)])
def test_two_operating_metrics_each_need_five_peers(count, scored, mocker):
    mocker.patch("data.us_stock._statement", return_value={})
    operating = build_operating(rows(), 'TEST', GENERAL)
    assert operating is not None
    operating['revenue_verified'] = True  # 明确已审核的合成协议样例，用于独立验证样本门槛
    quotes = {f'P{i}': {'symbol': f'P{i}', 'basis_verified': True, 'market_cap': 100, 'pe': 10, 'pb': 2} for i in range(count)}
    ctx = AnalysisContext(symbol='TEST', name='目标', market='us', industry_data=IndustryData(
        symbol='TEST', industry='软件', sector='', peers=list(quotes), comparison_snapshot={
            'target': {'basis_verified': True, 'market_cap': 100, 'pe': 10, 'pb': 2}, 'quotes': quotes,
            'operating': {'TEST': operating, **dict.fromkeys(quotes, operating)}}))
    UsIndustryEnricher().enrich(ctx)
    assert ctx.enriched_industry is not None
    comparison = ctx.enriched_industry.comparison
    assert (score_industry_comparison(comparison)[0] is not None) == scored
    assert all(p.roe is None for p in ctx.enriched_industry.top_peers)
    assert 'annual' not in comparison['periods']
    if scored:
        assert ctx.sufficiency.industry.level == 'sufficient'
        assert ctx.enriched_industry.top_peers[0].net_margin == pytest.approx(.1)
        assert comparison['periods']['latest']['metrics'][1]['target'] == 10
    # 目标缺失或同行报告过旧都不能借估值数字计分。
    assert operating is not None
    operating['end'] = (date(2026, 9, 30) - timedelta(days=200)).isoformat()
    assert ctx.industry_data is not None
    ctx.industry_data.comparison_snapshot['operating']['TEST'] = None
    UsIndustryEnricher().enrich(ctx)
    assert score_industry_comparison(ctx.enriched_industry.comparison)[0] is None


def test_f10_reported_annual_and_bank_special_facts():
    import json
    from pathlib import Path

    from data.us_operating import BANK, build_annual, build_special

    fixtures = Path(__file__).parents[1] / "resources" / "us_operating"
    aapl = json.loads((fixtures / "eastmoney-aapl-gmain-annual.json").read_text(encoding="utf-8"))
    jpm = json.loads((fixtures / "eastmoney-jpm-bmain-annual.json").read_text(encoding="utf-8"))
    annual = build_annual([aapl], "AAPL", GENERAL)
    assert annual is not None
    assert annual["basis"] == "FY" and annual["currency"] == "USD"
    assert annual["end"] == "2026-09-26"
    assert annual["revenue_growth_yoy"] == pytest.approx(15.0597965691)
    assert annual["roe"] == pytest.approx(142.2567347949)
    assert build_annual([aapl], "OTHER", GENERAL) is None
    assert build_special([aapl], "AAPL", GENERAL) is None
    special = build_special([jpm], "JPM", BANK)
    assert special is not None
    assert special["net_interest_income_ratio_pct"] == pytest.approx(95443 / 182447 * 100)
    assert special["provision_to_revenue_pct"] == pytest.approx(14212 / 182447 * 100)
    assert special["loan_to_deposit_pct"] == pytest.approx(58.352570214)
    assert special["roa_pct"] == pytest.approx(1.289249474564)
    missing = build_special([{**jpm, "LOAN_DEPOSIT": None}], "JPM", BANK)
    assert missing is not None and missing["loan_to_deposit_pct"] is None


def test_annual_reference_does_not_affect_scoring():
    from analysis.industry import IndustryAnalyzer

    period = {"start": "2024-01-01", "end": "2024-12-31", "basis": "TTM", "metrics": [
        {"key": key, "sample_count": 5, "status": "ok", "target": 10, "percentile": 50}
        for key in ("revenue_growth_yoy", "net_margin")
    ]}
    baseline = {"version": 1, "scope": "东财美股", "periods": {"latest": period}}
    updated = {**baseline, "score_periods": ["latest"], "periods": {
        **baseline["periods"], "annual": {**period, "basis": "FY", "reference_only": True,
                                         "metrics": [{"status": "insufficient"}]}}}
    assert score_industry_comparison(updated) == score_industry_comparison(baseline)
    assert IndustryAnalyzer._comparison_status(updated) == IndustryAnalyzer._comparison_status(baseline)
    updated["score_periods"] = ["annual"]
    assert score_industry_comparison(updated)[0] is None


def test_jpm_latest_special_keeps_cumulative_period_and_units():
    from data.us_operating import build_jpm_special

    row = {"SECURITY_CODE": "JPM", "SECUCODE": "JPM.N", "DATE_TYPE": "累计季报",
           "REPORT_TYPE": "2026/Q9", "START_DATE": "2026-01-01", "REPORT_DATE": "2026-09-30",
           "CURRENCY": "USD", "TOTAL_INCOME": 157355000000, "NET_INTEREST_INCOME": 77913000000,
           "LOAN_LOSS_PROVISION": 7765000000, "LOAN_DEPOSIT": .569852196837, "ROE": 14.220878270958,
           "ROA": 1.04003794526}
    facts = build_jpm_special([{**row, "DATE_TYPE": "单季报", "REPORT_TYPE": "2026/Q3", "TOTAL_INCOME": 1}, row])
    assert facts["basis"] == "Q9"
    assert facts["net_interest_income_ratio_pct"] == pytest.approx(77913 / 157355 * 100)
    assert facts["provision_to_revenue_pct"] == pytest.approx(7765 / 157355 * 100)
    assert facts["loan_to_deposit_pct"] == pytest.approx(56.9852196837)
    assert facts["roe_pct"] == pytest.approx(14.220878270958)
    assert facts["roa_pct"] == pytest.approx(1.04003794526)
