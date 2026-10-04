// 更新任务独立于页面 DOM：切换页面、标的池不会终止轮询。
const active = status => ['queued', 'running', 'retry_wait'].includes(status);
const element = (tag, cls, text) => { const node = document.createElement(tag); node.className = cls || ''; if (text != null) node.textContent = text; return node; };
const button = (text, callback) => { const node = element('button', 'subs-btn', text); node.type = 'button'; node.onclick = callback; return node; };
const phases = ['calendar', 'etf', 'overseas', 'finalizing'];
const labels = ['准备交易日历', '检查 ETF 场内行情', '获取海外指数行情', '整理更新结果'];
export function createRadarUpdateUI(request, onChange) {
  const tasks = new Map(), timers = new Map(), positions = {notice: null, dialog: null};
  let selected = null, minimized = false, context = null, generation = 0;
  const host = element('aside', 'radar-update-toast'); host.hidden = true; host.id = 'radarUpdateNotice'; host.setAttribute('role', 'status'); host.setAttribute('aria-live', 'polite');
  const dialog = element('dialog', 'radar-update-dialog'); dialog.setAttribute('aria-labelledby', 'radarUpdateTitle');
  const head = element('div', 'radar-update-dialog-head'); const title = element('h2', '', '更新结果与海外动态'); title.id = 'radarUpdateTitle';
  head.append(title, button('×', () => dialog.close())); head.lastChild.setAttribute('aria-label', '关闭更新结果');
  const body = element('div', 'radar-update-dialog-body'), footer = element('div', 'radar-update-dialog-actions'); dialog.append(head, body, footer); document.body.append(host, dialog);
  function handle(node, label) { node.dataset.dragHandle = 'true'; node.tabIndex = 0; node.setAttribute('aria-label', label + '；方向键移动，Home复位'); return node; }
  function place(node, key) {
    if (key === 'dialog' && innerWidth <= 760) { reset(node); return; }
    const pos = positions[key]; if (!pos) return; const rect = node.getBoundingClientRect(), margin = 12;
    pos.x = Math.max(margin, Math.min(pos.x, innerWidth - rect.width - margin)); pos.y = Math.max(margin, Math.min(pos.y, innerHeight - rect.height - margin));
    Object.assign(node.style, {left: pos.x + 'px', top: pos.y + 'px', right: 'auto', bottom: 'auto', margin: '0'});
  }
  function reset(node) { ['left','top','right','bottom','margin'].forEach(prop => node.style.removeProperty(prop)); }
  function draggable(node, key) {
    let drag = null;
    node.addEventListener('pointerdown', event => {
      if (!event.target.closest('[data-drag-handle]') || event.target.closest('button') || event.button !== 0 || key === 'dialog' && innerWidth <= 760) return;
      const rect = node.getBoundingClientRect(); drag = {x:event.clientX,y:event.clientY,left:rect.left,top:rect.top}; node.setPointerCapture(event.pointerId); node.classList.add('radar-update-dragging'); event.preventDefault();
    });
    node.addEventListener('pointermove', event => { if (!drag) return; positions[key] = {x:drag.left + event.clientX-drag.x,y:drag.top+event.clientY-drag.y}; place(node,key); });
    ['pointerup','pointercancel','lostpointercapture'].forEach(name => node.addEventListener(name, () => { drag=null;node.classList.remove('radar-update-dragging'); }));
    node.addEventListener('keydown', event => {
      if (!event.target.matches('[data-drag-handle]') || key === 'dialog' && innerWidth <= 760 || !['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home'].includes(event.key)) return;
      event.preventDefault(); if (event.key === 'Home') {positions[key]=null;reset(node);return;}
      const rect=node.getBoundingClientRect(),step=event.shiftKey?40:12; positions[key]={x:rect.left+(event.key==='ArrowLeft'?-step:event.key==='ArrowRight'?step:0),y:rect.top+(event.key==='ArrowUp'?-step:event.key==='ArrowDown'?step:0)};place(node,key);
    });
  }
  handle(head,'移动更新结果窗口'); draggable(host,'notice'); draggable(dialog,'dialog'); window.addEventListener('resize',()=>{place(host,'notice');if(dialog.open)place(dialog,'dialog');});
  function empty(task) {return !active(task.status) && task.universe_id==='overseas_etf' && task.overseas && Array.isArray(task.overseas.items) && !task.overseas.items.length;}
  function needsMapping(items) {return Array.isArray(items) && items.some(row=>row.status==='unmapped') && items.every(row=>['fresh','unmapped'].includes(row.status));}
  function icon(task) { const node=element('span','radar-update-status-icon '+(active(task.status)?'loading':empty(task)?'empty':task.status==='completed'?'success':'warning'));node.setAttribute('aria-hidden','true');return node; }
  function summary(task) { return active(task.status) ? labels[Math.max(0,phases.indexOf(task.phase))] : task.status==='completed'?'ETF 与海外行情检查完成。':task.error||'部分数据未更新，请查看结果中的日期与原因。'; }
  function updatedTime(task) {
    if(!task.updated_at)return '';
    const date=new Date(task.updated_at);if(!Number.isFinite(date.getTime()))return '';
    return ' · 本次更新 '+new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(date)+'（北京时间）';
  }
  function render(task) {
    if (!task) return; host.hidden=false;host.className='radar-update-toast'+(minimized?' minimized':'');host.replaceChildren();
    const text=active(task.status)?'更新中':empty(task)?'暂无海外数据':task.status==='completed'?'更新完成':task.status==='failed'?'更新失败':'更新部分完成';
    if (minimized) {const grip=handle(element('span','radar-update-mini-grip','⠿'),'移动更新提示');const expand=button(text+' · 查看反馈',()=>{minimized=false;render(task);});expand.className='radar-update-mini-button';expand.prepend(icon(task));host.append(grip,expand);}
    else {
      const heading=handle(element('div','radar-update-toast-title'),'移动更新提示'),strong=element('strong','',text);strong.prepend(icon(task));heading.append(strong,button('收起',()=>{minimized=true;render(task);}));
      host.append(heading,element('p','radar-meta',(task.universe_name||task.universe_id)+updatedTime(task)),element('p','radar-update-toast-stage',task.poll_error||summary(task)));
      if(active(task.status)){const list=element('ol','radar-update-steps');labels.forEach((label,i)=>list.append(element('li',i<phases.indexOf(task.phase)?'done':i===phases.indexOf(task.phase)?'current':'',label)));host.append(list);}
      else {const actions=element('div','radar-update-actions');actions.append(button('查看结果',()=>open(task.universe_id)));if(task.status!=='completed' && !needsMapping(task.overseas && task.overseas.items))actions.append(button('重试',()=>start(task.universe_id)));host.append(actions);}
    }
    place(host,'notice');
  }
  function receive(task) {
    const old=tasks.get(task.universe_id); tasks.set(task.universe_id,task);selected=task.universe_id;render(task);onChange(task,!!old && active(old.status) && !active(task.status));
    if(dialog.open && context && context.universeId===task.universe_id) {
      renderResult();
      if(old && active(old.status) && !active(task.status)) refreshResultSnapshot(task);
    }
    if(active(task.status)) poll(task.universe_id,task.task_id||task.id);
  }
  function poll(universeId,id) {
    if(timers.has(id))return;
    timers.set(id,setTimeout(()=>{
      request('/api/v1/radar/refresh/'+encodeURIComponent(id)).then(task=>{timers.delete(id);receive(task);}).catch(error=>{
        timers.delete(id);const task=tasks.get(universeId);if(!task)return;
        if(error.status===404){receive({...task,status:'failed',poll_error:null,error:'更新任务已不可读取，请重试更新。'});return;}
        task.poll_error='状态暂不可读取：'+error.message+'；正在重新连接。';render(task);poll(universeId,id);
      });
    },1600));
  }
  function start(universeId) {
    const old=tasks.get(universeId);if(old && active(old.status)) {selected=universeId;minimized=false;render(old);return Promise.resolve(old);}
    const pending={universe_id:universeId,status:'queued',phase:'calendar'};tasks.set(universeId,pending);minimized=false;selected=universeId;render(pending);onChange(pending,false);
    return request('/api/v1/radar/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({universe_id:universeId})}).then(receive).catch(error=>receive({universe_id:universeId,status:'failed',error:'无法启动更新：'+error.message}));
  }
  function recover(universeId) {
    return request('/api/v1/radar/updates/latest?universe_id='+encodeURIComponent(universeId)).then(task=>{
      const old=tasks.get(universeId);
      if(task && (!old || !active(old.status) && (old.task_id||old.id)!==(task.task_id||task.id))) {minimized=true;receive(task);}
    }).catch(()=>{});
  }
  function percent(value) {return value==null||!Number.isFinite(Number(value))?'—':(Number(value)*100).toFixed(2)+'%';}
  function renderResult() {
    const task=tasks.get(context.universeId);body.replaceChildren(element('p','radar-meta',context.item?context.item.name+' · '+context.item.symbol:context.universeId));
    const etf=element('section','panel radar-detail-section');etf.append(element('h3','','ETF 更新结果'),element('p','radar-meta',context.snapshot?'ETF 行情截至 '+context.snapshot.as_of_date+' · 场内行情用于评分':'尚无完成的 ETF 快照，仍可手动更新。'));
    if(task){
      const etfResult=task.etf||{};
      const etfText=etfResult.error||({completed:'场内行情检查完成。',partial:'部分 ETF 数据未更新，保留可用快照。',failed:'ETF 检查失败，保留已有快照。',queued:'等待检查 ETF 行情。',running:'正在检查 ETF 行情。'}[etfResult.status])||(active(task.status)?'任务进行中，尚未获得 ETF 检查结果。':'尚未获得 ETF 检查结果。');
      etf.append(element('p','radar-note',etfText));
      body.append(element('p','radar-update-toast-stage',summary(task)));
    }
    const overseas=element('section','panel radar-detail-section');overseas.append(element('h3','','海外市场动态'),element('p','radar-meta','原币指数的已完成收盘变化 · 不计入 ETF 评分'));
    if(context.error)overseas.append(element('p','radar-note',context.error));
    else if(!context.data)overseas.append(element('p','radar-note','正在读取海外行情缓存…'));
    else {
      const rows=(context.data.items||[]).filter(row=>!context.item||row.symbol===context.item.symbol);
      if(!rows.length)overseas.append(element('p','radar-note',context.universeId==='overseas_etf'?'尚无海外行情缓存，请更新数据。':'首期海外动态仅覆盖海外市场 ETF 池。'));
      const table=element('table','radar-update-market-table');table.innerHTML='<thead><tr><th>ETF / 跟踪指数</th><th>最近一日</th><th>基准后累计变化</th><th>海外行情截至</th></tr></thead>';const tbody=element('tbody');
      rows.forEach(row=>{const tr=element('tr'),name=element('td','',row.name+' · '+row.symbol);name.append(element('span','radar-meta radar-update-block',(row.index_name||'跟踪关系待核实')+' · '+(row.currency||'—')));const cumulative=element('td','',percent(row.cumulative_change));cumulative.append(element('span','radar-meta radar-update-block',(row.base_date||'无起点')+' → '+(row.as_of_date||'暂无')));const date=element('td','',row.as_of_date||'暂无');date.append(element('span','radar-meta radar-update-block',(row.status==='stale'?'沿用缓存 · ':'')+(row.error||({fresh:'已完成收盘',stale:'沿用缓存',unavailable:'暂不可用',unmapped:'口径待核实'}[row.status]||row.status))),element('span','radar-meta radar-update-block',(row.source||'')+' · '+(row.timezone||'')));tr.append(name,element('td','',percent(row.daily_change)),cumulative,date);tbody.append(tr);});table.append(tbody);overseas.append(table);
      overseas.append(element('p','radar-meta','累计起点：ETF 实际最后收盘时刻之前最近一次已完成的指数收盘；缺少起点时不计算。不是 ETF 净值或预计开盘涨幅。'));
    }
    body.append(etf,overseas);footer.replaceChildren();
    const mappingOnly=context.data && needsMapping(context.data.items);
    if(mappingOnly)footer.append(element('span','radar-meta','口径待核实的项需核实配置，重试不会自动补齐映射。'));
    else if(!task||!active(task.status))footer.append(button(task && task.status==='completed'?'更新数据':'重试更新',()=>start(context.universeId)));
    footer.append(button('关闭窗口',()=>dialog.close()));if(dialog.open)place(dialog,'dialog');
  }
  function readOverseas() {
    const token=++generation, snapshot=context.snapshot, universeId=context.universeId;
    const url='/api/v1/radar/overseas?universe_id='+encodeURIComponent(universeId)+(snapshot&&snapshot.run_id?'&snapshot_run_id='+encodeURIComponent(snapshot.run_id):'');
    return request(url).then(data=>{if(token!==generation)return;context.data=data;context.error=null;renderResult();}).catch(error=>{if(token!==generation)return;context.error='海外缓存暂不可读取：'+error.message;renderResult();});
  }
  function refreshResultSnapshot(task) {
    const runId=task.snapshot_run_id||task.run_id;
    if(context.historical || !runId)return readOverseas();
    const opened=context,token=++generation;
    return request('/api/v1/radar/snapshots/'+encodeURIComponent(runId)).then(snapshot=>{
      if(token!==generation || context!==opened || context.universeId!==task.universe_id)return;
      context.snapshot=snapshot;context.data=null;context.error=null;renderResult();return readOverseas();
    }).catch(error=>{if(token!==generation || context!==opened)return;context.error='本次完成快照暂不可读取：'+error.message;renderResult();});
  }
  function open(universeId,item,snapshot,historical=false) {
    context={universeId,item,snapshot,historical,data:null,error:null};++generation;renderResult();if(!dialog.open)dialog.showModal();place(dialog,'dialog');
    if(snapshot)return readOverseas();
    const opened=context,token=generation;
    return request('/api/v1/radar/snapshots/latest?universe_id='+encodeURIComponent(universeId)).catch(()=>null).then(latest=>{if(context!==opened || token!==generation)return;context.snapshot=latest;renderResult();return readOverseas();});
  }
  return {start,recover,open,isActive:universeId=>{const task=tasks.get(universeId);return !!task && active(task.status);},show:()=>render(tasks.get(selected))};
}
