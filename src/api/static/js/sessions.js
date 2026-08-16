// 侧边栏会话列表：新建/切换/删除/清空 + 历史消息恢复
import { store, bus, switchView } from "./state.js";
import { api } from "./api.js";
import { renderMessageHistory, clearChatScroll } from "./chat.js";
import { el, esc } from "./components.js";

const listEl = () => document.getElementById("sessionList");

export async function refreshSessionList() {
  try {
    const data = await api.listSessions();
    renderList(data.sessions || []);
  } catch (err) {
    renderList([]);
    console.error("获取会话列表失败:", err);
  }
}

function renderList(sessions) {
  const box = listEl();
  box.innerHTML = "";
  if (!sessions.length) {
    box.appendChild(el("div", "sessions-empty", "暂无会话，发送第一条消息后自动创建"));
    return;
  }
  for (const s of sessions) {
    const item = el("div", `sess${s.session_id === store.currentSessionId ? " active" : ""}`);
    const title = el("span", "t", s.title || "新会话");
    title.title = esc(s.title || "新会话");
    item.appendChild(title);
    const del = el("span", "x", "✕");
    del.title = "删除会话";
    del.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      if (!window.confirm(`删除会话「${s.title || "新会话"}」？该操作不可恢复。`)) return;
      try {
        await api.deleteSession(s.session_id);
        if (store.currentSessionId === s.session_id) {
          store.currentSessionId = null;
          delete store.sessionMessages[s.session_id];
          clearChatScroll();
          await ensureSession();
        }
        await refreshSessionList();
      } catch (err) {
        window.alert(`删除失败: ${err.message}`);
      }
    });
    item.appendChild(del);
    item.addEventListener("click", () => selectSession(s.session_id));
    box.appendChild(item);
  }
}

async function selectSession(id) {
  if (id === store.currentSessionId) return;
  store.currentSessionId = id;
  switchView("chat");
  clearChatScroll();
  if (!store.sessionMessages[id]) {
    try {
      const data = await api.getMessages(id);
      if (store.currentSessionId !== id) return;  // 期间已切换到其他会话
      store.sessionMessages[id] = data.messages || [];
    } catch (err) {
      delete store.sessionMessages[id];  // 失败不缓存空数组，下次选择重试
      console.error("恢复会话消息失败:", err);
      if (store.currentSessionId !== id) return;  // 期间已切换，避免空视图覆盖新会话
    }
  }
  renderMessageHistory(store.sessionMessages[id] || []);  // 恢复失败时渲染空视图，缓存不落盘
  await refreshSessionList();
}

export async function ensureSession() {
  if (store.currentSessionId) return;
  const data = await api.createSession();
  store.currentSessionId = data.session_id;
  store.sessionMessages[data.session_id] = [];
}

export function initSessions() {
  document.getElementById("newSessionBtn").addEventListener("click", async () => {
    try {
      const data = await api.createSession();
      store.currentSessionId = data.session_id;
      store.sessionMessages[data.session_id] = [];
      switchView("chat");
      clearChatScroll();
      await refreshSessionList();
    } catch (err) {
      window.alert(`新建会话失败: ${err.message}`);
    }
  });
  const collapseBtn = document.getElementById("collapseBtn");
  collapseBtn.addEventListener("click", () => {
    const sidebar = document.getElementById("sidebar");
    sidebar.classList.toggle("collapsed");
    collapseBtn.textContent = sidebar.classList.contains("collapsed") ? "»" : "«";
  });
  bus.addEventListener("chat-done", refreshSessionList);
}

export async function initSessionStartup() {
  await refreshSessionList();
  try {
    const data = await api.listSessions();
    const sessions = data.sessions || [];
    if (sessions.length) {
      await selectSession(sessions[0].session_id);
    } else {
      await ensureSession();
    }
  } catch {
    await ensureSession();
  }
}
