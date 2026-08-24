// 侧边栏会话列表：历史恢复、内联重命名与会话级缓存隔离。
import {
  store, bus, switchView, invalidateSessionDetail, markSessionListMutation,
  reviveSession, sessionDetailGeneration,
} from "./state.js";
import { api } from "./api.js";
import { renderMessageHistory, clearChatScroll } from "./chat.js";
import { closeReportDrawer } from "./report-drawer.js";
import { el } from "./components.js";

const listEl = () => document.getElementById("sessionList");
let initialized = false;
let selectionSequence = 0;
let listRequestSequence = 0;

function sessionValues() {
  return Object.values(store.sessionDetails).sort(
    (left, right) => Number(right.updated_at || 0) - Number(left.updated_at || 0),
  );
}

function relativeTime(value) {
  const raw = Number(value);
  if (!Number.isFinite(raw) || raw <= 0) return "";
  const milliseconds = raw < 1e12 ? raw * 1000 : raw;
  const seconds = Math.max(0, Math.floor((Date.now() - milliseconds) / 1000));
  if (seconds < 60) return "刚刚";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  if (seconds < 604800) return `${Math.floor(seconds / 86400)} 天前`;
  return new Date(milliseconds).toLocaleDateString("zh-CN");
}

function targetSymbols(session) {
  const source = `${session.title || ""} ${session.symbol || ""}`;
  const values = [];
  for (const match of source.matchAll(/(?:^|\D)(\d{6})(?!\d)/g)) {
    if (!values.includes(match[1])) values.push(match[1]);
  }
  return values.slice(0, 2);
}

function clearSessionCache(sessionId) {
  delete store.sessionMessages[sessionId];
  delete store.sessionArtifacts[sessionId];
  delete store.sessionRuns[sessionId];
  delete store.sessionDetails[sessionId];
}

function renderCachedSessions() {
  renderSessionList(sessionValues(), false);
}

function beginRename(item, session) {
  const original = session.title || "新会话";
  item.replaceChildren();
  const input = el("input", "sess-rename-input");
  input.value = original;
  input.setAttribute("aria-label", "重命名会话");
  const error = el("div", "sess-rename-error");
  let saving = false;

  const cancel = () => {
    if (!saving) renderCachedSessions();
  };
  const save = async () => {
    const title = input.value.trim();
    if (!title) {
      input.value = original;
      error.textContent = "标题不能为空";
      return;
    }
    saving = true;
    input.disabled = true;
    error.textContent = "";
    try {
      const updated = await api.renameSession(session.session_id, title);
      markSessionListMutation();
      store.sessionDetails[session.session_id] = {
        ...session,
        ...updated,
        session_id: session.session_id,
        title,
        updated_at: updated.updated_at ?? Date.now() / 1000,
      };
      renderCachedSessions();
    } catch (renameError) {
      saving = false;
      input.disabled = false;
      input.value = original;
      error.textContent = `重命名失败: ${renameError.message}`;
      input.focus();
    }
  };

  input.addEventListener("keydown", async (event) => {
    if (event.key === "Enter" && !event.isComposing) {
      event.preventDefault();
      await save();
    } else if (event.key === "Escape") {
      event.preventDefault();
      cancel();
    }
  });
  input.addEventListener("blur", cancel);
  item.appendChild(input);
  item.appendChild(error);
  input.focus();
}

async function deleteSession(session) {
  if (!window.confirm(`删除会话「${session.title || "新会话"}」？该操作不可恢复。`)) return;
  try {
    await api.deleteSession(session.session_id);
    const wasCurrent = store.currentSessionId === session.session_id;
    markSessionListMutation();
    invalidateSessionDetail(session.session_id, true);
    clearSessionCache(session.session_id);
    if (wasCurrent) {
      closeReportDrawer();
      store.currentSessionId = null;
      clearChatScroll();
      await ensureSession();
      renderMessageHistory([], []);
    }
    await refreshSessionList();
  } catch (error) {
    window.alert(`删除失败: ${error.message}`);
  }
}

function sessionItem(session) {
  const active = session.session_id === store.currentSessionId;
  const item = el("div", `sess${active ? " active" : ""}`);
  item.dataset.sessionId = session.session_id;

  const primary = el("button", "sess-main");
  primary.type = "button";
  primary.title = session.title || "新会话";
  primary.setAttribute("aria-current", active ? "true" : "false");
  primary.appendChild(el("span", "t", session.title || "新会话"));
  const meta = el("span", "sess-meta");
  for (const symbol of targetSymbols(session)) {
    meta.appendChild(el("span", "sess-target", symbol));
  }
  const time = relativeTime(session.updated_at);
  if (time) meta.appendChild(el("span", "sess-time", time));
  primary.appendChild(meta);
  primary.addEventListener("click", () => selectSession(session.session_id));
  item.appendChild(primary);

  const toggle = el("button", "sess-menu-toggle", "⋯");
  toggle.type = "button";
  toggle.title = "会话操作";
  toggle.setAttribute("aria-label", `打开「${session.title || "新会话"}」菜单`);
  toggle.setAttribute("aria-expanded", "false");
  const menu = el("div", "sess-menu");
  menu.hidden = true;
  const rename = el("button", "sess-menu-action", "重命名");
  rename.type = "button";
  rename.addEventListener("click", (event) => {
    event.stopPropagation();
    beginRename(item, session);
  });
  const remove = el("button", "sess-menu-action", "删除");
  remove.type = "button";
  remove.addEventListener("click", async (event) => {
    event.stopPropagation();
    await deleteSession(session);
  });
  menu.appendChild(rename);
  menu.appendChild(remove);
  toggle.addEventListener("click", (event) => {
    event.stopPropagation();
    menu.hidden = !menu.hidden;
    toggle.setAttribute("aria-expanded", menu.hidden ? "false" : "true");
  });
  item.appendChild(toggle);
  item.appendChild(menu);
  return item;
}

export function renderSessionList(sessions, updateCache = true) {
  const visibleSessions = sessions.filter(
    (session) => !store.sessionTombstones[session.session_id],
  );
  if (updateCache) {
    for (const session of visibleSessions) {
      store.sessionDetails[session.session_id] = {
        ...(store.sessionDetails[session.session_id] || {}),
        ...session,
      };
    }
  }
  const box = listEl();
  box.innerHTML = "";
  if (!visibleSessions.length) {
    box.appendChild(el("div", "sessions-empty", "暂无会话，发送第一条消息后自动创建"));
    return;
  }
  for (const session of visibleSessions) box.appendChild(sessionItem(session));
}

export async function refreshSessionList() {
  const request = ++listRequestSequence;
  const revision = store.sessionListRevision;
  try {
    const data = await api.listSessions();
    if (request !== listRequestSequence || revision !== store.sessionListRevision) return;
    const sessions = (data.sessions || []).filter(
      (session) => !store.sessionTombstones[session.session_id],
    );
    const activeIds = new Set(sessions.map((session) => session.session_id));
    for (const sessionId of Object.keys(store.sessionDetails)) {
      if (!activeIds.has(sessionId)) delete store.sessionDetails[sessionId];
    }
    renderSessionList(sessions);
  } catch (error) {
    if (request !== listRequestSequence || revision !== store.sessionListRevision) return;
    renderSessionList([], false);
    console.error("获取会话列表失败:", error);
  }
}

export async function selectSession(id) {
  if (id === store.currentSessionId) return;
  const sequence = ++selectionSequence;
  closeReportDrawer();
  store.currentSessionId = id;
  switchView("chat");
  clearChatScroll();
  const hasMessages = Object.hasOwn(store.sessionMessages, id);
  const hasArtifacts = Object.hasOwn(store.sessionArtifacts, id);
  if (!hasMessages || !hasArtifacts) {
    const generation = sessionDetailGeneration(id);
    try {
      const data = await api.getMessages(id);
      const valid = generation === sessionDetailGeneration(id)
        && !store.sessionTombstones[id];
      if (valid && !hasMessages && !Object.hasOwn(store.sessionMessages, id)) {
        store.sessionMessages[id] = data.messages || [];
      }
      if (valid && !hasArtifacts && !Object.hasOwn(store.sessionArtifacts, id)) {
        store.sessionArtifacts[id] = data.artifacts || [];
      }
    } catch (error) {
      if (generation === sessionDetailGeneration(id) && !store.sessionTombstones[id]) {
        if (!hasMessages) delete store.sessionMessages[id];
        if (!hasArtifacts) delete store.sessionArtifacts[id];
      }
      console.error("恢复会话详情失败:", error);
      if (store.currentSessionId !== id || sequence !== selectionSequence) return;
    }
  }
  if (store.currentSessionId !== id || sequence !== selectionSequence) return;
  // 加载期间也可能从其他入口打开抽屉；提交新会话视图前再次关闭。
  closeReportDrawer();
  renderMessageHistory(store.sessionMessages[id] || [], store.sessionArtifacts[id] || []);
  await refreshSessionList();
}

export async function ensureSession() {
  if (store.currentSessionId) return;
  const data = await api.createSession();
  markSessionListMutation();
  reviveSession(data.session_id);
  store.currentSessionId = data.session_id;
  store.sessionMessages[data.session_id] = [];
  store.sessionArtifacts[data.session_id] = [];
  store.sessionDetails[data.session_id] = {
    ...data,
    session_id: data.session_id,
    title: data.title || "新会话",
    updated_at: data.updated_at ?? Date.now() / 1000,
  };
}

export function initSessions() {
  if (initialized) return;
  initialized = true;
  document.getElementById("newSessionBtn").addEventListener("click", async () => {
    try {
      const data = await api.createSession();
      markSessionListMutation();
      reviveSession(data.session_id);
      closeReportDrawer();
      store.currentSessionId = data.session_id;
      store.sessionMessages[data.session_id] = [];
      store.sessionArtifacts[data.session_id] = [];
      store.sessionDetails[data.session_id] = {
        ...data,
        session_id: data.session_id,
        title: data.title || "新会话",
        updated_at: data.updated_at ?? Date.now() / 1000,
      };
      switchView("chat");
      renderMessageHistory([], []);
      await refreshSessionList();
    } catch (error) {
      window.alert(`新建会话失败: ${error.message}`);
    }
  });
  const collapseBtn = document.getElementById("collapseBtn");
  collapseBtn.addEventListener("click", () => {
    const sidebar = document.getElementById("sidebar");
    sidebar.classList.toggle("collapsed");
    collapseBtn.textContent = sidebar.classList.contains("collapsed") ? "»" : "«";
  });
  bus.addEventListener("chat-done", refreshSessionList);
  bus.addEventListener("session-title", renderCachedSessions);
}

export async function initSessionStartup() {
  await refreshSessionList();
  try {
    const sessions = sessionValues();
    if (sessions.length) {
      await selectSession(sessions[0].session_id);
    } else {
      await ensureSession();
      renderMessageHistory([], []);
    }
  } catch {
    await ensureSession();
    renderMessageHistory([], []);
  }
}
