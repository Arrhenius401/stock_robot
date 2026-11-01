"""网页消费共享日期展示值，旧 payload 无展示字段时仍可读取。"""
import json

from report.valuation_dates import date_presentation
from tests.api.test_static import _DOM_STUB, _module_url, _run_node


def test_stock_web_displays_one_cutoff_and_inline_exception(tmp_path):
    display = date_presentation({"pe_ttm": 20, "pb": 2, "ps_ttm": 3},
                                {"pe_ttm": "2026-10-22", "pb": "2026-10-22", "ps_ttm": "2026-10-21"})
    payload = {"symbol": "00700.HK", "market": "hk", "dimensions": {"valuation": {
        "status": "ok", "metrics": {"pe_ttm": 20, "pb": 2, "ps_ttm": 3}, "display": display}}}
    script = _DOM_STUB + "\nconst payload=" + json.dumps(payload, ensure_ascii=False) + ";\n"
    script += "const {renderStockReport}=await import(" + json.dumps(_module_url("src/api/static/js/report-renderer.js")) + ");\n"
    script += r'''
const page=renderStockReport(payload);
if(!page.textContent.includes('估值数据截至 2026-10-22')) throw new Error('缺少统一日期');
if(!page.textContent.includes('3.00（截至 2026-10-21）')) throw new Error('PS异日未跟随数值');
if(page.textContent.includes('2.00（截至')) throw new Error('同日PB重复日期');
'''
    _run_node(tmp_path, script)


def test_index_web_summary_and_comparison_keep_independent_dates(tmp_path):
    from datetime import date

    from data.schemas import AnalysisTarget, IndexAnalysisContext, IndexValuationData
    from index.analysis.valuation import IndexValuationAnalyzer
    from index.build_compare import IndexCompareReportBuilder
    from index.build_single import IndexReportBuilder

    contexts, reports = [], []
    for code, end in (("000015", date(2026, 10, 22)), ("000922", date(2026, 10, 21))):
        val = IndexValuationData(symbol=code, date=end, pe_ttm=20, pb=2, pe_percentile=50,
            pe_as_of=end, pb_as_of=date(2026, 9, 30), percentile_sample_end=end)
        ctx = IndexAnalysisContext(target=AnalysisTarget(target_type="index", symbol=code,
            name=code, index_style="strategy"), valuation_data=val)
        contexts.append(ctx)
        reports.append(IndexReportBuilder().build(ctx, [IndexValuationAnalyzer().analyze(ctx)]))
    compare = IndexCompareReportBuilder().build(contexts, reports)
    payload = {"reports": [report.model_dump(mode="json") for report in reports],
               "compare": {"headers": compare.headers, "rows": compare.rows, "valuation_cutoff": compare.valuation_cutoff}}
    script = _DOM_STUB + "\nconst payload=" + json.dumps(payload, ensure_ascii=False) + ";\n"
    script += "const {api}=await import(" + json.dumps(_module_url("src/api/static/js/api.js") + "?v=20261030-index-refresh") + ");\n"
    script += "const {openIndex}=await import(" + json.dumps(_module_url("src/api/static/js/indexview.js")) + ");\n"
    script += r'''
const box=makeElement('indexContent');
api.index=async()=>payload;
await openIndex('000015 000922');
if(!box.textContent.includes('50.00%（截至 2026-10-21）')) throw new Error('对比表漏异日日期');
const articles=byClass(box,'index-report');
if(articles.length!==2) throw new Error('新版指数报告数量不符');
for(const [index,article] of articles.entries()) {
 const rows=descendants(article).filter(e=>e.tagName==='TR').map(e=>e.children.map(c=>c.textContent));
 if(!rows.some(c=>c[0]==='PB'&&c[1]==='2.00'&&c[2].includes('2026-09-30'))) throw new Error('新版PB数值和独立日期未保留');
 const end=index===0?'2026-10-22':'2026-10-21';
 if(!rows.some(c=>c[0]==='PE'&&c[1]==='20.00'&&c[2].includes(end))) throw new Error('新版PE日期被其他指数或PB覆盖');
}
if(!box.textContent.includes(payload.compare.valuation_cutoff)) throw new Error('比较表统一日期缺失');
// 旧快照保留原日期展示，仍能读取行内异日标记。
api.index=async()=>({reports:payload.reports.map(r=>({...r,report_version:1}))});
await openIndex('000015 000922');
if(!descendants(box).some(e=>e.innerHTML.includes('PB 2.00（截至 2026-09-30）'))) throw new Error('旧报告摘要漏PB日期');
if((box.textContent.match(/估值数据截至 2026-10-22/g)||[]).length<1) throw new Error('旧报告概览日期丢失');
'''
    _run_node(tmp_path, script)
