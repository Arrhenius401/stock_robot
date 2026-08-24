// 全局状态与视图切换 — 组件模块唯一共享入口（避免与 app.js 循环导入）
export const store = {
  currentSessionId: null,
  currentView: "chat",
  reportCache: {},        // symbol -> analyze 报告 JSON
  sessionMessages: {},    // sid -> [{role, content}]
  sessionArtifacts: {},   // sid -> [artifact]
  sessionDetails: {},     // sid -> 会话标题、更新时间等列表元数据
  sessionRuns: {},        // sid -> 尚未结束的流式运行状态
  sessionDetailGenerations: {},
  sessionTombstones: {},
  sessionListRevision: 0,
  currentArtifact: null,
  reportDrawerOpen: false,
};

export const bus = new EventTarget();

export function markSessionListMutation() {
  store.sessionListRevision += 1;
  return store.sessionListRevision;
}

export function sessionDetailGeneration(sessionId) {
  return store.sessionDetailGenerations[sessionId] || 0;
}

export function invalidateSessionDetail(sessionId, deleted = false) {
  store.sessionDetailGenerations[sessionId] = sessionDetailGeneration(sessionId) + 1;
  if (deleted) store.sessionTombstones[sessionId] = true;
  return store.sessionDetailGenerations[sessionId];
}

export function reviveSession(sessionId) {
  delete store.sessionTombstones[sessionId];
  invalidateSessionDetail(sessionId);
}

export function switchView(name) {
  if (!document.getElementById(`view-${name}`)) return;
  store.currentView = name;
  document.querySelectorAll(".view").forEach((v) => {
    v.classList.toggle("active", v.id === `view-${name}`);
  });
  document.querySelectorAll(".nav-item").forEach((n) => {
    n.classList.toggle("on", n.dataset.view === name);
  });
}
