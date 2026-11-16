"""池选择、股票隔离与迟到响应的前端行为契约；真实浏览器另验收。"""
import json
from pathlib import Path

from tests.api.test_static import _DOM_STUB, _run_node


def test_picker_search_keyboard_and_selection(tmp_path):
    module = Path("src/api/static/js/radar-pool-picker.js").resolve().as_uri()
    script = _DOM_STUB + r"""
Element.prototype.append=function(...nodes){nodes.forEach(n=>this.appendChild(n));};
Element.prototype.getBoundingClientRect=()=>({left:40,bottom:100});
Element.prototype.showModal=function(){this.open=true;};
Element.prototype.close=function(){this.open=false;queueMicrotask(()=>this.dispatchEvent('close'));};
Element.prototype.scrollIntoView=()=>{};
globalThis.innerWidth=1280;globalThis.innerHeight=900;
const {createRadarPoolPicker}=await import(MODULE);
const pools=Array.from({length:24},(_,i)=>({id:'p'+i,name:'投资池'+i,asset_type:i<12?'etf':'stock',description:'用途'+i}));
let selected=null;
const picker=createRadarPoolPicker(pools,'p0',pool=>selected=pool);
const trigger=byClass(picker,'radar-pool-trigger')[0];
const dialog=byClass(picker,'radar-pool-dialog')[0];
dialog.style.setProperty=function(key,value){this[key]=value;};
const search=byClass(picker,'radar-pool-search')[0];
await trigger.click();
if(!dialog.open||document.activeElement!==search)throw new Error('打开没有聚焦搜索 '+dialog.open+' '+document.activeElement?.tagName+' '+search.tagName);
if(byClass(picker,'radar-pool-option').length!==24)throw new Error('扩展池被遗漏');
search.value='用途23';await search.dispatch('input');
const option=byClass(picker,'radar-pool-option')[0];
if(byClass(picker,'radar-pool-option').length!==1)throw new Error('搜索失效');
await dialog.dispatch('keydown',{key:'ArrowDown'});
if(document.activeElement!==option)throw new Error('键盘不能进入列表');
await option.click();
if(selected.id!=='p23'||dialog.open||document.activeElement!==trigger)throw new Error('选择或关闭焦点错误');
await trigger.click();search.value='不存在';await search.dispatch('input');
if(!picker.textContent.includes('没有匹配'))throw new Error('空搜索静默');
await dialog.dispatch('keydown',{key:'Escape'});
if(dialog.open)throw new Error('Esc未关闭');
"""
    _run_node(tmp_path, script.replace("MODULE", json.dumps(module)))


def test_stock_branch_avoids_etf_requests_and_discards_late_response(tmp_path):
    source = Path("src/api/static/js/allocation-view.js").read_text(encoding="utf-8")
    load = source.split("function allocationLoad(focusPicker) {", 1)[1].split("export function initRadar()", 1)[0]
    script = _DOM_STUB + r"""
Element.prototype.append=function(...nodes){nodes.forEach(n=>this.appendChild(n));};
const root=document.createElement('div');
let allocationLoadGeneration=0,allocationPickerFocusGeneration=null;
const allocationFocusCurrentPicker=()=>{};
const allocationState={universes:[],universeId:null,detail:null};
const allocationRoot=()=>root;
const allocationElement=(tag,cls,text)=>{const n=document.createElement(tag);n.className=cls;n.textContent=text;return n;};
const allocationHeader=()=>document.createElement('header');
let recover=0,lists=0,renders=[];
const allocationUpdates={recover:()=>recover++};
let allocationStockTasks=null;const createStockTaskUI=()=>({});
const allocationShowList=()=>lists++;
const allocationRestoreDetail=()=>{};
const renderRadarStockPool=(root,header,pool,payload)=>renders.push(pool.id);
window.location={hash:'#radar/stock-a'};
const pools=[{id:'stock-a',asset_type:'stock'},{id:'stock-b',asset_type:'stock'}];
const calls=[];let resolveFirst;
const allocationRequest=url=>{
 calls.push(url);
 if(url.endsWith('/pools'))return Promise.resolve(pools);
 if(url.includes('/stock-a/'))return new Promise(resolve=>resolveFirst=resolve);
 if(url.includes('/stock-b/'))return Promise.resolve({items:[],industries:[],status:'empty'});
 throw new Error('股票走了ETF请求：'+url);
};
function allocationLoad(focusPicker){LOAD
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
allocationLoad();await flush();
window.location.hash='#radar/stock-b';allocationLoad();await flush();
resolveFirst({items:[],status:'empty'});await flush();
if(recover||lists)throw new Error('股票进入ETF任务或列表');
if(JSON.stringify(renders)!=='["stock-b"]')throw new Error('迟到响应覆盖当前池');
if(calls.some(url=>url.includes('/snapshots')||url.includes('/collector')))throw new Error('股票触发ETF读取');
"""
    _run_node(tmp_path, script.replace("LOAD", load))


def test_update_is_gated_by_catalog_type_and_capability(tmp_path):
    source = Path("src/api/static/js/allocation-view.js").read_text(encoding="utf-8")
    header = source.split("function allocationHeader() {", 1)[1].split("function allocationSnapshotMeta", 1)[0]
    script = _DOM_STUB + r"""
Element.prototype.append=function(...nodes){nodes.forEach(n=>this.appendChild(n));};
const allocationElement=(tag,cls,text)=>{const n=document.createElement(tag);n.className=cls;n.textContent=text;return n;};
const createRadarPoolPicker=()=>document.createElement('div');
const allocationSnapshotMeta=()=>'';
const started=[];
const allocationUpdates={isActive:()=>false,start:id=>started.push(id)};
const allocationState={universes:[{id:'stock',asset_type:'stock',capabilities:{update:false}},{id:'etf',asset_type:'etf',capabilities:{update:true}}],universeId:'stock'};
function allocationHeader(){HEADER
for(const id of ['stock','unknown','etf']){
 allocationState.universeId=id;
 const refresh=byClass(allocationHeader(),'radar-refresh')[0];
 if(refresh.disabled!==(id!=='etf'))throw new Error('更新能力未约束：'+id);
 refresh.onclick();
}
if(JSON.stringify(started)!=='["etf"]')throw new Error('非ETF调用更新');
"""
    _run_node(tmp_path, script.replace("HEADER", header))


def test_default_etf_precedence_and_foreign_historical_snapshot(tmp_path):
    source = Path("src/api/static/js/allocation-view.js").read_text(encoding="utf-8")
    load = source.split("function allocationLoad(focusPicker) {", 1)[1].split("export function initRadar()", 1)[0]
    script = _DOM_STUB + r"""
Element.prototype.append=function(...nodes){nodes.forEach(n=>this.appendChild(n));};
let allocationLoadGeneration=0,allocationPickerFocusGeneration=null;
const allocationFocusCurrentPicker=()=>{};
const root=document.createElement('div');const allocationRoot=()=>root;
const allocationElement=(tag,cls,text)=>{const n=document.createElement(tag);n.className=cls;n.textContent=text;return n;};
const allocationHeader=()=>document.createElement('header');
const allocationState={universes:[],universeId:null};
const allocationUpdates={recover:()=>{}};
let lists=0,pools=[],latest=null,history=null;
const allocationShowList=()=>lists++;const allocationRestoreDetail=()=>{};
const renderRadarStockPool=()=>{};
window.location={hash:'#radar'};
const allocationRequest=async url=>{
 if(url.endsWith('/pools'))return pools;
 if(url.includes('/snapshots/latest'))return latest;
 if(url.includes('/snapshots/'))return history;
 if(url.includes('/stocks/'))return {items:[]};
 return {};
};
function allocationLoad(focusPicker){LOAD
const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
for(const [catalog,expected] of [
 [[{id:'stock',asset_type:'stock'},{id:'other',asset_type:'etf'},{id:'cn_hk_etf',asset_type:'etf'}],'cn_hk_etf'],
 [[{id:'stock',asset_type:'stock'},{id:'other',asset_type:'etf'}],'other'],
 [[{id:'stock',asset_type:'stock'}],'stock']
]){
 pools=catalog;allocationState.universeId=null;allocationLoad();await flush();
 if(allocationState.universeId!==expected)throw new Error('默认选择错误 '+expected);
}
pools=[{id:'cn_hk_etf',asset_type:'etf'}];window.location.hash='#radar/cn_hk_etf/foreign';
latest={run_id:'latest',universe_id:'cn_hk_etf'};history={run_id:'foreign',universe_id:'overseas_etf'};
const before=lists;allocationLoad();await flush();
if(lists!==before||!root.textContent.includes('快照所属池与当前选择不一致'))throw new Error('跨池历史快照被展示');
"""
    _run_node(tmp_path, script.replace("LOAD", load))


def test_delayed_initialization_resumes_only_active_radar(tmp_path):
    source = Path("src/api/static/js/allocation-view.js").read_text(encoding="utf-8")
    init = source.split("export function initRadar() {", 1)[1]
    script = _DOM_STUB + r"""
const view=makeElement('view-radar');
let allocationUpdates=null,loads=0,activations=0;
const allocationState={universeId:null};
const allocationRequest=()=>{};
const createRadarUpdateUI=()=>({});
const allocationLoad=()=>loads++;
const allocationActivate=()=>activations++;
window.location={hash:''};window.history={replaceState:(state,title,url)=>window.location.hash=url};
function initRadar(){INIT
// 模拟会话启动promise未完成时，用户已通过全局导航进入雷达。
view.classList.add('active');await Promise.resolve();initRadar();
if(loads!==1||window.location.hash!=='#radar'||activations)throw new Error('延迟初始化留下空雷达或绕过导航');
view.classList.remove('active');window.location.hash='';initRadar();
if(loads!==1||window.location.hash!=='')throw new Error('初始化擅自切换视图');
"""
    _run_node(tmp_path, script.replace("INIT", init))


def test_pool_switch_restores_connected_focus_and_prototype_named_pool(tmp_path):
    source = Path("src/api/static/js/allocation-view.js").read_text(encoding="utf-8")
    state = source.split("var allocationState = ", 1)[1].split(";", 1)[0]
    header = source.split("function allocationHeader() {", 1)[1].split("function allocationSnapshotMeta", 1)[0]
    focus = source.split("function allocationFocusCurrentPicker(generation) {", 1)[1].split("function allocationLoad", 1)[0]
    script = _DOM_STUB + r"""
Element.prototype.append=function(...nodes){nodes.forEach(n=>this.appendChild(n));};
const allocationState=STATE;
const root=document.createElement('div');const allocationRoot=()=>root;
const allocationElement=(tag,cls,text)=>{const n=document.createElement(tag);n.className=cls;n.textContent=text;return n;};
const allocationUpdates={isActive:()=>false};const allocationSnapshotMeta=()=>'';
let select;
const createRadarPoolPicker=(pools,id,callback)=>{select=callback;const n=document.createElement('button');n.className='radar-pool-trigger';return n;};
window.location={hash:'#radar/cn_hk_etf'};
window.history={pushState:(state,title,url)=>window.location.hash=url};
let allocationPickerFocusGeneration=null,allocationLoadGeneration=0;
function allocationFocusCurrentPicker(generation){FOCUS
const allocationLoad=focus=>{const generation=++allocationLoadGeneration;if(focus===true)allocationPickerFocusGeneration=generation;root.replaceChildren(allocationHeader());allocationFocusCurrentPicker(generation);};
function allocationHeader(){HEADER
allocationState.universes=[{id:'cn_hk_etf',asset_type:'etf',capabilities:{update:true}},{id:'constructor',asset_type:'stock',capabilities:{update:false}}];
allocationState.universeId='cn_hk_etf';root.append(allocationHeader());
const old=byClass(root,'radar-pool-trigger')[0];old.focus();
select(allocationState.universes[1]);
// 对话框close事件异步抵达时不能把焦点返回被移除的入口。
await Promise.resolve();
const current=byClass(root,'radar-pool-trigger')[0];
if(document.activeElement!==current||current===old)throw new Error('池切换没有聚焦新入口');
if(window.location.hash!=='#radar/constructor'||allocationState.benchmark!=='csi_300')throw new Error('原型名称池污染浏览状态');
"""
    _run_node(tmp_path, script.replace("STATE", state).replace("FOCUS", focus).replace("HEADER", header))
