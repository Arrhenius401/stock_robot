// 完整研报右侧抽屉：管理焦点、局部错误与请求竞态。
import { api } from "./api.js";
import { el } from "./components.js";
import { normalizeArtifactReport, renderStockReport } from "./report-renderer.js";
import { store } from "./state.js";

let initialized = false;
let requestSequence = 0;
let triggerElement = null;

const drawer = () => document.getElementById("reportDrawer");
const content = () => document.getElementById("reportDrawerContent");
const navigation = () => document.getElementById("reportDrawerNav");
const errorBox = () => document.getElementById("reportDrawerError");

function artifactId(artifact) {
  return artifact?.artifact_id ?? artifact?.id ?? null;
}

function cacheArtifact(artifact) {
  const sessionId = artifact?.session_id ?? store.currentSessionId;
  if (!sessionId) return;
  const artifacts = store.sessionArtifacts[sessionId] || [];
  const id = artifactId(artifact);
  const index = artifacts.findIndex((item) => artifactId(item) === id);
  if (index >= 0) artifacts[index] = artifact;
  else artifacts.push(artifact);
  store.sessionArtifacts[sessionId] = artifacts;
}

function clearError() {
  const box = errorBox();
  box.hidden = true;
  box.textContent = "";
}

function showError(message) {
  const box = errorBox();
  box.textContent = message;
  box.hidden = false;
}

function buildNavigation(article) {
  const nav = navigation();
  nav.replaceChildren();
  for (const reportSection of article.querySelectorAll("section[id]")) {
    const button = el("button", "report-drawer-nav-item",
      reportSection.dataset.sectionTitle || reportSection.id);
    button.type = "button";
    button.addEventListener("click", () => {
      reportSection.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    nav.appendChild(button);
  }
}

function renderDrawerContent(artifact) {
  const article = renderStockReport(normalizeArtifactReport(artifact), {
    sectionIdPrefix: "drawer-",
  });
  content().replaceChildren(article);
  buildNavigation(article);
}

function isCurrent(sequence, id) {
  return sequence === requestSequence
    && store.reportDrawerOpen
    && artifactId(store.currentArtifact) === id;
}

export async function openReportDrawer(artifact, trigger = null) {
  if (!artifact) return;
  const sequence = ++requestSequence;
  const id = artifactId(artifact);
  triggerElement = trigger || triggerElement || document.activeElement;
  store.currentArtifact = artifact;
  store.reportDrawerOpen = true;
  document.getElementById("appLayout").classList.add("drawer-open");
  const panel = drawer();
  panel.hidden = false;
  panel.setAttribute("aria-hidden", "false");
  clearError();
  document.getElementById("reportDrawerClose")?.focus();

  if (artifact.payload) {
    cacheArtifact(artifact);
    renderDrawerContent(artifact);
    return;
  }

  const sessionId = artifact.session_id ?? store.currentSessionId;
  if (!sessionId || !id) {
    showError("无法加载报告：缺少会话或成果标识");
    return;
  }

  try {
    const response = await api.getArtifact(sessionId, id);
    if (!isCurrent(sequence, id)) return;
    const loaded = response.artifact || response;
    store.currentArtifact = loaded;
    cacheArtifact(loaded);
    renderDrawerContent(loaded);
  } catch (error) {
    if (!isCurrent(sequence, id)) return;
    showError(`加载报告失败: ${error.message}`);
  }
}

function isVisibleFocusTarget(target) {
  if (!target || typeof target.focus !== "function" || target.isConnected === false) return false;
  if (target.closest?.("[hidden]")) return false;
  const view = target.closest?.(".view");
  return !view || view.classList.contains("active");
}

export function closeReportDrawer({ restoreFocus = true } = {}) {
  requestSequence += 1;
  store.currentArtifact = null;
  store.reportDrawerOpen = false;
  document.getElementById("appLayout").classList.remove("drawer-open");
  const panel = drawer();
  panel.hidden = true;
  panel.setAttribute("aria-hidden", "true");
  clearError();
  const target = triggerElement;
  triggerElement = null;
  if (restoreFocus && isVisibleFocusTarget(target)) {
    target.focus();
  } else if (panel.contains(document.activeElement)) {
    document.getElementById("workspaceMain")?.focus();
  }
}

async function refreshReport() {
  const artifact = store.currentArtifact;
  const id = artifactId(artifact);
  const symbol = artifact?.payload?.symbol ?? artifact?.payload?.code
    ?? artifact?.symbol ?? artifact?.code;
  if (!artifact || !symbol) {
    showError("无法刷新报告：缺少股票代码");
    return;
  }

  const sequence = ++requestSequence;
  clearError();
  try {
    const payload = await api.analyze(symbol);
    if (!isCurrent(sequence, id)) return;
    artifact.payload = payload;
    store.currentArtifact = artifact;
    cacheArtifact(artifact);
    renderDrawerContent(artifact);
  } catch (error) {
    if (!isCurrent(sequence, id)) return;
    showError(`刷新报告失败: ${error.message}`);
  }
}

export function initReportDrawer() {
  if (initialized) return;
  initialized = true;
  document.getElementById("reportDrawerClose").addEventListener("click", closeReportDrawer);
  document.getElementById("reportDrawerRefresh").addEventListener("click", refreshReport);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && store.reportDrawerOpen) {
      event.preventDefault();
      closeReportDrawer();
    }
  });
}
