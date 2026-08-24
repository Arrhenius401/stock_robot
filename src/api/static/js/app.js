// 入口：导航接线、工作台覆盖层与各视图初始化。
import { bus, switchView } from "./state.js";
import { initChat } from "./chat.js";
import { initReportView, openReport } from "./report.js";
import { initReportDrawer } from "./report-drawer.js";
import { closeReportDrawer } from "./report-drawer.js";
import { initIndexView } from "./indexview.js";
import { initSessions, initSessionStartup } from "./sessions.js";
import { initSubscriptions } from "./subscriptions.js";

const VIEW_TITLES = {
  chat: "会话研究",
  report: "个股报告",
  index: "指数分析",
  subscriptions: "订阅推送",
};

function isNarrowScreen() {
  return window.matchMedia?.("(max-width: 760px)").matches ?? false;
}

function updateViewTitle(view) {
  const title = document.getElementById("currentViewTitle");
  if (title) title.textContent = VIEW_TITLES[view] || "Stock Robot";
}

function closeMobileNavigation() {
  document.body.classList.remove("workspace-nav-open");
  const toggle = document.getElementById("mobileNavToggle");
  if (toggle) toggle.setAttribute("aria-expanded", "false");
}

function updateWorkspaceBackdrop() {
  const backdrop = document.getElementById("workspaceBackdrop");
  const layout = document.getElementById("appLayout");
  if (!backdrop || !layout) return;
  const needsBackdrop = isNarrowScreen()
    && (document.body.classList.contains("workspace-nav-open")
      || layout.classList.contains("drawer-open"));
  backdrop.hidden = !needsBackdrop;
}

function resetWorkspaceOverlays() {
  closeMobileNavigation();
  const layout = document.getElementById("appLayout");
  if (layout?.classList.contains("drawer-open")) closeReportDrawer();
  updateWorkspaceBackdrop();
}

function initWorkspaceShell() {
  const toggle = document.getElementById("mobileNavToggle");
  const backdrop = document.getElementById("workspaceBackdrop");
  const layout = document.getElementById("appLayout");
  const search = document.getElementById("globalStockSearch");
  const searchForm = search?.closest("form");

  toggle?.addEventListener("click", () => {
    const opening = !document.body.classList.contains("workspace-nav-open");
    document.body.classList.toggle("workspace-nav-open", opening);
    toggle.setAttribute("aria-expanded", String(opening));
    updateWorkspaceBackdrop();
  });
  backdrop?.addEventListener("click", () => resetWorkspaceOverlays());
  window.addEventListener("resize", updateWorkspaceBackdrop);
  if (layout && typeof MutationObserver !== "undefined") {
    new MutationObserver(updateWorkspaceBackdrop).observe(layout, {
      attributes: true,
      attributeFilter: ["class"],
    });
  }

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !document.body.classList.contains("workspace-nav-open")) return;
    event.preventDefault();
    closeMobileNavigation();
    updateWorkspaceBackdrop();
    toggle?.focus();
  });

  searchForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    const symbol = search.value.trim();
    if (!symbol) return;
    resetWorkspaceOverlays();
    updateViewTitle("report");
    openReport(symbol);
  });
}

function init() {
  document.querySelectorAll(".nav-item").forEach((node) => {
    node.addEventListener("click", () => {
      resetWorkspaceOverlays();
      switchView(node.dataset.view);
      updateViewTitle(node.dataset.view);
    });
  });
  // 全局连接状态条：网络层失败（conn-down）/恢复（conn-up）时切换显隐。
  const connStatus = document.getElementById("connStatus");
  bus.addEventListener("conn-down", () => { connStatus.hidden = false; });
  bus.addEventListener("conn-up", () => { connStatus.hidden = true; });
  initWorkspaceShell();
  initChat();
  initReportView();
  initReportDrawer();
  initIndexView();
  initSessions();
  initSubscriptions();
  initSessionStartup().catch((error) => console.error("会话初始化失败:", error));
}

init();
