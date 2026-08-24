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

export function openWorkspaceModal(kind) {
  if (!isNarrowScreen()) return false;
  if (activeModal && activeModal !== kind) closeWorkspaceModal(activeModal);
  setInert(elementsFor(kind), true);
  if (kind === "drawer") setDrawerModal(true);
  activeModal = kind;
  return true;
}

export function closeWorkspaceModal(kind) {
  if (activeModal !== kind) return;
  setInert(elementsFor(kind), false);
  if (kind === "drawer") setDrawerModal(false);
  activeModal = null;
}

export function syncWorkspaceModal() {
  if (activeModal && !isNarrowScreen()) closeWorkspaceModal(activeModal);
}
