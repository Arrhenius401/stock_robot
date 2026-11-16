// 股票行情、候选与财务任务独立保存，统一复用原消息窗口。
import { createRadarTaskWindow } from './radar-task-window.js?v=20261112-stock-results-1';
const API='/api/v1/radar/stocks';
const kinds={refresh_members:'行情更新',financial_review:'财务复核',discover_candidates:'候选准备'};
const statuses={queued:'排队中',running:'执行中',completed:'已完成',partial:'部分完成',failed:'失败',cancelled:'已取消'};
const active=t=>!!t&&['queued','running'].includes(t.status);
const node=(tag,cls='',text='')=>{const n=document.createElement(tag);n.className=cls;n.textContent=text;return n;};
const button=(text,callback)=>{const b=node('button','subs-btn',text);b.type='button';b.addEventListener('click',callback);return b;};
const post=data=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
const checkName=name=>{const match=/^annual_(parent|deducted)(?:_profit)?_(\d{4})$/.exec(name||'');return match?match[2]+'年'+(match[1]==='parent'?'归母净利润':'扣非净利润'):({ttm_parent_profit:'最近十二个月归母净利润',ttm_deducted_profit:'最近十二个月扣非净利润',ttm_parent:'最近十二个月归母净利润',ttm_deducted:'最近十二个月扣非净利润'}[name]||name||'盈利条件');};
export function createStockTaskUI(request,onChange=()=>{}){
 const shell=createRadarTaskWindow('radarStockUpdateNotice','股票任务结果');
 const {host,dialog,body,footer,title,handle,place}=shell;
 const tasks=new Map(),timers=new Map(),starting=new Map();
 let pool=null,kind='financial_review',minimized=true,context=null,resultGeneration=0;
 const key=(id,type)=>id+':'+type;
 function remember(task,taskPool,taskKind){
  if(!task)return;const k=key(taskPool.id,taskKind),old=tasks.get(k);tasks.set(k,task);
  if(task.run_id)for(const [savedKey,saved] of tasks)if(saved.run_id===task.run_id)tasks.set(savedKey,task);
  if(pool?.id===taskPool.id||pool&&tasks.get(key(pool.id,taskKind))?.run_id===task.run_id)render();
  onChange(task,active(old)&&!active(task));
  if(active(task))poll(taskPool,taskKind,task.run_id);
 }
 function render(){
  if(!pool)return;let task=tasks.get(key(pool.id,kind));
  if(!task){const entry=Object.keys(kinds).find(type=>tasks.has(key(pool.id,type)));if(entry){kind=entry;task=tasks.get(key(pool.id,kind));}}
  host.hidden=!task;if(!task)return;host.className='radar-update-toast'+(minimized?' minimized':'');host.replaceChildren();
  const status=statuses[task.status]||'状态待核验',label=kinds[kind]+' · '+status;
  if(minimized){const grip=handle(node('span','radar-update-mini-grip','⠿'),'移动更新提示');const expand=button(label+' · 查看反馈',()=>{minimized=false;render()});expand.className='radar-update-mini-button';host.append(grip,expand);}
  else{
   const head=handle(node('div','radar-update-toast-title'),'移动更新提示');head.append(node('strong','','更新消息'),button('收起',()=>{minimized=true;render()}));host.append(head,node('p','radar-meta',pool.name));
   const tabs=node('div','stock-task-tabs');for(const type of Object.keys(kinds)){const saved=tasks.get(key(pool.id,type));if(type==='discover_candidates'&&!saved)continue;const tab=button(kinds[type]+' · '+(saved?(statuses[saved.status]||'待核验'):'尚未开始'),()=>{kind=type;render()});tab.setAttribute('aria-pressed',String(type===kind));tabs.append(tab)}host.append(tabs);
   const progress=task.progress||{},total=progress.total??task.input_count??task.input_snapshot?.symbols?.length;
   const text=task.poll_error||(task.source_stopped?'来源返回限流（HTTP 429），已停止后续采集；已保存的进度保留。':task.error)||(task.status==='queued'?'等待前一项股票资料任务结束。':kind==='financial_review'?(active(task)?'已处理 '+(progress.attempted||0)+' / '+(total??'—')+' 家':'财务复核结束，详细结果可单独查看。'):'任务在后台继续，切换页面不会取消。');
   host.append(node('p','radar-update-toast-stage',text));
   if(kind==='financial_review')host.append(node('p','radar-meta','盈利数值初筛不等于正式入池；与行情更新分开处理。'));
   const actions=node('div','radar-update-actions');actions.append(button('查看'+(kind==='financial_review'?'财务':'任务')+'结果',()=>openResults(pool,kind)));
   if(task.status==='partial'||task.status==='failed'){const target=pool,type=kind;actions.append(button(kind==='financial_review'?'继续财务复核':'重试',()=>start(target,type,true).catch(error=>showError(error,target,type))));}host.append(actions);
  }
  place(host,'notice');
 }
 function showError(error,target=pool,type=kind){if(!target)return;const k=key(target.id,type),old=tasks.get(k);tasks.set(k,{...old,status:old?.status||'failed',error:'任务未能启动：'+error.message});if(pool?.id===target.id&&kind===type){minimized=false;render()}}
 function poll(taskPool,taskKind,id){
  if(!id||timers.has(id))return;
  timers.set(id,window.setTimeout(async()=>{
   timers.delete(id);if(tasks.get(key(taskPool.id,taskKind))?.run_id!==id)return;
   try{const task=await request(API+'/updates/'+encodeURIComponent(id));if(tasks.get(key(taskPool.id,taskKind))?.run_id!==id)return;remember(task,taskPool,taskKind)}
   catch(error){const task=tasks.get(key(taskPool.id,taskKind));if(!task||task.run_id!==id)return;if(error.status===404){remember({...task,status:'failed',error:'任务已不可读取，请重试。'},taskPool,taskKind);return}task.poll_error='状态暂不可读取：'+error.message+'；正在重新连接。';if(pool?.id===taskPool.id||pool&&tasks.get(key(pool.id,taskKind))?.run_id===task.run_id)render();poll(taskPool,taskKind,id)}
  },2000));
 }
 async function select(next){
  pool=next;render();
  await Promise.all(Object.keys(kinds).map(async type=>{
   const k=key(next.id,type),before=tasks.get(k);
   try{const task=await request(API+'/updates/latest?pool_id='+encodeURIComponent(next.id)+'&kind='+type);if(tasks.get(k)!==before)return;if(task)remember(task,next,type)}catch(error){if(pool?.id===next.id&&!before&&tasks.get(k)===before){tasks.set(k,{kind:type,status:'failed',error:'任务状态读取失败：'+error.message});render()}}
  }));
 }
 function start(next,type,retry=false){
  const k=key(next.id,type);pool=next;kind=type;minimized=false;
  if(starting.has(k))return starting.get(k);
  const old=tasks.get(k);if(active(old)){render();return Promise.resolve(old)}
  const path=type==='discover_candidates'?'/discover':'/'+encodeURIComponent(next.id)+(type==='financial_review'?'/financial-review':'/update');
  const work=request(API+path,post(type==='discover_candidates'?{pool_id:next.id}:retry&&type==='financial_review'&&old?.run_id?{resume_run_id:old.run_id}:{})).then(task=>{remember(task,next,type);return task}).finally(()=>starting.delete(k));starting.set(k,work);return work;
 }
 async function confirmFinancial(next){
  pool=next;kind='financial_review';const old=tasks.get(key(next.id,kind));if(active(old)){minimized=false;render();return}
  const token=++resultGeneration;context=null;title.textContent='开始财务复核';body.replaceChildren(node('p','radar-meta','正在读取当前池复核范围…'));footer.replaceChildren(button('取消',()=>{++resultGeneration;dialog.close()}));if(!dialog.open)dialog.showModal();place(dialog,'dialog');
  try{const scope=await request(API+'/'+encodeURIComponent(next.id)+'/financial-review/scope');if(token!==resultGeneration||!dialog.open)return;
   body.replaceChildren(node('h3','',next.name+' · '+scope.total+' 家'),node('p','radar-note',scope.scope_complete&&scope.total?'固定当前池已准备的指数初筛范围，不扩大到全市场。':'范围尚未完整准备，请先准备候选。'),node('p','radar-meta','复核三年及最近十二个月的归母与扣非利润。现金流另列参考，不自动修改成员。任务按现有队列顺序执行。'));
   body.append(node('p','radar-meta','成分范围日期：'+(scope.scope_as_of||scope.as_of||'未知')));for(const source of scope.source_snapshots||[])body.append(node('p','radar-meta',(source.name||source.index_code||'指数来源')+' · 快照 '+(source.snapshot_date||'未知')+' · 生效 '+(source.effective_date||'未知')));
   if(scope.scope_complete&&scope.total){const begin=button('开始复核',async()=>{begin.disabled=true;try{await start(next,'financial_review');if(token===resultGeneration&&dialog.open)dialog.close()}catch(error){if(token===resultGeneration){body.append(node('p','radar-note','启动失败：'+error.message));begin.disabled=false}}});footer.replaceChildren(begin,button('取消',()=>{++resultGeneration;dialog.close()}))}
  }catch(error){if(token===resultGeneration)body.replaceChildren(node('p','radar-note','范围读取失败：'+error.message),button('重试',()=>confirmFinancial(next)))}
 }
 async function openResults(next,type,page=1){
  if(!next)return;const task=tasks.get(key(next.id,type)),token=++resultGeneration;context={poolId:next.id,type};title.textContent=next.name+' · '+kinds[type]+'结果';body.replaceChildren(node('p','radar-meta','正在读取结果…'));footer.replaceChildren(button('关闭窗口',()=>dialog.close()));if(!dialog.open)dialog.showModal();place(dialog,'dialog');
  if(!task?.run_id){body.replaceChildren(node('p','radar-note',task?.error||'尚未启动此任务。'));return}
  if(type!=='financial_review'){body.replaceChildren(node('p','radar-note',statuses[task.status]||task.status));for(const item of task.results||[])body.append(node('p','stock-check',(item.scope||'资料')+' · '+(statuses[item.status]||'待复核')+' · '+item.summary));return}
  try{const data=await request(API+'/updates/'+encodeURIComponent(task.run_id)+'/financial-results?page='+page);if(token!==resultGeneration||!dialog.open)return;const sum=data.summary||{};
   body.replaceChildren(node('p','radar-meta','研究日期：'+(data.as_of||task.target_date||'未知')+' · 资料按实际抓取时间记录'),node('p','radar-meta','已处理 '+(sum.attempted||0)+' / '+(sum.total||0)+' 家 · 尚未处理 '+(sum.not_attempted||0)+' 家'),node('p','stock-financial-counts','盈利条件通过 '+(sum.numeric_passed||0)+' · 未通过 '+(sum.numeric_failed||0)+' · 资料待补 '+(sum.pending_verification||0)),node('p','radar-note',data.notice||'仅盈利数值初筛，其他资格与风格条件尚未覆盖，不代表正式入池。'));
   for(const item of data.items||[]){const row=node('details','stock-fold');row.append(node('summary','',(item.name||'')+' '+item.symbol+' · '+({passed:'盈利条件通过',failed:'未通过',pending_verification:'资料待补',not_attempted:'尚未处理'}[item.financial_numeric_status]||'资料待补')));
    for(const check of item.financial_numeric_checks||[])row.append(node('p','stock-check',checkName(check.name||check.code)+' · '+({passed:'通过',failed:'未通过',pending_verification:'待补'}[check.outcome||check.status]||'待核验')+' · '+(check.reason||check.message||'')+(check.amount_yuan!=null?' · 金额 '+check.amount_yuan+' 元':'')));
    if(item.source_diagnostics?.code&&item.source_diagnostics.code!=='complete')row.append(node('p','radar-note',item.source_diagnostics.message));
    if(item.error)row.append(node('p','radar-note',item.error));
    if(item.cash_flow_reference)row.append(node('p','radar-meta','现金流比率仅作参考：'+(item.cash_flow_reference.ratio??item.cash_flow_reference.value??'待核验')+'；金融适用性未核验。'));
    const references=[...(Array.isArray(item.sources)?item.sources:[]),...(item.financial_numeric_checks||[]).flatMap(check=>check.sources||[]),...(item.cash_flow_reference?.sources||[])];
    const unique=new Map(references.map(source=>[JSON.stringify(source),source]));
    if(unique.size){const sourceBox=node('details','stock-fold');sourceBox.append(node('summary','','来源与日期 · '+unique.size+' 条'));for(const source of unique.values()){
     const url=source.source_url||source.url||source.endpoint,entry=node('div','stock-check');
     if(typeof url==='string'&&url.startsWith('https://')){const a=node('a','','结构化数据来源');a.href=url;a.target='_blank';a.rel='noopener noreferrer';entry.append(a)}
     entry.append(node('p','radar-meta','报表期间：'+(source.period_end||'未知')+' · 披露日期：'+(source.disclosed_on||'未知')),node('p','radar-meta','抓取时间：'+(source.fetched_at||'未知')+' · 源修订时间：'+(source.source_updated_at||'未知')));
     if(source.source_field)entry.append(node('p','radar-meta','来源字段：'+source.source_field));sourceBox.append(entry)
    }row.append(sourceBox)}body.append(row)
   }
   const pager=node('div','stock-pagination'),prev=button('上一页',()=>openResults(next,type,page-1)),after=button('下一页',()=>openResults(next,type,page+1));prev.disabled=page<=1;after.disabled=page*(data.page_size||15)>=data.total;pager.append(prev,node('span','radar-meta','第 '+page+' 页'),after);body.append(pager);place(dialog,'dialog');
  }catch(error){if(token===resultGeneration)body.replaceChildren(node('p','radar-note','结果读取失败：'+error.message),button('重试',()=>openResults(next,type,page)))}
 }
 return {select,start,confirmFinancial,openResults,isActive:(id,type)=>active(tasks.get(key(id,type))),showError};
}
