"""三市场估值日期共同规则：不改变原始数值、日期或评分。"""
from datetime import date

import pytest

from data.schemas import AnalysisContext, ValuationData
from report.valuation_dates import date_presentation, stock_date_presentation


def test_same_dates_are_only_in_heading_and_different_date_follows_value():
    metrics = {"pe_ttm": 20.0, "pb": 2.0, "ps_ttm": 3.0}
    view = date_presentation(metrics, {"pe_ttm": date(2026, 10, 22),
        "pb": date(2026, 10, 22), "ps_ttm": date(2026, 10, 21)})
    assert view["cutoff"] == "估值数据截至 2026-10-22；日期不同的指标另行注明。"
    assert view["metrics"]["pe_ttm"] == "20.00"
    assert view["metrics"]["pb"] == "2.00"
    assert view["metrics"]["ps_ttm"] == "3.00（截至 2026-10-21）"
    assert metrics["ps_ttm"] == 3.0


def test_unknown_dates_are_not_assumed_and_missing_values_not_dated():
    view = date_presentation({"pe_ttm": 20, "pb": 2, "ps_ttm": None}, {"pb": date(2026, 10, 22)})
    assert view["metrics"]["pe_ttm"] == "20.00（日期未记录）"
    assert view["metrics"]["pb"] == "2.00"
    assert view["metrics"]["ps_ttm"] == "暂无数据"
    assert date_presentation({"pe_ttm": 20}, {})["cutoff"] == "估值数据日期未记录。"


@pytest.mark.parametrize("value", [-2.0, 0.0, 1000.0])
def test_summary_dates_cover_negative_zero_and_grouped_numbers_without_duplication(value):
    suffix = "（不适用）" if value <= 0 else ""
    metrics = {"pb": value}
    summary = f"PB {value:.2f}{suffix}"
    view = date_presentation(metrics, {}, summary)
    assert "（日期未记录）" in view["summary"]
    repeated = date_presentation(metrics, {}, view["summary"])
    assert repeated["summary"] == view["summary"]


def test_a_history_summary_date_can_differ_from_current_snapshot():
    view = date_presentation({"pe_ttm": 20, "pe_percentile": 42},
        {"pe_ttm": "2026-10-22", "pe_percentile": "2026-10-21"},
        "PE(TTM) 20.00；PE处于历史42.00%分位")
    assert "42.00%（截至 2026-10-21）分位" in view["summary"]


def test_quality_footer_does_not_promote_a_collection_date():
    from report.quality import _sample_dates

    ctx = AnalysisContext(symbol="600519", name="测试", valuation_data=ValuationData(
        symbol="600519", date=date(2026, 10, 23), pe_ttm=20))
    assert _sample_dates(ctx, "valuation")[1] == "未记录"
    assert ctx.valuation_data is not None
    ctx.valuation_data.pe_as_of = date(2026, 10, 21)
    assert _sample_dates(ctx, "valuation")[1] == "2026-10-21"


@pytest.mark.parametrize("symbol,market,known", [("600519", "a-shares", False), ("00700.HK", "hk", True)])
def test_legacy_cache_does_not_promote_a_share_collection_day(symbol, market, known):
    val = ValuationData(symbol=symbol, date=date(2026, 10, 22), pe_ttm=20, pb=2)
    ctx = AnalysisContext(symbol=symbol, name="测试", market=market, valuation_data=val)
    view = stock_date_presentation(ctx, {"pe_ttm": 20, "pb": 2})
    assert ("日期未记录" not in view["metrics"]["pe_ttm"]) is known


def test_real_a_snapshot_date_is_not_collection_date(mocker):
    from data.akshare import AkShareAdapter

    fields = [""] * 50
    fields[30], fields[39], fields[46] = "20261021150000", "20", "2"
    mocker.patch("requests.get").return_value.text = "~".join(fields)
    val = AkShareAdapter().fetch("600519", data_type="valuation")[0]
    assert val.pe_as_of == date(2026, 10, 21)
    assert val.pb_as_of == date(2026, 10, 21)


def test_bad_a_snapshot_timestamp_does_not_discard_values(mocker):
    from data.akshare import AkShareAdapter

    fields = [""] * 50
    fields[30], fields[39], fields[46] = "not-a-date", "20", "2"
    mocker.patch("requests.get").return_value.text = "~".join(fields)
    val = AkShareAdapter().fetch("600519", data_type="valuation")[0]
    assert val.pe_ttm == 20
    assert val.pe_as_of is None


def test_index_markdown_preserves_independent_dates_and_raw_values():
    from data.schemas import AnalysisTarget, IndexAnalysisContext, IndexValuationData
    from index.analysis.valuation import IndexValuationAnalyzer
    from index.build_single import IndexReportBuilder, render_index_report_markdown

    val = IndexValuationData(symbol="000015", date=date(2026, 10, 22), pe_ttm=20, pb=2,
                             pe_as_of=date(2026, 10, 22), pb_as_of=date(2026, 9, 30))
    ctx = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol="000015",
        name="上证红利", index_style="strategy"), valuation_data=val)
    result = IndexValuationAnalyzer().analyze(ctx)
    original = result.model_dump()
    report = IndexReportBuilder().build(ctx, [result])
    markdown = render_index_report_markdown(report)
    assert markdown.count("估值数据截至 2026-10-22") == 1
    assert "2.00（截至 2026-09-30）" in markdown
    assert "PB 2.00（截至 2026-09-30）" in report.section_valuation["display"]["summary"]
    assert result.model_dump() == original


def test_stock_markdown_and_payload_use_same_dates():
    from analysis.valuation import ValuationAnalyzer
    from data.enrichers.valuation_enricher import ValuationEnricher
    from report.payload import build_stock_payload
    from report.scoring import build_report

    val = ValuationData(symbol="00700.HK", date=date(2026, 10, 22), pe_ttm=20, pb=2,
                        ps_ttm=3, ps_as_of=date(2026, 10, 21), ps_source="测试")
    ctx = AnalysisContext(symbol=val.symbol, name="测试", market="hk", valuation_data=val)
    ValuationEnricher().enrich(ctx)
    result = ValuationAnalyzer().analyze(ctx)
    markdown = build_report(ctx.symbol, ctx.name, [result], {}, ctx, no_llm=True)
    payload = build_stock_payload(ctx.symbol, ctx.name, [result], {}, ctx, no_llm=True)
    display = payload["dimensions"]["valuation"]["display"]
    assert display["cutoff"] in markdown
    assert display["metrics"]["ps_ttm"] in markdown
    assert "3.00（截至 2026-10-21）" in markdown
    assert markdown.index("PS(TTM)：测试") > markdown.index("## 七、数据覆盖与时效")


def test_index_comparison_has_one_baseline_and_dates_on_exceptions():
    from data.schemas import AnalysisTarget, IndexAnalysisContext, IndexValuationData
    from index.analysis.valuation import IndexValuationAnalyzer
    from index.build_compare import IndexCompareReportBuilder
    from index.build_single import IndexReportBuilder

    contexts, reports = [], []
    for index, end in enumerate((date(2026, 10, 22), date(2026, 10, 21), None)):
        ctx = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol=f"00000{index}",
            name="测试", index_style="strategy"), valuation_data=IndexValuationData(symbol=f"00000{index}",
            date=date(2026, 10, 23), pe_ttm=20, pe_percentile=50, percentile_sample_end=end))
        contexts.append(ctx)
        reports.append(IndexReportBuilder().build(ctx, [IndexValuationAnalyzer().analyze(ctx)]))
    compare = IndexCompareReportBuilder().build(contexts, reports)
    assert "2026-10-22" in compare.valuation_cutoff
    assert compare.rows[0]["pe_pct"] == "50.00%"
    assert compare.rows[1]["pe_pct"] == "50.00%（截至 2026-10-21）"
    assert compare.rows[2]["pe_pct"] == "50.00%（日期未记录）"
