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
if((box.textContent.match(/无净流入或净流出/g)||[]).length<2) throw new Error('资金卡片/比较表未共同使用展示文案');
if(!descendants(box).some(e=>e.innerHTML.includes('无净流入或净流出'))) throw new Error('资金摘要未使用展示文案');
if(descendants(box).some(e=>e.innerHTML.includes('净流出 0.0亿'))) throw new Error('旧摘要仍漏出');
if(!box.textContent.includes(String(payload.reports[0].position_coeff))) throw new Error('综合系数被展示层改写');
'''
    _run_node(tmp_path, script)
