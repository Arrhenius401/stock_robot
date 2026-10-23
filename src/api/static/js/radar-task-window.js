// ETF与股票共用原消息窗口外壳：拖拽、收起手柄及结果窗口只维护一份。
const element = (tag, cls, text) => { const node=document.createElement(tag); node.className=cls||''; if(text!=null)node.textContent=text; return node; };
const button = (text, callback) => { const node=element('button','subs-btn',text); node.type='button'; node.onclick=callback; return node; };
export function createRadarTaskWindow(noticeId, resultTitle) {
  const positions={notice:null,dialog:null};
  const host = element('aside', 'radar-update-toast'); host.hidden = true; host.id = noticeId; host.setAttribute('role', 'status'); host.setAttribute('aria-live', 'polite');
  const dialog = element('dialog', 'radar-update-dialog'); dialog.setAttribute('aria-labelledby', noticeId + 'Title');
  const head = element('div', 'radar-update-dialog-head'); const title = element('h2', '', resultTitle); title.id = noticeId + 'Title';
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
  return {host,dialog,head,title,body,footer,handle,place};
}
