"""股票管理真实DOM交互契约，不使用字符串匹配代替行为。"""
import json
from pathlib import Path

from tests.api.test_static import _DOM_STUB, _run_node


def test_growth_task_channel_and_failure_reason_are_visible(tmp_path):
    _run_node(tmp_path, _script(r"""
const growthDetail = {company, evidence:[], reviews:[], evaluation:{items:[{
  pools:{growth_trend:{growth_research:{channel:'structured_financial_task',status:'unavailable',
    as_of:'2026-11-01',reason:'最新任务尚未处理，不能回退旧结果',research:null}}}}]}};
pool.id = 'growth_trend';
const request = async url => url.includes('/updates/latest') ? null : (url.includes('/companies/') ? growthDetail : list());
const dispose = renderRadarStockPool(root, header, pool, empty, {request}); await tick();
await click(root, '候选与成员管理'); await click(root, '测试公司 · 600001');
assert.ok(root.textContent.includes('财务复核任务'));
assert.ok(root.textContent.includes('最新任务尚未处理，不能回退旧结果'));
dispose();
"""))


def test_reference_industry_failure_is_visible_in_original_details(tmp_path):
    _run_node(tmp_path, _script(r"""
detail.evaluation.items[0].industry_source = {status:'unavailable',industry:null,
  reference_task:{reason:'来源未完成'},reason:'最新基础资料任务来源校验失败'};
const request = async url => url.includes('/updates/latest') ? null : (url.includes('/companies/') ? detail : list());
const dispose = renderRadarStockPool(root, header, pool, empty, {request}); await tick();
await click(root, '候选与成员管理'); await click(root, '测试公司 · 600001');
assert.ok(root.textContent.includes('基础资料任务：最新基础资料任务来源校验失败'));
dispose();
"""))


def test_local_industry_mapping_is_named_without_fake_snapshot_date(tmp_path):
    _run_node(tmp_path, _script(r"""
detail.evaluation.items[0].industry_source = {status:'local_reference',as_of:null,
  industry:{level1:'公用事业',level2:'电力'},source:{provider:'local_industry_mapping'}};
company.industry_level1='公用事业';company.industry_level2='电力';
const request = async url => url.includes('/updates/latest') ? null : (url.includes('/companies/') ? detail : list());
const dispose = renderRadarStockPool(root, header, pool, empty, {request}); await tick();
await click(root, '候选与成员管理');
assert.ok(root.textContent.includes('公用事业 / 电力'));
await click(root, '测试公司 · 600001');
assert.ok(root.textContent.includes('来源：本地映射表'));
assert.ok(root.textContent.includes('公用事业 / 电力'));
assert.ok(!root.textContent.includes('行业分类参考 · 来源截点'));
dispose();
"""))


def _script(body: str) -> str:
    url = Path('src/api/static/js/radar-stock-view.js').resolve().as_uri()
    return _DOM_STUB + '\n' + "import assert from 'node:assert/strict';\n" + f"const {{renderRadarStockPool: renderOriginal}} = await import({json.dumps(url)});\n" + r"""
const renderRadarStockPool = (root,header,pool,payload,options={}) => renderOriginal(root,header,pool,payload,{...options,legacy:true});
Element.prototype.append = function(...children) { children.forEach(child => this.appendChild(child)); };
globalThis.innerWidth = 1280; globalThis.innerHeight = 900;
const events = {};
window.addEventListener = (name, callback) => { (events[name] ||= []).push(callback); };
window.removeEventListener = (name, callback) => { events[name] = (events[name] || []).filter(item => item !== callback); };
let timers = new Map(), timerId = 0;
window.setTimeout = callback => { timers.set(++timerId, callback); return timerId; };
window.clearTimeout = id => timers.delete(id);
const tick = async () => { for(let i = 0; i < 12; i++) await Promise.resolve(); };
const find = (root, text) => descendants(root).find(el => el.tagName === 'BUTTON' && el.textContent === text);
const click = async (root, text) => { const b = find(root, text); assert.ok(b, text); assert.equal(b.disabled, false, text); await b.click(); await tick(); };
const root = makeElement('root');
const header = document.createElement('div');
const refresh = document.createElement('button'); refresh.className = 'radar-refresh'; header.appendChild(refresh);
const pool = { id: 'dividend_value', name: '红利价值' };
const empty = { items: [], unavailable_reason: '正式规则尚未启用' };
const company = { symbol: '600001', name: '测试公司', revision: 3, data_as_of: '2026-10-16' };
const detail = { company, evidence: [
 { evidence_id: 'hint', kind: 'research_hint', confirmable: false, payload: {name: '<script>bad</script>'}, source_url: 'javascript:alert(1)' },
 { evidence_id: 'fact', kind: 'financial_document', confirmable: true, payload: {value: 1}, source_url: 'https://example.org/document' }
], reviews: [], evaluation: {items: [{checks: [], pools: {dividend_value: {checks: []}}}]} };
const list = (page = 1) => ({items: [company], total: 31, page, page_size: 15, scope_status: 'complete'});
let calls = [], current = true;
""" + body


def test_stock_scope_search_pagination_and_detail_return(tmp_path):
    _run_node(tmp_path, _script(r"""
const request = async (url, options) => { calls.push([url, options]); if (url.includes('/updates/latest')) return null; if (url.includes('/companies/')) return detail; return list(Number(new URL('http://test' + url).searchParams.get('page'))); };
const dispose = renderRadarStockPool(root, header, pool, empty, {request, isCurrent: () => current}); await tick();
assert.ok(root.textContent.includes('尚无正式成员'));
await click(root, '候选与成员管理');
assert.ok(calls.filter(([url]) => url.includes('/candidates?')).at(-1)[0].includes('pool_id=dividend_value'));
await click(root, '下一页'); assert.ok(calls.filter(([url]) => url.includes('/candidates?')).at(-1)[0].includes('page=2'));
const search = byClass(root, 'stock-search')[0]; search.value = '测试';
await click(root, '搜索'); assert.ok(calls.filter(([url]) => url.includes('/candidates?')).at(-1)[0].includes('search=%E6%B5%8B%E8%AF%95&page=1'));
await click(root, '清除'); assert.ok(calls.filter(([url]) => url.includes('/candidates?')).at(-1)[0].includes('page=2'));
root.scrollTop = 91;
await click(root, '测试公司 · 600001'); assert.ok(root.textContent.includes('共同条件')); assert.ok(root.textContent.includes('红利价值条件'));
assert.equal(descendants(root).filter(el => el.tagName === 'A').length, 1);
await click(root, '返回初筛范围'); assert.ok(calls.filter(([url]) => url.includes('/candidates?')).at(-1)[0].includes('page=2')); assert.equal(root.scrollTop, 91);
dispose();
"""))


def test_stock_review_conflict_draft_and_unload_protection(tmp_path):
    _run_node(tmp_path, _script(r"""
let conflict = true, pendingResolve;
const request = async (url, options) => { calls.push([url, options]); if (url.includes('/updates/latest')) return null; if (options?.method === 'POST') { if (url.endsWith('/reviews') && conflict) { conflict = false; const e = new Error('revision changed'); e.status = 409; throw e; } if (url.endsWith('/draft')) return new Promise(resolve => pendingResolve = resolve); return {revision: 4}; } return url.includes('/companies/') ? detail : list(); };
const dispose = renderRadarStockPool(root, header, pool, empty, {request}); await tick();
await click(root, '候选与成员管理'); await click(root, '测试公司 · 600001');
const fill = async () => { const reason = byClass(root, 'stock-reason')[0]; reason.value = '需要核对原始报表'; await reason.dispatch('input'); const actor = byClass(root, 'stock-reviewer')[0]; actor.value = '用户'; await actor.dispatch('input'); };
await fill(); await click(root, '研究线索 · 待核验'); assert.equal(find(root, '确认事实').disabled, true);
await click(root, '财务原文 · 待核验'); assert.equal(find(root, '确认事实').disabled, false);
await click(root, '确认事实'); assert.ok(root.textContent.includes('资料版本已变化'));
const saved = JSON.parse(calls.find(([url]) => url.endsWith('/reviews'))[1].body); assert.equal(saved.expected_revision, 3); assert.equal(saved.evidence_id, 'fact'); assert.equal(saved.reviewer, '用户');
await click(root, '重新读取资料'); assert.equal(byClass(root, 'stock-reason')[0].value, '需要核对原始报表');
const unload = {preventDefault() {this.prevented = true;}}; events.beforeunload[0](unload); assert.equal(unload.prevented, true);
await click(root, '返回初筛范围'); await click(root, '测试公司 · 600001'); assert.equal(byClass(root, 'stock-reason')[0].value, '需要核对原始报表');
const draftButton = find(root, '保存关注草案'); const saving = draftButton.click(); await tick(); assert.equal(draftButton.disabled, true);
const draftCall = calls.find(([url]) => url.endsWith('/draft')); const body = JSON.parse(draftCall[1].body); assert.equal(body.pool_id, pool.id); assert.equal(body.expected_revision, 3); assert.ok(body.request_id); assert.equal(body.evaluation, undefined);
pendingResolve({status:'blocked', blockers:['规则未配置']}); await saving; await tick(); assert.ok(root.textContent.includes('仍受阻')); assert.equal(byClass(root, 'stock-reason')[0].value, '');
assert.equal(find(root, '新增 / 恢复正式成员').disabled, true); dispose();
"""))


def test_stock_task_poll_is_single_chain_and_stops_on_switch(tmp_path):
    _run_node(tmp_path, _script(r"""
let latest = 0;
const request = async (url, options) => { calls.push([url, options]); if (url.includes('/updates/')) { latest++; return latest === 1 ? null : {run_id:'task1', status:'running', phase:'collect'}; } return {run_id:'task1', status:'queued'}; };
const dispose = renderRadarStockPool(root, header, pool, empty, {request, isCurrent: () => current}); await tick();
await click(root, '准备候选'); assert.equal(timers.size, 1); assert.ok(calls.some(([url]) => url.endsWith('/updates/task1')));
const start = calls.find(([url]) => url.endsWith('/discover')); assert.deepEqual(JSON.parse(start[1].body), {pool_id: pool.id});
const timer = [...timers.values()][0]; timers.clear(); await timer(); await tick(); assert.equal(timers.size, 1);
current = false; const count = calls.length; const stop = [...timers.values()][0]; timers.clear(); await stop(); await tick(); assert.equal(calls.length, count); assert.equal(timers.size, 0); dispose();
"""))


def test_stock_late_response_and_retry(tmp_path):
    _run_node(tmp_path, _script(r"""
let fail = true, resolveList;
const request = async url => { if (url.includes('/updates/latest')) return null; if (fail) {fail = false; throw new Error('network down');} return new Promise(resolve => resolveList = resolve); };
const dispose = renderRadarStockPool(root, header, pool, empty, {request}); await tick(); await click(root, '候选与成员管理');
assert.ok(root.textContent.includes('network down')); await click(root, '重试');
renderRadarStockPool(root, header, {id:'growth_trend', name:'成长趋势'}, empty, {request: async () => null}); await tick();
resolveList(list()); await tick(); assert.ok(root.textContent.includes('成长趋势')); assert.equal(byClass(root, 'stock-company').length, 0); dispose();
"""))


def test_stock_checks_are_chinese_and_collapsed(tmp_path):
    _run_node(tmp_path, _script(r"""
detail.evaluation.items[0].checks = [{name:'annual_parent_profit_2023', outcome:'pending_verification', reason:'需要原文'}];
detail.evaluation.items[0].pools.dividend_value.unconfigured_rules = ['dividend_payout_ratio'];
const request = async url => url.includes('/updates/latest') ? null : url.includes('/companies/') ? detail : list();
const dispose = renderRadarStockPool(root, header, pool, empty, {request}); await tick();
await click(root, '候选与成员管理'); await click(root, '测试公司 · 600001');
assert.ok(root.textContent.includes('2023年归母净利润'));
assert.ok(root.textContent.includes('股利支付率'));
assert.ok(!root.textContent.includes('annual_parent_profit'));
const detailFold = descendants(root).find(el => el.tagName === 'DETAILS' && el.textContent.includes('查看核验明细'));
assert.ok(detailFold); assert.ok(!detailFold.open); dispose();
"""))


def test_detail_financial_source_dates_and_conflicts_remain_visible(tmp_path):
    _run_node(tmp_path, _script(r"""
detail.evaluation.items[0].as_of = '2026-10-24';
detail.evaluation.items[0].financial_source = {as_of:'2026-10-18', scope_as_of:'2026-10-16', status:'historical', reason:'历史财务结果仅供参考，不能提升本次截点的盈利核验'};
detail.evaluation.items[0].checks = [{code:'ttm_parent_profit', outcome:'pending_verification', reason:'两方正负冲突待核验', channel:'structured_financial', manual_check:{outcome:'failed'}, structured_check:{outcome:'passed'}}];
const request = async url => url.includes('/updates/latest') ? null : url.includes('/companies/') ? detail : list();
const dispose = renderRadarStockPool(root, header, pool, empty, {request}); await tick();
await click(root, '候选与成员管理'); await click(root, '测试公司 · 600001');
assert.ok(root.textContent.includes('本次评估截点 2026-10-24'));
assert.ok(root.textContent.includes('财务研究截点 2026-10-18'));
assert.ok(root.textContent.includes('范围日期 2026-10-16'));
assert.ok(root.textContent.includes('历史财务结果仅供参考'));
assert.ok(root.textContent.includes('结构化来源：通过，人工事实：未通过'));
assert.ok(!descendants(root).find(el => el.tagName === 'DETAILS' && el.textContent.includes('查看核验明细')).open);
dispose();
"""))


def test_historical_per_check_reference_keeps_current_pending(tmp_path):
    _run_node(tmp_path, _script(r"""
detail.evaluation.items[0].checks = [
 {code:'ttm_parent_profit', outcome:'pending_verification', channel:'structured_financial', reason:'历史财务参考（2026-10-18）：通过，仅供参考，不作本次资格依据', reference_as_of:'2026-10-18', reference_check:{outcome:'passed'}, structured_check:{outcome:'pending_verification'}, manual_check:{outcome:'pending_verification'}},
 {code:'ttm_deducted_profit', outcome:'pending_verification', channel:'structured_financial', reason:'历史财务参考（2026-10-18）：不满足，仅供参考，不作本次资格依据', reference_as_of:'2026-10-18', reference_check:{outcome:'failed'}, structured_check:{outcome:'pending_verification'}, manual_check:{outcome:'pending_verification'}}
];
const request = async url => url.includes('/updates/latest') ? null : url.includes('/companies/') ? detail : list();
const dispose = renderRadarStockPool(root, header, pool, empty, {request}); await tick();
await click(root, '候选与成员管理'); await click(root, '测试公司 · 600001');
const rows = byClass(root, 'stock-check');
assert.ok(rows.some(row => row.textContent.includes('最近十二个月归母净利润 · 待核验') && row.textContent.includes('2026-10-18') && row.textContent.includes('通过，仅供参考')));
assert.ok(rows.some(row => row.textContent.includes('最近十二个月扣非净利润 · 待核验') && row.textContent.includes('2026-10-18') && row.textContent.includes('不满足，仅供参考')));
assert.ok(root.textContent.includes('本次结构化来源：待核验'));
dispose();
"""))


def test_hidden_stock_view_stops_poll_without_hash_change(tmp_path):
    _run_node(tmp_path, _script(r"""
const view = document.createElement('div'); view.className = 'view active'; root.closest = () => view;
const request = async (url, options) => { calls.push([url, options]); return url.includes('/updates/latest') ? null : {run_id:'task1',status:'running'}; };
const dispose = renderRadarStockPool(root, header, pool, empty, {request}); await tick();
await click(root, '准备候选'); assert.equal(timers.size, 1);
view.classList.remove('active'); const count = calls.length;
const timer = [...timers.values()][0]; timers.clear(); await timer(); await tick();
assert.equal(calls.length, count); assert.equal(timers.size, 0); dispose();
"""))


def test_integrated_stock_view_uses_shared_task_window_instead_of_inline_card(tmp_path):
    _run_node(tmp_path, _script(r"""
let selected=0,finance=0,started=[];
const taskUI={select:async()=>selected++,confirmFinancial:async()=>finance++,start:async(p,k)=>started.push(k)};
const request=async()=>{throw Error('页面重复轮询任务');};
const dispose=renderRadarStockPool(root,header,pool,empty,{request,taskUI});await tick();
assert.equal(byClass(root,'stock-task')[0].hidden,true);
assert.equal(selected,1);
await click(root,'财务复核');assert.equal(finance,1);
await click(root,'准备候选');assert.deepEqual(started,['discover_candidates']);
assert.equal(timers.size,0);
dispose();
"""))


def test_research_summary_and_ai_are_on_demand(tmp_path):
    _run_node(tmp_path, _script(r"""
detail.research_summaries = {dividend_value: {status:'research_candidate', passed:[{code:'listing_age',reason:'满三年'}], failed:[], pending:[{code:'audit',reason:'待核验'}], unconfigured_rules:['dividend_yield']}};
detail.research_summaries.dividend_value.reference_rules = {dividend_payout_ratio:'disabled', value_valuation:'pending_verification'};
detail.audit_clues = {status:'available', classification:{status:'other_opinion'}, annual:[{year:2025,status:'available',opinion:'标准无保留意见',other_opinion:'附加强调事项'}]};
detail.risk_warning = {status:'current', observation:'not_observed', snapshot_date:'2026-11-03'};
const request = async (url, options) => {
  calls.push([url, options]);
  if (url.includes('/updates/latest')) return null;
  if (url.includes('/research-explanation')) return {text:'这是研究解释。'};
  if (url.includes('/research-summary')) return detail.research_summaries.dividend_value;
  if (url.includes('/companies/')) return detail;
  const result = list(); result.items[0] = {...company,research_summary:detail.research_summaries.dividend_value}; return result;
};
const dispose = renderRadarStockPool(root, header, pool, empty, {request, isCurrent: () => current}); await tick();
await click(root, '候选与成员管理');
assert.ok(root.textContent.includes('研究')); assert.ok(!calls.some(([url]) => url.includes('/research-explanation')));
await click(root, '测试公司 · 600001');
assert.ok(root.textContent.includes('研究候选')); assert.ok(root.textContent.includes('标准无保留意见')); assert.ok(root.textContent.includes('附加强调事项'));
assert.ok(root.textContent.includes('审计意见分类：存在其他意见，需核验'));
assert.ok(root.textContent.includes('股利支付率：暂不启用')); assert.ok(root.textContent.includes('价值估值：机会参考，待核验'));
assert.ok(root.textContent.includes('风险名单未观察到，不代表没有风险'));
await click(root, 'AI解释研究资料');
assert.ok(root.textContent.includes('这是研究解释')); assert.equal(calls.filter(([url]) => url.includes('/research-explanation')).length, 1);
dispose();
"""))

def test_screening_home_rules_counts_and_full_list(tmp_path):
    _run_node(tmp_path, _script(r"""
document.body=makeElement('body');
Object.defineProperty(Element.prototype,'lastChild',{get(){return this.children.at(-1)},configurable:true});
Element.prototype.showModal=function(){this.open=true;};
Element.prototype.close=function(){this.open=false;};
const request=async url=>{
 calls.push([url]);
 if(url.includes('/tasks/latest'))return null;
 if(url.endsWith('/rules'))return {rule_version:'formal-v1',items:Array.from({length:7},(_,i)=>({code:'c'+i,label:'规则'+i,description:'判断条件'+i,group:'common'}))};
 return {items:Array.from({length:20},(_,i)=>({symbol:String(600000+i),name:'公司'+i,industry:'行业',reason:'通过依据'})),total:30,page:1,page_size:20,counts:{screened:32,passed:30,pending:1,failed:1},as_of:'2026-11-07'};
};
const dispose=renderOriginal(root,header,pool,empty,{request});await tick();
assert.ok(root.textContent.includes('前20家'));
assert.equal(byClass(root,'stock-company').length,20);
assert.equal(byClass(root,'stock-screening-rule-list')[0].children.length,5);
await click(root,'展开全部 · 共 7 条');assert.equal(byClass(root,'stock-screening-rule-list')[0].children.length,7);
await click(root,'查看全部');assert.ok(root.textContent.includes('下一页'));
assert.ok(calls.some(([url])=>url.includes('page_size=20')));
dispose();
"""))


def test_completion_plan_zero_and_conflict_are_visible(tmp_path):
    _run_node(tmp_path, _script(r"""
document.body=makeElement('body');
Object.defineProperty(Element.prototype,'lastChild',{get(){return this.children.at(-1)},configurable:true});
Element.prototype.showModal=function(){this.open=true;};
Element.prototype.close=function(){this.open=false;};
const request=async (url,options)=>{
 if(url.includes('/tasks/latest'))return null;
 if(url.endsWith('/rules'))return {items:[]};
 if(url.endsWith('/completion/plan'))return {plan_sha256:'bound-plan',summary:{symbols:1},items:[{symbol:'600001',name:'测试公司',condition:'liquidity',source:{kind:'market_turnover_60d'}}]};
 if(url.endsWith('/completion/apply')){assert.equal(JSON.parse(options.body).plan_sha256,'bound-plan');const e=new Error('changed');e.status=409;throw e;}
 return {items:[],total:0,counts:{screened:0,passed:0,pending:0,failed:0}};
};
const dispose=renderOriginal(root,header,pool,empty,{request});await tick();
await click(root,'补全待检验');await click(document.body,'全部补全');
assert.ok(document.body.textContent.includes('计划或资料已变化'));
assert.ok(find(document.body,'重新生成计划'));
dispose();
"""))


def test_completion_plan_native_gaps_and_manual_items(tmp_path):
    _run_node(tmp_path, _script(r"""
document.body=makeElement('body');
Object.defineProperty(Element.prototype,'lastChild',{get(){return this.children.at(-1)},configurable:true});
Element.prototype.showModal=function(){this.open=true;};Element.prototype.close=function(){this.open=false;};
const request=async url=>{
 if(url.includes('/tasks/latest'))return null;
 if(url.endsWith('/rules'))return {items:[]};
 if(url.endsWith('/completion/plan'))return {plan_sha256:'native-plan',items:[
 {symbol:'603087',condition:'cash_review',gap:{years:[2023,2024,2025],fields:['netcash_operate','end_cash'],reason:'现金流资料不全'},source:{kind:'annual_report_cashflow',provider:'annual_report'}},
 {symbol:'600001',condition:'governance',gap:{reason:'需要官方证据'},source:{kind:'manual_review'}}]};
 return {items:[],total:0,counts:{}};
};
const dispose=renderOriginal(root,header,pool,empty,{request});await tick();await click(root,'补全待检验');
assert.ok(document.body.textContent.includes('2023、2024、2025'));
assert.ok(document.body.textContent.includes('经营活动现金流净额、期末现金余额'));
assert.ok(document.body.textContent.includes('年报现金流量表 · 年报归档'));
assert.ok(document.body.textContent.includes('需人工证据，无法自动补全'));
assert.ok(document.body.textContent.includes('人工项继续保留待检验'));
assert.ok(!document.body.textContent.includes('[object Object]'));
assert.equal(find(document.body,'全部补全').disabled,false);
dispose();
"""))


def test_screening_detail_uses_snapshot_not_current_recalculation(tmp_path):
    _run_node(tmp_path, _script(r"""
document.body=makeElement('body');
Object.defineProperty(Element.prototype,'lastChild',{get(){return this.children.at(-1)},configurable:true});
Element.prototype.close=function(){this.open=false;};
const request=async url=>{
 calls.push([url]);
 if(url.includes('/tasks/latest'))return null;
 if(url.endsWith('/rules'))return {items:[]};
 if(url.includes('/screening/companies/'))return {as_of:'2026-11-07',rule_version:'formal-frozen',item:{symbol:'600001',name:'测试公司',status:'passed',reason:'补全复核通过',checks:[{code:'liquidity',outcome:'passed',reason:'60日窗口已补齐'},{code:'annual_parent_profit_2023',outcome:'passed',reason:'盈利为正',sources:[{source_url:'https://example.org/report',period_end:'2023-12-31'}]}]}};
 if(url.includes('/companies/'))throw new Error('不得静默重算当天结果');
 return {items:[{symbol:'600001',name:'测试公司',reason:'补全复核通过'}],total:1,counts:{passed:1}};
};
const dispose=renderOriginal(root,header,pool,empty,{request});await tick();await click(root,'测试公司 · 600001');
assert.ok(root.textContent.includes('筛选截点 2026-11-07'));
assert.ok(root.textContent.includes('成交活跃度 · 通过：60日窗口已补齐'));
assert.ok(root.textContent.includes('当前池结论：通过'));
assert.ok(root.textContent.includes('2023年归母净利润'));
assert.ok(root.textContent.includes('报告期 2023-12-31'));
assert.ok(!byClass(root,'stock-snapshot-checks')[0].open);
assert.ok(find(root,'查看当前资料'));
assert.equal(calls.filter(([url])=>url.includes('/screening/companies/')).length,1);
await click(root,'返回筛选列表');assert.ok(root.textContent.includes('补全复核通过'));
dispose();
"""))


def test_empty_completion_plan_keeps_unresolved_pending_visible(tmp_path):
    _run_node(tmp_path, _script(r"""
document.body=makeElement('body');
Object.defineProperty(Element.prototype,'lastChild',{get(){return this.children.at(-1)},configurable:true});
Element.prototype.showModal=function(){this.open=true;};Element.prototype.close=function(){this.open=false;};
let pending=1;
const request=async url=>{
 if(url.includes('/tasks/latest'))return null;
 if(url.endsWith('/rules'))return {items:[]};
 if(url.endsWith('/completion/plan'))return {plan_sha256:'empty-plan',items:[],pending_total:pending,automatic_count:0,manual_count:pending,blocked_items:pending?[{symbol:'600001',name:'测试公司',reason:'共同条件未齐：治理证据缺失'}]:[]};
 return {items:[],total:0,counts:{}};
};
const dispose=renderOriginal(root,header,pool,empty,{request});await tick();await click(root,'补全待检验');
assert.ok(document.body.textContent.includes('待检验 1 家'));
assert.ok(document.body.textContent.includes('共同条件未齐：治理证据缺失'));
assert.ok(document.body.textContent.includes('当前尚不支持自动补全'));
assert.ok(!document.body.textContent.includes('当前没有待补全事项'));
assert.equal(find(document.body,'全部补全').disabled,true);
pending=0;await click(root,'补全待检验');
assert.ok(document.body.textContent.includes('当前没有待补全事项'));
dispose();
"""))


def test_unsupported_completion_source_is_not_presented_as_automatic(tmp_path):
    _run_node(tmp_path, _script(r"""
document.body=makeElement('body');
Object.defineProperty(Element.prototype,'lastChild',{get(){return this.children.at(-1)},configurable:true});
Element.prototype.showModal=function(){this.open=true;};Element.prototype.close=function(){this.open=false;};
const request=async url=>{
 if(url.includes('/tasks/latest'))return null;
 if(url.endsWith('/rules'))return {items:[]};
 if(url.endsWith('/completion/plan'))return {items:[{symbol:'600001',condition:'dividend_history',source:{kind:'annual_report_profit_distribution'}}]};
 return {items:[],total:0,counts:{}};
};
const dispose=renderOriginal(root,header,pool,empty,{request});await tick();await click(root,'补全待检验');
assert.ok(document.body.textContent.includes('当前来源尚不支持自动补全'));
assert.ok(document.body.textContent.includes('可自动执行 0 项'));
assert.equal(find(document.body,'全部补全').disabled,true);
dispose();
"""))


def test_finished_screening_refreshes_bound_rule_description(tmp_path):
    _run_node(tmp_path, _script(r"""
document.body=makeElement('body');
Object.defineProperty(Element.prototype,'lastChild',{get(){return this.children.at(-1)},configurable:true});
Element.prototype.close=function(){this.open=false;};
let ruleReads=0;
const task={run_id:'bound-run',kind:'screening',phase:'review',progress:{attempted:1,total:1},results:[]};
const request=async url=>{
 if(url.includes('/tasks/latest'))return {...task,status:'running'};
 if(url.includes('/screening/tasks/bound-run'))return {...task,status:'completed'};
 if(url.endsWith('/rules')){ruleReads++;return {items:[{label:'行业地位',description:ruleReads===1?'尚未绑定':'当前快照：前5名，同业至少5家',group:'common'}]};}
 return {items:[],total:0,counts:{}};
};
const dispose=renderOriginal(root,header,pool,empty,{request});await tick();
assert.equal(ruleReads,1);
for(const callback of [...timers.values()])await callback();await tick();
assert.equal(ruleReads,2);
assert.ok(root.textContent.includes('当前快照：前5名，同业至少5家'));
dispose();
"""))


def test_update_previews_shared_scope_before_start_and_uses_original_mini_strip(tmp_path):
    _run_node(tmp_path, _script(r"""
document.body=makeElement('body');
Object.defineProperty(Element.prototype,'lastChild',{get(){return this.children.at(-1)},configurable:true});
Element.prototype.showModal=function(){this.open=true;};Element.prototype.close=function(){this.open=false;};
const task={run_id:'v2-run',kind:'screening',status:'running',phase:'peer_market_value',stage_errors:[{stage:'governance',reason:'监管来源缺失'}],progress:{attempted:2,total:626},results:[{scope:'reference:market_value:000001',status:'completed',summary:'复用有效缓存'}]};
const request=async (url,options)=>{
 calls.push([url,options]);
 if(url.includes('/tasks/latest'))return null;
 if(url.includes('/screening/tasks/v2-run'))return task;
 if(url.endsWith('/rules'))return {items:[]};
 if(url.endsWith('/screening/preview'))return {preview_sha256:'bound-preview',as_of:'2026-11-14',company_count:10,shared_stages:[{stage:'peer_market_value',label:'同业市值',symbol_count:626,cost_hint:'共享范围较大',status:'cached'}]};
 if(url.endsWith('/screening/update')){assert.deepEqual(JSON.parse(options.body),{preview_sha256:'bound-preview'});return task;}
 return {items:[],total:0,counts:{}};
};
const dispose=renderOriginal(root,header,pool,empty,{request});await tick();await refresh.onclick();await tick();
assert.equal(calls.filter(([url])=>url.endsWith('/screening/update')).length,0);
assert.ok(document.body.textContent.includes('本池 10 家'));
assert.ok(document.body.textContent.includes('共享采集范围 626 家'));
assert.ok(document.body.textContent.includes('复用有效缓存'));
await click(document.body,'开始更新');
assert.equal(calls.filter(([url])=>url.endsWith('/screening/update')).length,1);
assert.ok(document.body.textContent.includes('准备完整同业市值'));
assert.equal(byClass(document.body,'stock-task-steps')[0].children.length,4);
assert.ok(byClass(document.body,'stock-task-steps')[0].children[0].textContent.includes('资料不足'));
assert.ok(!byClass(document.body,'stock-task-steps')[0].children[0].textContent.includes('已完成'));
assert.ok(!byClass(document.body,'stock-task-logs')[0].open);
assert.ok(byClass(document.body,'radar-update-toast')[0].classList.contains('minimized'));
assert.equal(byClass(document.body,'radar-update-mini-button').length,1);
dispose();
"""))


def test_v2_completion_shared_stage_is_executable_and_external_is_not(tmp_path):
    _run_node(tmp_path, _script(r"""
document.body=makeElement('body');
Object.defineProperty(Element.prototype,'lastChild',{get(){return this.children.at(-1)},configurable:true});
Element.prototype.showModal=function(){this.open=true;};Element.prototype.close=function(){this.open=false;};
const request=async url=>{
 if(url.includes('/tasks/latest'))return null;
 if(url.endsWith('/rules'))return {items:[]};
 if(url.endsWith('/completion/plan'))return {items:[
 {symbol:'600001',condition:'leadership',source:{kind:'manual_review'},evidence_class:'shared_refresh',executable:{mode:'shared_stage',stage:'industry_scale'}},
 {symbol:'600002',condition:'audit',source:{kind:'manual_review'},evidence_class:'external_required',executable:{mode:'none'}}],shared_stages:[{stage:'industry_scale',symbol_count:9}],execution_preview:{shared_stages:[{stage:'industry_scale',label:'行业规模排名',symbol_count:626,status:'refresh_required',cost_hint:'完整同业范围'}]}};
 return {items:[],total:0,counts:{}};
};
const dispose=renderOriginal(root,header,pool,empty,{request});await tick();await click(root,'补全待检验');
assert.ok(document.body.textContent.includes('共享证据刷新'));
assert.ok(document.body.textContent.includes('需补充外部证据'));
assert.ok(document.body.textContent.includes('共享采集范围 626 家'));
assert.ok(document.body.textContent.includes('可自动执行 1 项'));
assert.equal(find(document.body,'全部补全').disabled,false);
dispose();
"""))
