// 本机运行日志页：仅展示服务端已写入的日志，不提供写入或删除能力。
import { api } from "./api.js";
import { bus, store } from "./state.js";

const REFRESH_INTERVAL_MS = 5_000;
let selectedLevel = "";
let refreshTimer = null;
let renderedSignature = null;

function content() {
  return document.getElementById("logsContent");
}

function levelOf(line) {
  const match = line.match(/\b(INFO|WARNING|ERROR)\b/);
  return match ? match[1].toLowerCase() : "default";
}

function isNearBottom(node) {
  return node.scrollHeight - node.scrollTop - node.clientHeight < 12;
}

function captureScrollState(node) {
  const listBounds = node.getBoundingClientRect();
  const anchor = Array.from(node.querySelectorAll(".logs-line"))
    .find((row) => row.getBoundingClientRect().bottom > listBounds.top);
  return {
    anchorOffset: anchor ? anchor.getBoundingClientRect().top - listBounds.top : null,
    anchorText: anchor?.textContent ?? null,
    scrollTop: node.scrollTop,
    wasAtBottom: isNearBottom(node),
  };
}

function restoreScrollPosition(list, previousListState, view, viewScrollTop) {
  window.requestAnimationFrame(() => {
    if (previousListState?.wasAtBottom) {
      list.scrollTop = list.scrollHeight;
    } else if (previousListState?.anchorText) {
      const anchor = Array.from(list.querySelectorAll(".logs-line"))
        .find((row) => row.textContent === previousListState.anchorText);
      if (anchor && previousListState.anchorOffset !== null) {
        const desiredTop = list.getBoundingClientRect().top + previousListState.anchorOffset;
        list.scrollTop += anchor.getBoundingClientRect().top - desiredTop;
      } else {
        list.scrollTop = previousListState.scrollTop;
      }
    }
    if (view) view.scrollTop = viewScrollTop;
  });
}

function stopRefresh() {
  if (refreshTimer !== null) window.clearInterval(refreshTimer);
  refreshTimer = null;
}

function startRefresh() {
  stopRefresh();
  refreshTimer = window.setInterval(() => {
    if (store.currentView === "logs") loadLogs();
  }, REFRESH_INTERVAL_MS);
}

function render(lines, available) {
  const root = content();
  if (!root) return;
  const previousList = root.querySelector(".logs-list");
  const previousListState = previousList ? captureScrollState(previousList) : null;
  const view = document.getElementById("view-logs");
  const viewScrollTop = view?.scrollTop ?? 0;
  root.replaceChildren();
  const status = document.getElementById("logsHeaderStatus");
  if (status) {
    status.textContent = available ? "本地日志可用" : "尚未生成日志";
    status.className = `logs-status ${available ? "available" : "missing"}`;
  }

  const panel = document.createElement("section");
  panel.className = "logs-panel";
  const toolbar = document.createElement("div");
  toolbar.className = "logs-toolbar";
  toolbar.innerHTML = `<label>等级 <select aria-label="日志等级"><option value="">全部</option><option value="INFO">info</option><option value="WARNING">warning</option><option value="ERROR">error</option></select></label><span>显示最近 ${lines.length} 条</span>`;
  const select = toolbar.querySelector("select");
  if (select) {
    select.value = selectedLevel;
    select.addEventListener("change", () => {
      selectedLevel = select.value;
      loadLogs();
    });
  }
  panel.append(toolbar);

  const list = document.createElement("div");
  list.className = "logs-list";
  if (lines.length === 0) {
    list.innerHTML = `<p class="logs-empty">${available ? "当前筛选条件下没有日志。" : "服务首次启动后，这里会显示本机运行记录。"}</p>`;
  } else {
    for (const line of lines) {
      const row = document.createElement("div");
      row.className = `logs-line ${levelOf(line)}`;
      row.textContent = line;
      list.append(row);
    }
  }
  panel.append(list);
  root.append(panel);
  restoreScrollPosition(list, previousListState, view, viewScrollTop);
}

async function loadLogs() {
  try {
    const payload = await api.getRuntimeLogs(selectedLevel);
    const lines = payload.lines || [];
    const signature = JSON.stringify([selectedLevel, Boolean(payload.available), lines]);
    if (signature === renderedSignature && content()?.childElementCount) return;
    renderedSignature = signature;
    render(lines, Boolean(payload.available));
  } catch (error) {
    renderedSignature = null;
    const status = document.getElementById("logsHeaderStatus");
    if (status) {
      status.textContent = "读取失败";
      status.className = "logs-status missing";
    }
    const root = content();
    if (root) root.innerHTML = `<section class="logs-panel"><p class="logs-empty">日志读取失败：${error.message}</p></section>`;
  }
}

export function initLogs() {
  bus.addEventListener("view-change", (event) => {
    if (event.detail.view === "logs") {
      renderedSignature = null;
      loadLogs();
      startRefresh();
    } else {
      stopRefresh();
    }
  });
}
