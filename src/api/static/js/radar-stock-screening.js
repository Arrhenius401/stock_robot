import { createRadarTaskWindow } from './radar-task-window.js?v=20261112-stock-results-1';
const API='/api/v1/radar/stocks', saved=new Map();
const checkNames={liquidity:'成交活跃度',cash_review:'现金质量核验',cash_ratio:'经营现金流与归母利润比率',dividend_history:'连续分红',audit:'审计意见',governance:'治理风险',leadership:'行业地位',financial_quality:'财务质量',listing_age:'上市年限',capitalization_current:'当前公司总市值',capitalization_average_60d:'60日平均公司总市值',ttm_parent_profit:'TTM归母净利润',ttm_deducted_profit:'TTM扣非净利润'};
const fieldNames={netcash_operate:'经营活动现金流净额',construct_long_asset:'购建长期资产支付现金',assign_dividend_profit:'分配股利及利息支付现金',end_cash:'期末现金余额'};
const sourceNames={annual_report_profit_distribution:'年报利润分配',annual_report_cashflow:'年报现金流量表',market_turnover_60d:'60日成交额',manual_review:'人工证据核验',annual_report:'年报归档',turnover:'成交额正式来源'};
const resultNames={passed:'通过',failed:'未通过',pending:'待检验',pending_verification:'待核验',attention:'需关注',not_applicable:'不适用',unavailable:'资料不足'};
const phases={reference_review:'采集与核验基础资料',snapshot:'读取资料快照',financial_review:'采集与核验财务资料',dividend_review:'采集与核验分红资料',turnover_review:'采集成交额',review:'复核筛选条件',finalizing:'归档完整结果'};
const statuses={queued:'排队中',running:'执行中',completed:'已完成',partial:'部分完成',failed:'失败',interrupted:'已中断',cancelled:'已取消'};
const node=(tag,cls='',text='')=>{const n=document.createElement(tag);n.className=cls;n.textContent=text;return n;};
const add=(n,...children)=>{children.forEach(c=>n.appendChild(c));return n;};
const button=(text,action,cls='')=>{const b=node('button','subs-btn '+cls,text);b.type='button';b.addEventListener('click',action);return b;};
function checkLabel(value){const annual=/^annual_(parent|deducted)_profit_(\d{4})$/.exec(value||'');return annual?annual[2]+'年'+(annual[1]==='parent'?'归母净利润':'扣非净利润'):checkNames[value]||'核验条件';}
function taskItemLabel(item){
 const parts=String(item.scope||'').split(':'),channel=parts[0]==='reference'?parts[1]:parts[0];
 const labels={financial:'财务资料',dividend:'分红资料',turnover:'成交额资料',market_value:'市值资料',profile:'上市资料',company_profile:'上市资料',roster:'上市资料',industry:'行业分类资料',governance:'治理资料',leadership:'行业地位资料',index_scope_v1:'候选范围'};
 const symbol=item.symbol||parts.find(part=>/^\d{6}$/.test(part));
 return (labels[channel]||(item.condition?checkLabel(item.condition):'资料核验'))+(symbol?' · '+symbol:'');
}
function taskItemSummary(item){const summary=item.reason||item.error||item.summary;return typeof summary==='string'?summary:(summary?.message||'');}
const automaticKinds=new Set(['annual_report_cashflow','market_turnover_60d']);
const canComplete=item=>automaticKinds.has(item.source?.kind);
const post=body=>({method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
let shell=null;
export function renderStockScreening(root,header,pool,options={}){
 const request=options.request,base=API+'/'+encodeURIComponent(pool.id);
 const state=saved.get(pool.id)||{all:false,status:'passed',page:1,search:'',expanded:false};saved.set(pool.id,state);
 const panel=node('section','stock-management stock-screening'),notice=node('p','radar-note'),stats=node('div','stock-screening-stats'),rules=node('section','panel stock-screening-rules'),list=node('section','panel stock-screening-list');
 const head=add(node('div','radar-pool-content-head'),node('h2','',pool.name),button('资料与成员管理',()=>options.openLegacy()));
 const date=node('p','radar-meta');add(panel,head,date,notice,stats,rules,list);root.replaceChildren(header,panel);
 let disposed=false,serial=0,timer=null,pollSerial=0,task=null,windowMode='task',plan=null;
 const current=()=>!disposed&&root.contains(panel)&&(!options.isCurrent||options.isCurrent());
 if(!shell)shell=createRadarTaskWindow('stockScreeningTaskNotice','股票池任务',{resizable:true});
 const {host,dialog,title,body,footer,head:windowHead}=shell;
 function error(exc){if(current())notice.replaceChildren(node('span','','读取失败：'+exc.message+'；上一次完整结果保留。'),button('重新读取结果',load));}
 function taskWindow(){windowMode='task';title.textContent=task?.kind==='completion'?'补全待检验':'更新股票池';body.replaceChildren();if(task){const progress=task.progress||{};add(body,node('p','',statuses[task.status]||task.status),node('p','',phases[task.phase]||'准备资料'),node('p','',`已处理 ${progress.attempted??0} / ${progress.total??'—'}`));if(progress.total>0){const bar=node('progress');bar.max=progress.total;bar.value=progress.attempted||0;bar.setAttribute('aria-label','任务进度');body.appendChild(bar);}if(task.error)body.appendChild(node('p','radar-note',task.error));for(const item of task.results||[])body.appendChild(node('p','stock-check',taskItemLabel(item)+' · '+(statuses[item.status]||resultNames[item.status]||'待核验')+' · '+taskItemSummary(item)));}body.appendChild(node('p','radar-meta','收起窗口或切换页面后，任务在后台继续。'));footer.replaceChildren(button('收起',()=>dialog.close()),button('重新读取进度',()=>poll()));if(task&&['failed','partial','interrupted'].includes(task.status))footer.appendChild(button('重试',()=>task.kind==='completion'?openPlan():startUpdate()));}
 function remember(value){task=value?.task||value;if(!task)return;host.hidden=false;host.replaceChildren();const grip=shell.handle(node('span','radar-update-mini-grip','⠿'),'移动股票任务');add(host,grip,button((task.kind==='completion'?'补全':'更新')+' · '+(statuses[task.status]||task.status)+' · 查看',()=>{taskWindow();if(!dialog.open)dialog.showModal();shell.place(dialog,'dialog');}));shell.place(host,'notice');if(dialog.open&&windowMode==='task')taskWindow();}
 async function poll(){const generation=++pollSerial;clearTimeout(timer);try{const value=await request(task?.run_id?API+'/screening/tasks/'+encodeURIComponent(task.run_id):base+'/screening/tasks/latest');if(!current()||generation!==pollSerial)return;const old=task;remember(value);if(task&&['running','queued'].includes(task.status))timer=window.setTimeout(poll,2000);else if(old&&['running','queued'].includes(old.status)){load();loadRules();}}catch(exc){if(current()&&generation===pollSerial){notice.textContent='任务状态暂不可读取：'+exc.message+'；正在重连。';timer=window.setTimeout(poll,4000);}}}
 async function startUpdate(){try{notice.textContent='';const value=await request(base+'/screening/update',post({}));if(!current())return;remember(value);taskWindow();if(!dialog.open)dialog.showModal();shell.place(dialog,'dialog');poll();}catch(exc){error(exc);}}
 async function openPlan(){
  windowMode='plan';plan=null;title.textContent='待检验资料补全';
  body.replaceChildren(node('p','','正在生成补全计划…'));footer.replaceChildren(button('关闭',()=>dialog.close()));if(!dialog.open)dialog.showModal();shell.place(dialog,'dialog');
  try{
   const value=await request(base+'/completion/plan');if(!current()||windowMode!=='plan')return;plan=value;
   const items=plan.items||[],manual=plan.manual_count??items.filter(item=>!canComplete(item)).length,automatic=plan.automatic_count??items.filter(canComplete).length,pending=plan.pending_total??items.length;
   body.replaceChildren(node('p','',`补全计划 · ${plan.summary?.symbols??new Set(items.map(i=>i.symbol)).size} 家 / ${items.length} 项`));
   if(pending)body.appendChild(node('p','radar-note',`待检验 ${pending} 家；可自动执行 ${automatic} 项；需人工证据 ${manual} 项。全部补全只执行已有自动来源，人工项继续保留待检验。`));
   for(const item of items){
    const source=item.source||{},gap=item.gap&&typeof item.gap==='object'?item.gap:{};
    const row=add(node('div','stock-check'),node('strong','',(item.name||'')+' '+item.symbol),node('p','',item.label||checkNames[item.condition]||item.condition));
    if(item.reason||gap.reason)add(row,node('p','radar-meta',item.reason||gap.reason));
    const years=gap.years||item.years||source.years||[];if(years.length)add(row,node('p','radar-meta','所需年份：'+years.join('、')));
    const fields=gap.fields||[];if(fields.length)add(row,node('p','radar-meta','待补字段：'+fields.map(value=>fieldNames[value]||checkNames[value]||value).join('、')));
    if(gap.window)add(row,node('p','radar-meta','所需窗口：'+gap.window));
    add(row,node('p','radar-meta','资料来源：'+(sourceNames[source.kind]||source.kind||'现有正式来源')+(source.provider?' · '+(sourceNames[source.provider]||source.provider):'')));
    add(row,node('p','radar-meta',canComplete(item)?'使用现有正式来源自动补全':source.kind==='manual_review'?'需人工证据，无法自动补全':'当前来源尚不支持自动补全，需补充证据'));
    body.appendChild(row);
   }
   for(const blocked of plan.blocked_items||[]){
    const reasons=blocked.reason||blocked.gap?.reason||(blocked.checks||[]).map(check=>check.reason).filter(Boolean).join('；')||'共同条件尚未满足，暂无可执行的自动补全来源';
    body.appendChild(add(node('div','stock-check'),node('strong','',(blocked.name||'')+' '+(blocked.symbol||'')),node('p','radar-note',reasons)));
   }
   if(!items.length)body.appendChild(node('p','radar-note',pending>0?'仍有待检验资料，当前尚不支持自动补全；请查看上述阻碍与待补证原因。':'当前没有待补全事项。'));
   const apply=button('全部补全',async()=>{
    apply.disabled=true;
    try{const result=await request(base+'/completion/apply',post({plan_sha256:plan.plan_sha256}));if(!current())return;remember(result);taskWindow();poll();}
    catch(exc){if(current()){body.appendChild(node('p','radar-note',exc.status===409?'计划或资料已变化，请重新生成计划。':exc.message));footer.replaceChildren(button('重新生成计划',openPlan),button('关闭',()=>dialog.close()));}}
   });
   apply.disabled=automatic===0;footer.replaceChildren(apply,button('关闭',()=>dialog.close()));
  }catch(exc){if(current())body.replaceChildren(node('p','radar-note','计划读取失败：'+exc.message),button('重试',openPlan));}
 }
 async function openDetail(symbol){
  const token=++serial;list.replaceChildren(node('p','','正在读取筛选快照…'));
  try{
   const data=await request(base+'/screening/companies/'+encodeURIComponent(symbol));if(!current()||token!==serial)return;
   const item=data.item;
   list.replaceChildren(add(node('div','radar-pool-content-head'),node('h3','',(item.name||symbol)+' · '+symbol),button('返回筛选列表',load)),node('p','radar-meta','筛选截点 '+data.as_of+' · 规则版本 '+data.rule_version),node('p','','当前池结论：'+(resultNames[item.status]||item.status)),node('p','',item.reason||''));
   const evidence=node('details','stock-snapshot-checks stock-fold');evidence.appendChild(node('summary','','查看条件明细 · '+(item.checks||[]).length+' 项'));
   for(const check of item.checks||[]){add(evidence,node('p','stock-check',(check.label||checkLabel(check.code||check.name))+' · '+(resultNames[check.outcome||check.result||check.status]||check.outcome||check.result||check.status||'待核验')+'：'+(check.reason||'')));
    const references=[...(Array.isArray(check.sources)?check.sources:[]),...(check.source&&typeof check.source==='object'?[check.source]:[])];
    for(const source of references){
     const sourceRow=node('p','radar-meta');const url=source.source_url||source.url;
     if(typeof url==='string'&&url.startsWith('https://')){const link=node('a','','证据来源');link.href=url;link.target='_blank';link.rel='noopener noreferrer';sourceRow.appendChild(link);}
     const dates=[source.period_end?'报告期 '+source.period_end:'',source.disclosed_on?'披露日期 '+source.disclosed_on:'',source.fetched_at?'抓取时间 '+source.fetched_at:''].filter(Boolean);
     if(dates.length)sourceRow.appendChild(node('span','',' '+dates.join(' · ')));if(sourceRow.textContent)evidence.appendChild(sourceRow);
    }
   }
   add(list,evidence,node('p','radar-note','以上为当前列表绑定的筛选快照（含补全复核结果）。当前资料可能已更新，可另行查看。'),button('查看当前资料',()=>options.openLegacy(symbol)));
  }catch(exc){if(current()&&token===serial)list.replaceChildren(node('p','radar-note','筛选详情读取失败：'+exc.message),button('重试',()=>openDetail(symbol)),button('返回筛选列表',load));}
 }
 function renderRules(data){rules.replaceChildren(add(node('div','radar-pool-content-head'),node('h3','','筛选规则'),node('span','radar-meta','版本 '+(data.rule_version||'未知'))));const items=data.items||[],ol=node('ol','stock-screening-rule-list');(state.expanded?items:items.slice(0,5)).forEach(item=>add(ol,add(node('li'),node('span','radar-meta',(item.group==='common'?'共同条件':'当前池条件')+' · '),node('strong','',item.label||item.code),node('span','','：'+item.description))));add(rules,ol);if(items.length>5)rules.appendChild(button(state.expanded?'收起规则':`展开全部 · 共 ${items.length} 条`,()=>{state.expanded=!state.expanded;renderRules(data);},'stock-rules-toggle'));}
 function renderStats(data){stats.replaceChildren();const group=node('div','stock-screening-counts');for(const [key,label,icon] of [['screened','已筛选','◎'],['passed','已通过','✓'],['pending','待检验','◷'],['failed','已落选','×']]){const b=button('',()=>{state.status=key==='screened'?'all':key;state.all=true;state.page=1;load();},'stock-count '+key);add(b,node('span','',icon+' '+label),node('strong','',String(data.counts?.[key]??0)));b.setAttribute('aria-label',label+' '+(data.counts?.[key]??0));group.appendChild(b);}add(stats,group,button('补全待检验',openPlan,'stock-completion-open'));}
 async function load(){const token=++serial;try{const query=new URLSearchParams({status:state.status,search:state.search,page:String(state.page),page_size:'20'});const data=await request(base+'/screening?'+query);if(!current()||token!==serial)return;notice.textContent='';date.textContent='资料截点 '+(data.as_of||'尚未筛选')+' · 筛选通过不代表发布成员';renderStats(data);list.replaceChildren();const titleText=state.status==='passed'?(state.all?'已通过股票':'已通过股票 · 前20家'):({pending:'待检验股票',failed:'已落选股票',all:'已筛选股票'}[state.status]);const h=add(node('div','radar-pool-content-head'),node('h3','',titleText));if(!state.all)h.appendChild(button('查看全部',()=>{state.all=true;load();}));else h.appendChild(button('返回前20家',()=>{state.all=false;state.status='passed';state.search='';state.page=1;load();}));list.appendChild(h);if(state.all){const form=node('form','stock-toolbar');const input=node('input','stock-search');input.type='search';input.placeholder='搜索代码或公司名称';input.value=state.search;const submit=button('搜索',()=>{state.search=input.value;state.page=1;load();});form.addEventListener('submit',e=>{e.preventDefault();state.search=input.value;state.page=1;load();});add(form,input,submit);list.appendChild(form);}for(const item of data.items||[]){const row=node('div','stock-row stock-screening-row');add(row,button((item.name||item.symbol)+' · '+item.symbol,()=>openDetail(item.symbol),'stock-company'),node('span','radar-meta',item.industry||[item.industry_level1,item.industry_level2].filter(Boolean).join(' / ')||'行业待核验'),node('span','radar-meta stock-screening-reason',item.reason||item.summary||(state.status==='passed'?'共同条件及本池风格通过':'')));list.appendChild(row);}if(!data.items?.length)list.appendChild(node('p','radar-note','当前没有此类股票。'));if(state.all){const bar=node('div','stock-pagination'),prev=button('上一页',()=>{state.page--;load();}),next=button('下一页',()=>{state.page++;load();});prev.disabled=state.page<=1;next.disabled=state.page*20>=data.total;add(bar,prev,node('span','radar-meta',`第 ${state.page} 页 · 共 ${data.total} 家`),next);list.appendChild(bar);}}catch(exc){if(current()&&token===serial){error(exc);if(!list.children.length)list.appendChild(button('重试',load));}}}
 const refresh=header.querySelector('.radar-refresh');if(refresh){refresh.disabled=false;refresh.textContent='更新股票池';refresh.onclick=startUpdate;}
 const minimize=button('—',()=>dialog.close());minimize.setAttribute('aria-label','收起股票任务窗口');if(shell.actions)shell.actions.replaceChildren(minimize,...Array.from(shell.actions.children));else windowHead.appendChild(minimize);
 async function loadRules(){try{const data=await request(base+'/screening/rules');if(current())renderRules(data);}catch(exc){if(current())rules.replaceChildren(node('p','radar-note','规则读取失败：'+exc.message),button('重试',loadRules));}}
 loadRules();load();poll();
 return ()=>{disposed=true;serial++;pollSerial++;window.clearTimeout(timer);host.hidden=true;dialog.close();minimize.remove();};
}
