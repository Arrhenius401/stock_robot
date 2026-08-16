// DOM 工具与共享组件 — 供 chat/report/indexview/sessions 复用

export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

export function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function statusBadge(status) {
  const map = { ok: ["正常", "ok"], partial: ["部分数据", "part"], unavailable: ["不可用", "na"] };
  const [text, cls] = map[status] || [String(status || "未知"), "na"];
  return el("span", `badge ${cls}`, text);
}

export function tagChip(text, kind) {
  return el("span", `tag ${kind}`, text);
}

export function priceBar(pct) {
  const wrap = el("div", "bar");
  const fill = el("i");
  const num = Number(pct);
  fill.style.width = `${Number.isFinite(num) ? Math.max(0, Math.min(100, num)) : 0}%`;
  wrap.appendChild(fill);
  return wrap;
}

export function kv(label, value) {
  const box = el("div");
  box.appendChild(el("div", "k", label));
  box.appendChild(el("div", "v", value));
  return box;
}

export function errorCard(message, onRetry) {
  const card = el("div", "error-card");
  card.appendChild(el("div", "error-title", "出错了"));
  card.appendChild(el("div", "error-msg", message));
  if (onRetry) {
    const btn = el("button", "btn-retry", "重试");
    btn.addEventListener("click", onRetry);
    card.appendChild(btn);
  }
  return card;
}

export function skeleton(lines = 6) {
  const box = el("div", "skeleton");
  for (let i = 0; i < lines; i += 1) {
    const row = el("div", "sk-line");
    row.style.width = `${60 + ((i * 17) % 40)}%`;
    box.appendChild(row);
  }
  return box;
}
