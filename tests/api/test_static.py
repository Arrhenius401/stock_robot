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
  async dispatch(type, event = {}) {
    event.preventDefault ||= () => { event.defaultPrevented = true; };
    event.stopPropagation ||= () => {};
    for (const handler of this.listeners[type] || []) await handler(event);
  }
  async blur() {
    if (this.ownerDocument.activeElement === this) this.ownerDocument.activeElement = null;
    await this.dispatch("blur");
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
    async def test_workspace_shell_has_accessible_landmarks(self, client):
        """工作台外壳提供移动端可访问入口与覆盖层。"""
        html = (await client.get("/")).text

        assert 'id="appLayout"' in html
        assert 'aria-label="主要导航"' in html
        assert 'id="globalStockSearch"' in html
        assert 'id="mobileNavToggle"' in html
        assert 'id="workspaceBackdrop"' in html

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
                     "/js/report-drawer.js"):
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
const toolCards = byClass(chatScroll, "card-title")
  .filter((node) => node.textContent === "工具结果");
const toolHtml = byClass(chatScroll, "tooltext").map((node) => node.innerHTML).join("\n");
if (toolCards.length !== 1 || !toolHtml.includes("000001 失败")
    || toolHtml.includes("000001 成功") || toolHtml.includes("000001 再次成功")) {
  throw new Error("报告工具 repr 未按具体成果关联去重");
}

renderMessageHistory([], []);
const suggestions = byClass(chatScroll, "research-suggestion");
if (suggestions.length !== 3) throw new Error("空会话应展示三个研究建议");
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
    || !renderedHtml.includes("000001 失败结果")) {
  throw new Error("实时报告工具卡未按当前 run 与成果 symbol 精确去重");
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

    def test_clear_invalidates_stream_and_manual_title_blocks_late_event(self, tmp_path):
        chat_url = json.dumps(_module_url("src/api/static/js/chat.js"))
        api_url = json.dumps(_module_url("src/api/static/js/api.js"))
        state_url = json.dumps(_module_url("src/api/static/js/state.js"))
        script = _DOM_STUB + r"""
const chatScroll = makeElement("chatScroll");
makeElement("chatInput", "textarea");
makeElement("sendBtn", "button");
makeElement("quickTiming", "button");
makeElement("quickTools", "button");
const quickClear = makeElement("quickClear", "button");
makeElement("appLayout");
const drawer = makeElement("reportDrawer", "aside");
drawer.hidden = true;
drawer.setAttribute("aria-hidden", "true");
makeElement("reportDrawerError");
makeElement("reportDrawerNav", "nav");
makeElement("reportDrawerContent");

const { api } = await import(__API_URL__);
const { store } = await import(__STATE_URL__);
const { initChat, sendMessage } = await import(__CHAT_URL__);
store.currentSessionId = "s1";
store.sessionDetails.s1 = {
  session_id: "s1", title: "用户手动标题", title_source: "manual", titleRevision: 4,
};
store.sessionMessages.s1 = [];
store.sessionArtifacts.s1 = [];
window.confirm = () => true;
api.clearSession = async () => ({});
let streamCalls = 0;
let handlers;
let resolveStream;
api.chatStream = (_message, _sessionId, streamHandlers) => {
  streamCalls += 1;
  handlers = streamHandlers;
  return new Promise((resolve) => { resolveStream = resolve; });
};
initChat();
const pending = sendMessage("清空前问题");
await Promise.resolve();
handlers.session_title({ session_id: "s1", title: "迟到自动标题" });
if (store.sessionDetails.s1.title !== "用户手动标题"
    || store.sessionDetails.s1.title_source !== "manual") {
  throw new Error("重载的 manual 标题被迟到 session_title 覆盖");
}
await quickClear.click();
if (document.getElementById("sendBtn").disabled) {
  throw new Error("clear 后发送按钮仍被旧 run 锁定");
}
let newHandlers;
let resolveNewStream;
api.chatStream = (_message, _sessionId, streamHandlers) => {
  streamCalls += 1;
  newHandlers = streamHandlers;
  return new Promise((resolve) => { resolveNewStream = resolve; });
};
const newPending = sendMessage("清空后新问题");
await Promise.resolve();
if (streamCalls !== 2 || !newHandlers || store.sessionRuns.s1.length !== 1) {
  throw new Error("clear 未释放 sending/发送按钮，无法立即发送新消息");
}
handlers.text({ content: "清空后迟到正文" });
handlers.artifact({ artifact: {
  artifact_id: "late", session_id: "s1", message_id: 99,
  payload: { symbol: "000001", name: "清空后迟到成果" },
}, persisted: true });
handlers.done({});
resolveStream();
await pending;
const html = descendants(chatScroll).map((node) => node.innerHTML).join("\n");
if (!store.sessionMessages.s1.some((message) => message.content === "清空后新问题")
    || store.sessionArtifacts.s1.length !== 0 || store.sessionRuns.s1.length !== 1
    || html.includes("清空后迟到正文")
    || chatScroll.textContent.includes("清空后迟到成果")) {
  throw new Error("clear 后旧 run 的迟到事件仍更新缓存或 UI");
}
newHandlers.done({});
resolveNewStream();
await newPending;

store.currentSessionId = "s2";
store.sessionMessages.s2 = [];
store.sessionArtifacts.s2 = [];
api.chatStream = async () => { throw new Error("断线"); };
await sendMessage("需要重试");
const oldRunId = store.sessionRuns.s2[0].id;
let retryHandlers;
let resolveRetry;
api.chatStream = (_message, _sessionId, streamHandlers) => {
  retryHandlers = streamHandlers;
  return new Promise((resolve) => { resolveRetry = resolve; });
};
await byClass(chatScroll, "btn-retry")[0].click();
if (store.sessionRuns.s2.length !== 1 || store.sessionRuns.s2[0].id === oldRunId) {
  throw new Error("retry 未完成并移除旧 interrupted run，切回会出现幽灵卡");
}
retryHandlers.done({});
resolveRetry();
""".replace("__CHAT_URL__", chat_url).replace("__API_URL__", api_url)
        script = script.replace("__STATE_URL__", state_url)
        _run_node(tmp_path, script)

    @pytest.mark.asyncio
    async def test_css_served(self, client):
        resp = await client.get("/css/app.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers.get("content-type", "")
