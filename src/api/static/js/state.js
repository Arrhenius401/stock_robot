// 全局状态与视图切换 — 组件模块唯一共享入口（避免与 app.js 循环导入）
export const store = {
  currentSessionId: null,
  currentView: "chat",
  reportCache: {},        // symbol -> analyze 报告 JSON
  sessionMessages: {},    // sid -> [{role, content}]
  sessionArtifacts: {},   // sid -> [artifact]
  currentArtifact: null,
  reportDrawerOpen: false,
};

export const bus = new EventTarget();

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
