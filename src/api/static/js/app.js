// 入口：导航接线 + 各视图初始化
import { bus, switchView } from "./state.js";
import { initChat } from "./chat.js";
import { initReportView } from "./report.js";
import { initReportDrawer } from "./report-drawer.js";
import { initIndexView } from "./indexview.js";
import { initSessions, initSessionStartup } from "./sessions.js";
import { initSubscriptions } from "./subscriptions.js";

function init() {
  document.querySelectorAll(".nav-item").forEach((n) => {
    n.addEventListener("click", () => switchView(n.dataset.view));
  });
  // 全局连接状态条：网络层失败（conn-down）/恢复（conn-up）时切换显隐
  const connStatus = document.getElementById("connStatus");
  bus.addEventListener("conn-down", () => { connStatus.hidden = false; });
  bus.addEventListener("conn-up", () => { connStatus.hidden = true; });
  initChat();
  initReportView();
  initReportDrawer();
  initIndexView();
  initSessions();
  initSubscriptions();
  initSessionStartup().catch((e) => console.error("会话初始化失败:", e));
}

init();
