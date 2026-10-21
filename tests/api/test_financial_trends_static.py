"""网页消费与 Markdown 同源的固定模板列表，不计算或请求 AI。"""
import json

from tests.api.test_static import _DOM_STUB, _module_url, _run_node


def test_web_shows_fixed_trends_once_and_labels_source(tmp_path):
    script = _DOM_STUB + "\nconst {renderStockReport}=await import(" + json.dumps(
        _module_url("src/api/static/js/report-renderer.js")) + ");\n"
    script += r'''
const text='2026-06-30 较 2025-06-30：净利润扭亏为盈（-300.00 万 → 500.00 万）。';
const report=renderStockReport({market:'hk',dimensions:{financial:{status:'partial',
 metrics:{operating_trends:[text],trend_note:'币种未记录，金额比较受限',cash_flow_source:'推算值（非直接披露）',
 gross_margin:0.5724660617132261,gross_margin_basis:'计算值：同期间披露毛利÷营运收入；营运收入可能包含其他营业收入'}}}});
for(const word of ['经营趋势','趋势分析限制','经营现金流来源','扭亏为盈','推算值',
 '毛利率计算口径','毛利÷营运收入','其他营业收入','57.25%']){
 if(!report.textContent.includes(word)) throw new Error('缺少 '+word);
}
if(report.textContent.split(text).length!==2) throw new Error('摘要丢失或重复');
const limited=renderStockReport({dimensions:{financial:{status:'partial',
 metrics:{operating_trends:[],trend_note:'同口径同期样本不足'}}}});
if(limited.textContent.includes('经营趋势')) throw new Error('没有趋势时不应留下空标题');
if(!limited.textContent.includes('同口径同期样本不足')) throw new Error('降级原因丢失');
const known=renderStockReport({market:'hk',dimensions:{financial:{status:'partial',
 metrics:{amount_basis:'最新利润表原币：CNY（不代表其他报表币种）',operating_trends:[text],trend_note:''}}}});
if(known.textContent.includes('币种未记录')) throw new Error('摘要不得以固定文案否定已知币种');
if(!known.textContent.includes('CNY')) throw new Error('明确币种未披露');
'''
    _run_node(tmp_path, script)
