// 入口：导航接线 + 各视图初始化
import { switchView } from "./state.js";
import { initChat } from "./chat.js";
import { initReportView } from "./report.js";
import { initIndexView } from "./indexview.js";
import { initSessions, initSessionStartup } from "./sessions.js";

function init() {
  document.querySelectorAll(".nav-item").forEach((n) => {
    n.addEventListener("click", () => switchView(n.dataset.view));
  });
  initChat();
  initReportView();
  initIndexView();
  initSessions();
  initSessionStartup().catch((e) => console.error("会话初始化失败:", e));
}

init();
