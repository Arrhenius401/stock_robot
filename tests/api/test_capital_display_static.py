"""报告展示元数据在网页资金卡片与比较表中使用，计算字段不变。"""
import json
from datetime import date

from data.schemas import AnalysisTarget, CapitalFlowData, IndexAnalysisContext
from index.analysis.capital_flow import CapitalFlowAnalyzer
from index.build_compare import IndexCompareReportBuilder
from index.build_single import IndexReportBuilder
from tests.api.test_static import _DOM_STUB, _module_url, _run_node


def test_web_uses_capital_display_without_recalculating_summary(tmp_path):
    ctx = IndexAnalysisContext(target=AnalysisTarget(
        target_type="index", symbol="000300", name="沪深300", index_style="broad"))
    ctx.capital_flow = CapitalFlowData(symbol="000300", date=date(2026, 10, 20), north_bound=0)
    report = IndexReportBuilder().build(ctx, [CapitalFlowAnalyzer().analyze(ctx)])
    compare = IndexCompareReportBuilder().build([ctx], [report])
    payload = {"reports": [report.model_dump(mode="json")], "compare": {"headers": compare.headers, "rows": compare.rows}}
    script = _DOM_STUB + "\nconst payload=" + json.dumps(payload, ensure_ascii=False) + ";\n"
    script += "const {api}=await import(" + json.dumps(_module_url("src/api/static/js/api.js")) + ");\n"
    script += "const {openIndex}=await import(" + json.dumps(_module_url("src/api/static/js/indexview.js")) + ");\n"
    script += r'''
const box=makeElement('indexContent');
api.index=async()=>payload;
await openIndex('000300');
if(!box.textContent.includes('无净流入或净流出')) throw new Error('新版资金未正确解释真实零值');
if(payload.compare.headers.includes('资金') || payload.compare.headers.includes('综合评级')) throw new Error('新版比较表仍将资金作为投票标签');
if(!box.textContent.includes(payload.reports[0].section_capital.display.summary)) throw new Error('新版资金摘要未使用共享展示文案');
if(box.textContent.includes('净流出 0.0亿')) throw new Error('零值被误报为流出');
if(payload.reports[0].position_coeff!==null || /仓位|综合评级/.test(box.textContent)) throw new Error('新版出现仓位或评级');
// 旧归档仍沿用已有展示元数据；新契约不反向重写旧快照。
const legacy={...payload.reports[0],report_version:1,position_coeff:0.4,composite_comment:'旧归档结论',visible_sections:['capital']};
api.index=async()=>({reports:[legacy],compare:{headers:['指数名称','资金'],rows:payload.compare.rows}});
await openIndex('000300');
if((box.textContent.match(/无净流入或净流出/g)||[]).length<2) throw new Error('旧归档资金卡片与比较表展示不一致');
if(!descendants(box).some(e=>e.innerHTML.includes('旧归档结论'))||!box.textContent.includes('0.4')) throw new Error('旧归档结论或系数丢失');
if(box.textContent.includes('净流出 0.0亿')) throw new Error('旧归档忽略共享零值展示文案');
'''
    _run_node(tmp_path, script)
