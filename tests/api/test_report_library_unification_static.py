"""报告库个股快照展示与只读原文切换回归。"""
import json

from tests.api.test_static import _DOM_STUB, _module_url, _run_node


def test_library_stock_snapshot_and_legacy_fallback(tmp_path):
    script = _DOM_STUB + r"""
const root=makeElement("reportLibraryContent");
const {api}=await import(__API__);
const {bus}=await import(__STATE__);
const {initReportLibrary}=await import(__LIBRARY__);
const markdown="# 原始报告\n\n## 五、多风格观察视角\n\n观察原文\n\n## 六、工具局限性 + 免责声明\n\n局限原文\n> （部分内容由 AI 生成）\n\n## 自定义附录\n\n完整附录";
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
if(!sections.some(s=>s.textContent.includes("多风格观察视角"))||!sections.at(-1).children[1].innerHTML.includes("局限原文"))throw new Error("存档丢失原说明内容");
if(sections.at(-1).id!=="library-report-disclaimer"||sections.at(-2).id!=="library-report-quality"||sections.at(-3).id!=="library-report-pending")throw new Error("免责声明应当最后展示");
if(sections.at(-1).children[0].textContent!=="免责声明")throw new Error("旧标题未归一");
if(sections.at(-1).children[1].innerHTML.includes("部分内容由 AI 生成"))throw new Error("旧库研报视图未移除尾注");
const buttons=()=>descendants(root).filter(n=>n.tagName==="BUTTON");
await buttons().find(n=>n.textContent==="Markdown 原文").click();
if(!byClass(root,"report-library-markdown")[0].innerHTML.includes("完整附录"))throw new Error("原文缺少未知附录");
await buttons().find(n=>n.textContent==="研报视图").click();
if(!root.textContent.includes("结构化名称"))throw new Error("无法返回研报视图");
await byClass(root,"report-library-back")[0].click();
snapshot=null;
await byClass(root,"report-library-open")[0].click();
if(root.textContent.includes("结构化名称")||!root.textContent.includes("自定义附录"))throw new Error("历史报告未回退完整章节");
const legacySections=byClass(root,"stock-report")[0].children;
if(legacySections.at(-1).children[0].textContent!=="免责声明")throw new Error("旧Markdown免责声明未移至最后");
if(legacySections.at(-1).children[1].innerHTML.includes("部分内容由 AI 生成"))throw new Error("历史Markdown研报视图未移除尾注");
"""
    for token, path in {
        "__API__": "src/api/static/js/api.js",
        "__STATE__": "src/api/static/js/state.js",
        "__LIBRARY__": "src/api/static/js/report-library.js",
    }.items():
        script = script.replace(token, json.dumps(_module_url(path)))
    _run_node(tmp_path, script)


def test_live_and_new_archived_disclaimers_are_last_and_unique(tmp_path):
    script = _DOM_STUB + r"""
const {renderStockReport,renderStockMarkdownReport}=await import(__RENDERER__);
for(const market of ["a-shares","hk"]){
  for(const options of [{},{sectionIdPrefix:"drawer-"},{readOnly:true,archiveMarkdown:"# 报告\n\n## 八、免责声明\n\n原有说明"}]){
    const article=renderStockReport({market,disclaimer:["原有说明","市场有风险，投资需谨慎。","（部分内容由 AI 生成）"]},options);
    const cards=article.children;
    if(cards.at(-1).children[0].textContent!=="免责声明")throw new Error("实时或存档报告末项不是免责声明");
    if(cards.filter(c=>c.children[0]?.textContent==="免责声明").length!==1)throw new Error("免责声明重复");
    if(!descendants(cards.at(-1)).some(node=>(node.textContent+node.innerHTML).includes("原有说明")))throw new Error("说明内容丢失");
    if(descendants(cards.at(-1)).some(node=>(node.textContent+node.innerHTML).includes("部分内容由 AI 生成")))throw new Error("结构化免责声明尾注未删除");
  }
}
const legacy=renderStockMarkdownReport("# 报告\n\n## 六、工具局限性 + 免责声明\n\n旧说明\n\n## 七、待验证事项\n\n待核实\n\n## 八、数据覆盖与时效\n\n数据依据");
if(legacy.children.at(-1).children[0].textContent!=="免责声明")throw new Error("历史Markdown未统一标题或排序");
if(!legacy.children.at(-1).children[1].innerHTML.includes("旧说明"))throw new Error("历史说明丢失");
""".replace("__RENDERER__", json.dumps(_module_url("src/api/static/js/report-renderer.js")))
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


def test_perspectives_are_merged_into_ai_card_without_duplicate_archive_pointer(tmp_path):
    script = _DOM_STUB + r'''
const {renderStockReport,renderStockMarkdownReport}=await import(__RENDERER__);
const bulk="## 1.维度分项解读\n\n财务原文\n\n## 3.多风格观察视角\n\n价值观察原文\n\n## 4.综合评级\n\n综合评价原文";
const archived="# 报告\n\n## 四、AI 中性解读 + 风险汇总\n\n"+bulk+"\n\n---\n\n## 五、多风格观察视角\n\n> 多风格观察视角内容已包含在上方「AI 中性解读」的第 3 段中。\n\n## 六、待验证事项\n\n待核实";
for(const status of ['ok','failed','disabled','insufficient']){
 const article=renderStockReport({commentary:status==='ok'?bulk:'',commentary_status:status},
  {archiveMarkdown:status==='ok'?archived:'# 报告\n\n## 五、多风格观察视角\n\n> 内容已包含在上方'});
 const cards=article.children, ai=cards.find(c=>c.id==='report-commentary');
 if(ai.children[0].textContent!=='AI 综合解读')throw new Error('AI标题未统一');
 if(cards.some(c=>/多风格观察/.test(c.children[0]?.textContent||'')))throw new Error('仍有独立多风格卡片');
 const html=descendants(article).map(n=>n.innerHTML).join('\n');
 if(html.includes('已包含在上方'))throw new Error('仍有空指引');
 if(status==='ok'&&(html.split('价值观察原文').length!==2||!html.includes('综合评价原文')))throw new Error('多风格重复或综合评价丢失');
 if(status==='ok'&&(!html.includes('<h3>3.多风格观察视角</h3>')||html.includes('<h2>3.多风格观察视角</h2>')))throw new Error('网页内部标题未降级');
}
const legacy=renderStockMarkdownReport(archived), ai=legacy.children.find(c=>c.children[0]?.textContent==='AI 综合解读');
if(!ai)throw new Error('旧Markdown没有合并AI章节');
const html=descendants(ai).map(n=>n.innerHTML).join('\n');
if(!html.includes('价值观察原文')||!html.includes('综合评价原文')||html.includes('已包含在上方'))throw new Error('旧Markdown内容未合并');
if(legacy.children.some(c=>/多风格观察/.test(c.children[0]?.textContent||'')))throw new Error('旧Markdown仍有独立多风格卡片');
const mixed=renderStockReport({commentary_status:'failed'}, {archiveMarkdown:'# 报告\n\n## 五、多风格观察视角\n\n独立历史观察原文\n\n> 其他内容已包含在上方'});
const mixedAi=mixed.children.find(c=>c.id==='report-commentary');
const mixedHtml=descendants(mixedAi).map(n=>n.innerHTML).join('\n');
if(!mixedHtml.includes('独立历史观察原文')||mixedHtml.includes('已包含在上方'))throw new Error('清理空指引丢失同节历史正文');
'''.replace("__RENDERER__", json.dumps(_module_url("src/api/static/js/report-renderer.js")))
    _run_node(tmp_path, script)
