"""报告库个股快照展示与只读原文切换回归。"""
import json

from tests.api.test_static import _DOM_STUB, _module_url, _run_node


def test_library_stock_snapshot_and_legacy_fallback(tmp_path):
    script = _DOM_STUB + r"""
const root=makeElement("reportLibraryContent");
const {api}=await import(__API__);
const {bus}=await import(__STATE__);
const {initReportLibrary}=await import(__LIBRARY__);
const markdown="# 原始报告\n\n## 五、多风格观察视角\n\n观察原文\n\n## 六、工具局限性 + 免责声明\n\n局限原文\n\n## 自定义附录\n\n完整附录";
api.listReports=async()=>({total:1,reports:[{id:"stock",type:"stock",title:"快照报告"}]});
let snapshot={symbol:"000001",name:"结构化名称",commentary_status:"failed",commentary_retry_id:"不得生效",
  score:{final:5}, dimensions:{financial:{status:"ok",score:5,score_detail:"实际评分依据"}}};
api.getReport=async()=>({report:{id:"stock",type:"stock",title:"快照报告"},markdown,stock_payload:snapshot});
api.downloadReportUrl=()=>"/download";
initReportLibrary();
const event=new Event("view-change");Object.defineProperty(event,"detail",{value:{view:"report-library"}});bus.dispatchEvent(event);
await new Promise(resolve=>setTimeout(resolve,0));
await byClass(root,"report-library-open")[0].click();
if(!root.textContent.includes("结构化名称") || !root.textContent.includes("实际评分依据"))throw new Error("未默认呈现共享研报及评分依据");
if(byClass(root,"report-library-metrics").length)throw new Error("个股不得显示回测指标");
if(byClass(root,"report-retry-commentary").length||byClass(root,"report-configure-commentary").length)throw new Error("存档不得提供即时操作");
const sections=byClass(root,"stock-report")[0].children;
if(!sections.some(s=>s.textContent.includes("多风格观察视角"))||!sections.some(s=>s.textContent.includes("工具局限性")))throw new Error("存档丢失原五六章节");
if(sections.at(-1).id!=="library-report-quality"||sections.at(-2).id!=="library-report-pending")throw new Error("补充原文章节破坏末尾卡片顺序");
const buttons=()=>descendants(root).filter(n=>n.tagName==="BUTTON");
await buttons().find(n=>n.textContent==="Markdown 原文").click();
if(!byClass(root,"report-library-markdown")[0].innerHTML.includes("完整附录"))throw new Error("原文缺少未知附录");
await buttons().find(n=>n.textContent==="研报视图").click();
if(!root.textContent.includes("结构化名称"))throw new Error("无法返回研报视图");
await byClass(root,"report-library-back")[0].click();
snapshot=null;
await byClass(root,"report-library-open")[0].click();
if(root.textContent.includes("结构化名称")||!root.textContent.includes("自定义附录"))throw new Error("历史报告未回退完整章节");
"""
    for token, path in {
        "__API__": "src/api/static/js/api.js",
        "__STATE__": "src/api/static/js/state.js",
        "__LIBRARY__": "src/api/static/js/report-library.js",
    }.items():
        script = script.replace(token, json.dumps(_module_url(path)))
    _run_node(tmp_path, script)


def test_archived_renderer_ignores_action_callbacks(tmp_path):
    renderer_url = json.dumps(_module_url("src/api/static/js/report-renderer.js"))
    script = _DOM_STUB + r"""
const {renderStockReport}=await import(__RENDERER__);
for(const status of ["failed","config"]){
  const article=renderStockReport({commentary_status:status},{readOnly:true,onRetryCommentary:()=>{},onConfigure:()=>{}});
  if(byClass(article,"report-retry-commentary").length||byClass(article,"report-configure-commentary").length)throw new Error("只读渲染器不得暴露业务操作");
}
""".replace("__RENDERER__", renderer_url)
    _run_node(tmp_path, script)
