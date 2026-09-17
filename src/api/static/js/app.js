// 应用主入口：导航接线、工作台覆盖层与各视图初始化。
import { bus, switchView } from "./state.js";
import { initChat } from "./chat.js";
import { initReportView, openReport } from "./report.js?v=20260916-report-readability";
import { initReportDrawer } from "./report-drawer.js";
import { closeReportDrawer } from "./report-drawer.js";
import { closeWorkspaceModal, openWorkspaceModal, syncWorkspaceModal } from "./workspace-modal.js";
import { initIndexView } from "./indexview.js";
import { initReportLibrary } from "./report-library.js?v=20260916-report-readability";
import { initSessions, initSessionStartup } from "./sessions.js";
import { initSubscriptions } from "./subscriptions.js";
import { initSettings } from "./settings.js";
import { initLogs } from "./logs.js?v=20260917-log-sidebar-resize-fix";

const SIDEBAR_WIDTH_KEY = "stockRobot.sidebarWidth";
const SIDEBAR_MIN_WIDTH = 210;
const SIDEBAR_MAX_WIDTH = 420;

const VIEW_TITLES = {
  chat: "会话研究",
  report: "个股报告",
  index: "指数分析",
  radar: "配置雷达",
  "report-library": "报告库",
  subscriptions: "订阅推送",
  logs: "日志",
  settings: "配置",
};

function isNarrowScreen() {
  return window.matchMedia?.("(max-width: 760px)").matches ?? false;
}

function updateViewTitle(view) {
  const title = document.getElementById("currentViewTitle");
  if (title) title.textContent = VIEW_TITLES[view] || "Stock Robot";
}

function mainContent() {
  return document.getElementById("workspaceMain");
}

function focusVisible(target) {
  if (!target || typeof target.focus !== "function" || target.isConnected === false) return false;
  if (target.closest?.("[hidden]")) return false;
  const view = target.closest?.(".view");
  if (view && !view.classList.contains("active")) return false;
  target.focus();
  return true;
}

function closeMobileNavigation(focusTarget = null) {
  const sidebar = document.getElementById("sidebar");
  const activeInSidebar = sidebar?.contains(document.activeElement);
  document.body.classList.remove("workspace-nav-open");
  closeWorkspaceModal("navigation");
  const toggle = document.getElementById("mobileNavToggle");
  if (toggle) toggle.setAttribute("aria-expanded", "false");
  if (activeInSidebar && !focusVisible(focusTarget)) {
    focusVisible(mainContent()) || focusVisible(toggle);
  }
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

function resetWorkspaceOverlays({ focusTarget = null } = {}) {
  closeMobileNavigation(focusTarget);
  const layout = document.getElementById("appLayout");
  if (layout?.classList.contains("drawer-open")) closeReportDrawer({ restoreFocus: false });
  updateWorkspaceBackdrop();
  focusVisible(focusTarget);
}

function initWorkspaceShell() {
  const toggle = document.getElementById("mobileNavToggle");
  const backdrop = document.getElementById("workspaceBackdrop");
  const layout = document.getElementById("appLayout");
  const search = document.getElementById("globalStockSearch");
  const searchForm = search?.closest("form");
  const sidebar = document.getElementById("sidebar");
  const sidebarResizer = document.getElementById("sidebarResizer");
  let resizePointerId = null;
  let appliedSidebarWidth = null;

  function sidebarWidthFromStorage() {
    try {
      const width = Number(window.sessionStorage?.getItem(SIDEBAR_WIDTH_KEY));
      return Number.isFinite(width) ? width : null;
    } catch (_error) {
      return null;
    }
  }

  function applySidebarWidth(width) {
    if (!layout) return;
    const bounded = Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, width));
    appliedSidebarWidth = bounded;
    layout.style.setProperty("--sidebar-width", `${bounded}px`);
  }

  function finishSidebarResize() {
    if (resizePointerId === null) return;
    resizePointerId = null;
    document.body.classList.remove("sidebar-resizing");
    layout?.classList.remove("sidebar-resizing");
    try {
      if (appliedSidebarWidth !== null) {
        window.sessionStorage?.setItem(SIDEBAR_WIDTH_KEY, String(Math.round(appliedSidebarWidth)));
      }
    } catch (_error) {
      // 浏览器禁用会话存储时仅放弃记忆，不影响本次拖拽。
    }
  }

  const savedSidebarWidth = sidebarWidthFromStorage();
  if (savedSidebarWidth !== null) applySidebarWidth(savedSidebarWidth);
  sidebarResizer?.addEventListener("pointerdown", (event) => {
    if (!layout || !sidebar || sidebar.classList.contains("collapsed") || isNarrowScreen()) return;
    event.preventDefault();
    resizePointerId = event.pointerId;
    sidebarResizer.setPointerCapture?.(event.pointerId);
    document.body.classList.add("sidebar-resizing");
    layout.classList.add("sidebar-resizing");
  });
  document.addEventListener("pointermove", (event) => {
    if (resizePointerId !== event.pointerId || !layout) return;
    applySidebarWidth(event.clientX - layout.getBoundingClientRect().left);
  });
  document.addEventListener("pointerup", (event) => {
    if (resizePointerId === event.pointerId) finishSidebarResize();
  });
  document.addEventListener("pointercancel", (event) => {
    if (resizePointerId === event.pointerId) finishSidebarResize();
  });

  toggle?.addEventListener("click", () => {
    const opening = !document.body.classList.contains("workspace-nav-open");
    document.body.classList.toggle("workspace-nav-open", opening);
    toggle.setAttribute("aria-expanded", String(opening));
    if (opening) {
      openWorkspaceModal("navigation");
      document.getElementById("newSessionBtn")?.focus();
    } else {
      closeWorkspaceModal("navigation");
    }
    updateWorkspaceBackdrop();
  });
  backdrop?.addEventListener("click", () => resetWorkspaceOverlays());
  window.addEventListener("resize", updateWorkspaceBackdrop);
  window.addEventListener("resize", syncWorkspaceModal);
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
    resetWorkspaceOverlays({ focusTarget: search });
    openReport(symbol, "globalStockSearch");
  });
}

function init() {
  document.querySelectorAll(".nav-item").forEach((node) => {
    node.addEventListener("click", () => {
      const focusTarget = isNarrowScreen() ? mainContent() : node;
      resetWorkspaceOverlays({ focusTarget });
      switchView(node.dataset.view);
    });
  });
  // 全局连接状态条：网络层失败（conn-down）/恢复（conn-up）时切换显隐。
  const connStatus = document.getElementById("connStatus");
  bus.addEventListener("conn-down", () => { connStatus.hidden = false; });
  bus.addEventListener("conn-up", () => { connStatus.hidden = true; });
  bus.addEventListener("view-change", (event) => {
    updateViewTitle(event.detail.view);
    resetWorkspaceOverlays({ focusTarget: mainContent() });
  });
  initWorkspaceShell();
  initChat();
  initReportView();
  initReportDrawer();
  initIndexView();
  initReportLibrary();
  initSessions();
  initSubscriptions();
  initLogs();
  initSettings();
  initSessionStartup()
    .catch((error) => console.error("会话初始化失败:", error))
    .finally(() => import("./allocation-view.js?v=20260916-radar-data-date")
      .then(({ initRadar: initializeRadar }) => initializeRadar())
      .catch((error) => {
        console.error("配置雷达初始化失败:", error);
        const radarContent = document.getElementById("radarContent");
        if (radarContent) radarContent.textContent = "配置雷达暂时无法初始化，请刷新后重试。";
      }));
}

init();
