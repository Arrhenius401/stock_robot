"""个股报告章节、缺失原因与安全链接的网页契约。"""
import json

from tests.api.test_static import _DOM_STUB, _module_url, _run_node


def test_stock_report_order_quality_and_safe_signal(tmp_path):
    script = _DOM_STUB + r'''
const {renderStockReport}=await import(__RENDERER__);
const report={
  market:'a-shares',scoring_method:'five-dimension-v1',name:'测试',
  signal:{label:'进攻',basis:'7.5分，进攻阈值7分',action:'可考虑建仓',position:'60%-80%'},
  score:{final:7.5}, score_rows:[], commentary_status:'failed',
  commentary:'量化摘要：经营承压。',
  dimensions:{financial:{status:'ok',metrics:{roe:0.16,roe_trend:[{quarter:'2026-06-30',roe:0.16}],
    operating_trends:['经营趋势事实'],trend_note:'',amount_basis:'CNY',gross_margin_basis:'披露值'}},
    sentiment:{metrics:{news_items:[{title:'新闻',publish_date:'2026-01-01',url:'https://example.com/a',source:'来源'}]}}},
  data_quality:{status:'partial',coverage:95,signal_allowed:true,dimensions:[{
    key:'financial',title:'财务',status:'ok',fields:[{label:'样本范围',value:'12期'},
      {label:'数据来源',value:'甲来源'},{label:'数据截止',value:'2026-06-30'},
      {label:'采集限制',value:'主源失败，使用备用源'},{label:'缓存状态',value:'过期缓存'}]}]}
};
let retries=0;
const article=renderStockReport(report,{onRetryCommentary:()=>{retries+=1;}});
const ids=article.children.map(x=>x.id);
const seq=['report-summary','report-financial','report-technical','report-valuation','report-industry','report-sentiment','report-score','report-commentary','report-quality','report-disclaimer'];
if(seq.some((id,i)=>ids.indexOf(id)!==i))throw new Error('章节顺序不符合确认方案: '+ids.join(','));
if(ids.includes('report-pending'))throw new Error('待验证事项不应显示');
const text=article.textContent;
if(text.includes('60%-80%')||text.includes('可考虑建仓'))throw new Error('旧仓位或操作文案泄漏');
const commentary=article.children.find(x=>x.id==='report-commentary');
if(!commentary.textContent.includes('进攻')||!commentary.textContent.includes('7.5分'))throw new Error('信号与依据须放在研判后');
if(!descendants(commentary).some(x=>x.innerHTML.includes('经营承压')))throw new Error('失败时量化摘要丢失');
await byClass(article,'report-retry-commentary')[0].click();
if(retries!==1)throw new Error('重试动作丢失');
const financial=article.children.find(x=>x.id==='report-financial');
if(financial.textContent.includes('净资产收益率走势')||financial.textContent.includes('趋势分析限制'))throw new Error('历史表或空限制不应显示');
if(!financial.textContent.includes('经营趋势事实')||!financial.textContent.includes('CNY'))throw new Error('经营趋势或口径丢失');
const quality=article.children.find(x=>x.id==='report-quality');
if(!quality.textContent.includes('主源失败，使用备用源')||!quality.textContent.includes('过期缓存'))throw new Error('采集异常与过期原因丢失');
const sentiment=article.children.find(x=>x.id==='report-sentiment');
const details=descendants(sentiment).find(x=>x.tagName==='DETAILS');
if(!details||!descendants(details).some(x=>x.tagName==='A'&&x.getAttribute('href')==='https://example.com/a'))throw new Error('新闻未折叠或来源丢失');
'''.replace('__RENDERER__', json.dumps(_module_url('src/api/static/js/report-renderer.js')))
    _run_node(tmp_path, script)


def test_angle_bracket_markdown_links_remain_safe(tmp_path):
    script = _DOM_STUB + r'''
const {renderMarkdown}=await import(__MARKDOWN__);
const rendered=renderMarkdown('[来源](<https://example.com/path?a=1&b=2>) [伪造](<javascript:alert(1)>) <script>alert(1)</script>');
if(!rendered.includes('href="https://example.com/path?a=1&amp;b=2"'))throw new Error('标准角括号链接不可用: '+rendered);
if(rendered.includes('href="javascript:')||rendered.includes('<script>'))throw new Error('危险链接或HTML注入');
const code=renderMarkdown('`[文档](<https://example.com>)`');
if(!code.includes('<code>[文档](&lt;https://example.com&gt;)</code>')||code.includes('<a '))throw new Error('行内代码应保留字面文本');
'''.replace('__MARKDOWN__', json.dumps(_module_url('src/api/static/js/markdown.js')))
    _run_node(tmp_path, script)


def test_legacy_markdown_view_hides_position_but_retains_signal(tmp_path):
    script = _DOM_STUB + r'''
const {renderStockMarkdownReport}=await import(__RENDERER__);
const original='# 历史报告\n\n> **操作信号：进攻** — 可考虑建仓/加仓（建议仓位 60%-80%）\n\n## 四、AI 综合解读\n\n原有解读\n\n## 六、数据覆盖与时效\n\n原有数据';
const article=renderStockMarkdownReport(original);
const rendered=descendants(article).map(n=>n.textContent+' '+n.innerHTML).join('\n');
if(rendered.includes('60%-80%')||rendered.includes('可考虑建仓'))throw new Error('旧报告展示泄漏仓位或操作文案');
const commentary=article.children.find(c=>c.children[0]?.textContent==='AI 综合解读');
if(!commentary||!commentary.textContent.includes('进攻')||!commentary.textContent.includes('历史报告未记录生成规则'))throw new Error('旧信号与依据说明应归入解读');
if(!original.includes('60%-80%'))throw new Error('原文不得改写');
const noSignal=renderStockMarkdownReport('# 旧报告\n\n> **操作信号：暂未生成**\n> 原因：数据不足\n\n## 四、AI 综合解读\n\n正文');
const noSignalText=descendants(noSignal).map(n=>n.textContent+' '+n.innerHTML).join(' ');
if(noSignalText.includes('原因：数据不足')||noSignalText.includes('操作信号'))throw new Error('旧占位原因残留');
const newMarkdown=renderStockMarkdownReport('# 新报告\n\n## 四、综合研判\n\n核心判断原文。\n\n### 模型信号\n\n**模型信号：进攻**');
const newCard=newMarkdown.children.find(c=>c.children[0]?.textContent==='综合研判');
const newText=newCard?descendants(newCard).map(n=>n.textContent+' '+n.innerHTML).join(' '):'';
if(!newCard||!newText.includes('模型信号：进攻'))throw new Error('新综合研判章节未识别');
'''.replace('__RENDERER__', json.dumps(_module_url('src/api/static/js/report-renderer.js')))
    _run_node(tmp_path, script)


def test_score_display_and_legacy_financial_limit_are_preserved(tmp_path):
    script = _DOM_STUB + r'''
const {renderStockReport}=await import(__RENDERER__);
const article=renderStockReport({market:'a-shares',scoring_method:'five-dimension-v1',
  score:{final:7.5},score_rows:[{label:'财务健康',score:'8.0',weight:'30%',
    detail:'年度 ROE 32.53%，得 3/3 分；有效子项 8.00/10.00 分，按原相对权重折算为 8.0/10'}],
  dimensions:{valuation:{status:'ok',metrics:{pe_static:20.2}},
    financial:{status:'ok',score_detail:'年度 ROE 32.53%，得 3/3 分'}},
  data_quality:{dimensions:[{key:'financial',title:'财务',status:'ok',fields:[
    {label:'财务包说明',value:'字段已按等效口径补齐，非单一来源完整包'}]}]}});
const score=article.children.find(x=>x.id==='report-score');
if(!score.textContent.includes('年度 ROE 32.53%')||score.textContent.includes('有效子项 8.00'))throw new Error('评分依据未正确精简');
const valuation=article.children.find(x=>x.id==='report-valuation');
if(!valuation.textContent.includes('PE（静）')||valuation.textContent.includes('pe static'))throw new Error('静态PE名称未统一');
const quality=article.children.find(x=>x.id==='report-quality');
if(!quality.textContent.includes('字段已按等效口径补齐'))throw new Error('旧财务包限制被隐藏');
'''.replace('__RENDERER__', json.dumps(_module_url('src/api/static/js/report-renderer.js')))
    _run_node(tmp_path, script)


def test_valuation_ps_reason_and_dimension_risks_are_not_duplicated(tmp_path):
    script = _DOM_STUB + r'''
const {renderStockReport}=await import(__RENDERER__);
const report={scoring_method:'five-dimension-v1',dimensions:{
 financial:{status:'ok',risk_flags:['high_debt','high_debt'],metrics:{}},
 valuation:{status:'partial',risk_flags:['high_pe_premium'],metrics:{ps_ttm:null,valuation_notes:['PS(TTM)：暂无数据']}},
 sentiment:{status:'partial',risk_flags:['major_negative_news'],metrics:{}}
}};
const article=renderStockReport(report);
const valuation=article.children.find(x=>x.id==='report-valuation');
if((valuation.textContent.match(/暂无数据/g)||[]).length!==1)throw new Error('PS缺失提示重复');
const financial=article.children.find(x=>x.id==='report-financial');
if((financial.textContent.match(/资产负债率偏高/g)||[]).length!==1)throw new Error('维度风险未去重');
const sentiment=article.children.find(x=>x.id==='report-sentiment');
if(!sentiment.textContent.includes('存在重大负面舆情'))throw new Error('舆情风险遗漏');
const commentary=article.children.find(x=>x.id==='report-commentary');
if(commentary.textContent.includes('资产负债率偏高')||commentary.textContent.includes('存在重大负面舆情'))throw new Error('研判重复风险清单');
const specific=renderStockReport({...report,dimensions:{valuation:{status:'partial',metrics:{ps_ttm:null,valuation_notes:['PS(TTM)：收入为负，不适用']}}}});
if(!specific.textContent.includes('收入为负，不适用'))throw new Error('具体估值限制不得删除');
const displayNotes=['PS(TTM)：暂无数据'];
const displayed=renderStockReport({scoring_method:'five-dimension-v1',dimensions:{valuation:{status:'partial',
  metrics:{ps_ttm:null,valuation_notes:displayNotes},
  display:{metrics:{pe_ttm:'20.43',ps_ttm:'暂无数据',valuation_notes:displayNotes}}}}});
const displayedValuation=displayed.children.find(x=>x.id==='report-valuation');
if((displayedValuation.textContent.match(/暂无数据/g)||[]).length!==1)throw new Error('展示副本导致PS提示重复');
'''.replace('__RENDERER__', json.dumps(_module_url('src/api/static/js/report-renderer.js')))
    _run_node(tmp_path, script)


def test_commentary_fallback_status_is_not_repeated(tmp_path):
    script = _DOM_STUB + r'''
const {renderStockReport}=await import(__RENDERER__);
const article=renderStockReport({commentary_status:'disabled',
  commentary:'> 未启用 AI 解读，以下为程序生成的量化摘要。\n\n最终综合得分为 8.0/10。\n- 财务健康：8.0/10。'});
const card=article.children.find(x=>x.id==='report-commentary');
const shown=card.textContent+' '+descendants(card).map(n=>n.innerHTML).join(' ');
if(!shown.includes('最终综合得分为 8.0/10'))throw new Error('量化摘要正文丢失');
if((shown.match(/量化摘要/g)||[]).length!==1)throw new Error('降级说明重复');
if((shown.match(/AI 解读未启用/g)||[]).length!==1||shown.includes('未启用 AI 解读'))throw new Error('降级状态重复');
'''.replace('__RENDERER__', json.dumps(_module_url('src/api/static/js/report-renderer.js')))
    _run_node(tmp_path, script)


def test_internal_scoring_structures_and_iso_time_are_not_exposed(tmp_path):
    script = _DOM_STUB + r'''
const {renderStockReport}=await import(__RENDERER__);
const article=renderStockReport({market:'a-shares',scoring_method:'five-dimension-v1',
  generated_at:'2026-08-24T08:30:00+08:00',dimensions:{financial:{status:'ok',metrics:{
    roe:0.1675,annual_roe:{value:0.32530000000000003,period_start:'2025-01-01',
      period_end:'2025-12-31',source:'同花顺财报',basis:'加权权益ROE'},
    scoring_basis:{available_max:10,eligible_max:10,reasons:[]}}}}});
const text=article.textContent;
for(const raw of ['annual roe','scoring basis','0.32530000000000003','2026-08-24T08:30:00']){
  if(text.includes(raw))throw new Error('内部评分结构或原始时间泄漏: '+raw);
}
if(!text.includes('16.75%')||!text.includes('生成时间：2026-08-24 08:30:00'))throw new Error('保留指标或时间格式丢失');
'''.replace('__RENDERER__', json.dumps(_module_url('src/api/static/js/report-renderer.js')))
    _run_node(tmp_path, script)


def test_legacy_structured_summary_is_visible_without_old_trading_advice(tmp_path):
    script = _DOM_STUB + r'''
const {renderStockReport}=await import(__RENDERER__);
for (const legacy of [{summary:'原有经营观察：增长承压'}, {overview:{summary:'原有经营观察：增长承压'}}]) {
  const article=renderStockReport(legacy);
  const commentary=article.children.find(x=>x.id==='report-commentary');
  if(!descendants(commentary).some(x=>x.innerHTML.includes('原有经营观察：增长承压')))throw new Error('历史摘要被遗漏');
}
const unsafe=renderStockReport({summary:'可考虑建仓/加仓，建议仓位 60%-80%'});
const text=descendants(unsafe).map(x=>x.textContent+' '+x.innerHTML).join(' ');
if(text.includes('60%-80%')||text.includes('可考虑建仓'))throw new Error('旧交易建议暴露');
'''.replace('__RENDERER__', json.dumps(_module_url('src/api/static/js/report-renderer.js')))
    _run_node(tmp_path, script)
