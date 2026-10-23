"""股票任务分流、断线与迟到响应的行为契约。"""
import json
from pathlib import Path

from tests.api.test_static import _DOM_STUB, _run_node


def test_financial_and_market_tasks_do_not_overwrite_each_other(tmp_path):
    url = Path('src/api/static/js/radar-stock-tasks.js').resolve().as_uri()
    script = _DOM_STUB + r"""
document.body=document.createElement('body');
Element.prototype.append=function(...nodes){nodes.forEach(n=>this.appendChild(n));};
Object.defineProperty(Element.prototype,'lastChild',{get(){return this.children.at(-1);}});
Element.prototype.getBoundingClientRect=()=>({left:20,top:120,width:440,height:260});
Element.prototype.showModal=function(){this.open=true;};Element.prototype.close=function(){this.open=false;};
globalThis.innerWidth=1280;globalThis.innerHeight=900;
const timers=[];window.setTimeout=fn=>{timers.push(fn);return timers.length;};
const {createStockTaskUI}=await import(MODULE_URL);
const calls=[];let outage=true;const oldResolvers=[];
const request=async(url,options)=>{calls.push([url,options]);
 if(url.includes('/scope'))return {total:578,scope_complete:true};
 if(url.includes('latest')){if(url.includes('growth_trend'))return new Promise(resolve=>oldResolvers.push(resolve));return null;}
 if(options?.method==='POST')return {run_id:url.includes('financial-review')?'finance':'market',kind:url.includes('financial-review')?'financial_review':'refresh_members',pool_ids:['dividend_value'],status:'queued',input_count:578};
 if(url.endsWith('/finance')){if(outage){outage=false;throw Error('断线');}return {run_id:'finance',kind:'financial_review',pool_ids:['dividend_value'],status:'completed',progress:{total:578,attempted:578}};}
 if(url.endsWith('/market'))return {run_id:'market',kind:'refresh_members',pool_ids:['dividend_value'],status:'running'};
 if(url.includes('financial-results'))return {items:[{symbol:'600001',name:'测试公司',financial_numeric_status:'failed',financial_numeric_checks:[{name:'annual_parent_profit_2023',outcome:'failed',reason:'利润不为正',sources:[{source_url:'https://example.org/table',period_end:'2023-12-31',disclosed_on:'2024-03-31',fetched_at:'2026-10-18T14:00:00+00:00'}]}]}],summary:{total:578,attempted:578,numeric_passed:473,numeric_failed:105,pending_verification:0},page:1,page_size:15,total:1};
};
const ui=createStockTaskUI(request);const pool={id:'dividend_value',name:'红利价值池'};
await ui.select(pool);await ui.start(pool,'refresh_members');await ui.start(pool,'financial_review');
if(!ui.isActive(pool.id,'refresh_members')||!ui.isActive(pool.id,'financial_review'))throw Error('两个任务未分开保存');
const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
let f=timers.shift();await f();await flush();f=timers.shift();await f();await flush();
if(!document.body.textContent.includes('重新连接'))throw Error('断线静默');
f=timers.shift();await f();await flush();f=timers.shift();await f();await flush();
if(ui.isActive(pool.id,'financial_review')||!ui.isActive(pool.id,'refresh_members'))throw Error('财务完成覆盖行情状态');
await ui.openResults(pool,'financial_review');
if(!document.body.textContent.includes('盈利条件通过 473')||!document.body.textContent.includes('未通过 105'))throw Error('计数未展示');
if(!document.body.textContent.includes('2023年归母净利润')||!document.body.textContent.includes('2024-03-31'))throw Error('条件或来源未按真实契约展示');
if(!document.body.textContent.includes('未通过')||!document.body.textContent.includes('利润不为正'))throw Error('逐项失败未展示');
if(descendants(document.body).filter(n=>n.tagName==='A').length!==1)throw Error('来源链接未按source_url展示');
const pending=ui.select({id:'growth_trend',name:'成长趋势池'});await ui.select(pool);oldResolvers.forEach(resolve=>resolve(null));await pending;
if(document.getElementById('radarStockUpdateNotice').textContent.includes('成长趋势池'))throw Error('迟到旧池覆盖当前池');
"""
    _run_node(tmp_path, script.replace('MODULE_URL', json.dumps(url)))


_UI_SETUP = r"""
document.body=document.createElement('body');
Element.prototype.append=function(...nodes){nodes.forEach(n=>this.appendChild(n));};
Element.prototype.prepend=function(n){this.children.unshift(n);};
Object.defineProperty(Element.prototype,'lastChild',{get(){return this.children.at(-1);}});
Element.prototype.getBoundingClientRect=()=>({left:20,top:120,width:440,height:260});
Element.prototype.showModal=function(){this.open=true;};Element.prototype.close=function(){this.open=false;};
globalThis.innerWidth=1280;globalThis.innerHeight=900;
const timers=[];window.setTimeout=fn=>{timers.push(fn);return timers.length;};
const {createStockTaskUI}=await import(MODULE_URL);
const pool={id:'dividend_value',name:'红利价值池'};
const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
"""


def _task_script(body):
    url = Path('src/api/static/js/radar-stock-tasks.js').resolve().as_uri()
    return _DOM_STUB + _UI_SETUP.replace('MODULE_URL', json.dumps(url)) + body


def test_late_recovery_error_cannot_overwrite_new_task(tmp_path):
    _run_node(tmp_path, _task_script(r"""
let rejectLatest;
const request=async(url,options)=>{
 if(url.includes('latest')&&url.includes('kind=financial_review'))return new Promise((_,reject)=>rejectLatest=reject);
 if(url.includes('latest'))return null;
 if(options?.method==='POST')return {run_id:'new-finance',kind:'financial_review',status:'queued',pool_ids:[pool.id]};
};
const ui=createStockTaskUI(request),recovering=ui.select(pool);await flush();
await ui.start(pool,'financial_review');
if(!ui.isActive(pool.id,'financial_review'))throw Error('未启动');
rejectLatest(Error('旧连接失败'));await recovering;
if(!ui.isActive(pool.id,'financial_review'))throw Error('旧latest失败覆盖刚启动的任务');
"""))


def test_late_start_cannot_close_new_results_dialog(tmp_path):
    _run_node(tmp_path, _task_script(r"""
let finishStart;
const request=async(url,options)=>{
 if(url.includes('latest'))return null;
 if(url.endsWith('/scope'))return {total:1,scope_complete:true,source_snapshots:[]};
 if(options?.method==='POST')return new Promise(resolve=>finishStart=resolve);
};
const ui=createStockTaskUI(request);await ui.select(pool);await ui.confirmFinancial(pool);
const begin=descendants(document.body).find(n=>n.tagName==='BUTTON'&&n.textContent==='开始复核');
const starting=begin.click();await flush();
await ui.openResults(pool,'refresh_members');
finishStart({run_id:'new',kind:'financial_review',status:'queued',pool_ids:[pool.id]});await starting;
const dialog=byClass(document.body,'radar-update-dialog')[0];
if(!dialog.open||!dialog.textContent.includes('行情更新结果'))throw Error('迟到启动关闭了新窗口');
"""))


def test_shared_discovery_completion_updates_current_other_pool(tmp_path):
    _run_node(tmp_path, _task_script(r"""
const task={run_id:'shared',kind:'discover_candidates',status:'queued',pool_ids:['dividend_value','growth_trend']};
const request=async(url,options)=>{
 if(options?.method==='POST')return task;
 if(url.includes('latest'))return url.includes('kind=discover_candidates')?task:null;
 return {...task,status:'completed'};
};
const ui=createStockTaskUI(request);await ui.select(pool);await ui.start(pool,'discover_candidates');
await ui.select({id:'growth_trend',name:'成长趋势池'});
await timers.shift()();await flush();
if(ui.isActive('growth_trend','discover_candidates'))throw Error('同run状态未同步');
if(!document.getElementById('radarStockUpdateNotice').textContent.includes('候选准备 · 已完成'))throw Error('当前池界面未刷新');
"""))
