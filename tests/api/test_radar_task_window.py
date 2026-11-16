"""共用消息窗口拖拽与复位契约。"""
import json
from pathlib import Path

from tests.api.test_static import _DOM_STUB, _run_node


def test_task_window_shares_drag_handles_for_expanded_and_minimized(tmp_path):
    url = Path('src/api/static/js/radar-task-window.js').resolve().as_uri()
    script = _DOM_STUB + r"""
document.body=document.createElement('body');
Element.prototype.append=function(...nodes){nodes.forEach(n=>this.appendChild(n));};
Element.prototype.getBoundingClientRect=function(){return {left:Number.parseFloat(this.style.left)||20,top:Number.parseFloat(this.style.top)||120,width:300,height:200};};
Element.prototype.setPointerCapture=function(){};
Element.prototype.matches=function(s){return s==='[data-drag-handle]'&&this.dataset.dragHandle==='true';};
const originalClosest=Element.prototype.closest;
Element.prototype.closest=function(s){if(s==='[data-drag-handle]')return this.matches(s)?this:this.parentNode?.closest(s);if(s==='button')return this.tagName==='BUTTON'?this:null;return originalClosest.call(this,s);};
Object.defineProperty(Element.prototype,'lastChild',{get(){return this.children.at(-1);}});
const oldCreate=document.createElement.bind(document);
document.createElement=tag=>{const n=oldCreate(tag);n.style.removeProperty=k=>delete n.style[k];return n;};
globalThis.innerWidth=1280;globalThis.innerHeight=900;
const {createRadarTaskWindow}=await import(MODULE_URL);
const ui=createRadarTaskWindow('stockTest','股票结果');
const heading=ui.handle(document.createElement('div'),'移动消息');ui.host.append(heading);
await ui.host.dispatch('pointerdown',{target:heading,button:0,pointerId:1,clientX:30,clientY:130});
await ui.host.dispatch('pointermove',{clientX:130,clientY:230});
await ui.host.dispatch('pointerup',{});
if(ui.host.style.left!=='120px'||ui.host.style.top!=='220px')throw Error('拖拽未移动');
await ui.host.dispatch('keydown',{target:heading,key:'Home'});
if(ui.host.style.left!==undefined)throw Error('Home未复位');
const grip=ui.handle(document.createElement('span'),'移动收起消息');ui.host.replaceChildren(grip);
await ui.host.dispatch('keydown',{target:grip,key:'ArrowRight',shiftKey:true});
if(ui.host.style.left!=='60px')throw Error('收起后的手柄不能移动');
await ui.host.dispatch('pointerdown',{target:grip,button:0,pointerId:2,clientX:60,clientY:120});
await ui.host.dispatch('pointermove',{clientX:5000,clientY:5000});
if(ui.host.style.left!=='968px'||ui.host.style.top!=='688px')throw Error('拖出了视口');
"""
    _run_node(tmp_path, script.replace('MODULE_URL', json.dumps(url)))
