// 订阅推送管理视图：列表 + 创建 + 启停/删除/手动触发
import { el } from "./components.js";
import { api } from "./api.js";

const CHANNEL_LABELS = { email: "邮箱（全文）", wecom: "企业微信（摘要）" };

let reqSeq = 0;  // 请求令牌：快速连续操作时丢弃迟到响应

const content = () => document.getElementById("subsList");
const errorBox = () => document.getElementById("subsError");

export function initSubscriptions() {
  const btn = document.getElementById("subCreateBtn");
  btn.addEventListener("click", createSubscription);
  loadList();
}

function showError(msg) {
  errorBox().textContent = msg;
}

function clearError() {
  errorBox().textContent = "";
}

async function loadList() {
  const seq = ++reqSeq;
  const box = content();
  box.innerHTML = "";
  box.appendChild(el("div", "panel-title", "订阅列表"));
  let data;
  try {
    data = await api.listSubscriptions();
  } catch (err) {
    if (seq !== reqSeq) return;  // 已有更新请求，迟到错误不覆盖新视图
    box.appendChild(el("div", "subs-error", `加载失败: ${err.message}`));
    return;
  }
  if (seq !== reqSeq) return;  // 已有更新请求，迟到响应不覆盖新视图
  const subs = data.subscriptions || [];
  if (!subs.length) {
    box.appendChild(el("div", "dim", "暂无订阅，先在上方创建"));
    return;
  }
  for (const sub of subs) box.appendChild(subCard(sub));
}

function subCard(sub) {
  const card = el("div", "panel sub-card");
  const head = el("div", "sub-head");
  head.appendChild(el("span", "sub-name", sub.name));
  head.appendChild(el("span", "chip", CHANNEL_LABELS[sub.channel] || sub.channel));
  head.appendChild(el("span", "chip", sub.time));
  head.appendChild(el("span", "chip", sub.enabled ? "启用" : "停用"));
  const last = sub.last_run;
  if (last) {
    // ran_at 可能缺失，避免渲染 "Invalid Date"/"undefined"
    const t = new Date(last.ran_at * 1000);
    const timeText = isNaN(t.getTime()) ? "" : t.toLocaleString();
    head.appendChild(el("span", "chip",
        `上次执行 ${timeText ? `${timeText} · ` : ""}${last.ok}/${last.total} 成功`));
  }
  card.appendChild(head);
  // symbols 为 SubscriptionSymbol dict 列表：{symbol, kind, index_style}
  const label = (s) => s.kind === "index" ? `${s.symbol}（指数${s.index_style ? `/${s.index_style}` : ""}）`
    : s.kind === "stock" ? `${s.symbol}（股票）` : s.symbol;
  card.appendChild(el("div", "sub-symbols", sub.symbols.map(label).join("、")));
  const actions = el("div", "sub-actions");
  const toggleBtn = el("button", "btn-sm", sub.enabled ? "停用" : "启用");
  toggleBtn.addEventListener("click", async () => {
    clearError();
    try {
      await api.updateSubscription(sub.id, {
        name: sub.name, symbols: sub.symbols,
        channel: sub.channel, time: sub.time, enabled: !sub.enabled,
      });
      loadList();
    } catch (err) {
      showError(err.message);
    }
  });
  const runBtn = el("button", "btn-sm", "立即推送");
  runBtn.addEventListener("click", async () => {
    clearError();
    try {
      await api.triggerSubscription(sub.id);
      showError(`已触发推送 #${sub.id}，报告生成约需数分钟，完成后可在此查看结果`);
    } catch (err) {
      showError(err.message);
    }
  });
  const delBtn = el("button", "btn-sm danger", "删除");
  delBtn.addEventListener("click", async () => {
    clearError();
    if (!confirm(`确认删除订阅「${sub.name}」？`)) return;
    try {
      await api.deleteSubscription(sub.id);
      loadList();
    } catch (err) {
      showError(err.message);
    }
  });
  actions.appendChild(toggleBtn);
  actions.appendChild(runBtn);
  actions.appendChild(delBtn);
  card.appendChild(actions);
  return card;
}

async function createSubscription() {
  clearError();
  const name = document.getElementById("subName").value.trim();
  const symbols = document.getElementById("subSymbols").value.trim().split(/\s+/).filter(Boolean);
  const kind = document.getElementById("subKind").value;
  const indexStyle = document.getElementById("subIndexStyle").value || null;
  const channel = document.getElementById("subChannel").value;
  const time = document.getElementById("subTime").value;
  if (!name) return showError("请输入订阅名称");
  if (!symbols.length) return showError("请输入至少一个标的代码");
  // 订阅级类型作为所有标的默认值；kind=auto 时 index_style 不传
  const items = symbols.map((s) => kind === "auto"
    ? s : { symbol: s, kind, index_style: indexStyle });
  try {
    await api.createSubscription({ name, symbols: items, channel, time });
    document.getElementById("subName").value = "";
    document.getElementById("subSymbols").value = "";
    loadList();
  } catch (err) {
    showError(err.message);
  }
}
