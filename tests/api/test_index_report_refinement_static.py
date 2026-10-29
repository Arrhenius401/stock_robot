"""新版指数报告的展示与降级契约。"""
import json

from tests.api.test_static import _DOM_STUB, _module_url, _run_node


def test_index_report_uses_shared_display_and_quality(tmp_path):
    script = _DOM_STUB + r'''
const {renderStockReport}=await import(__RENDERER__);
const report={report_version:2,code:'000300',name:'沪深300',overview:{display_rows:[{label:'PE',value:'13.15',note:'月末'}]},
 section_technical:{display:{rows:[{label:'ADX(14)',value:'24',note:'过渡'}]}},
 section_performance:{display:{windows:[{label:'近12个月',start_date:'2025-10-28',end_date:'2026-10-28',return_pct:3,max_drawdown_pct:-5,annualized_volatility_pct:12}],benchmark_rows:[{label:'同日基准',value:'2%'}],etf_rows:[{label:'ETF自身区间',value:'1%'}]}},
 section_valuation:{display:{rows:[{label:'PB',value:'1.36',note:'月末'}]}},
 section_capital:{summary:'资金不可用'},section_macro:{summary:'宏观不可用'},
 section_sentiment:{metrics:{items:[{title:'要闻',url:'https://example.com',source:'来源'}, {title:'新闻二',url:'javascript:alert(1)'},{title:'新闻三'},{title:'新闻四'}]}},
 data_quality:{summary_rows:[{title:'估值',status:'partial',sample:'月度',cutoff:'2026-09-30',source:'官方'}],limitations:[{dimension:'估值',label:'缺失原因',value:'历史缺失'}]},
 commentary:'## 1.核心判断\n受限摘要',commentary_status:'failed',commentary_retry_id:'token',disclaimer:['免责声明内容'],position_coeff:0.9};
let count=0;const article=renderStockReport(report,{onRetryCommentary:()=>{count++}});
const ids=article.children.map(x=>x.id);
for(const id of ['index-overview','index-technical','index-valuation','index-background','report-commentary','report-quality','report-disclaimer'])if(!ids.includes(id))throw Error('缺少章节 '+id);
if(article.textContent.includes('仓位系数'))throw Error('旧仓位泄露');
if(!article.textContent.includes('历史缺失')||!article.textContent.includes('ADX(14)'))throw Error('展示或质量原因缺失');
if(!article.textContent.includes('3.00%')||!article.textContent.includes('同日基准')||!article.textContent.includes('ETF自身区间'))throw Error('表现、基准或ETF独立展示缺失');
const background=article.children.find(x=>x.id==='index-background');
const details=descendants(background).find(x=>x.tagName==='DETAILS');
if(!details||details.textContent.includes('要闻')||!details.textContent.includes('新闻四'))throw Error('前三条默认显示，其余折叠');
if(descendants(background).some(x=>x.tagName==='A'&&(x.getAttribute('href')||'').startsWith('javascript:')))throw Error('危险链接');
await byClass(article,'report-retry-commentary')[0].click();if(count!==1)throw Error('重试不可用');
const strategy=renderStockReport({...report,visible_sections:['technical','valuation','strategy','performance'],section_capital:{summary:'资金流向：暂无数据'}});
const strategyBackground=strategy.children.find(x=>x.id==='index-background');
if(strategyBackground.textContent.includes('资金流向：暂无数据')||strategyBackground.textContent.includes('要闻'))throw Error('不适用维度不得展示旧摘要或新闻');
if(!strategyBackground.textContent.includes('不适用'))throw Error('应明确标注不适用');
'''.replace('__RENDERER__', json.dumps(_module_url('src/api/static/js/report-renderer.js')))
    _run_node(tmp_path, script)
