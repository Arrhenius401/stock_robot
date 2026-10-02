"""策略指数目录与收益口径的前端契约测试。"""
import json
from pathlib import Path

from tests.api.test_static import _DOM_STUB, _module_url, _run_node


def test_index_directory_filter_select_retry_and_idempotence(tmp_path):
    script = _DOM_STUB + r"""
const input = makeElement("indexInput", "input");
makeElement("indexBtn", "button");
makeElement("indexDirectoryResults");
const search = makeElement("indexDirectorySearch", "input");
makeElement("indexContent");
const { api } = await import(__API__);
let calls = 0;
api.indices = async () => { calls++; return { indices: [
 {symbol:"930740",name:"沪深300红利低波动",index_style:"strategy",aliases:["红利低波"]},
 {symbol:"980092",name:"国证自由现金流",index_style:"strategy",aliases:["现金流"]},
], etfs:[{symbol:"515300",name:"嘉实红利低波ETF",index_symbol:"930740"}] }; };
const view = await import(__INDEX__);
await view.initIndexView();
await view.initIndexView();
if (calls !== 1 || input.listeners.keydown.length !== 1) throw new Error("重复初始化重复请求或绑定");
search.value = "现金流"; await search.dispatch("input");
const box = document.getElementById("indexDirectoryResults");
if (!box.textContent.includes("980092") || box.textContent.includes("930740")) throw new Error("目录过滤错误");
await byClass(box,"index-directory-item")[0].click();
if(input.value !== "980092") throw new Error("点击目录未填入代码");
search.value = "无此指数"; await search.dispatch("input");
if(!box.textContent.includes("没有匹配")) throw new Error("缺少空目录说明");
api.indices = async () => { throw new Error("目录暂不可用"); };
await view.loadIndexDirectory(true);
if(!box.textContent.includes("目录暂不可用") || !byClass(box,"btn-refresh").length) throw new Error("失败缺少重试");
api.indices = async () => ({indices:[{symbol:"930740",name:"红利低波",index_style:"strategy"}],etfs:[]});
search.value=""; await byClass(box,"btn-refresh")[0].click();
if(!box.textContent.includes("930740")) throw new Error("目录重试失败");
"""
    script = script.replace("__API__", json.dumps(_module_url("src/api/static/js/api.js")))
    script = script.replace("__INDEX__", json.dumps(_module_url("src/api/static/js/indexview.js")))
    _run_node(tmp_path, script)


def test_strategy_report_units_missing_and_legacy(tmp_path):
    script = _DOM_STUB + r"""
makeElement("indexInput", "input"); makeElement("indexContent");
const {api} = await import(__API__);
api.index = async () => ({reports:[{code:"930740",name:"沪深300红利低波动",
requested_instrument:{symbol:"515300",name:"嘉实红利低波ETF",index_symbol:"930740"},
visible_sections:["strategy","performance"],
section_strategy:{status:"success",summary:"专项分析",metrics:{as_of:"2026-09-30",top10_weight_pct:40,
industry_weights:[{industry:"银行",weight_pct:25}],financial_years:[2023,2024],
indicators:[{key:"fcf_yield",label:"自由现金流收益率",value:null,unit:"%",coverage_pct:15,scope:"已覆盖样本估算",reason:"缺少资本开支"}]}},
section_performance:{status:"success",metrics:{return_pct:12.123456789,return_basis:"价格指数，不含分红",etf_return_pct:13,etf_return_basis:"前复权价格，含分红调整"}}}]});
const {openIndex} = await import(__INDEX__);
await openIndex("515300");
const box=document.getElementById("indexContent");
for(const text of ["515300","跟踪指数","40%","银行","25.00%","15.00%","缺少资本开支","12.12%","价格指数，不含分红","前复权价格，含分红调整"]) {
 if(!box.textContent.includes(text)) throw new Error("报告缺少 "+text+"："+box.textContent);
}
if(box.textContent.includes("[object Object]") || box.textContent.includes("fcf yield")) throw new Error("裸对象或内部键外泄");
api.index=async()=>({reports:[{code:"000300",name:"沪深300",section_technical:{summary:"趋势正常",metrics:{latest_close:4000}}}]});
await openIndex("000300");
if(!descendants(box).some(node => node.innerHTML.includes("趋势正常")) || box.textContent.includes("专项分析")) throw new Error("旧报告不兼容");
"""
    script = script.replace("__API__", json.dumps(_module_url("src/api/static/js/api.js")))
    script = script.replace("__INDEX__", json.dumps(_module_url("src/api/static/js/indexview.js")))
    _run_node(tmp_path, script)



def test_stock_entry_routes_known_etf_without_blocking_other_symbols(tmp_path):
    script = _DOM_STUB + r"""
makeElement("stockInput", "input"); makeElement("indexInput", "input");
makeElement("reportContent"); makeElement("indexContent");
const {api} = await import(__API__);
let analyzed=[]; let indexed=[];
api.indices=async()=>({indices:[],etfs:[{symbol:"515300",name:"嘉实红利低波ETF",index_symbol:"930740"}]});
api.analyze=async(symbol)=>{analyzed.push(symbol); return {symbol,name:"普通股票"};};
api.index=async(symbols)=>{indexed.push(symbols); return {reports:[]};};
const {openReport}=await import(__REPORT__);
await openReport("515300");
if(analyzed.length || indexed[0]?.[0] !== "515300" || document.getElementById("indexInput").value !== "515300") throw new Error("ETF没有从个股入口转指数");
await openReport("600519");
await openReport("159999");
if(analyzed.join(",")!=="600519,159999") throw new Error("未知股票被目录识别阻断");
const {loadIndexDirectory}=await import(__INDEX__);
makeElement("indexDirectoryResults");
api.indices=async()=>{throw new Error("目录读取失败");};
await loadIndexDirectory(true);
await openReport("159998");
if(analyzed.at(-1)!=="159998") throw new Error("目录失败阻断原入口");
"""
    script = script.replace("__API__", json.dumps(_module_url("src/api/static/js/api.js")))
    script = script.replace("__REPORT__", json.dumps(_module_url("src/api/static/js/report.js")))
    script = script.replace("__INDEX__", json.dumps(_module_url("src/api/static/js/indexview.js") + "?v=20261002-strategy-indices-3"))
    _run_node(tmp_path, script)


def test_strategy_date_and_etf_metric_labels_follow_backend_contract():
    static = Path(__file__).parents[2] / "src" / "api" / "static"
    renderer = (static / "js" / "report-renderer.js").read_text(encoding="utf-8")
    for key in ("weight_as_of", "market_cap_dates", "benchmark_aligned_index_return_pct",
                "etf_start_date", "etf_end_date", "etf_sample_count", "etf_unavailable_reason"):
        assert key + ":" in renderer
    assert 'as_of: "采样日期"' in renderer
    indexview = (static / "js" / "indexview.js").read_text(encoding="utf-8")
    assert 'sector: "行业指数"' in indexview


def test_ambiguous_index_clears_loading_placeholder(tmp_path):
    """歧义输入失败后必须清除加载占位，并显示输入错误。"""
    script = _DOM_STUB + r"""
const wrap = makeElement("indexWrap"); wrap.className = "entry-input-wrap";
wrap.appendChild(makeElement("indexInput", "input")); makeElement("indexContent");
const {api} = await import(__API__);
api.index = async () => { const e = new Error("匹配到多个指数"); e.status = 422; throw e; };
const {openIndex} = await import(__INDEX__);
await openIndex("自由现金流");
if (document.getElementById("indexContent").children.length) throw new Error("歧义错误留下加载占位");
if (!wrap.querySelector(".entry-error").textContent.includes("匹配到多个指数")) throw new Error("歧义错误未显示");
"""
    script = script.replace("__API__", json.dumps(_module_url("src/api/static/js/api.js")))
    script = script.replace("__INDEX__", json.dumps(_module_url("src/api/static/js/indexview.js")))
    _run_node(tmp_path, script)
