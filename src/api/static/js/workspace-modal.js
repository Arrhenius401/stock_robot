// 窄屏覆盖层的背景焦点隔离：侧栏和研报抽屉共用，避免 Tab 落到被遮住的内容。
let activeModal = null;

function isNarrowScreen() {
  return window.matchMedia?.("(max-width: 760px)").matches ?? false;
}

function elementsFor(kind) {
  const topbar = document.querySelector(".topbar");
  const sidebar = document.getElementById("sidebar");
  const main = document.getElementById("workspaceMain");
  const drawer = document.getElementById("reportDrawer");
  if (kind === "drawer") return [topbar, sidebar, main];
  if (kind === "navigation") return [topbar, main, drawer];
  return [];
}

function setInert(elements, value) {
  for (const element of elements) {
    if (element) element.inert = value;
  }
}

function setDrawerModal(value) {
  const drawer = document.getElementById("reportDrawer");
  if (drawer) drawer.setAttribute("aria-modal", String(value));
}

function focusModal(kind) {
  const target = kind === "drawer"
    ? document.getElementById("reportDrawerClose")
    : document.getElementById("newSessionBtn");
  target?.focus();
}

function activateModal(kind) {
  setInert(elementsFor(kind), true);
  if (kind === "drawer") setDrawerModal(true);
  activeModal = kind;
  focusModal(kind);
}

function deactivateModal(kind) {
  setInert(elementsFor(kind), false);
  if (kind === "drawer") setDrawerModal(false);
  activeModal = null;
}

function visibleModalKind() {
  if (!isNarrowScreen()) return null;
  const layout = document.getElementById("appLayout");
  const drawer = document.getElementById("reportDrawer");
  if (layout?.classList.contains("drawer-open") && !drawer?.hidden) return "drawer";
  if (document.body?.classList.contains("workspace-nav-open")) return "navigation";
  return null;
}

export function openWorkspaceModal(kind) {
  if (!isNarrowScreen()) return false;
  if (activeModal && activeModal !== kind) deactivateModal(activeModal);
  if (activeModal !== kind) activateModal(kind);
  return true;
}

export function closeWorkspaceModal(kind) {
  if (activeModal !== kind) return;
  deactivateModal(kind);
}

export function syncWorkspaceModal() {
  const desired = visibleModalKind();
  if (activeModal === desired) return;
  if (activeModal) deactivateModal(activeModal);
  if (desired) activateModal(desired);
  else setDrawerModal(false);
}
