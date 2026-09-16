// 本机运行日志页：仅展示服务端已写入的日志，不提供写入或删除能力。
import { api } from "./api.js";
import { bus, store } from "./state.js";

const REFRESH_INTERVAL_MS = 5_000;
let selectedLevel = "";
let refreshTimer = null;

function content() {
  return document.getElementById("logsContent");
}

function levelOf(line) {
  const match = line.match(/\b(INFO|WARNING|ERROR)\b/);
  return match ? match[1].toLowerCase() : "default";
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
  root.replaceChildren();

  const heading = document.createElement("section");
  heading.className = "logs-heading";
  heading.innerHTML = `<div><h2>运行日志</h2><p>本机服务最近运行记录，每 5 秒自动刷新。</p></div><span class="logs-status ${available ? "available" : "missing"}">${available ? "本地日志可用" : "尚未生成日志"}</span>`;
  root.append(heading);

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
    list.scrollTop = list.scrollHeight;
  }
  panel.append(list);
  root.append(panel);
}

async function loadLogs() {
  try {
    const payload = await api.getRuntimeLogs(selectedLevel);
    render(payload.lines || [], Boolean(payload.available));
  } catch (error) {
    const root = content();
    if (root) root.innerHTML = `<section class="logs-panel"><p class="logs-empty">日志读取失败：${error.message}</p></section>`;
  }
}

export function initLogs() {
  bus.addEventListener("view-change", (event) => {
    if (event.detail.view === "logs") {
      loadLogs();
      startRefresh();
    } else {
      stopRefresh();
    }
  });
}
