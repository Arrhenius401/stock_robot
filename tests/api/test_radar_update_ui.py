"""独立更新窗口的任务恢复、轮询和历史缓存契约。"""
import json
from pathlib import Path

import pytest

from tests.api.test_static import _DOM_STUB, _run_node


def test_update_survives_navigation_and_reads_historical_cache(tmp_path):
    module_url = Path("src/api/static/js/radar-update-ui.js").resolve().as_uri()
    script = _DOM_STUB + r"""
document.body = document.createElement('body');
Element.prototype.append = function(...nodes) { nodes.forEach(node=>this.appendChild(node)); };
Element.prototype.prepend = function(node) {node.parentNode=this;this.children.unshift(node);};
Object.defineProperty(Element.prototype,'lastChild',{get(){return this.children.at(-1);}});
Element.prototype.getBoundingClientRect = function(){return {left:20,top:20,width:300,height:200};};
Element.prototype.showModal=function(){this.open=true;};
Element.prototype.close=function(){this.open=false;};
globalThis.innerWidth=1366;globalThis.innerHeight=900;
const timers=[];globalThis.setTimeout=fn=>{timers.push(fn);return timers.length;};
const {createRadarUpdateUI}=await import(MODULE_URL);
const calls=[],changes=[];
let failOnce=true;
const request=async (url,opts)=>{
 calls.push([url,opts]);
 if(url.includes('updates/latest'))return {id:'a',universe_id:'overseas',status:'running',phase:'overseas'};
 if(url.includes('/refresh/a')){if(failOnce){failOnce=false;throw new Error('断连');}return {id:'a',universe_id:'overseas',status:'partial',phase:'finalizing',error:'口径待核实'};}
 if(url.endsWith('/refresh'))return {id:'b',universe_id:'domestic',status:'queued',phase:'calendar'};
 if(url.includes('/refresh/b'))return {id:'b',universe_id:'domestic',status:'completed',etf:{status:'completed'}};
 if(url.includes('/overseas'))return {items:[{symbol:'513500',name:'ETF',index_name:'SP500',as_of_date:'2026-10-02',base_date:'2026-09-29',status:'fresh',currency:'USD',timezone:'America/New_York',daily_change:null,cumulative_change:null}]};
};
const ui=createRadarUpdateUI(request,(task,finished)=>changes.push([task.universe_id,task.status,finished]));
await ui.recover('overseas');
if(!ui.isActive('overseas')||timers.length!==1)throw new Error('未恢复任务');
await ui.recover('overseas');
if(timers.length!==1)throw new Error('重复恢复产生多个轮询');
await ui.start('domestic');
if(!ui.isActive('domestic')||timers.length!==2)throw new Error('切池丢失任务');
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
timers.shift()();await flush();
if(!document.body.textContent.includes('正在重新连接'))throw new Error('断连静默');
timers.shift()();await flush();
timers.shift()();await flush();
if(ui.isActive('overseas')||ui.isActive('domestic'))throw new Error('完成未终止轮询');
if(!changes.some(row=>row[0]==='overseas'&&row[2]))throw new Error('未通知完成');
const dialog=byClass(document.body,'radar-update-dialog')[0];
if(dialog.open)throw new Error('完成自动打开窗口');
await ui.open('overseas',{symbol:'513500',name:'历史ETF'},{run_id:'historical',as_of_date:'2026-09-30'},true);
if(!calls.at(-1)[0].includes('snapshot_run_id=historical'))throw new Error('历史起点缺失');
if(!document.body.textContent.includes('—'))throw new Error('缺少起点被表示为零');
if(!dialog.open)throw new Error('未打开结果窗口');
dialog.close();
await ui.start('domestic');
if(calls.filter(row=>row[1]&&row[1].method==='POST').length!==2)throw new Error('完成后不能重试');
"""
    _run_node(tmp_path, script.replace("MODULE_URL", json.dumps(module_url)))


@pytest.mark.parametrize("historical", [False, True])
def test_open_result_advances_latest_snapshot_but_preserves_history(tmp_path, historical):
    module_url = Path("src/api/static/js/radar-update-ui.js").resolve().as_uri()
    script = _DOM_STUB + r"""
document.body=document.createElement('body');
Element.prototype.append=function(...nodes){nodes.forEach(node=>this.appendChild(node));};
Element.prototype.prepend=function(node){node.parentNode=this;this.children.unshift(node);};
Object.defineProperty(Element.prototype,'lastChild',{get(){return this.children.at(-1);}});
Element.prototype.getBoundingClientRect=()=>({left:20,top:20,width:300,height:200});
Element.prototype.showModal=function(){this.open=true;};
Element.prototype.close=function(){this.open=false;};
globalThis.innerWidth=1366;globalThis.innerHeight=900;
const timers=[],calls=[];globalThis.setTimeout=fn=>{timers.push(fn);return timers.length;};
let delaySnapshot=false,resolveSnapshot,resolveLatest;
const {createRadarUpdateUI}=await import(MODULE_URL);
const request=async (url,opts)=>{
 calls.push(url);
 if(url.includes('/snapshots/latest'))return new Promise(resolve=>{resolveLatest=resolve;});
 if(url.endsWith('/refresh'))return {id:'task',universe_id:'overseas_etf',status:'running',phase:'etf'};
 if(url.endsWith('/refresh/task'))return {id:'task',universe_id:'overseas_etf',status:'completed',run_id:'new-run',etf:{status:'completed'}};
 if(url.endsWith('/snapshots/new-run'))return delaySnapshot ? new Promise(resolve=>{resolveSnapshot=resolve;}) : {run_id:'new-run',as_of_date:'2026-10-09'};
 if(url.includes('/overseas'))return {items:[{symbol:'513500',name:'ETF',index_name:'SP500',status:'fresh',as_of_date:'2026-10-09',base_date:url.includes('snapshot_run_id=new-run')?'2026-10-08':'2026-09-29'}]};
};
const ui=createRadarUpdateUI(request,()=>{});
await ui.open('overseas_etf',null,{run_id:'old-run',as_of_date:'2026-09-30'},HISTORICAL);
await ui.start('overseas_etf');timers.shift()();for(let i=0;i<15;i++)await Promise.resolve();
const expected=HISTORICAL?'old-run':'new-run';
if(!calls.at(-1).includes('snapshot_run_id='+expected))throw new Error('结果累计起点未按窗口上下文更新');
const dialog=byClass(document.body,'radar-update-dialog')[0];
if(!dialog.textContent.includes(HISTORICAL?'2026-09-30':'2026-10-09'))throw new Error('ETF 日期未同步');
if(!HISTORICAL&&!dialog.textContent.includes('2026-10-08'))throw new Error('海外累计起点仍旧');
if(HISTORICAL&&calls.some(url=>url.includes('/snapshots/new-run')))throw new Error('历史窗口被新快照覆盖');
delaySnapshot=true;
await ui.open('overseas_etf',null,{run_id:'old-run',as_of_date:'2026-09-30'});
await ui.start('overseas_etf');timers.shift()();for(let i=0;i<8;i++)await Promise.resolve();
if(!resolveSnapshot)throw new Error('未开始读取完成快照');
await ui.open('domestic',null,{run_id:'domestic-run',as_of_date:'2026-08-01'});
resolveSnapshot({run_id:'new-run',as_of_date:'2026-10-09'});for(let i=0;i<10;i++)await Promise.resolve();
if(!dialog.textContent.includes('2026-08-01')||dialog.textContent.includes('ETF 行情截至 2026-10-09'))throw new Error('旧池迟到快照覆盖新窗口');
if(!calls.at(-1).includes('universe_id=domestic'))throw new Error('旧任务在新窗口读取错误池的海外数据');

delaySnapshot=false;
const opening=ui.open('overseas_etf');
await ui.start('overseas_etf');timers.shift()();for(let i=0;i<15;i++)await Promise.resolve();
if(!dialog.textContent.includes('2026-10-09'))throw new Error('完成快照未写入窗口');
resolveLatest({run_id:'old-run',as_of_date:'2026-09-30'});await opening;
if(!dialog.textContent.includes('2026-10-09')||!calls.at(-1).includes('snapshot_run_id=new-run'))throw new Error('迟到的 latest 覆盖本次完成快照');
"""
    _run_node(tmp_path, script.replace("MODULE_URL", json.dumps(module_url)).replace("HISTORICAL", json.dumps(historical)))


@pytest.mark.parametrize("symbol", [None, "513500"])
def test_completion_replaces_latest_list_or_detail_url(tmp_path, symbol):
    source = Path("src/api/static/js/allocation-view.js").read_text(encoding="utf-8")
    callback = source.split("createRadarUpdateUI(allocationRequest, ", 1)[1].split("\n  });", 1)[0]
    script = """
const urls=[];
const allocationState={universeId:'overseas_etf',snapshotHistorical:false,detail:DETAIL};
const window={location:{hash:'#radar/overseas_etf/old-run'},history:{state:null,replaceState:(state,title,url)=>urls.push(url)}};
let loaded=0;const allocationLoad=()=>loaded++;
const callback=CALLBACK;
callback({universe_id:'overseas_etf',snapshot_run_id:'new-run'},true);
if(urls[0]!==EXPECTED||loaded!==1)throw new Error('最新列表或详情 URL 未前进');
allocationState.snapshotHistorical=true;
callback({universe_id:'overseas_etf',snapshot_run_id:'newer-run'},true);
if(urls.length!==1||loaded!==1)throw new Error('历史页面被更新覆盖');
"""
    # 历史分支只需详情状态，避免执行与本回归无关的标题渲染。
    script = script.replace("allocationState.snapshotHistorical=true;", "allocationState.snapshotHistorical=true;allocationState.detail={symbol:'513500'};")
    detail = {"symbol": symbol} if symbol else None
    expected = "#radar/overseas_etf/new-run" + (f"/{symbol}" if symbol else "")
    _run_node(tmp_path, script.replace("DETAIL", json.dumps(detail)).replace("CALLBACK", callback + "}").replace("EXPECTED", json.dumps(expected)))
