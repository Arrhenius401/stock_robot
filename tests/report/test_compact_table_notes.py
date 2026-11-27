"""表注保留必要的财务口径，避免推断缺失数值。"""

from report.table_notes import prepare_table


def test_notes_follow_first_table_reference_after_reordering_and_removal():
    """编号取决于当前表格顺序，不取决于指标名称或说明生成顺序。"""
    import re

    metrics = {"revenue": 100, "total_assets": 300, "total_equity": 200,
               "common_equity": 180, "roe": .1, "gross_margin": .3, "net_margin": .2,
               "amount_basis": "金额币种：CNY"}
    digits = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")

    def references(view):
        return [int(marker.translate(digits)) for label in view["metrics"]
                for marker in re.findall(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+", label)]

    view = prepare_table("financial", metrics)
    assert references(view) == [1, 1, 1, 2, 3, 4, 4]
    assert len(view["notes"]) == 4
    reordered = prepare_table("financial", dict(reversed(list(metrics.items()))))
    assert references(reordered) == [1, 1, 2, 3, 4, 4, 4]
    for current in (view, reordered):
        assert [int(note.split(" ", 1)[0].translate(digits)) for note in current["notes"]] == [1, 2, 3, 4]
    del metrics["amount_basis"]
    assert references(prepare_table("financial", metrics)) == [1, 2, 3, 3]


def test_shared_note_reuses_number_and_double_digit_markers_are_contiguous():
    from report.table_notes import number_table_notes

    values = {f"字段{index}": index for index in range(12)}
    groups = {(f"字段{index}",): [f"说明{index}"] for index in reversed(range(12))}
    view = number_table_notes(values, groups)
    assert next(iter(view["metrics"])) == "字段0¹"
    assert list(view["metrics"])[9:] == ["字段9¹⁰", "字段10¹¹", "字段11¹²"]
    assert view["notes"][0] == "¹ 说明0。" and view["notes"][-1] == "¹² 说明11。"
    shared = number_table_notes({"甲": 1, "乙": 2}, {("甲",): ["相同说明"], ("乙",): ["相同说明"]})
    assert list(shared["metrics"]) == ["甲¹", "乙¹"]
    assert shared["notes"] == ["¹ 相同说明。"]
    overlap = number_table_notes({"甲": 1, "乙": 2}, {("乙",): ["仅乙"], ("甲", "乙"): ["共用"]})
    assert list(overlap["metrics"]) == ["甲¹", "乙¹、²"]
    assert overlap["notes"] == ["¹ 共用。", "² 仅乙。"]


def test_table_notes_have_bidirectional_references_and_keep_raw_evidence():
    """防止新元数据和新增说明绕过表注出口，不锁定整篇报告文案。"""
    import copy
    import re

    for dimension, metrics in [
        ("financial", {"revenue": 100, "common_equity": None, "gross_margin": .5,
                       "net_margin": None, "operating_cash_flow": None,
                       "cash_flow_limitation": "现金流期间或币种证据不足，不推算",
                       "amount_currency_reason": "币种冲突，未核验", "new_internal_metadata": "不要自动展示"}),
        ("valuation", {"pe_ttm": 20, "pb": 3, "pe_percentile": 12,
                       "valuation_notes": ["估值源失败；日期未记录", "估值源失败；日期未记录"]}),
    ]:
        original = copy.deepcopy(metrics)
        view = prepare_table(dimension, metrics)
        refs = set(re.findall(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+", " ".join(view["metrics"])))
        notes = [re.match(r"([⁰¹²³⁴⁵⁶⁷⁸⁹]+) ", note) for note in view["notes"]]
        assert all(notes)
        assert refs == {match[1] for match in notes if match}
        assert len(view["notes"]) == len(set(view["notes"]))
        assert "new_internal_metadata" not in str(view)
        assert metrics == original
        if dimension == "financial":
            assert "证据不足" in str(view) and "币种冲突" in str(view)
            assert "不以股东权益替代" in str(view)
        else:
            assert str(view).count("估值源失败") == 1


def test_long_window_facts_are_table_values_and_notes_keep_their_subject():
    view = prepare_table("valuation", {"pe_ttm": 11.69, "pe_percentile": .3, "ps_ttm": 2.1}, details=[
        "PE 近五年历史：百度同源实测估值，2021-11-30 至 2026-11-26，有效采样点 617，分位 1.1%；仅作展示，不参与评分",
        "PS(TTM)：市值÷TTM营收；期间截至2026-09-30；仅展示；来源东财日频估值历史",
    ], cutoff="估值数据截至 2026-11-27；PE分位区间：2025-11-27 至 2026-11-26，365个有效样本；百度同源实测估值")
    history = view["metrics"]["PE 近五年历史"]
    assert "617" in history and "1.1%" in history and "百度" in history and "不参与评分" in history
    assert "2025-11-27" in str(view["notes"]) and "365" in str(view["notes"])
    assert any("市值÷最近12个月营收；期间截至2026-09-30；仅展示" in note for note in view["notes"])
    ps_marker = next(label for label in view["metrics"] if label.startswith("PS(TTM)")).removeprefix("PS(TTM)")
    assert any(note.startswith(ps_marker + " ") and "东方财富逐日估值历史" in note for note in view["notes"])


def test_payload_and_markdown_share_the_table_contract(tmp_path):
    import html
    import json

    from data.schemas import AnalysisContext, AnalysisResult
    from report.payload import build_stock_payload
    from report.scoring import build_report
    from tests.api.test_static import _DOM_STUB, _module_url, _run_node

    context = AnalysisContext(symbol="AAPL", name="苹果", market="us", extra_analysis_enabled=False)
    result = AnalysisResult(dimension="financial", status="partial", summary="现金流源采集失败", metrics={
        "basic_eps": 2.3, "common_equity": None, "net_margin": .25,
        "net_margin_basis": "单季净利润÷营业收入；期间2026-06-28至2026-09-30；来源东财",
        "operating_cash_flow": None, "cash_flow_limitation": "现金流源采集失败",
    })
    markdown = html.unescape(build_report(context.symbol, context.name, [result], {}, context, no_llm=True))
    payload = build_stock_payload(context.symbol, context.name, [result], {}, context, no_llm=True)
    dimension = payload["dimensions"]["financial"]
    assert dimension["metrics"]["net_margin"] == .25
    view = dimension["table_view"]
    for label, value in view["metrics"].items():
        assert label in markdown and str(value) in markdown
    assert all(note in markdown for note in view["notes"])
    assert "源采集失败" in markdown and "2026-06-28" in markdown
    script = _DOM_STUB + "\nconst {renderStockReport}=await import(" + json.dumps(
        _module_url("src/api/static/js/report-renderer.js")) + ");\n"
    script += "const data=" + json.dumps(payload, ensure_ascii=False) + ";\n"
    script += r'''
const article=renderStockReport(data);
const financial=descendants(article).find(node=>node.id==='report-financial');
const view=data.dimensions.financial.table_view;
for (const [label,value] of Object.entries(view.metrics)) {
  if (!financial.textContent.includes(label) || !financial.textContent.includes(String(value))) throw new Error('网页表格与共享副本不一致');
}
for (const note of view.notes) if (!financial.textContent.includes(note)) throw new Error('网页表注遗漏');
if (descendants(financial).filter(node=>node.classList.contains('report-table-note')).length!==view.notes.length) throw new Error('网页重复或遗漏表注');
'''
    _run_node(tmp_path, script)


def test_index_table_deduplicates_dates_but_keeps_missing_history_reason():
    from report.table_notes import index_valuation_table

    display = {"rows": [{"label": "PB", "value": "1.26", "note": "2026-11-26；官方每日估值"}],
               "details": ["PB数据日期：2026-11-26", "PB计算口径：官方每日估值"]}
    view = index_valuation_table(display, ["官方未提供可验证的PB日频历史，历史分位不可用"])
    assert len(view["rows"]) == 1 and view["rows"][0] == display["rows"][0]
    assert "PB数据日期" not in str(view["table_notes"])
    assert "历史分位不可用" in str(view["table_notes"])
    assert view["table_notes"][0].startswith(view["table_note_marker"] + " ")


def test_rerendering_legacy_index_keeps_reasons_without_mutating_snapshot():
    from data.schemas import AnalysisResult, AnalysisTarget, IndexAnalysisContext
    from index.build_single import IndexReportBuilder, render_index_report_markdown

    ctx = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol="000300", name="沪深300", index_style="broad"))
    report = IndexReportBuilder().build(ctx, [
        AnalysisResult(dimension="index_valuation", status="unavailable", summary="估值不可用", metrics={"valuation_notes": ["估值源采集失败"]}),
        AnalysisResult(dimension="index_technical", status="unavailable", summary="行情不可用", metrics={"methodology": "技术口径限制", "errors": ["行情源失败"]}),
    ])
    for section in (report.section_technical, report.section_valuation):
        section["display"].pop("table_notes", None)
        section["display"].pop("table_note_marker", None)
    original = report.model_dump()
    markdown = render_index_report_markdown(report)
    assert "估值源采集失败" in markdown and "行情源失败" in markdown and "技术口径限制" in markdown
    assert report.model_dump() == original


def test_missing_margin_is_not_derived_from_parent_profit():
    view = prepare_table("financial", {"net_profit": 10, "revenue": 100, "gross_margin": .5,
                                       "net_margin": None})
    assert next(value for label, value in view["metrics"].items() if label.startswith("净利率")) == "暂无数据"


def test_unknown_amount_currency_cannot_be_replaced_by_directory_currency():
    view = prepare_table("financial", {"amount_basis":
        "金额币种未核实；目录列报币种：HKD（不等同于金额币种）"})
    assert "金额币种：HKD" not in str(view)
    assert "金额币种未核实" in str(view)


def test_profit_statement_scope_and_derived_margin_are_not_hidden():
    view = prepare_table("financial", {"gross_margin": .5,
        "amount_basis": "金额币种：USD（仅代表已核实的利润表金额）",
        "gross_margin_basis": "计算值：毛利÷营运收入；与营业额口径不同",
        "cash_flow_source": "期间或币种证据不足", "operating_cash_flow": None})
    text = str(view["notes"])
    assert "利润表金额" in text and "其他报表" in text
    assert "毛利÷营运收入" in text and "营业额" in text
    assert "现金流期间或币种证据不足" in text


def test_valuation_window_and_derived_restriction_remain_in_notes():
    from report.table_notes import valuation_window_notes

    notes = valuation_window_notes("估值数据截至 2026-11-20；日期不同另注。 PE、PB分位区间：2025-01-01 至 2026-11-20，420个有效样本；以本次财务分母派生估值，仅参考，不保证覆盖上市以来完整历史。")
    text = str(notes)
    assert "2025-01-01" in text and "420" in text
    assert "派生" in text and "仅参考" in text and "不代表上市以来" in text


def test_compact_peer_table_does_not_invent_rank():
    from report.peer_comparison import format_peer_comparison_markdown

    comparison = {"scope": "申万三级·机器人", "peer_count": 17, "valuation": {},
                  "periods": {}, "market_cap_rank": {"rank": 1, "total": 5, "as_of": "2026-11-20"}}
    md = format_peer_comparison_markdown(comparison, compact=True)
    assert any("同行市值排名" in line and "暂缺" in line for line in md.splitlines())
    assert "指标口径与统计方法" not in md and "#### 数据来源与统计口径" not in md


def test_margin_cache_refreshes_once_without_promoting_missing_value():
    import time
    from datetime import date

    from core.resilient_collection import ResilientCollector, Snapshot
    from data.cache import CacheEntry
    from data.collection_state import make_record
    from data.schemas import FinancialData

    row = FinancialData(symbol="688836", fiscal_quarter=date(2026, 9, 30), precision_version=1,
                        financial_source="同花顺财报", revenue=100)
    snap = Snapshot([row], make_record("同花顺财报", "complete"), CacheEntry("[]", time.time(), 3600, None))
    assert not ResilientCollector._fresh(snap)
    row.net_margin_basis = "未取得源披露净利率"
    assert ResilientCollector._fresh(snap)
    assert row.net_margin is None


def test_compact_peer_table_shows_partial_coverage_rank():
    from report.peer_comparison import format_peer_comparison_markdown

    comparison = {"scope": "申万三级·机器人", "peer_count": 16, "valuation": {},
                  "periods": {}, "market_cap_rank": {
                      "rank": 1, "total": 5, "pool_total": 17, "coverage": "5/17",
                      "as_of": "2026-11-20"}}
    md = format_peer_comparison_markdown(comparison, compact=True)
    assert "第 1／5 家（同日有效覆盖 5/17）" in md
    assert "暂缺" not in md


def test_annual_cashflow_fallback_is_distinguished_from_ttm():
    view = prepare_table('financial', {
        'capital_expenditure': None, 'capital_expenditure_latest': 100000000,
        'free_cash_flow_latest': 200000000,
        'cash_flow_latest_period': '2025-12-31，最近完整年度，非 TTM；HKD',
    })
    text = str(view)
    assert '均为 TTM' not in text
    assert 'capital_expenditure_latest' not in text
    assert '1.00 亿' in text and '非 TTM' in text
