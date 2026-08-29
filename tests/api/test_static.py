"""静态 Web UI 冒烟测试 — 页面、共享研报渲染与抽屉行为。"""
import json
import shutil
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
    this.value = "";
    this.disabled = false;
    this.inert = false;
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
  removeAttribute(name) { delete this.attributes[name]; }
  getAttribute(name) { return name === "id" ? this.id : this.attributes[name] ?? null; }
  addEventListener(type, handler) {
    (this.listeners[type] = this.listeners[type] || []).push(handler);
  }
  dispatchEvent(event) {
    const type = typeof event === "string" ? event : event.type;
    const payload = (event && typeof event === "object")
      ? { ...event, type, target: event.target || this, currentTarget: this }
      : { type, target: this, currentTarget: this };
    for (const handler of (this.listeners[type] || []).slice()) handler(payload);
    return true;
  }
  async click() {
    for (const handler of this.listeners.click || []) {
      await handler({ preventDefault() {}, stopPropagation() {} });
    }
  }
  async dispatch(type, event = {}) {
    event.preventDefault ||= () => { event.defaultPrevented = true; };
    event.stopPropagation ||= () => {};
    for (const handler of this.listeners[type] || []) await handler(event);
  }
  async blur() {
    if (this.ownerDocument.activeElement === this) this.ownerDocument.activeElement = null;
    await this.dispatch("blur");
  }
  focus() {
    if (this.closest("[inert]")) return;
    this.ownerDocument.activeElement = this;
  }
  contains(node) {
    if (node === this) return true;
    return this.children.some((child) => child.contains(node));
  }
  closest(selector) {
    let node = this;
    while (node) {
      if (selector.split(",").some((part) => {
        const value = part.trim();
        if (value.startsWith(".")) return node.classList.contains(value.slice(1));
        if (value === "[hidden]") return node.hidden;
        if (value === "[inert]") return node.inert || node.attributes.inert !== undefined;
        return false;
      })) return node;
      node = node.parentNode;
    }
    return null;
  }
  scrollIntoView(options) { this.scrolledWith = options; }
  querySelectorAll(selector) {
    const all = [];
    const visit = (node) => {
      for (const child of node.children) {
        if (selector === "section[id]" && child.tagName === "SECTION" && child.id) all.push(child);
        if (selector.startsWith(".") && child.classList.contains(selector.slice(1))) all.push(child);
        const attr = selector.match(/^\[data-([a-z-]+)=([a-z0-9_-]+)\]$/);
        if (attr && child.dataset[attr[1]] === attr[2]) all.push(child);
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
  createElementNS(_namespace, tagName) { return new Element(tagName, this); }
  getElementById(id) { return this.ids.get(id) || null; }
  addEventListener(type, handler) {
    (this.listeners[type] = this.listeners[type] || []).push(handler);
  }
  async dispatch(type, event) {
    for (const handler of this.listeners[type] || []) await handler(event);
  }
  querySelectorAll(selector) {
    return [...this.ids.values()].filter((node) => selector.startsWith(".")
      && node.classList.contains(selector.slice(1)));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}

globalThis.document = new DocumentStub();
globalThis.window = globalThis;
globalThis.addEventListener = () => {};

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
        assert ('<aside id="reportDrawer" role="dialog" aria-label="完整研报" '
                'aria-modal="false" aria-hidden="true" hidden>') in resp.text
        assert 'id="reportDrawerClose"' in resp.text
        assert 'id="reportDrawerRefresh"' in resp.text
        assert 'id="reportDrawerError"' in resp.text
        assert 'id="reportDrawerContent"' in resp.text
        assert 'id="reportDrawerNav"' in resp.text

    @pytest.mark.asyncio
    async def test_workspace_shell_has_accessible_landmarks(self, client):
        """工作台外壳提供移动端可访问入口与覆盖层。"""
        html = (await client.get("/")).text

        assert 'id="appLayout"' in html
        assert 'aria-label="主要导航"' in html
        assert 'id="mobileNavToggle"' in html
        assert 'id="workspaceBackdrop"' in html

    @pytest.mark.asyncio
    async def test_index_contains_report_library_view(self, client):
        html = (await client.get("/")).text

        assert 'data-view="report-library"' in html
        assert "<span>报告库</span>" in html
        assert 'id="view-report-library"' in html
        assert 'id="reportLibraryContent"' in html
        assert 'id="globalStockSearch"' not in html

    @pytest.mark.asyncio
    async def test_report_library_modules_served(self, client):
        app_js = (await client.get("/js/app.js")).text
        api_js = (await client.get("/js/api.js")).text

        assert 'import { initReportLibrary } from "./report-library.js";' in app_js
        assert "initReportLibrary();" in app_js
        assert "listReports(" in api_js
        assert "getReport(" in api_js
        assert "downloadReportUrl(" in api_js
        assert (await client.get("/js/report-library.js")).status_code == 200

    @pytest.mark.asyncio
    async def test_chat_uses_textarea_input(self, client):
        resp = await client.get("/")

        assert '<textarea id="chatInput"' in resp.text
        assert '<input id="chatInput"' not in resp.text

    @pytest.mark.asyncio
    async def test_js_modules_served(self, client):
        for path in ("/js/app.js", "/js/api.js", "/js/state.js", "/js/markdown.js",
                     "/js/chat.js", "/js/sessions.js", "/js/components.js",
                     "/js/report.js", "/js/indexview.js", "/js/report-renderer.js",
                     "/js/report-drawer.js", "/js/workspace-modal.js",
                     "/js/settings.js", "/js/report-library.js"):
            resp = await client.get(path)
            assert resp.status_code == 200, path

    @pytest.mark.asyncio
    async def test_app_initializes_report_drawer(self, client):
        resp = await client.get("/js/app.js")

        assert 'import { initReportDrawer } from "./report-drawer.js";' in resp.text
        assert "initReportDrawer();" in resp.text

    def test_markdown_renderer_escapes_untrusted_input_and_formats_safe_blocks(self):
        """防止块级解析放行脚本、危险链接或破坏代码块原样显示。"""
        node = shutil.which("node")
        if node is None:
            pytest.skip("Node.js 不可用")

        markdown_url = json.dumps(_module_url("src/api/static/js/markdown.js"))
        script = r"""
const { renderMarkdown } = await import(__MARKDOWN_URL__);
const html = renderMarkdown(`# 标题

1. 第一项
2. 第二项

> 风险提示
> 关注现金流

普通段落中的 *强调* 与 [安全链接](https://example.com)。

<script>alert(1)</script>
[坏链接](javascript:alert(1))

\`\`\`
**代码原样** [不应解析](https://example.com)
\`\`\``);

if (!html.includes("<h1>标题</h1>")) throw new Error("缺少标题");
if (!html.includes("<ol><li>第一项</li><li>第二项</li></ol>")) {
  throw new Error("缺少有序列表");
}
if (!html.includes("<blockquote>风险提示<br>关注现金流</blockquote>")) {
  throw new Error("缺少连续引用");
}
if (!html.includes("<em>强调</em>")) throw new Error("缺少强调");
if (!html.includes('href="https://example.com"')) throw new Error("安全链接未保留");
if (html.includes("<script>") || !html.includes("&lt;script&gt;alert(1)&lt;/script&gt;")) {
  throw new Error("未转义脚本");
}
if (html.includes("javascript:")) throw new Error("放行危险链接");
const code = html.match(/<pre><code>([\s\S]*?)<\/code><\/pre>/)?.[1] || "";
if (!code.includes("**代码原样** [不应解析](https://example.com)")
    || code.includes("<strong>") || code.includes("<a ")) {
  throw new Error("代码块不应进行行内格式化");
}
""".replace("__MARKDOWN_URL__", markdown_url)
        result = subprocess.run(
            [node, "--input-type=module", "--eval", script],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

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
const drawerArticle = renderStockReport(report, { sectionIdPrefix: "drawer-" });
const pageArticle = renderStockReport(report, { sectionIdPrefix: "page-" });
const allSectionIds = [
  ...drawerArticle.querySelectorAll("section[id]"),
  ...pageArticle.querySelectorAll("section[id]"),
].map((section) => section.id);
if (new Set(allSectionIds).size !== allSectionIds.length
    || !allSectionIds.includes("drawer-report-summary")
    || !allSectionIds.includes("page-report-summary")) {
  throw new Error("主报告与抽屉并存时章节 ID 必须唯一");
}
""".replace("__RENDERER_URL__", renderer_url)
        _run_node(tmp_path, script)

    def test_entry_error_follows_moved_and_global_search_inputs(self, tmp_path):
        """422 必须在实际触发输入附近给出可见提示。"""
        components_url = json.dumps(_module_url("src/api/static/js/components.js"))
        script = _DOM_STUB + r"""
const globalForm = makeElement("globalForm", "form");
globalForm.className = "global-stock-search";
const globalWrap = makeElement("globalWrap");
globalWrap.className = "entry-input-wrap";
const globalInput = makeElement("globalStockSearch", "input");
globalWrap.appendChild(globalInput);
globalForm.appendChild(globalWrap);
const analysisEntry = makeElement("analysisEntry");
analysisEntry.className = "analysis-entry";
const analysisWrap = makeElement("analysisWrap");
analysisWrap.className = "entry-input-wrap";
const stockInput = makeElement("stockInput", "input");
analysisWrap.appendChild(stockInput);
analysisEntry.appendChild(analysisWrap);

const { showEntryError, clearEntryError } = await import(__COMPONENTS_URL__);
showEntryError("globalStockSearch", "股票代码无效");
showEntryError("stockInput", "股票代码无效");
if (!globalWrap.querySelector(".entry-error")
    || !analysisWrap.querySelector(".entry-error")
    || globalInput.getAttribute("aria-describedby") !== "globalStockSearch-error") {
  throw new Error("迁移后的输入没有显示 422 错误");
}
clearEntryError("globalStockSearch");
if (globalWrap.querySelector(".entry-error")
    || globalInput.getAttribute("aria-describedby") !== null) {
  throw new Error("清理全局搜索错误后仍残留提示");
}
""".replace("__COMPONENTS_URL__", components_url)
        _run_node(tmp_path, script)

    def test_report_library_renders_list_detail_and_back_button(self, tmp_path):
        node = shutil.which("node")
        if node is None:
            pytest.skip("Node.js 不可用")

        library_url = json.dumps(_module_url("src/api/static/js/report-library.js"))
        api_url = json.dumps(_module_url("src/api/static/js/api.js"))
        state_url = json.dumps(_module_url("src/api/static/js/state.js"))
        script = _DOM_STUB + r"""
const root = makeElement("reportLibraryContent");
makeElement("currentViewTitle", "h1");
const { api } = await import(__API_URL__);
const { bus } = await import(__STATE_URL__);
const { initReportLibrary } = await import(__LIBRARY_URL__);

api.listReports = async () => ({ total: 1, reports: [{
  id: "abc", type: "backtest", title: "000001 技术策略回测报告",
  symbol: "000001", path: "backtests/report_technical/000001/2026-08/run-1/report.md",
  generated_at: 1787994863, strategy_id: "report_technical",
  strategy_version: "v1", start_date: "2025-01-02", end_date: "2026-08-28",
  has_equity_curve: true, has_trades: true, legacy: false,
}] });
api.getReport = async () => ({
  report: { id: "abc", type: "backtest", title: "000001 技术策略回测报告",
    symbol: "000001", path: "backtests/report_technical/000001/2026-08/run-1/report.md",
    generated_at: 1787994863, strategy_id: "report_technical" },
  markdown: "# 回测报告\n\n正文",
  summary: { metrics: { total_return: 0.1842, max_drawdown: -0.0786, sharpe: 1.21 },
    trades_count: 26 },
  equity_curve: { columns: ["净值日期", "策略净值", "基准净值"],
    rows: [{ "净值日期": "2026-01-01", "策略净值": "1.0", "基准净值": "1.0" },
           { "净值日期": "2026-01-02", "策略净值": "1.1", "基准净值": "1.02" }] },
  trades: { columns: ["trade_date", "side", "price", "return_pct"],
    rows: [{ trade_date: "2026-04-26", side: "sell", price: "11.31", return_pct: "0.0854" }] },
  missing_artifacts: [],
});
api.downloadReportUrl = () => "/api/v1/reports/abc/download";

initReportLibrary();
const event = new Event("view-change");
Object.defineProperty(event, "detail", { value: { view: "report-library" } });
bus.dispatchEvent(event);
await new Promise((resolve) => setTimeout(resolve, 0));

if (!root.textContent.includes("已保存报告") || !root.textContent.includes("打开详情")) {
  throw new Error("未渲染报告库列表页");
}
await byClass(root, "report-library-open")[0].click();
await new Promise((resolve) => setTimeout(resolve, 0));
if (!root.textContent.includes("累计收益") || !root.textContent.includes("+18.42%")
    || !root.textContent.includes("净值曲线") || !root.textContent.includes("交易明细")) {
  throw new Error("未渲染报告详情页的摘要与回测页签");
}
const buttons = descendants(root).filter((item) => item.tagName === "BUTTON");
await buttons.find((item) => item.textContent.includes("净值曲线")).click();
if (!byClass(root, "report-library-curve")[0]) {
  throw new Error("未渲染净值曲线");
}
await descendants(root).filter((item) => item.tagName === "BUTTON")
  .find((item) => item.textContent.includes("交易明细")).click();
if (!root.textContent.includes("trade_date") || !root.textContent.includes("sell")) {
  throw new Error("未渲染交易明细表格");
}
const back = byClass(root, "report-library-back")[0];
if (!back || back.getAttribute("aria-label") !== "返回报告库" || back.textContent.trim()) {
  throw new Error("返回按钮必须是仅含可访问名称的 Chevron 图标按钮");
}
await back.click();
if (!root.textContent.includes("已保存报告")) {
  throw new Error("返回按钮未回到列表页");
}
""".replace("__LIBRARY_URL__", library_url)
        script = script.replace("__API_URL__", api_url).replace("__STATE_URL__", state_url)
        _run_node(tmp_path, script)

    def test_mobile_modal_isolates_background_focus_and_restores_it(self, tmp_path):
        """覆盖层开启后背景控件不可被 Tab 聚焦，关闭后应恢复。"""
        modal_url = json.dumps(_module_url("src/api/static/js/workspace-modal.js"))
        script = _DOM_STUB + r"""
let narrow = true;
window.matchMedia = () => ({ matches: narrow });
document.body = makeElement("body", "body");
const topbar = makeElement("topbar");
topbar.className = "topbar";
const topbarButton = makeElement("topbarButton", "button");
topbar.appendChild(topbarButton);
const sidebar = makeElement("sidebar", "aside");
const navButton = makeElement("navButton", "button");
sidebar.appendChild(navButton);
const main = makeElement("workspaceMain", "main");
const mainButton = makeElement("mainButton", "button");
main.appendChild(mainButton);
const drawer = makeElement("reportDrawer", "aside");
const drawerButton = makeElement("drawerButton", "button");
drawer.appendChild(drawerButton);
const closeButton = makeElement("reportDrawerClose", "button");
drawer.appendChild(closeButton);
const newSessionButton = makeElement("newSessionBtn", "button");
sidebar.appendChild(newSessionButton);
const navToggle = makeElement("mobileNavToggle", "button");
const layout = makeElement("appLayout");

const { openWorkspaceModal, closeWorkspaceModal, syncWorkspaceModal } = await import(__MODAL_URL__);
openWorkspaceModal("drawer");
if (!topbar.inert || !sidebar.inert || !main.inert || drawer.inert
    || drawer.getAttribute("aria-modal") !== "true") {
  throw new Error("报告抽屉未隔离背景焦点");
}
drawerButton.focus();
topbarButton.focus();
if (document.activeElement !== drawerButton) {
  throw new Error("背景控件仍能从抽屉夺取焦点");
}
closeWorkspaceModal("drawer");
if (topbar.inert || sidebar.inert || main.inert
    || drawer.getAttribute("aria-modal") !== "false") {
  throw new Error("关闭抽屉后没有恢复背景焦点");
}
topbarButton.focus();
if (document.activeElement !== topbarButton) {
  throw new Error("关闭抽屉后背景控件不能重新聚焦");
}
openWorkspaceModal("navigation");
if (!topbar.inert || !main.inert || sidebar.inert || !drawer.inert) {
  throw new Error("移动侧栏未仅保留自身焦点范围");
}
closeWorkspaceModal("navigation");

layout.classList.add("drawer-open");
narrow = false;
syncWorkspaceModal();
narrow = true;
syncWorkspaceModal();
if (!topbar.inert || !sidebar.inert || !main.inert
    || drawer.getAttribute("aria-modal") !== "true"
    || document.activeElement !== closeButton) {
  throw new Error("桌面已开抽屉缩窄后未重新建立模态隔离");
}
narrow = false;
syncWorkspaceModal();
if (topbar.inert || sidebar.inert || main.inert
    || drawer.getAttribute("aria-modal") !== "false") {
  throw new Error("抽屉变宽后仍错误隔离桌面背景");
}
layout.classList.remove("drawer-open");
document.body.classList.add("workspace-nav-open");
narrow = true;
syncWorkspaceModal();
if (!topbar.inert || !main.inert || sidebar.inert || !drawer.inert
    || document.activeElement !== newSessionButton) {
  throw new Error("侧栏宽窄往返后未恢复焦点隔离");
}

document.body.classList.remove("workspace-nav-open");
closeWorkspaceModal("navigation");
narrow = true;
document.body.classList.add("workspace-nav-open");
syncWorkspaceModal();
narrow = false;
syncWorkspaceModal();
layout.classList.add("drawer-open");
drawer.hidden = false;
narrow = true;
syncWorkspaceModal();
layout.classList.remove("drawer-open");
drawer.hidden = true;
closeWorkspaceModal("drawer");
if (!topbar.inert || !main.inert || sidebar.inert || !drawer.inert
    || document.activeElement !== newSessionButton
    || navToggle.getAttribute("aria-expanded") !== "true") {
  throw new Error("关闭抽屉后未重新激活保留的移动侧栏");
}
mainButton.focus();
if (document.activeElement !== newSessionButton) {
  throw new Error("关闭抽屉后背景报告触发器重新进入焦点顺序");
}
""".replace("__MODAL_URL__", modal_url)
        _run_node(tmp_path, script)

    def test_switch_view_announces_programmatic_view_changes(self, tmp_path):
        """会话选择、校验回退等程序化切换也要能同步页面标题。"""
        state_url = json.dumps(_module_url("src/api/static/js/state.js"))
        script = _DOM_STUB + r"""
const chat = makeElement("view-chat");
chat.className = "view active";
const report = makeElement("view-report");
report.className = "view";
const chatNav = makeElement("chatNav", "button");
chatNav.className = "nav-item on";
chatNav.dataset.view = "chat";
const reportNav = makeElement("reportNav", "button");
reportNav.className = "nav-item";
reportNav.dataset.view = "report";

const { bus, switchView } = await import(__STATE_URL__);
let received = null;
bus.addEventListener("view-change", (event) => { received = event.detail.view; });
switchView("report");
if (received !== "report" || !report.classList.contains("active")
    || chat.classList.contains("active")) {
  throw new Error("程序化视图切换没有通知标题层");
}
""".replace("__STATE_URL__", state_url)
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
if (document.activeElement !== closeButton) {
  throw new Error("打开抽屉后键盘焦点未进入抽屉");
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

await openReportDrawer(artifactB, trigger);
await openReportDrawer(artifactB);
closeReportDrawer();
if (document.activeElement !== trigger) {
  throw new Error("无 trigger 重开抽屉时丢失了原始焦点来源");
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

    def test_chat_stream_artifacts_stay_in_their_session(self, tmp_path):
        chat_url = json.dumps(_module_url("src/api/static/js/chat.js"))
        api_url = json.dumps(_module_url("src/api/static/js/api.js"))
        state_url = json.dumps(_module_url("src/api/static/js/state.js"))
        drawer_url = json.dumps(_module_url("src/api/static/js/report-drawer.js"))
        script = _DOM_STUB + r"""
const chatScroll = makeElement("chatScroll");
const chatInput = makeElement("chatInput", "textarea");
const sendButton = makeElement("sendBtn", "button");
makeElement("appLayout");
const drawer = makeElement("reportDrawer", "aside");
drawer.hidden = true;
drawer.setAttribute("aria-hidden", "true");
makeElement("reportDrawerError");
makeElement("reportDrawerNav", "nav");
makeElement("reportDrawerContent");

const { api } = await import(__API_URL__);
const { store, bus } = await import(__STATE_URL__);
const { sendMessage } = await import(__CHAT_URL__);
const { closeReportDrawer } = await import(__DRAWER_URL__);
store.currentSessionId = "s1";
let titleEvents = 0;
bus.addEventListener("session-title", () => { titleEvents += 1; });
const firstArtifact = {
  artifact_id: null, session_id: "s1", symbol: "000001",
  payload: { symbol: "000001", name: "平安银行", commentary: "结论" },
};
api.chatStream = async (message, sessionId, handlers) => {
  if (message !== "分析平安银行" || sessionId !== "s1") throw new Error("发送参数错误");
  handlers.session_title({ session_id: "s1", title: "平安银行研究" });
  handlers.artifact({ artifact: firstArtifact, persisted: false });
  handlers.done({});
};
await sendMessage("分析平安银行");
if (store.sessionDetails.s1.title !== "平安银行研究" || titleEvents !== 1) {
  throw new Error("session_title 未更新当前会话元数据并通知列表");
}
if (store.sessionArtifacts.s1.length !== 1 || store.sessionArtifacts.s1[0].persisted !== false) {
  throw new Error("artifact 未按会话缓存持久化状态");
}
const cards = byClass(chatScroll, "report-summary-card");
if (cards.length !== 1 || !cards[0].textContent.includes("未保存到历史记录")) {
  throw new Error("未持久化成果缺少非阻塞卡片提示");
}
const openButton = byClass(cards[0], "report-summary-open")[0];
await openButton.click();
if (!store.reportDrawerOpen || store.currentArtifact?.symbol !== "000001") {
  throw new Error("报告摘要按钮未打开对应成果");
}
closeReportDrawer();
if (document.activeElement !== openButton) throw new Error("抽屉焦点未归还摘要触发按钮");

const cardCount = byClass(chatScroll, "report-summary-card").length;
store.currentSessionId = "s1";
api.chatStream = async (_message, _sessionId, handlers) => {
  store.currentSessionId = "s2";
  handlers.artifact({ artifact: {
    artifact_id: "late", session_id: "s1", symbol: "600000",
    payload: { symbol: "600000", name: "迟到报告" },
  }, persisted: true });
  handlers.done({});
};
await sendMessage("继续分析");
if (!store.sessionArtifacts.s1.some((item) => item.artifact_id === "late")) {
  throw new Error("切换会话后迟到成果未缓存回原会话");
}
if (byClass(chatScroll, "report-summary-card").length !== cardCount) {
  throw new Error("旧会话迟到成果渲染进了新会话");
}
""".replace("__CHAT_URL__", chat_url).replace("__API_URL__", api_url)
        script = script.replace("__STATE_URL__", state_url).replace("__DRAWER_URL__", drawer_url)
        _run_node(tmp_path, script)

    def test_chat_history_restores_artifacts_and_empty_suggestions(self, tmp_path):
        chat_url = json.dumps(_module_url("src/api/static/js/chat.js"))
        state_url = json.dumps(_module_url("src/api/static/js/state.js"))
        script = _DOM_STUB + r"""
const chatScroll = makeElement("chatScroll");
const chatInput = makeElement("chatInput", "textarea");
const { store } = await import(__STATE_URL__);
const { renderMessageHistory, handleChatInputKeydown } = await import(__CHAT_URL__);
store.currentSessionId = "history";
const linked = {
  artifact_id: "linked", session_id: "history", message_id: "tool-1",
  symbol: "000001", payload: { symbol: "000001", name: "关联成果" },
};
const linkedAgain = {
  artifact_id: "linked-again", session_id: "history", message_id: "tool-3",
  symbol: "000001", payload: { symbol: "000001", name: "同代码第二份成果" },
};
const legacy = {
  artifact_id: "legacy", session_id: "history", message_id: null,
  symbol: "600000", payload: { symbol: "600000", name: "旧成果" },
};
renderMessageHistory([
  { role: "user", content: "分析", id: "question-1" },
  { role: "assistant", content: "结论", id: "answer-1" },
  { role: "tool", content: "[analyze_stock] success: 000001 成功", message_id: "tool-1" },
  { role: "tool", content: "[analyze_stock] error: 000001 失败", message_id: "tool-2" },
  { role: "tool", content: "[analyze_stock] success: 000001 再次成功", message_id: "tool-3" },
], [linked, linkedAgain, legacy]);
const cards = byClass(chatScroll, "report-summary-card");
if (cards.length !== 3) throw new Error("历史成果卡恢复数量错误");
const orphanGroups = byClass(chatScroll, "artifact-history-orphans");
if (orphanGroups.length !== 1 || !orphanGroups[0].textContent.includes("研究成果")
    || !orphanGroups[0].textContent.includes("旧成果")) {
  throw new Error("无 message_id 的旧成果未放入结尾研究成果区");
}
const internalCards = byClass(chatScroll, "card-title")
  .filter((node) => ["工具结果", "执行计划"].includes(node.textContent));
if (internalCards.length !== 0 || chatScroll.textContent.includes("000001 失败")) {
  throw new Error("历史会话泄露了工具结果或执行计划气泡");
}

renderMessageHistory([], []);
const suggestions = byClass(chatScroll, "research-suggestion");
    if (suggestions.length !== 4) throw new Error("空会话应展示四个研究建议");
await suggestions[0].click();
if (!chatInput.value || byClass(chatScroll, "msg").length !== 0) {
  throw new Error("研究建议应只填充输入框而不发送");
}

let sends = 0;
const send = () => { sends += 1; };
const ime = { key: "Enter", isComposing: true, preventDefault() { this.blocked = true; } };
handleChatInputKeydown(ime, send);
const shifted = { key: "Enter", shiftKey: true, preventDefault() { this.blocked = true; } };
handleChatInputKeydown(shifted, send);
const plain = { key: "Enter", preventDefault() { this.blocked = true; } };
handleChatInputKeydown(plain, send);
if (sends !== 1 || ime.blocked || shifted.blocked || !plain.blocked) {
  throw new Error("IME 或 Enter/Shift+Enter 键盘行为错误");
}
""".replace("__CHAT_URL__", chat_url).replace("__STATE_URL__", state_url)
        _run_node(tmp_path, script)

    def test_sessions_restore_artifacts_and_support_inline_rename(self, tmp_path):
        sessions_url = json.dumps(_module_url("src/api/static/js/sessions.js"))
        api_url = json.dumps(_module_url("src/api/static/js/api.js"))
        state_url = json.dumps(_module_url("src/api/static/js/state.js"))
        script = _DOM_STUB + r"""
const sessionList = makeElement("sessionList");
makeElement("chatScroll");
makeElement("appLayout");
const drawer = makeElement("reportDrawer", "aside");
drawer.hidden = false;
drawer.setAttribute("aria-hidden", "false");
makeElement("reportDrawerError");
makeElement("reportDrawerNav", "nav");
makeElement("reportDrawerContent");

const { api } = await import(__API_URL__);
const { store } = await import(__STATE_URL__);
const { renderSessionList } = await import(__SESSIONS_URL__);
const now = Date.now() / 1000;
const sessions = [{
  session_id: "s1", title: "分析 600519 贵州茅台", updated_at: now - 90,
  message_count: 2,
}];
api.listSessions = async () => ({ sessions });
api.getMessages = async () => ({
  messages: [{ role: "assistant", content: "历史结论", id: "a1" }],
  artifacts: [{ artifact_id: "r1", session_id: "s1", message_id: "a1",
    symbol: "600519", payload: { symbol: "600519", name: "贵州茅台" } }],
});
api.renameSession = async (id, title) => ({ session_id: id, title, title_source: "manual" });
store.currentSessionId = "old";
store.reportDrawerOpen = true;
renderSessionList(sessions);
const primary = byClass(sessionList, "sess-main")[0];
if (!primary || primary.tagName !== "BUTTON" || !primary.textContent.includes("600519")
    || !primary.textContent.includes("分钟前")) {
  throw new Error("会话主区域、标的标签或相对时间错误");
}
await primary.click();
if (store.currentSessionId !== "s1" || store.reportDrawerOpen
    || store.sessionMessages.s1.length !== 1 || store.sessionArtifacts.s1.length !== 1
    || byClass(document.getElementById("chatScroll"), "report-summary-card").length !== 1) {
  throw new Error("切换会话未关闭抽屉并同时恢复消息与成果");
}

let menu = byClass(sessionList, "sess-menu-toggle")[0];
if (!menu || menu.tagName !== "BUTTON") throw new Error("省略号菜单必须使用 button");
await menu.click();
let actions = byClass(sessionList, "sess-menu-action");
if (actions.length !== 2 || actions.map((item) => item.textContent).join(",") !== "重命名,删除") {
  throw new Error("会话菜单只能包含重命名和删除");
}
await actions[0].click();
let input = byClass(sessionList, "sess-rename-input")[0];
input.value = "茅台估值跟踪";
await input.dispatch("keydown", { key: "Enter" });
if (store.sessionDetails.s1.title !== "茅台估值跟踪"
    || !sessionList.textContent.includes("茅台估值跟踪")) {
  throw new Error("Enter 未保存内联重命名");
}

menu = byClass(sessionList, "sess-menu-toggle")[0];
await menu.click();
actions = byClass(sessionList, "sess-menu-action");
await actions[0].click();
input = byClass(sessionList, "sess-rename-input")[0];
input.value = "不应保存";
await input.dispatch("keydown", { key: "Escape" });
if (!sessionList.textContent.includes("茅台估值跟踪")
    || sessionList.textContent.includes("不应保存")) {
  throw new Error("Escape 未取消重命名");
}

menu = byClass(sessionList, "sess-menu-toggle")[0];
await menu.click();
actions = byClass(sessionList, "sess-menu-action");
await actions[0].click();
input = byClass(sessionList, "sess-rename-input")[0];
input.value = "失焦不保存";
await input.blur();
if (sessionList.textContent.includes("失焦不保存")) throw new Error("失焦未取消重命名");

api.renameSession = async () => { throw new Error("重命名失败"); };
menu = byClass(sessionList, "sess-menu-toggle")[0];
await menu.click();
actions = byClass(sessionList, "sess-menu-action");
await actions[0].click();
input = byClass(sessionList, "sess-rename-input")[0];
input.value = "失败标题";
await input.dispatch("keydown", { key: "Enter" });
const renameError = byClass(sessionList, "sess-rename-error")[0];
if (!renameError?.textContent.includes("重命名失败")
    || input.value !== "茅台估值跟踪") {
  throw new Error("重命名失败未局部提示并恢复旧值");
}
""".replace("__SESSIONS_URL__", sessions_url).replace("__API_URL__", api_url)
        script = script.replace("__STATE_URL__", state_url)
        _run_node(tmp_path, script)

    def test_session_list_and_detail_reject_stale_responses(self, tmp_path):
        sessions_url = json.dumps(_module_url("src/api/static/js/sessions.js"))
        chat_url = json.dumps(_module_url("src/api/static/js/chat.js"))
        api_url = json.dumps(_module_url("src/api/static/js/api.js"))
        state_url = json.dumps(_module_url("src/api/static/js/state.js"))
        script = _DOM_STUB + r"""
const sessionList = makeElement("sessionList");
makeElement("chatScroll");
makeElement("chatInput", "textarea");
makeElement("sendBtn", "button");
makeElement("appLayout");
const drawer = makeElement("reportDrawer", "aside");
drawer.hidden = true;
drawer.setAttribute("aria-hidden", "true");
makeElement("reportDrawerError");
makeElement("reportDrawerNav", "nav");
makeElement("reportDrawerContent");

const { api } = await import(__API_URL__);
const { store } = await import(__STATE_URL__);
const { sendMessage } = await import(__CHAT_URL__);
const { renderSessionList, refreshSessionList, selectSession } = await import(__SESSIONS_URL__);
const oldSession = { session_id: "s1", title: "旧标题", updated_at: 1 };
store.sessionDetails.s1 = oldSession;
renderSessionList([oldSession]);

let resolveList;
api.listSessions = () => new Promise((resolve) => { resolveList = resolve; });
const staleList = refreshSessionList();
api.renameSession = async () => ({ session_id: "s1", title: "本地新标题" });
let menu = byClass(sessionList, "sess-menu-toggle")[0];
await menu.click();
let rename = byClass(sessionList, "sess-menu-action")[0];
await rename.click();
let input = byClass(sessionList, "sess-rename-input")[0];
input.value = "本地新标题";
await input.dispatch("keydown", { key: "Enter" });
resolveList({ sessions: [oldSession] });
await staleList;
if (store.sessionDetails.s1.title !== "本地新标题"
    || !sessionList.textContent.includes("本地新标题")) {
  throw new Error("重命名后的旧列表响应覆盖了本地新标题");
}

let resolveDetail;
api.getMessages = () => new Promise((resolve) => { resolveDetail = resolve; });
api.chatStream = async (_message, _sessionId, handlers) => { handlers.done({}); };
api.listSessions = async () => ({ sessions: [store.sessionDetails.s1] });
store.currentSessionId = "other";
const staleDetail = selectSession("s1");
await Promise.resolve();
await sendMessage("详情加载期间的新消息");
resolveDetail({
  messages: [{ role: "assistant", content: "过期历史" }],
  artifacts: [{ artifact_id: "old-artifact", session_id: "s1",
    payload: { symbol: "000001", name: "过期成果" } }],
});
await staleDetail;
if (!store.sessionMessages.s1.some((message) => message.content === "详情加载期间的新消息")
    || store.sessionMessages.s1.some((message) => message.content === "过期历史")
    || (store.sessionArtifacts.s1 || []).some((item) => item.artifact_id === "old-artifact")) {
  throw new Error("新消息失效后，旧详情响应仍写入会话缓存");
}

let deleteHandlers;
let resolveDeleteStream;
api.chatStream = (_message, _sessionId, handlers) => {
  deleteHandlers = handlers;
  return new Promise((resolve) => { resolveDeleteStream = resolve; });
};
const deletedPending = sendMessage("删除前问题");
await Promise.resolve();
store.currentSessionId = "other";
renderSessionList([store.sessionDetails.s1]);
let resolveDeletedList;
let deleteListCalls = 0;
api.listSessions = () => {
  deleteListCalls += 1;
  if (deleteListCalls === 1) {
    return new Promise((resolve) => { resolveDeletedList = resolve; });
  }
  return Promise.resolve({ sessions: [] });
};
const listBeforeDelete = refreshSessionList();
window.confirm = () => true;
api.deleteSession = async () => ({});
menu = byClass(sessionList, "sess-menu-toggle")[0];
await menu.click();
const remove = byClass(sessionList, "sess-menu-action")[1];
await remove.click();
deleteHandlers.text({ content: "删除后迟到正文" });
deleteHandlers.artifact({ artifact: {
  artifact_id: "deleted-late", session_id: "s1", message_id: 200,
  payload: { symbol: "000001", name: "删除后迟到成果" },
}, persisted: true });
deleteHandlers.done({});
resolveDeleteStream();
await deletedPending;
resolveDeletedList({ sessions: [oldSession] });
await listBeforeDelete;
if (store.sessionDetails.s1 || !store.sessionTombstones.s1
    || store.sessionMessages.s1 || store.sessionArtifacts.s1 || store.sessionRuns.s1
    || sessionList.textContent.includes("旧标题")) {
  throw new Error("删除后的旧响应或 SSE 事件复活了 tombstone 会话");
}
""".replace("__SESSIONS_URL__", sessions_url).replace("__CHAT_URL__", chat_url)
        script = script.replace("__API_URL__", api_url).replace("__STATE_URL__", state_url)
        _run_node(tmp_path, script)

    def test_stream_remounts_after_session_switch_and_deduplicates_report_tool(self, tmp_path):
        sessions_url = json.dumps(_module_url("src/api/static/js/sessions.js"))
        chat_url = json.dumps(_module_url("src/api/static/js/chat.js"))
        api_url = json.dumps(_module_url("src/api/static/js/api.js"))
        state_url = json.dumps(_module_url("src/api/static/js/state.js"))
        script = _DOM_STUB + r"""
const sessionList = makeElement("sessionList");
const chatScroll = makeElement("chatScroll");
makeElement("chatInput", "textarea");
makeElement("sendBtn", "button");
makeElement("appLayout");
const drawer = makeElement("reportDrawer", "aside");
drawer.hidden = true;
drawer.setAttribute("aria-hidden", "true");
makeElement("reportDrawerError");
makeElement("reportDrawerNav", "nav");
makeElement("reportDrawerContent");

const { api } = await import(__API_URL__);
const { store } = await import(__STATE_URL__);
const { sendMessage } = await import(__CHAT_URL__);
const { selectSession } = await import(__SESSIONS_URL__);
store.currentSessionId = "s1";
store.sessionDetails = {
  s1: { session_id: "s1", title: "会话一", updated_at: 2 },
  s2: { session_id: "s2", title: "会话二", updated_at: 1 },
};
store.sessionMessages.s1 = [];
store.sessionMessages.s2 = [];
store.sessionArtifacts.s1 = [];
store.sessionArtifacts.s2 = [];
api.listSessions = async () => ({ sessions: Object.values(store.sessionDetails) });
let streamHandlers;
let finishStream;
api.chatStream = (_message, _sessionId, handlers) => {
  streamHandlers = handlers;
  return new Promise((resolve) => { finishStream = resolve; });
};
const pendingSend = sendMessage("分析两个标的");
await Promise.resolve();
streamHandlers.result({
  summary: "阶段结论",
  tool_results: [
        { tool: "analyze_stock", symbol: "000001", status: "done",
          content: "000001 原始结果", message_id: 101 },
        { tool: "analyze_stock", symbol: "000001", status: "error",
          content: "000001 失败结果", message_id: 102 },
  ],
});
await selectSession("s2");
await selectSession("s1");
streamHandlers.text({ content: "切回后最终正文" });
streamHandlers.artifact({ artifact: {
  artifact_id: "artifact-1", session_id: "s1", symbol: "000001",
  message_id: 101,
  payload: { symbol: "000001", name: "平安银行", commentary: "成果结论" },
}, persisted: true });
const renderedHtml = descendants(chatScroll).map((node) => node.innerHTML).join("\n");
if (!renderedHtml.includes("切回后最终正文")
    || !chatScroll.textContent.includes("平安银行")) {
  throw new Error("切走再切回后，流式正文或成果仍写入脱离 DOM 的旧节点");
}
if (renderedHtml.includes("000001 原始结果")
    || renderedHtml.includes("000001 失败结果")) {
  throw new Error("实时会话泄露了工具结果气泡");
}
streamHandlers.done({});
finishStream();
await pendingSend;
let detailReloads = 0;
api.getMessages = async (id) => {
  if (id === "s1") {
    detailReloads += 1;
    return {
      messages: [
        { role: "user", content: "分析两个标的", message_id: 100 },
        { role: "tool", content: "[analyze_stock] success: 000001 原始结果",
          message_id: 101 },
        { role: "assistant", content: "阶段结论\n\n切回后最终正文", message_id: 103 },
      ],
      artifacts: [store.sessionArtifacts.s1[0]],
    };
  }
  return { messages: [], artifacts: [] };
};
await selectSession("s2");
await selectSession("s1");
if (detailReloads !== 1
    || byClass(chatScroll, "artifact-history-orphans").length !== 0
    || byClass(chatScroll, "report-summary-card").length !== 1) {
  throw new Error("run 完成后切回未强制恢复带 message_id 的历史，成果落入孤儿区");
}
""".replace("__SESSIONS_URL__", sessions_url).replace("__CHAT_URL__", chat_url)
        script = script.replace("__API_URL__", api_url).replace("__STATE_URL__", state_url)
        _run_node(tmp_path, script)

    def test_session_title_adopts_cold_stream_before_plan_once(self, tmp_path):
        chat_url = json.dumps(_module_url("src/api/static/js/chat.js"))
        api_url = json.dumps(_module_url("src/api/static/js/api.js"))
        state_url = json.dumps(_module_url("src/api/static/js/state.js"))
        script = _DOM_STUB + r"""
makeElement("chatScroll");
makeElement("chatInput", "textarea");
makeElement("sendBtn", "button");
const { api } = await import(__API_URL__);
const { store } = await import(__STATE_URL__);
const { sendMessage } = await import(__CHAT_URL__);
store.currentSessionId = null;
api.chatStream = async (_message, sessionId, handlers) => {
  if (sessionId !== null) throw new Error("冷启动请求应不带 session");
  handlers.session_title({ session_id: "cold-sid", title: "冷启动标题" });
  handlers.session_title({ session_id: "cold-sid", title: "精炼冷启动标题" });
  handlers.text({ content: "冷启动回答" });
  handlers.done({});
};
await sendMessage("冷启动问题");
const userMessages = (store.sessionMessages["cold-sid"] || [])
  .filter((message) => message.role === "user");
if (store.currentSessionId !== "cold-sid" || userMessages.length !== 1
    || userMessages[0].content !== "冷启动问题") {
  throw new Error("plan 前 session_title 未接管 sid，或用户消息被重复缓存");
}
""".replace("__CHAT_URL__", chat_url).replace("__API_URL__", api_url)
        script = script.replace("__STATE_URL__", state_url)
        _run_node(tmp_path, script)

    def test_cold_stream_failure_shows_actionable_error(self, tmp_path):
        """服务在返回 session id 前失败时，用户仍必须看到可重试错误。"""
        chat_url = json.dumps(_module_url("src/api/static/js/chat.js"))
        api_url = json.dumps(_module_url("src/api/static/js/api.js"))
        state_url = json.dumps(_module_url("src/api/static/js/state.js"))
        script = _DOM_STUB + r"""
const chatScroll = makeElement("chatScroll");
makeElement("chatInput", "textarea");
makeElement("sendBtn", "button");
const { api } = await import(__API_URL__);
const { store } = await import(__STATE_URL__);
const { sendMessage } = await import(__CHAT_URL__);
store.currentSessionId = null;
api.chatStream = async () => { throw new Error("服务未启动"); };
await sendMessage("你好");
const errors = byClass(chatScroll, "error-card");
if (errors.length !== 1 || !errors[0].textContent.includes("连接中断")) {
  throw new Error("冷启动连接失败时未展示可见错误");
}
""".replace("__CHAT_URL__", chat_url).replace("__API_URL__", api_url)
        script = script.replace("__STATE_URL__", state_url)
        _run_node(tmp_path, script)

    def test_text_delta_renders_before_stream_completion(self, tmp_path):
        """SSE 正文片段到达时必须立即展示，不能等切换会话后的历史恢复。"""
        chat_url = json.dumps(_module_url("src/api/static/js/chat.js"))
        api_url = json.dumps(_module_url("src/api/static/js/api.js"))
        state_url = json.dumps(_module_url("src/api/static/js/state.js"))
        script = _DOM_STUB + r"""
const chatScroll = makeElement("chatScroll");
makeElement("chatInput", "textarea");
makeElement("sendBtn", "button");
const { api } = await import(__API_URL__);
const { store } = await import(__STATE_URL__);
const { sendMessage } = await import(__CHAT_URL__);
store.currentSessionId = "s1";
let sawLive = false;
api.chatStream = async (_message, _sessionId, handlers) => {
  handlers.session_title({ session_id: "s1", title: "流式测试" });
  handlers.text_delta({ content: "实时正文片段" });
  const html = descendants(chatScroll).map((item) => item.innerHTML).join("\n");
  if (!html.includes("实时正文片段")) {
    throw new Error("正文片段未在 SSE 完成前渲染");
  }
  sawLive = true;
  handlers.text({ content: "实时正文片段已完成" });
  handlers.done({});
};
await sendMessage("流式回答");
if (!sawLive) throw new Error("流式正文处理器未在结束前完成渲染");
""".replace("__CHAT_URL__", chat_url).replace("__API_URL__", api_url)
        script = script.replace("__STATE_URL__", state_url)
        _run_node(tmp_path, script)

    def test_assistant_thinking_precedes_body_and_can_be_collapsed(self, tmp_path):
        """历史消息的思考区应位于正文前，默认展开且保留可收起入口。"""
        chat_url = json.dumps(_module_url("src/api/static/js/chat.js"))
        state_url = json.dumps(_module_url("src/api/static/js/state.js"))
        script = _DOM_STUB + r"""
const chatScroll = makeElement("chatScroll");
const { store } = await import(__STATE_URL__);
const { renderMessageHistory } = await import(__CHAT_URL__);
store.currentSessionId = "thinking-history";
renderMessageHistory([{
  role: "assistant", content: "这是独立的正文。", thinking: "这是可展开的思考。",
}], []);
const message = byClass(chatScroll, "assistant-message")[0];
if (!message || !message.children[0].classList.contains("message-thinking")
    || !message.children[0].open || !message.children[1].classList.contains("md")
    || !message.children[0].textContent.includes("已思考")) {
  throw new Error("思考区没有在正文前默认展开，或缺少已思考入口");
}
message.children[0].open = false;
if (message.children[0].open || !message.children[0].textContent.includes("已思考")
    || !message.children[1].innerHTML.includes("这是独立的正文。")) {
  throw new Error("收起思考后未保留入口，或错误隐藏了正文");
}
""".replace("__CHAT_URL__", chat_url).replace("__STATE_URL__", state_url)
        _run_node(tmp_path, script)

    def test_thinking_stream_stays_visible_after_body_and_hides_internal_cards(self, tmp_path):
        """思考流完成正文后仍可查看，且对象内容不应渲染为 object。"""
        chat_url = json.dumps(_module_url("src/api/static/js/chat.js"))
        api_url = json.dumps(_module_url("src/api/static/js/api.js"))
        state_url = json.dumps(_module_url("src/api/static/js/state.js"))
        script = _DOM_STUB + r"""
const chatScroll = makeElement("chatScroll");
makeElement("chatInput", "textarea");
makeElement("sendBtn", "button");
const { api } = await import(__API_URL__);
const { store } = await import(__STATE_URL__);
const { sendMessage } = await import(__CHAT_URL__);
store.currentSessionId = "s1";
api.chatStream = async (_message, _sessionId, handlers) => {
  handlers.session_title({ session_id: "s1", title: "思考流测试" });
  handlers.plan({ session_id: "s1", steps: ["内部步骤"] });
  handlers.thinking({ content: { opaque: true } });
  handlers.thinking({ content: "正在核对数据" });
  // 用户手动收起思考区（stub 无原生 toggle，手动派发）
  const thinking = byClass(chatScroll, "message-thinking")[0];
  thinking.open = false;
  thinking.dispatchEvent({ type: "toggle", target: thinking });
  handlers.tool_result({ tool: "analyze_stock", content: "内部工具结果" });
  handlers.text({ content: "最终正文" });
  handlers.done({});
};
await sendMessage("测试思考流");
// stub 的 innerHTML 与 textContent 是互斥通道：md 正文走 innerHTML，思考明文走 textContent
const visibleHtml = descendants(chatScroll).map((item) => item.innerHTML).join("\n");
const visibleText = chatScroll.textContent;
const collapsed = byClass(chatScroll, "message-thinking")[0];
if (!visibleHtml.includes("最终正文")
    || !visibleText.includes("正在核对数据")
    || visibleText.includes("[object Object]")
    || visibleText.includes("内部工具结果")
    || visibleText.includes("执行计划")
    || visibleText.includes("思考中...")
    || collapsed.open) {
  throw new Error("思考与正文未正确分离、持久展示、折叠状态丢失，或泄露内部气泡");
}
""".replace("__CHAT_URL__", chat_url).replace("__API_URL__", api_url)
        script = script.replace("__STATE_URL__", state_url)
        _run_node(tmp_path, script)

    def test_thinking_fallback_survives_after_final_body(self, tmp_path):
        """无法提取明文思考时，正文结束后仍保留可展开的思考占位。"""
        chat_url = json.dumps(_module_url("src/api/static/js/chat.js"))
        api_url = json.dumps(_module_url("src/api/static/js/api.js"))
        state_url = json.dumps(_module_url("src/api/static/js/state.js"))
        script = _DOM_STUB + r"""
const chatScroll = makeElement("chatScroll");
makeElement("chatInput", "textarea");
makeElement("sendBtn", "button");
const { api } = await import(__API_URL__);
const { store } = await import(__STATE_URL__);
const { sendMessage } = await import(__CHAT_URL__);
store.currentSessionId = "s1";
api.chatStream = async (_message, _sessionId, handlers) => {
  handlers.session_title({ session_id: "s1", title: "思考占位测试" });
  handlers.thinking({ content: { opaque: true } });
  handlers.text({ content: "最终正文" });
  handlers.done({});
};
await sendMessage("测试无明文思考");
const thinking = byClass(chatScroll, "message-thinking")[0];
const visibleHtml = descendants(chatScroll).map((item) => item.innerHTML).join("\n");
if (!thinking || !thinking.open || !thinking.textContent.includes("思考中...")
    || !visibleHtml.includes("最终正文")) {
  throw new Error("正文完成后丢失了思考占位或正文");
}
thinking.open = false;
if (thinking.open || !thinking.textContent.includes("思考中...")
    || !visibleHtml.includes("最终正文")) {
  throw new Error("收起思考占位后没有保留入口，或错误隐藏了正文");
}
""".replace("__CHAT_URL__", chat_url).replace("__API_URL__", api_url)
        script = script.replace("__STATE_URL__", state_url)
        _run_node(tmp_path, script)

    def test_draft_session_reuses_input_and_adopts_persisted_session(self, tmp_path):
        """草稿会话不落库，首条 SSE 事件原子接管全部本地缓存。"""
        sessions_url = json.dumps(_module_url("src/api/static/js/sessions.js"))
        chat_url = json.dumps(_module_url("src/api/static/js/chat.js"))
        api_url = json.dumps(_module_url("src/api/static/js/api.js"))
        state_url = json.dumps(_module_url("src/api/static/js/state.js"))
        script = _DOM_STUB + r"""
const chatScroll = makeElement("chatScroll");
const chatInput = makeElement("chatInput", "textarea");
makeElement("sendBtn", "button");
makeElement("newSessionBtn", "button");
makeElement("collapseBtn", "button");
makeElement("sessionList");
makeElement("appLayout");
const drawer = makeElement("reportDrawer", "aside");
drawer.hidden = true;
drawer.setAttribute("aria-hidden", "true");
makeElement("reportDrawerError");
makeElement("reportDrawerNav", "nav");
makeElement("reportDrawerContent");

const { api } = await import(__API_URL__);
const { store } = await import(__STATE_URL__);
const { initSessions, initSessionStartup } = await import(__SESSIONS_URL__);
const { sendMessage } = await import(__CHAT_URL__);
let createCalls = 0;
api.createSession = async () => { createCalls += 1; return { session_id: "unexpected" }; };
api.listSessions = async () => ({ sessions: [] });
initSessions();
await initSessionStartup();
const draftId = store.currentSessionId;
if (!draftId?.startsWith("draft-") || createCalls !== 0 || store.sessionDetails[draftId]) {
  throw new Error("启动空会话不应持久化，且必须使用 draft- ID");
}
chatInput.value = "未发送内容";
await document.getElementById("newSessionBtn").click();
if (store.currentSessionId !== draftId || chatInput.value !== "" || createCalls !== 0) {
  throw new Error("重复新建应复用草稿并清空输入，不得请求创建会话");
}

store.sessionMessages[draftId] = [];
store.sessionArtifacts[draftId] = [{ artifact_id: "old-artifact", session_id: draftId }];
store.sessionRunEpochs[draftId] = 3;
store.sessionDetailGenerations[draftId] = 4;
store.sessionDetailStale[draftId] = true;
let handlers;
let resolveStream;
api.chatStream = (_message, sessionId, streamHandlers) => {
  if (sessionId !== draftId) throw new Error("首条消息必须携带草稿 ID");
  handlers = streamHandlers;
  handlers.plan({ session_id: "server-1", steps: [] });
  handlers.session_title({ session_id: "server-1", title: "真实会话" });
  return new Promise((resolve) => { resolveStream = resolve; });
};
const pending = sendMessage("首条问题");
await Promise.resolve();
const requiredCaches = [
  "sessionMessages", "sessionArtifacts", "sessionRuns", "sessionRunEpochs",
  "sessionDetailGenerations", "sessionDetailStale",
];
for (const name of requiredCaches) {
  if (Object.hasOwn(store[name], draftId) || !Object.hasOwn(store[name], "server-1")) {
    throw new Error(`${name} 未从草稿原子迁移到真实会话`);
  }
}
if (store.currentSessionId !== "server-1" || store.sessionDetails[draftId]
    || !store.sessionDetails["server-1"]
    || store.sessionRuns["server-1"][0].sessionId !== "server-1"
    || store.sessionArtifacts["server-1"][0].session_id !== "server-1") {
  throw new Error("草稿接管后当前会话、详情、run 或成果仍指向草稿");
}
handlers.done({});
resolveStream();
await pending;
""".replace("__SESSIONS_URL__", sessions_url).replace("__CHAT_URL__", chat_url)
        script = script.replace("__API_URL__", api_url).replace("__STATE_URL__", state_url)
        _run_node(tmp_path, script)

    def test_cancelled_draft_stream_does_not_adopt_late_persisted_session(self, tmp_path):
        """草稿流被新建操作取消后，迟到 SSE 不得接管或复活缓存。"""
        sessions_url = json.dumps(_module_url("src/api/static/js/sessions.js"))
        chat_url = json.dumps(_module_url("src/api/static/js/chat.js"))
        api_url = json.dumps(_module_url("src/api/static/js/api.js"))
        state_url = json.dumps(_module_url("src/api/static/js/state.js"))
        script = _DOM_STUB + r"""
makeElement("chatScroll");
makeElement("chatInput", "textarea");
makeElement("sendBtn", "button");
makeElement("newSessionBtn", "button");
makeElement("collapseBtn", "button");
makeElement("sessionList");
makeElement("appLayout");
const drawer = makeElement("reportDrawer", "aside");
drawer.hidden = true;
drawer.setAttribute("aria-hidden", "true");
makeElement("reportDrawerError");
makeElement("reportDrawerNav", "nav");
makeElement("reportDrawerContent");

const { api } = await import(__API_URL__);
const { store } = await import(__STATE_URL__);
const { initSessions } = await import(__SESSIONS_URL__);
const { sendMessage } = await import(__CHAT_URL__);
const draftId = "draft-cancelled";
store.currentSessionId = draftId;
store.sessionMessages[draftId] = [];
store.sessionArtifacts[draftId] = [];
let handlers;
let resolveStream;
api.chatStream = (_message, sessionId, streamHandlers) => {
  if (sessionId !== draftId) throw new Error("取消前的请求必须使用草稿 ID");
  handlers = streamHandlers;
  return new Promise((resolve) => { resolveStream = resolve; });
};
initSessions();
const pending = sendMessage("会被取消的问题");
await Promise.resolve();
await document.getElementById("newSessionBtn").click();
handlers.session_title({ session_id: "server-late", title: "迟到标题" });
handlers.plan({ session_id: "server-late", steps: [] });
handlers.artifact({ artifact: {
  artifact_id: "late-artifact", session_id: "server-late",
  payload: { symbol: "000001", name: "迟到成果" },
}, persisted: true });
if (store.currentSessionId !== draftId || store.sessionDetails["server-late"]
    || store.sessionMessages["server-late"] || store.sessionArtifacts["server-late"]
    || store.sessionRuns["server-late"] || (store.sessionRuns[draftId] || []).length !== 0) {
  throw new Error(`取消后的迟到 SSE 仍接管或复活了草稿会话: current=${store.currentSessionId}, `
    + `detail=${Boolean(store.sessionDetails["server-late"])}, `
    + `messages=${Boolean(store.sessionMessages["server-late"])}, `
    + `artifacts=${Boolean(store.sessionArtifacts["server-late"])}, `
    + `lateRuns=${Boolean(store.sessionRuns["server-late"])}, `
    + `draftRunCount=${(store.sessionRuns[draftId] || []).length}`);
}
handlers.done({});
resolveStream();
await pending;
""".replace("__SESSIONS_URL__", sessions_url).replace("__CHAT_URL__", chat_url)
        script = script.replace("__API_URL__", api_url).replace("__STATE_URL__", state_url)
        _run_node(tmp_path, script)

    def test_static_assets_do_not_expose_clear_session_operation(self):
        """已废弃的会话创建、清空操作不得留在静态界面。"""
        html = Path("src/api/static/index.html").read_text(encoding="utf-8")
        api_source = Path("src/api/static/js/api.js").read_text(encoding="utf-8")

        assert 'id="quickClear"' not in html
        assert "createSession(" not in api_source
        assert "clearSession(" not in api_source

    def test_settings_renders_configuration_and_handles_secrets_and_save(self, tmp_path):
        """设置页应维护本地草稿，编辑只更新提示条计数，保存时才提交变更。"""
        settings_url = json.dumps(_module_url("src/api/static/js/settings.js"))
        api_url = json.dumps(_module_url("src/api/static/js/api.js"))
        state_url = json.dumps(_module_url("src/api/static/js/state.js"))
        script = _DOM_STUB + r"""
const settingsContent = makeElement("settingsContent");
const settingsView = makeElement("view-settings");
settingsView.className = "view active";
const { api } = await import(__API_URL__);
const { store } = await import(__STATE_URL__);
const { initSettings, renderSettings } = await import(__SETTINGS_URL__);
const payload = {
  config: {
    llm: { enabled: true, provider: "openai", model: "gpt-4o",
      api_key: { configured: true, masked: "sk-ab*****wxyz" }, base_url: "",
      temperature: 0.3, max_tokens: 2000, retry_times: 2, timeout_seconds: 60 },
    data: { disclaimer_accepted: false,
      cache_ttl: { daily: 86400, quarterly: 604800, news: 21600 } },
    api: { host: "127.0.0.1", port: 25618 },
    push: { enabled: true, max_symbols_per_subscription: 20,
      email: { smtp_host: "smtp.qq.com", smtp_port: 465, smtp_user: "user",
        smtp_password: { configured: true, masked: "smtp*****xyz" }, to_addr: "to@example.com" },
      wecom: { corp_id: "corp", agent_id: "agent",
        secret: { configured: true, masked: "weco*****cret" }, to_user: "@all" } },
    signal: { thresholds: { attack: 7, watch: 4 }, actions: {
      attack: { action: "可考虑建仓/加仓", position: "60%-80%" },
      watch: { action: "持有观察，等待明确方向", position: "30%-50%" },
      defend: { action: "减仓或回避", position: "0%-20%" },
    } },
  },
  paths: { state_dir: "D:/project/.stock_robot", config_file: "D:/project/.stock_robot/config.yaml" },
};
let credentialCalls = 0;
let putBody = null;
let putResult = { persisted: true, applied: true, restart_required: false };
api.getConfig = async () => payload;
api.getCredential = async (key) => {
  credentialCalls += 1;
  if (key !== "llm.api_key") throw new Error("读取了错误密钥");
  return { value: "sk-actual-secret-wxyz" };
};
api.updateConfig = async (config) => {
  putBody = config;
  return { ...payload, ...putResult };
};
store.currentView = "settings";
initSettings();
renderSettings(payload);
for (const heading of ["LLM 设置", "数据与缓存", "服务设置", "推送设置", "信号策略"]) {
  if (!settingsContent.textContent.includes(heading)) throw new Error(`缺少分区: ${heading}`);
}
if (!settingsContent.textContent.includes("D:/project/.stock_robot")) {
  throw new Error("未显示只读路径");
}
if (!document.getElementById("settingsDirtyBar").hidden) {
  throw new Error("未编辑时不应显示全局未保存提示");
}
const apiKey = document.getElementById("settings-llm-api_key");
if (byClass(settingsContent, "settings-secret-value").length
    || apiKey.value || apiKey.placeholder !== "sk-ab*****wxyz"
    || apiKey.type !== "password") {
  throw new Error("密钥应使用单一输入框展示掩码，且不保留额外展示列");
}
const toggle = byClass(settingsContent, "settings-secret-toggle")[0];
if (toggle.getAttribute("aria-label") !== "显示完整密钥") {
  throw new Error("密钥默认未使用显示按钮语义");
}
await toggle.click();
if (credentialCalls !== 1 || apiKey.value !== "sk-actual-secret-wxyz" || apiKey.type !== "text"
    || toggle.getAttribute("aria-label") !== "隐藏完整密钥") {
  throw new Error("睁眼未按需读取或展示完整密钥");
}
await toggle.click();
if (credentialCalls !== 1 || apiKey.value || apiKey.placeholder !== "sk-ab*****wxyz"
    || apiKey.type !== "password"
    || toggle.getAttribute("aria-label") !== "显示完整密钥") {
  throw new Error("闭眼未擦除完整密钥并恢复掩码");
}
const model = document.getElementById("settings-llm-model");
model.value = "gpt-5";
await model.dispatch("input");
if (putBody !== null || document.getElementById("settingsDirtyBar").hidden
    || !settingsContent.textContent.includes("有 1 项配置尚未保存")) {
  throw new Error("编辑配置不应立即保存，且必须显示全局未保存提示");
}
apiKey.value = "sk-new-secret-wxyz";
await apiKey.dispatch("input");
if (putBody !== null || document.getElementById("settingsDirtyBar").hidden
    || !settingsContent.textContent.includes("有 2 项配置尚未保存")) {
  throw new Error("多字段编辑未显示 N=2 计数，或编辑时不应触发保存");
}
await document.getElementById("settingsSaveBtn").click();
if (putBody?.llm?.model !== "gpt-5" || putBody?.llm?.api_key !== "sk-new-secret-wxyz") {
  throw new Error("保存未提交草稿，或密钥未在保存时才进入 PUT body");
}
if (!document.getElementById("settingsDirtyBar").hidden
    || !settingsContent.textContent.includes("配置已保存并应用")) {
  throw new Error("热更新成功未隐藏提示条或未显示已保存并应用");
}
const host = document.getElementById("settings-api-host");
const port = document.getElementById("settings-api-port");
host.value = "0.0.0.0";
await host.dispatch("input");
port.value = "8080";
await port.dispatch("input");
if (document.getElementById("settingsDirtyBar").hidden
    || !settingsContent.textContent.includes("有 2 项配置尚未保存")) {
  throw new Error("监听地址与端口编辑未显示 N=2 未保存计数");
}
putResult = { persisted: true, applied: false, restart_required: true };
await document.getElementById("settingsSaveBtn").click();
if (putBody?.api?.host !== "0.0.0.0" || putBody?.api?.port !== 8080) {
  throw new Error("保存未提交监听地址与端口");
}
if (!settingsContent.textContent.includes("配置已保存；监听地址或端口在重启 stock-robot run 后生效")) {
  throw new Error("缺少服务配置重启提示");
}
const model2 = document.getElementById("settings-llm-model");
model2.value = "gpt-6";
await model2.dispatch("input");
putResult = {
  persisted: true, applied: false, restart_required: false,
  reload_error: "运行时未连接，配置将在下次启动时生效",
};
await document.getElementById("settingsSaveBtn").click();
if (model2.value !== "gpt-6" || document.getElementById("settingsDirtyBar").hidden
    || !settingsContent.textContent.includes("有 1 项配置尚未保存")
    || !settingsContent.textContent.includes("运行时未连接，配置将在下次启动时生效")
    || document.getElementById("settingsSaveBtn").textContent !== "保存并应用") {
  throw new Error("运行时应用失败应保留输入与提示条，并显示 reload_error");
}
putResult = {
  persisted: true, applied: false, restart_required: true,
  reload_error: "LLM 后端初始化失败",
};
await document.getElementById("settingsSaveBtn").click();
if (model2.value !== "gpt-6" || document.getElementById("settingsDirtyBar").hidden
    || !settingsContent.textContent.includes("有 1 项配置尚未保存")
    || !settingsContent.textContent.includes("监听地址或端口已保存，重启后生效")
    || !settingsContent.textContent.includes("LLM 后端初始化失败")) {
  throw new Error("混合更新失败应保留草稿，并同时提示重启与 reload_error");
}
api.updateConfig = async () => { const error = new Error("llm.model: 不允许"); error.status = 422; throw error; };
await document.getElementById("settingsSaveBtn").click();
if (model2.value !== "gpt-6" || document.getElementById("settingsDirtyBar").hidden
    || !settingsContent.textContent.includes("llm.model: 不允许")) {
  throw new Error("保存失败覆盖了编辑值，或未显示校验错误，或未保留未保存提示");
}
""".replace("__SETTINGS_URL__", settings_url).replace("__API_URL__", api_url)
        script = script.replace("__STATE_URL__", state_url)
        _run_node(tmp_path, script)

    def test_settings_loads_only_on_entry_and_offers_retry_after_failure(self, tmp_path):
        """配置请求应延迟至进入视图，并在失败后保留明确的重试入口。"""
        settings_url = json.dumps(_module_url("src/api/static/js/settings.js"))
        api_url = json.dumps(_module_url("src/api/static/js/api.js"))
        state_url = json.dumps(_module_url("src/api/static/js/state.js"))
        script = _DOM_STUB + r"""
const settingsContent = makeElement("settingsContent");
const { api } = await import(__API_URL__);
const { bus } = await import(__STATE_URL__);
const { initSettings } = await import(__SETTINGS_URL__);
let configCalls = 0;
api.getConfig = async () => { configCalls += 1; throw new Error("服务未启动"); };
initSettings();
if (configCalls !== 0) throw new Error("初始化时不应提前加载配置");
const enter = new Event("view-change");
Object.defineProperty(enter, "detail", { value: { view: "settings" } });
bus.dispatchEvent(enter);
await new Promise((resolve) => setTimeout(resolve, 0));
if (configCalls !== 1 || !settingsContent.textContent.includes("无法加载配置，请检查服务连接后重试")) {
  throw new Error("进入设置页后未显示加载失败提示");
}
await byClass(settingsContent, "btn-retry")[0].click();
if (configCalls !== 2) throw new Error("加载失败后的重试按钮未重新请求配置");
""".replace("__SETTINGS_URL__", settings_url).replace("__API_URL__", api_url)
        script = script.replace("__STATE_URL__", state_url)
        _run_node(tmp_path, script)

    def test_settings_ignores_stale_secrets_and_labels_secret_inputs(self, tmp_path):
        """密钥请求只能作用于当前设置页，且连续睁眼应复用同一请求。"""
        settings_url = json.dumps(_module_url("src/api/static/js/settings.js"))
        api_url = json.dumps(_module_url("src/api/static/js/api.js"))
        state_url = json.dumps(_module_url("src/api/static/js/state.js"))
        script = _DOM_STUB + r"""
const settingsContent = makeElement("settingsContent");
const settingsView = makeElement("view-settings");
settingsView.className = "view active";
const { api } = await import(__API_URL__);
const { bus, store } = await import(__STATE_URL__);
const { initSettings, renderSettings } = await import(__SETTINGS_URL__);
const payload = {
  config: {
    llm: { api_key: { configured: true, masked: "mask-llm" } },
    push: {
      email: { smtp_password: { configured: true, masked: "mask-smtp" } },
      wecom: { secret: { configured: true, masked: "mask-wecom" } },
    },
  },
  paths: { state_dir: "D:/project/.stock_robot", config_file: "D:/project/.stock_robot/config.yaml" },
};
store.currentView = "settings";
initSettings();
renderSettings(payload);
for (const path of ["llm.api_key", "push.email.smtp_password", "push.wecom.secret"]) {
  const input = document.getElementById(`settings-${path.replaceAll(".", "-")}`);
  const labelId = `settings-${path.replaceAll(".", "-")}-label`;
  if (input.getAttribute("aria-labelledby") !== labelId || !document.getElementById(labelId)) {
    throw new Error(`密钥输入框缺少可访问名称: ${path}`);
  }
}
let calls = 0;
const resolvers = [];
api.getCredential = () => {
  calls += 1;
  return new Promise((resolve) => resolvers.push(resolve));
};
let toggle = byClass(settingsContent, "settings-secret-toggle")[0];
const first = toggle.click();
const second = toggle.click();
if (calls !== 1) throw new Error("连续睁眼不应重复读取完整密钥");
resolvers.shift()({ value: "first-complete-secret" });
await Promise.all([first, second]);
if (document.getElementById("settings-llm-api_key").value === "first-complete-secret") {
  throw new Error("第二次点击闭眼后迟到响应不应显示完整密钥");
}

renderSettings(payload);
store.currentView = "settings";
settingsView.classList.add("active");
toggle = byClass(settingsContent, "settings-secret-toggle")[0];
const afterEdit = toggle.click();
const replacement = document.getElementById("settings-llm-api_key");
replacement.value = "new-secret";
await replacement.dispatch("input");
resolvers.shift()({ value: "late-after-edit" });
await afterEdit;
if (replacement.value === "late-after-edit") {
  throw new Error("编辑新密钥后迟到响应泄露了完整值");
}

renderSettings(payload);
store.currentView = "settings";
settingsView.classList.add("active");
toggle = byClass(settingsContent, "settings-secret-toggle")[0];
const afterLeave = toggle.click();
const leave = new Event("view-change");
Object.defineProperty(leave, "detail", { value: { view: "chat" } });
store.currentView = "chat";
settingsView.classList.remove("active");
bus.dispatchEvent(leave);
resolvers.shift()({ value: "late-after-leave" });
await afterLeave;
if (document.getElementById("settings-llm-api_key").value === "late-after-leave") {
  throw new Error("离开设置页后迟到响应泄露了完整值");
}
""".replace("__SETTINGS_URL__", settings_url).replace("__API_URL__", api_url)
        script = script.replace("__STATE_URL__", state_url)
        _run_node(tmp_path, script)

    @pytest.mark.asyncio
    async def test_css_served(self, client):
        resp = await client.get("/css/app.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers.get("content-type", "")
