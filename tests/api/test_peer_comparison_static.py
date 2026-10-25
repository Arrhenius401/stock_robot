"""同行卡片交互只切换已生成的证据，不发起补齐请求。"""
import json

from tests.api.test_static import _DOM_STUB, _module_url, _run_node
from tests.report.test_peer_comparison import (
    comparison_sample,
    missing_latest_comparison,
)


def test_peer_comparison_card_keeps_summaries_without_peer_list_and_legacy_fallback(tmp_path):
    script = _DOM_STUB + "\nconst {renderStockReport}=await import(" + json.dumps(
        _module_url("src/api/static/js/report-renderer.js")) + ");\n"
    script += "const comparison=" + json.dumps(comparison_sample(), ensure_ascii=False) + ";\n"
    script += r'''
for (const market of ['a-shares','hk']) {
 const report={market,dimensions:{industry:{status:'partial',score:9,summary:'',
  metrics:{comparison,industry_median_pe:9999,top_peers:[{symbol:'旧同行'}]}}}};
 const article=renderStockReport(report), card=byClass(article,'peer-comparison')[0];
 if(!card)throw new Error('缺少同行专用卡片');
 for(const value of ['不计分','估值对比','经营表现','12.50%','-2.50%','样本不足','5 家',
  '估值来源','经营来源','2026-10-23']) {
  if(!card.textContent.includes(value))throw new Error('缺少 '+value+': '+card.textContent);
 }
 if(card.textContent.includes('9999')||card.textContent.includes('旧同行'))throw new Error('重复旧行业证据');
 if(card.textContent.includes('1250.00%'))throw new Error('百分数重复乘百');
 const tabs=byClass(card,'peer-period-button');
 if(tabs.length!==2)throw new Error('需要两种期间');
 await tabs[1].click();
 const panel=byClass(card,'peer-business-panel')[0];
 if(!panel.textContent.includes('6.25%')||!panel.textContent.includes('半年度累计'))throw new Error('期间未切换');
 if(tabs[1].getAttribute('aria-selected')!=='true')throw new Error('期间状态未更新');
 if(byClass(card,'peer-all').length||byClass(card,'peer-detail-table').length)throw new Error('仍展示同行明细');
 if(card.textContent.includes('查看全部同行')||card.textContent.includes('同行6'))throw new Error('同行列表文字泄露');
 if(JSON.stringify(report.dimensions.industry.metrics.comparison)!==JSON.stringify(comparison))throw new Error('改写数据');
}
const failed=structuredClone(comparison);
failed.periods.latest={label:'最新一期',metrics:[],peers:[],reason:'经营接口超时'};
failed.errors=['经营接口超时'];
const failure=renderStockReport({dimensions:{industry:{status:'partial',metrics:{comparison:failed}}}});
if(!failure.textContent.includes('经营接口超时'))throw new Error('失败原因被隐藏');
const legacy=renderStockReport({dimensions:{industry:{status:'ok',score:8,metrics:{industry:'银行',peer_count:8}}}});
if(!legacy.textContent.includes('银行'))throw new Error('旧报告不可读');
'''
    _run_node(tmp_path, script)


def test_latest_period_reason_remains_visible_with_three_empty_metrics(tmp_path):
    comparison = missing_latest_comparison()
    assert len(comparison["periods"]["latest"]["metrics"]) == 3
    script = _DOM_STUB + "\nconst {renderStockReport}=await import(" + json.dumps(
        _module_url("src/api/static/js/report-renderer.js")) + ");\n"
    script += "const comparison=" + json.dumps(comparison, ensure_ascii=False) + ";\n"
    script += r'''
const report=renderStockReport({dimensions:{industry:{status:'partial',metrics:{comparison}}}});
await byClass(report,'peer-period-button')[1].click();
const panel=byClass(report,'peer-business-panel')[0];
if(!panel.textContent.includes(comparison.periods.latest.reason))throw new Error('最新期缺失原因被空指标遮住');
if(!panel.textContent.includes('2026-06-30'))throw new Error('缺少实际已知最新期');
if(!panel.textContent.includes('旧期参考·年报')||panel.textContent.includes('最新一期·年报'))throw new Error('旧年报被误标为最新期');
'''
    _run_node(tmp_path, script)
