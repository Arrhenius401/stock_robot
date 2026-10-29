"""研报数据依据、无结论与 AI 操作的网页契约回归。"""
import json

from tests.api.test_static import _DOM_STUB, _module_url, _run_node


def test_quality_sections_and_commentary_actions(tmp_path):
    renderer_url = json.dumps(_module_url("src/api/static/js/report-renderer.js"))
    script = _DOM_STUB + r"""
const { renderStockReport } = await import(__RENDERER_URL__);
let retries = 0;
const report = {
  name: "样本", score: {final: null},
  signal: {label: "买入", position: "80%"},
  dimensions: {financial: {status: "partial", score: null, metrics: {roe: 3}}},
  commentary_status: "failed",
  pending_verifications: ["复核现金流"],
  data_quality: {status: "partial", coverage: 50, signal_allowed: false,
    dimensions: [{key:"financial",title:"财务",status:"partial", fields:[
      {label:"数据截止",value:"2026-06-30"},
      {label:"样本范围",value:"2期财报"},
      {label:"数据来源",value:"实际来源"},
      {label:"缺失原因",value:"缺少同期对比"},
    ]}]},
};
const article = renderStockReport(report, {onRetryCommentary: () => { retries += 1; }});
const ids = article.children.map(node => node.id);
if (ids.at(-1) !== "report-disclaimer" || ids.at(-2) !== "report-quality" || ids.at(-3) !== "report-commentary" || ids.includes("report-pending")) throw new Error("免责声明应置于数据覆盖与时效之后，不显示待验证事项");
const quality = article.children.find(node => node.id === "report-quality");
if (!["2期财报", "实际来源", "2026-06-30", "缺少同期对比"].every(value=>quality.textContent.includes(value))) throw new Error("精简汇总必须保留样本、来源、截止和缺失原因");
if (descendants(quality).some(node => node.tagName === "BUTTON")) throw new Error("不得添加采集重试");
const allHtml = descendants(article).map(node => node.innerHTML).join("");
if (allHtml.includes("买入") || allHtml.includes("80%")) throw new Error("不完整数据不得生成信号建议");
if (byClass(article, "score-num")[0].textContent !== "暂无评分") throw new Error("暂无评分不可附加 /10");
const financial = article.children.find(node => node.id === "report-financial");
if (!financial.children[0].classList.contains("report-dimension-heading")
    || !byClass(financial.children[0], "dim-score").length) throw new Error("标题和模块评分必须同行");
await byClass(article, "report-retry-commentary")[0].click();
if (retries !== 1) throw new Error("AI 重试未回调");
const unavailable = renderStockReport({...report, commentary_status:"insufficient"});
if (byClass(unavailable,"report-retry-commentary").length) throw new Error("无依据不能重试 AI");
const legacy = renderStockReport({name:"旧报告", dimensions:{}});
if (!legacy.textContent.includes("未记录") || legacy.textContent.includes("实际来源")) throw new Error("旧报告不得捏造依据");
const config = renderStockReport({commentary_status:"config"}, {onConfigure:()=>{retries += 1;}});
await byClass(config,"report-configure-commentary")[0].click();
if (retries !== 2) throw new Error("配置操作未回调");
""".replace("__RENDERER_URL__", renderer_url)
    _run_node(tmp_path, script)


def test_commentary_retry_updates_original_session_after_switch(tmp_path):
    drawer_url = json.dumps(_module_url("src/api/static/js/report-drawer.js"))
    api_url = json.dumps(_module_url("src/api/static/js/api.js"))
    state_url = json.dumps(_module_url("src/api/static/js/state.js"))
    script = _DOM_STUB + r"""
makeElement("appLayout");
const drawer = makeElement("reportDrawer", "aside");
drawer.hidden = true;
makeElement("reportDrawerClose", "button");
makeElement("reportDrawerError");
makeElement("reportDrawerNav", "nav");
const content = makeElement("reportDrawerContent");
const {api} = await import(__API_URL__);
const {store} = await import(__STATE_URL__);
const {openReportDrawer, closeReportDrawer} = await import(__DRAWER_URL__);
const original = {artifact_id:"a", session_id:"s1", payload:{
  symbol:"000001", name:"原会话报告", commentary_status:"failed", commentary_retry_id:"token-a",
}};
const other = {artifact_id:"b", session_id:"s2", payload:{symbol:"000002",name:"另一个会话报告"}};
store.currentSessionId = "s1";
await openReportDrawer(original);
let finish;
api.retryCommentary = (token, target) => {
  if(token !== "token-a" || target.session_id !== "s1" || target.artifact_id !== "a") throw new Error("重试未绑定原会话成果");
  return new Promise(resolve => {finish=resolve;});
};
const pending = byClass(content,"report-retry-commentary")[0].click();
if (!byClass(content,"report-retry-commentary")[0].disabled) throw new Error("重试未禁用重复操作");
closeReportDrawer();
store.currentSessionId = "s2";
await openReportDrawer(other);
finish({symbol:"000001",name:"原会话报告",commentary_status:"ok",commentary:"原会话解读成功"});
await pending;
if (!content.textContent.includes("另一个会话报告") || store.currentArtifact !== other) throw new Error("迟到重试覆盖当前会话");
if (store.sessionArtifacts.s1[0].payload.commentary_status !== "ok") throw new Error("迟到成功未更新原会话缓存");
closeReportDrawer();
store.currentSessionId = "s1";
await openReportDrawer(store.sessionArtifacts.s1[0]);
if (byClass(content,"report-retry-commentary").length
    || !descendants(content).some(node=>node.innerHTML.includes("原会话解读成功"))) throw new Error("返回原会话仍展示失败报告");

// 同一成果换了新分析批次时，旧解读不能覆盖新报告。
original.payload = {symbol:"000001", name:"旧批次", commentary_status:"failed", commentary_retry_id:"token-a"};
await openReportDrawer(original);
const oldPending = byClass(content,"report-retry-commentary")[0].click();
original.payload = {symbol:"000001", name:"新批次", commentary_status:"failed", commentary_retry_id:"token-new"};
await openReportDrawer(original);
finish({symbol:"000001", name:"旧批次迟到", commentary_status:"ok", commentary:"旧解读"});
await oldPending;
if (original.payload.name !== "新批次" || !content.textContent.includes("新批次")) throw new Error("旧批次解读覆盖新报告");
""".replace("__DRAWER_URL__", drawer_url)
    script = script.replace("__API_URL__", api_url).replace("__STATE_URL__", state_url)
    _run_node(tmp_path, script)
