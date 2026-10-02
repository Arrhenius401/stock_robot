"""策略指数的真实口径、独立覆盖率与价格降级回归。"""
from datetime import date

import pytest

from data.schemas import (
    AnalysisTarget,
    AnnualFinancialSnapshot,
    ConstituentSnapshot,
    IndexAnalysisContext,
    IndexPriceData,
    StrategySnapshot,
)
from index.analysis.performance import IndexPerformanceAnalyzer
from index.analysis.strategy import IndexStrategyAnalyzer
from index.strategy_data import (
    StrategyDataProvider,
    merge_annual_financials,
    parse_price_rows,
)


def target():
    return AnalysisTarget(target_type="index", symbol="980092", name="自由现金流", index_style="strategy")


def price(day, close, symbol="980092"):
    return IndexPriceData(symbol=symbol, trade_date=date(2025, 1, day), open=close, high=close, low=close, close=close, volume=0)


def financial(year, **kwargs):
    return AnnualFinancialSnapshot(report_date=date(year, 12, 31), available_date=date(year + 1, 4, 1), **kwargs)


def analyze(members, kind="free_cash_flow"):
    ctx = IndexAnalysisContext(target=target(), strategy_data=StrategySnapshot(source="官方", as_of=date(2026, 9, 30), strategy_kind=kind, members=members))
    return IndexStrategyAnalyzer().analyze(ctx)


def indicator(result, key):
    return next(i for i in result.metrics["indicators"] if i["key"] == key)


def test_strategy_compare_does_not_claim_neutral_without_capital_data():
    """策略指数没有资金源，横向对比不能借用默认中性标签。"""
    from index.build_compare import IndexCompareReportBuilder
    from index.build_single import IndexReportBuilder

    ctx = IndexAnalysisContext(target=target())
    report = IndexReportBuilder().build(ctx, [])
    table = IndexCompareReportBuilder().build([ctx], [report])
    assert table.rows[0]["capital"] == "N/A"


def test_performance_matches_dates_and_deduplicates():
    ctx = IndexAnalysisContext(target=target(), price_data=[price(3, 121), price(1, 100), price(2, 200), price(2, 110)], benchmark_prices=[price(1, 100, "000300"), price(3, 110, "000300")])
    r = IndexPerformanceAnalyzer().analyze(ctx)
    assert r.metrics["return_pct"] == pytest.approx(21)
    assert r.metrics["excess_return_pct"] == pytest.approx(11)
    assert r.metrics["benchmark_sample_count"] == 2
    assert "价格指数" in r.metrics["return_basis"]


def test_performance_drawdown_and_etf_are_separate():
    ctx = IndexAnalysisContext(target=target(), price_data=[price(1,100),price(2,120),price(3,90)], etf_prices=[price(1,2,"515300"),price(2,2.1,"515300"),price(3,2.2,"515300")])
    r = IndexPerformanceAnalyzer().analyze(ctx)
    assert r.metrics["max_drawdown_pct"] == pytest.approx(-25)
    assert r.metrics["etf_return_pct"] == pytest.approx(10)
    assert "前复权" in r.metrics["etf_return_basis"]


def test_independent_coverage_and_no_full_index_claim():
    m1=ConstituentSnapshot(symbol="600001",name="甲",weight=40,industry="工业",annual_financials=[financial(2025,operating_cash_flow=50,capital_expenditure=10,market_cap=1000,net_profit=20)])
    m2=ConstituentSnapshot(symbol="600002",name="乙",weight=60,annual_financials=[financial(2025,operating_cash_flow=60,net_profit=30)])
    r=analyze([m1,m2])
    fcf=indicator(r,"free_cash_flow_yield")
    assert fcf["coverage_pct"] == 40
    assert fcf["value"] == pytest.approx(4)
    assert fcf["scope"] == "已覆盖样本估算"
    assert indicator(r,"operating_cash_flow_profit_ratio")["coverage_pct"] == 100
    assert r.metrics["industry_coverage_pct"] == 40
    assert r.metrics["industry_weights"] == [{"industry":"工业","weight_pct":40}]


def test_missing_year_and_nan_are_not_zero():
    m=ConstituentSnapshot(symbol="600001",weight=100,annual_financials=[financial(2023,operating_cash_flow=10,capital_expenditure=1,dividend_per_share=1),financial(2025,operating_cash_flow=float("nan"),capital_expenditure=1,dividend_per_share=2)])
    r=analyze([m],"dividend")
    assert indicator(r,"dividend_continuity")["value"] is None
    assert indicator(r,"dividend_stability")["coverage_pct"] == 0


def test_dividend_three_year_continuity_and_stability():
    m=ConstituentSnapshot(symbol="600001",weight=100,annual_financials=[financial(y,dividend_per_share=v,close_price=20) for y,v in [(2023,1),(2024,1),(2025,2)]])
    r=analyze([m],"dividend_low_volatility")
    assert indicator(r,"dividend_continuity")["value"] == 100
    assert indicator(r,"dividend_yield")["value"] == 10
    assert indicator(r,"dividend_stability")["value"] == pytest.approx(35.355339,rel=1e-5)


def test_annual_merge_filters_future_quarters_and_missing_notice():
    rows=[{"SECURITY_CODE":"600001","REPORT_DATE":"2025-12-31","NOTICE_DATE":"2026-04-01","NETCASH_OPERATE":10,"CONSTRUCT_LONG_ASSET":3},{"SECURITY_CODE":"600001","REPORT_DATE":"2026-06-30","NOTICE_DATE":"2026-08-01","NETCASH_OPERATE":200},{"SECURITY_CODE":"600001","REPORT_DATE":"2024-12-31","NETCASH_OPERATE":300},{"SECURITY_CODE":"600001","REPORT_DATE":"2023-12-31","NOTICE_DATE":"2026-10-02","NETCASH_OPERATE":400}]
    result=merge_annual_financials("600001",date(2026,9,30),rows,[],[],[],{})
    assert len(result)==1
    assert result[0].operating_cash_flow == 10
    assert result[0].capital_expenditure == 3


def test_annual_dividend_sums_interim_and_final_only_implemented():
    base={"SECURITY_CODE":"600001","NOTICE_DATE":"2026-07-01","ASSIGN_PROGRESS":"实施分配","EX_DIVIDEND_DATE":"2026-07-10"}
    dividends=[dict(base,REPORT_DATE="2025-06-30",PRETAX_BONUS_RMB=5),dict(base,REPORT_DATE="2025-12-31",PRETAX_BONUS_RMB=10),dict(base,REPORT_DATE="2024-12-31",PRETAX_BONUS_RMB=99,ASSIGN_PROGRESS="董事会预案")]
    r=merge_annual_financials("600001",date(2026,9,30),[],[],[],dividends,{"600001":{"close":10,"market_cap":1000,"date":date(2026,9,30)}})
    assert len(r)==1
    assert r[0].dividend_per_share == 1.5


def test_price_parser_sorts_deduplicates_and_cni_pct():
    rows=[{"日期":"2025-01-03","收盘":110,"开盘":110,"最高":110,"最低":110,"涨跌幅":.1},{"日期":"2025-01-01","收盘":100,"开盘":100,"最高":100,"最低":100},{"日期":"2025-01-03","收盘":111,"开盘":111,"最高":111,"最低":111,"涨跌幅":.11},{"日期":"2025-01-04","收盘":float("nan") }]
    r=parse_price_rows("980092",rows,provider="cni")
    assert [p.close for p in r]==[100,111]
    assert r[-1].change_pct==11


def test_strategy_price_sources_are_independent_on_failure(monkeypatch,tmp_path):
    provider=StrategyDataProvider(cache_path=tmp_path/"cache.db")
    monkeypatch.setattr(provider,"_official_prices",lambda *_: (_ for _ in ()).throw(TimeoutError("官方超时")))
    monkeypatch.setattr(provider,"_fallback_prices",lambda *_: [price(1,100),price(2,110)])
    assert len(provider.fetch_prices("930740",provider="csi"))==2


def test_failure_is_cached_without_repeating_requests(monkeypatch,tmp_path):
    provider=StrategyDataProvider(cache_path=tmp_path/"cache.db")
    calls=[]
    def failed(*_args,**_kwargs):
        calls.append(1)
        raise TimeoutError("网络故障")
    monkeypatch.setattr(provider,"_get_json",failed)
    assert provider._cached_report("RPT_DMSK_FN_CASHFLOW",["600001"],date(2026,9,30))[0]==[]
    assert provider._cached_report("RPT_DMSK_FN_CASHFLOW",["600001"],date(2026,9,30))[0]==[]
    assert len(calls)==1


def test_annual_window_before_new_year_report_disclosure():
    member=ConstituentSnapshot(symbol="600001",weight=100,annual_financials=[financial(y,operating_cash_flow=10,capital_expenditure=2,net_profit=5,market_cap=100) for y in [2022,2023,2024]])
    snapshot=StrategySnapshot(source="官方",as_of=date(2026,2,1),strategy_kind="free_cash_flow",members=[member])
    result=IndexStrategyAnalyzer().analyze(IndexAnalysisContext(target=target(),strategy_data=snapshot))
    assert result.metrics["financial_years"]==[2022,2023,2024]
    assert indicator(result,"free_cash_flow_continuity")["value"]==100


def test_tencent_quote_units_and_announcement_day(monkeypatch,tmp_path):
    provider=StrategyDataProvider(cache_path=tmp_path/"cache.db")
    parts=["0"]*50
    parts[2]="600001";parts[3]="10.5";parts[30]="20260930150000";parts[45]="123.45"
    class Response:
        text='v_sh600001="'+"~".join(parts)+'";'
        def raise_for_status(self):
            return None
    monkeypatch.setattr("index.strategy_data.requests.get",lambda *_args,**_kwargs:Response())
    result=provider._tencent_quotes(["600001"],date(2026,10,1))
    assert result["600001"]["market_cap"]==pytest.approx(123.45e8)
    assert result["600001"]["close"]==10.5
    assert result["600001"]["date"]=="2026-09-30"


def test_etf_fallback_requires_qfq_not_raw_day(monkeypatch,tmp_path):
    provider=StrategyDataProvider(cache_path=tmp_path/"cache.db")
    monkeypatch.setattr(provider,"_get_json",lambda *_args,**_kwargs:{"data":{"sh515300":{"day":[["2026-09-30","1","1.1","1.1","1","0"]]}}})
    with pytest.raises(ValueError,match="前复权"):
        provider._tencent_etf_prices("515300",date(2026,10,1))


def test_markdown_strategy_hides_unused_tags_and_keeps_aligned_returns():
    from index.build_single import IndexReportBuilder, render_index_report_markdown
    ctx=IndexAnalysisContext(target=target(),price_data=[price(1,100),price(2,110),price(3,121)],benchmark_prices=[price(2,100,"000300"),price(3,105,"000300")])
    result=IndexPerformanceAnalyzer().analyze(ctx)
    assert result.metrics["return_pct"]==pytest.approx(21)
    assert result.metrics["benchmark_aligned_index_return_pct"]==pytest.approx(10)
    report=IndexReportBuilder().build(ctx,[result])
    markdown=render_index_report_markdown(report)
    assert "| 区间收益率 | 21.00% |" in markdown
    assert "| 同日指数区间收益 | 10.00% |" in markdown
    assert "## 舆情面" not in markdown
    assert "| 舆情面 |" not in markdown
    assert "| 资金面 |" not in markdown
    assert "| 宏观面 |" not in markdown


def test_strategy_pipeline_rejects_empty_price_success(monkeypatch):
    from index.pipeline import IndexPipeline
    pipeline=IndexPipeline()
    monkeypatch.setattr(pipeline._collector,"collect",lambda *_args,**_kwargs:IndexAnalysisContext(target=target()))
    result=pipeline.run([target()])
    assert result.reports==[]
    assert "真实行情不足" in result.errors[0]


@pytest.mark.parametrize("industry", [float("nan"), None, "  "])
def test_official_missing_industry_does_not_inflate_coverage(tmp_path, monkeypatch, industry):
    """官方文件空行业须保留为未知，不能把 NaN 字符串计入覆盖率。"""
    from types import SimpleNamespace

    import pandas as pd

    provider = StrategyDataProvider(tmp_path / "cache.db")
    frame = pd.DataFrame([["2026-09-30", "600001", "甲", industry, 100, 100]])
    monkeypatch.setattr("index.strategy_data.requests.get", lambda *a, **k: SimpleNamespace(content=b"file", raise_for_status=lambda: None))
    monkeypatch.setattr("index.strategy_data.pd.read_excel", lambda *a, **k: frame)
    members, _ = provider._members("980092", "cni")
    assert members[0].industry is None
    result = analyze(members)
    assert result.metrics["industry_coverage_pct"] == 0
    assert result.metrics["industry_weights"] == []
