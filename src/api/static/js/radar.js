// 配置雷达：只消费完成态快照，刷新任务通过 API 轮询。
import { api } from "./api.js";
import { el, errorCard, skeleton } from "./components.js";

let selectedUniverse = null;

function badge(status) {
  return el("span", `radar-badge ${status}`, status === "fresh" ? "最新" : status === "stale" ? "已过期" : "失败");
}

function renderSnapshot(root, snapshot) {
  const header = el("div", "radar-head");
  const title = el("div");
  title.append(el("h2", "", "ETF 配置雷达"), el("p", "radar-meta", `数据截至 ${snapshot.as_of_date} · 池版本 v${snapshot.universe_version}`));
  const refresh = el("button", "radar-refresh", "手动更新");
  refresh.addEventListener("click", () => runRefresh(root, refresh));
  header.append(title, refresh);
  root.replaceChildren(header, el("p", "radar-note", snapshot.research_notice || "研究评分，不构成投资建议。"));
  const groups = new Map();
  for (const item of snapshot.items) groups.set(item.category, [...(groups.get(item.category) || []), item]);
  for (const [category, items] of groups) {
    const section = el("section", "radar-section");
    section.appendChild(el("h3", "", category));
    const table = document.createElement("table");
    table.innerHTML = "<thead><tr><th>排名</th><th>标的</th><th>评分</th><th>等级</th><th>数据状态</th></tr></thead>";
    const body = document.createElement("tbody");
    for (const item of items) {
      const row = document.createElement("tr");
      for (const text of [item.rank || "-", `${item.name} · ${item.symbol}`, item.score == null ? "-" : item.score.toFixed(1), item.grade]) row.appendChild(el("td", "", text));
      const cell = document.createElement("td"); cell.appendChild(badge(item.status)); row.appendChild(cell);
      row.className = "radar-row"; row.tabIndex = 0;
      row.addEventListener("click", () => showDetail(root, item, snapshot));
      row.addEventListener("keydown", (event) => { if (event.key === "Enter") showDetail(root, item, snapshot); });
      body.appendChild(row);
    }
    table.appendChild(body); section.appendChild(table); root.appendChild(section);
  }
}

async function showDetail(root, item, snapshot) {
  root.querySelector(".radar-detail")?.remove();
  const panel = el("aside", "radar-detail");
  const close = el("button", "radar-detail-close", "关闭"); close.addEventListener("click", () => panel.remove());
  panel.append(close, el("h3", "", `${item.name} · ${item.symbol}`), el("p", "radar-meta", `评分 ${item.score == null ? "-" : Number(item.score).toFixed(1)} · 类别排名 ${item.rank ?? "-"} · ${snapshot.as_of_date}`));
  panel.appendChild(el("h4", "", "评分依据"));
  const factors = document.createElement("dl");
  const labels = {
    trend: "趋势原始值", drawdown: "回撤控制原始值", volatility: "波动控制原始值", liquidity: "流动性原始值",
    trend_percentile: "趋势类别分位", drawdown_percentile: "回撤类别分位", volatility_percentile: "波动类别分位", liquidity_percentile: "流动性类别分位",
    trend_contribution: "趋势分数贡献", drawdown_contribution: "回撤分数贡献", volatility_contribution: "波动分数贡献", liquidity_contribution: "流动性分数贡献",
  };
  for (const [key, value] of Object.entries(item.factors || {})) {
    factors.append(el("dt", "", labels[key] || key), el("dd", "", formatFactor(key, value)));
  }
  panel.appendChild(factors);
  panel.appendChild(el("h4", "", "策略回测"));
  const backtest = el("div", "radar-backtest", "正在读取该标的池的最近一次策略回测…");
  panel.appendChild(backtest);
  root.appendChild(panel);
  try {
    const result = await api.latestRadarBacktest(snapshot.universe_id);
    if (!panel.isConnected) return;
    renderBacktest(backtest, result);
  } catch (error) {
    if (!panel.isConnected) return;
    backtest.replaceChildren(el("p", "radar-note", "暂无已生成回测。可通过 radar backtest 命令生成；页面不会自动发起回测。"));
  }
}

function renderBacktest(root, result) {
  const metrics = document.createElement("div");
  metrics.className = "radar-backtest-metrics";
  for (const key of ["累计收益", "年化收益", "最大回撤", "夏普比率"]) {
    const value = result.summary?.[key];
    const percent = key !== "夏普比率";
    metrics.append(el("div", "radar-backtest-metric", `${key}\n${value == null ? "-" : percent ? `${(Number(value) * 100).toFixed(1)}%` : Number(value).toFixed(2)}`));
  }
  root.replaceChildren(metrics, el("p", "radar-meta", `${result.report.strategy_id || "策略"} · ${result.report.start_date || "-"} 至 ${result.report.end_date || "-"}`));
  const values = (result.equity_curve?.rows || []).map((row) => Number(row.equity)).filter(Number.isFinite);
  if (values.length > 1) root.appendChild(equityCurve(values));
}

function equityCurve(values) {
  const width = 280, height = 92, pad = 8;
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  const points = values.map((value, index) => `${pad + (index * (width - pad * 2)) / (values.length - 1)},${height - pad - ((value - min) * (height - pad * 2)) / span}`).join(" ");
  const chart = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  chart.setAttribute("viewBox", `0 0 ${width} ${height}`); chart.classList.add("radar-equity-curve");
  chart.innerHTML = `<polyline points="${points}"/>`;
  return chart;
}

function formatFactor(key, value) {
  if (value == null) return "数据不足";
  const number = Number(value);
  if (key.endsWith("_percentile")) return `${(number * 100).toFixed(0)}%`;
  if (key.endsWith("_contribution")) return `${number.toFixed(1)} 分`;
  if (key === "liquidity") return `${(number / 1e8).toFixed(2)} 亿元`;
  if (key === "trend" || key === "drawdown" || key === "volatility") return `${(number * 100).toFixed(2)}%`;
  return number.toFixed(4);
}

async function load(root) {
  root.replaceChildren(skeleton(7));
  try {
    const universes = await api.listRadarUniverses();
    selectedUniverse ||= universes[0]?.id;
    const selector = document.createElement("select");
    for (const universe of universes) selector.add(new Option(universe.name, universe.id, false, universe.id === selectedUniverse));
    selector.addEventListener("change", () => { selectedUniverse = selector.value; load(root); });
    const snapshot = await api.latestRadarSnapshot(selectedUniverse);
    renderSnapshot(root, snapshot); root.querySelector(".radar-head")?.prepend(selector);
  } catch (error) { root.replaceChildren(errorCard(error.message, () => load(root))); }
}

async function runRefresh(root, button) {
  button.disabled = true; button.textContent = "更新中…";
  try {
    const task = await api.refreshRadar(selectedUniverse);
    for (;;) {
      await new Promise((resolve) => setTimeout(resolve, 1200));
      const status = await api.radarRefreshStatus(task.task_id);
      if (status.status === "completed") { await load(root); return; }
      if (status.status === "failed") throw new Error(status.error || "更新失败");
    }
  } catch (error) { button.disabled = false; button.textContent = "重新更新"; alert(error.message); }
}

export function initRadar() {
  const root = document.getElementById("radarContent");
  document.querySelector('[data-view="radar"]')?.addEventListener("click", () => load(root));
}
