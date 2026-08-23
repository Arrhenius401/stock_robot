"""静态 Web UI 冒烟测试 — 页面、共享研报渲染与抽屉行为。"""
import json
import subprocess
import textwrap
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from api.app import create_app

_DOM_STUB = r"""
class ClassList {
  constructor(owner) { this.owner = owner; }
  values() { return new Set(this.owner.className.split(/\s+/).filter(Boolean)); }
  write(values) { this.owner.className = [...values].join(" "); }
  add(...names) { const values = this.values(); names.forEach((name) => values.add(name)); this.write(values); }
  remove(...names) { const values = this.values(); names.forEach((name) => values.delete(name)); this.write(values); }
  contains(name) { return this.values().has(name); }
  toggle(name, force) {
    const enabled = force === undefined ? !this.contains(name) : Boolean(force);
    if (enabled) this.add(name); else this.remove(name);
    return enabled;
  }
}

class Element {
  constructor(tagName, ownerDocument) {
    this.tagName = tagName.toUpperCase();
    this.ownerDocument = ownerDocument;
    this.children = [];
    this.parentNode = null;
    this.className = "";
    this.classList = new ClassList(this);
    this.dataset = {};
    this.style = {};
    this.hidden = false;
    this.attributes = {};
    this.listeners = {};
    this._textContent = "";
    this._innerHTML = "";
    this._id = "";
  }
  set id(value) {
    this._id = String(value);
    if (this.ownerDocument) this.ownerDocument.ids.set(this._id, this);
  }
  get id() { return this._id; }
  set textContent(value) {
    this._textContent = value == null ? "" : String(value);
    this._innerHTML = "";
    this.children = [];
  }
  get textContent() {
    return this._textContent + this.children.map((child) => child.textContent).join("");
  }
  set innerHTML(value) {
    this._innerHTML = String(value);
    this._textContent = "";
    this.children = [];
  }
  get innerHTML() { return this._innerHTML; }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  replaceChildren(...children) {
    this.children.forEach((child) => { child.parentNode = null; });
    this.children = [];
    children.forEach((child) => this.appendChild(child));
  }
  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
    this.parentNode = null;
  }
  setAttribute(name, value) {
    if (name === "id") this.id = value;
    else this.attributes[name] = String(value);
  }
  getAttribute(name) { return name === "id" ? this.id : this.attributes[name] ?? null; }
  addEventListener(type, handler) {
    (this.listeners[type] = this.listeners[type] || []).push(handler);
  }
  async click() {
    for (const handler of this.listeners.click || []) {
      await handler({ preventDefault() {}, stopPropagation() {} });
    }
  }
  focus() { this.ownerDocument.activeElement = this; }
  scrollIntoView(options) { this.scrolledWith = options; }
  querySelectorAll(selector) {
    const all = [];
    const visit = (node) => {
      for (const child of node.children) {
        if (selector === "section[id]" && child.tagName === "SECTION" && child.id) all.push(child);
        if (selector.startsWith(".") && child.classList.contains(selector.slice(1))) all.push(child);
        visit(child);
      }
    };
    visit(this);
    return all;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

class DocumentStub {
  constructor() { this.ids = new Map(); this.listeners = {}; this.activeElement = null; }
  createElement(tagName) { return new Element(tagName, this); }
  getElementById(id) { return this.ids.get(id) || null; }
  addEventListener(type, handler) {
    (this.listeners[type] = this.listeners[type] || []).push(handler);
  }
  async dispatch(type, event) {
    for (const handler of this.listeners[type] || []) await handler(event);
  }
  querySelectorAll() { return []; }
}

globalThis.document = new DocumentStub();
globalThis.window = globalThis;

function makeElement(id, tagName = "div") {
  const node = document.createElement(tagName);
  node.id = id;
  return node;
}

function descendants(node) {
  return [node, ...node.children.flatMap(descendants)];
}

function byClass(node, className) {
  return descendants(node).filter((item) => item.classList.contains(className));
}
"""


def _module_url(path: str) -> str:
    return Path(path).resolve().as_uri()


def _run_node(tmp_path: Path, script: str) -> None:
    script_path = tmp_path / "static-contract.mjs"
    script_path.write_text(textwrap.dedent(script), encoding="utf-8")
    result = subprocess.run(
        ["node", str(script_path)], capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


@pytest.fixture
async def client():
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestStaticUI:
    @pytest.mark.asyncio
    async def test_index_html_served(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert "Stock Robot" in resp.text

    @pytest.mark.asyncio
    async def test_index_contains_report_drawer_structure(self, client):
        resp = await client.get("/")

        assert 'id="appLayout"' in resp.text
        assert ('<aside id="reportDrawer" aria-label="完整研报" '
                'aria-hidden="true" hidden>') in resp.text
        assert 'id="reportDrawerClose"' in resp.text
        assert 'id="reportDrawerRefresh"' in resp.text
        assert 'id="reportDrawerError"' in resp.text
        assert 'id="reportDrawerContent"' in resp.text
        assert 'id="reportDrawerNav"' in resp.text

    @pytest.mark.asyncio
    async def test_js_modules_served(self, client):
        for path in ("/js/app.js", "/js/api.js", "/js/state.js", "/js/markdown.js",
                     "/js/chat.js", "/js/sessions.js", "/js/components.js",
                     "/js/report.js", "/js/indexview.js", "/js/report-renderer.js",
                     "/js/report-drawer.js"):
            resp = await client.get(path)
            assert resp.status_code == 200, path

    @pytest.mark.asyncio
    async def test_app_initializes_report_drawer(self, client):
        resp = await client.get("/js/app.js")

        assert 'import { initReportDrawer } from "./report-drawer.js";' in resp.text
        assert "initReportDrawer();" in resp.text

    def test_report_renderer_handles_variants_and_unsafe_text(self, tmp_path):
        renderer_url = json.dumps(_module_url("src/api/static/js/report-renderer.js"))
        script = _DOM_STUB + r"""
const {
  normalizeArtifactReport, renderStockReport, renderReportSummary,
} = await import(__RENDERER_URL__);

const report = {
  code: "000001",
  name: '<img src=x onerror="boom">',
  overview: { industry: "银行", latest_close: 12.3, change_pct: -1.2 },
  score: { final: 7.6, risk_deduction: 1 },
  score_rows: [{ label: "财务", score: 8, weight: "30%" }],
  dimensions: {
    financial: {
      status: "ok",
      score: 8,
      summary: "**稳健** <script>alert(1)</script>",
      metrics: { nested: { value: 3 } },
      risk_flags: ["集中度偏高", "集中度偏高"],
    },
    sentiment: { summary: "消息平稳", risk_flags: ["舆情波动"] },
  },
  comments: ["第一段结论。\n\n第二段", "补充说明"],
  signal: { label: "持有", action: "控制仓位" },
  generated_at: "2026-08-24T08:30:00+08:00",
};

const article = renderStockReport(report);
if (article.tagName !== "ARTICLE" || !article.classList.contains("stock-report")) {
  throw new Error("renderStockReport 必须返回 stock-report article");
}
const sectionIds = article.querySelectorAll("section[id]").map((section) => section.id);
const expectedIds = [
  "report-summary", "report-score", "report-financial", "report-technical",
  "report-valuation", "report-industry", "report-sentiment", "report-risks",
  "report-commentary",
];
for (const id of expectedIds) {
  if (!sectionIds.includes(id)) throw new Error(`缺少稳定章节 ${id}`);
}
if (!article.textContent.includes('<img src=x onerror="boom">')) {
  throw new Error("标的名称应以文本展示");
}
const htmlValues = descendants(article).map((node) => node.innerHTML).filter(Boolean);
if (htmlValues.some((value) => value.includes("<script>"))) {
  throw new Error("自然语言字段未经 markdown 转义");
}
if (!htmlValues.some((value) => value.includes("&lt;script&gt;"))) {
  throw new Error("维度摘要未经过 markdown 渲染");
}
if (article.textContent.includes("undefined") || article.textContent.includes("[object Object]")) {
  throw new Error("缺失字段或对象指标不可泄漏 JS 默认字符串");
}
if (!article.textContent.includes('{\n  "value": 3\n}')) {
  throw new Error("对象指标应使用可读 JSON");
}
if (byClass(article, "report-risk-item").length !== 2) {
  throw new Error("风险应跨维度去重聚合");
}

const artifact = {
  artifact_id: "artifact-1",
  symbol: "000001",
  created_at: 1787531400,
  payload: report,
};
const summary = renderReportSummary(artifact);
if (summary.dataset.artifactId !== "artifact-1") throw new Error("摘要卡缺少成果 ID");
if (!summary.textContent.includes("2 项风险")) throw new Error("摘要卡风险计数错误");
const conclusion = byClass(summary, "report-summary-conclusion")[0];
if (!conclusion || !conclusion.innerHTML.includes("第一段结论")) {
  throw new Error("摘要卡应优先使用 commentary/comments 第一段");
}
const openButton = byClass(summary, "report-summary-open")[0];
if (!openButton || (openButton.listeners.click || []).length !== 0) {
  throw new Error("摘要卡按钮不得内置业务监听器");
}
const partialArtifact = {
  artifact_id: "artifact-2",
  symbol: "600000",
  generated_at: "2026-08-24T10:00:00+08:00",
  payload: { symbol: null, code: undefined, name: "浦发银行" },
};
const normalized = normalizeArtifactReport(partialArtifact);
if (normalized.symbol !== "600000" || normalized.name !== "浦发银行"
    || normalized.generated_at !== "2026-08-24T10:00:00+08:00") {
  throw new Error("payload 的 null/undefined 不得覆盖成果外层有效报告字段");
}
const partialSummary = renderReportSummary(partialArtifact);
if (!partialSummary.textContent.includes("600000")) {
  throw new Error("payload 缺少代码时应兼容成果外层 symbol");
}
""".replace("__RENDERER_URL__", renderer_url)
        _run_node(tmp_path, script)

    def test_report_api_and_state_contract(self, tmp_path):
        api_url = json.dumps(_module_url("src/api/static/js/api.js"))
        state_url = json.dumps(_module_url("src/api/static/js/state.js"))
        script = r"""
const calls = [];
globalThis.fetch = async (url, options = {}) => {
  calls.push({ url, options });
  return {
    ok: true,
    status: 200,
    json: async () => calls.length === 1
      ? { session_id: "s1", title: "新标题", title_source: "manual" }
      : { artifact: { artifact_id: "a1", session_id: "s1" } },
  };
};
const { api } = await import(__API_URL__);
const { store } = await import(__STATE_URL__);
await api.renameSession("s1", "新标题");
await api.getArtifact("s1", "a1");

if (calls[0].url !== "/api/v1/sessions/s1" || calls[0].options.method !== "PATCH") {
  throw new Error("renameSession 请求错误");
}
if (JSON.parse(calls[0].options.body).title !== "新标题") throw new Error("重命名标题错误");
if (calls[1].url !== "/api/v1/sessions/s1/artifacts/a1") {
  throw new Error("getArtifact 请求错误");
}
if (!store.sessionArtifacts || Array.isArray(store.sessionArtifacts)
    || Object.keys(store.sessionArtifacts).length !== 0) {
  throw new Error("sessionArtifacts 初始状态错误");
}
if (store.currentArtifact !== null || store.reportDrawerOpen !== false) {
  throw new Error("抽屉状态初值错误");
}
""".replace("__API_URL__", api_url).replace("__STATE_URL__", state_url)
        _run_node(tmp_path, script)

    def test_report_drawer_guards_focus_and_request_races(self, tmp_path):
        drawer_url = json.dumps(_module_url("src/api/static/js/report-drawer.js"))
        api_url = json.dumps(_module_url("src/api/static/js/api.js"))
        state_url = json.dumps(_module_url("src/api/static/js/state.js"))
        script = _DOM_STUB + r"""
const layout = makeElement("appLayout");
const drawer = makeElement("reportDrawer", "aside");
drawer.hidden = true;
drawer.setAttribute("aria-hidden", "true");
const closeButton = makeElement("reportDrawerClose", "button");
const refreshButton = makeElement("reportDrawerRefresh", "button");
const errorBox = makeElement("reportDrawerError");
errorBox.hidden = true;
const nav = makeElement("reportDrawerNav", "nav");
const content = makeElement("reportDrawerContent");
const trigger = makeElement("artifactTrigger", "button");

const { api } = await import(__API_URL__);
const { store } = await import(__STATE_URL__);
const { openReportDrawer, closeReportDrawer, initReportDrawer } = await import(__DRAWER_URL__);
initReportDrawer();
initReportDrawer();
if ((closeButton.listeners.click || []).length !== 1
    || (refreshButton.listeners.click || []).length !== 1
    || (document.listeners.keydown || []).length !== 1) {
  throw new Error("initReportDrawer 重复绑定监听器");
}

const payloadB = {
  symbol: null, name: "B 报告", commentary: "B 结论",
  dimensions: { financial: { risk_flags: [] } },
};
const artifactB = {
  artifact_id: "b", session_id: "s1", symbol: "000002", payload: payloadB,
};
await openReportDrawer(artifactB, trigger);
if (drawer.hidden || drawer.getAttribute("aria-hidden") !== "false"
    || !layout.classList.contains("drawer-open") || !store.reportDrawerOpen) {
  throw new Error("抽屉未正确展开");
}
if (!content.textContent.includes("B 报告") || !content.textContent.includes("000002")
    || nav.children.length === 0) {
  throw new Error("抽屉未使用共享归一化报告或动态章节导航");
}
await nav.children[0].click();
const firstSection = content.children[0].querySelectorAll("section[id]")[0];
if (firstSection.scrolledWith?.behavior !== "smooth" || firstSection.scrolledWith?.block !== "start") {
  throw new Error("章节导航未平滑滚动");
}
closeReportDrawer();
if (!drawer.hidden || drawer.getAttribute("aria-hidden") !== "true"
    || layout.classList.contains("drawer-open") || store.reportDrawerOpen
    || document.activeElement !== trigger) {
  throw new Error("抽屉关闭状态或焦点归还错误");
}

let resolveA;
api.getArtifact = () => new Promise((resolve) => { resolveA = resolve; });
const pendingA = openReportDrawer({ artifact_id: "a", session_id: "s1", symbol: "000001" });
await openReportDrawer(artifactB);
resolveA({ artifact: {
  artifact_id: "a", session_id: "s1",
  payload: { symbol: "000001", name: "迟到的 A 报告" },
} });
await pendingA;
if (!content.textContent.includes("B 报告") || content.textContent.includes("迟到的 A 报告")) {
  throw new Error("迟到加载响应覆盖了当前报告");
}

const oldReport = content.children[0];
let rejectRefresh;
api.analyze = () => new Promise((resolve, reject) => { rejectRefresh = reject; });
const failedRefresh = refreshButton.click();
if (content.children[0] !== oldReport) throw new Error("刷新期间不应清空旧报告");
rejectRefresh(new Error("刷新失败"));
await failedRefresh;
if (content.children[0] !== oldReport || errorBox.hidden
    || !errorBox.textContent.includes("刷新失败")) {
  throw new Error("刷新失败应保留旧内容并仅显示错误");
}

let resolveRefresh;
api.analyze = () => new Promise((resolve) => { resolveRefresh = resolve; });
const lateRefresh = refreshButton.click();
closeReportDrawer();
resolveRefresh({ symbol: "000002", name: "关闭后迟到刷新" });
await lateRefresh;
if (store.currentArtifact?.payload?.name === "关闭后迟到刷新") {
  throw new Error("关闭后的迟到刷新更新了当前成果");
}

await openReportDrawer(artifactB, trigger);
await document.dispatch("keydown", { key: "Escape", preventDefault() {} });
if (!drawer.hidden || document.activeElement !== trigger) throw new Error("Escape 未关闭抽屉");
""".replace("__DRAWER_URL__", drawer_url)
        script = script.replace("__API_URL__", api_url).replace("__STATE_URL__", state_url)
        _run_node(tmp_path, script)

    @pytest.mark.asyncio
    async def test_css_served(self, client):
        resp = await client.get("/css/app.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers.get("content-type", "")
