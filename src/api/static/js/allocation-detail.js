// 配置模块完整视图：保留为后续兼容层恢复回测与单标的历史表现。
import { api } from "./api.js?v=20260909-instrument-performance";
import { el, errorCard, skeleton } from "./components.js";
import { switchView } from "./state.js";

const BENCHMARKS = [["money_fund", "货币基金"], ["csi_300", "沪深 300"], ["csi_all_bond", "中证全债"]];
const BACKTEST_PERIODS = [[3, "近3月"], [6, "近半年"], [12, "近1年"], [24, "近2年"], [36, "近3年"], [60, "近5年"], ["since", "成立以来"]];
const state = { selectedUniverse: null, snapshot: null, universes: [], detail: null, listScrollTop: 0, backtest: null, performance: null, benchmark: "csi_300", backtestMonths: 12, performanceMonths: 12 };

function root() { return document.getElementById("radarContent"); }
function badge(status) { return el("span", `radar-badge ${status}`, status === "fresh" ? "最新" : status === "stale" ? "已过期" : "失败"); }
function percent(value) { const number = Number(value); return Number.isFinite(number) ? `${(number * 100).toFixed(1)}%` : "—"; }
function metric(label, value) { const node = el("div", "radar-detail-metric"); node.append(el("span", "k", label), el("strong", "v", value)); return node; }

function periodRange(months) {
  if (months === "since") return { start: null, end: state.snapshot.as_of_date };
  const end = new Date(`${state.snapshot.as_of_date}T12:00:00`); const start = new Date(end);
  start.setDate(1); start.setMonth(start.getMonth() - months + 1);
  const format = (value) => `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
  return { start: format(start), end: state.snapshot.as_of_date };
}

function backtestRange() { return periodRange(state.backtestMonths); }
function performanceRange() { return periodRange(state.performanceMonths); }

function maxDrawdown(values) { let peak = values[0] || 1; return Math.min(...values.map((value) => { peak = Math.max(peak, value); return value / peak - 1; })); }

function sliceBacktest(result, range) {
  if (!range.start) return result;
  const rows = ((result.equity_curve && result.equity_curve.rows) || []).filter((row) => row.date >= range.start && row.date <= range.end);
  if (!rows.length) return result;
  const strategyBase = Number(rows[0].strategy_equity != null ? rows[0].strategy_equity : rows[0].equity);
  const normalized = rows.map((row) => ({ ...row, strategy_equity: Number(row.strategy_equity != null ? row.strategy_equity : row.equity) / strategyBase }));
  BENCHMARKS.forEach(([id]) => { const base = Number(rows[0][`${id}_equity`]); normalized.forEach((row, index) => { row[`${id}_equity`] = Number(rows[index][`${id}_equity`]) / base; }); });
  const strategy = normalized.map((row) => row.strategy_equity); const summary = { ...(result.summary || {}), "累计收益": strategy[strategy.length - 1] - 1, "最大回撤": maxDrawdown(strategy), benchmarks: {} };
  BENCHMARKS.forEach(([id]) => { const values = normalized.map((row) => row[`${id}_equity`]); const value = values[values.length - 1] - 1; const previous = (result.summary && result.summary.benchmarks && result.summary.benchmarks[id]) || {}; summary.benchmarks[id] = { ...previous, "累计收益": value, "超额累计收益": summary["累计收益"] - value }; });
  return { ...result, summary, equity_curve: { ...result.equity_curve, rows: normalized }, selection: { ...(result.selection || {}), requested_start_date: range.start } };
}

function factorValue(key, value) {
  if (value == null) return "数据不足";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  if (key.endsWith("_percentile")) return `${(number * 100).toFixed(0)}%`;
  if (key.endsWith("_contribution")) return `${number.toFixed(1)} 分`;
  if (key === "liquidity") return `${(number / 1e8).toFixed(2)} 亿元`;
  if (["trend", "drawdown", "volatility"].includes(key)) return `${(number * 100).toFixed(2)}%`;
  return number.toFixed(4);
}

function renderListHeader(snapshot = state.snapshot) {
  const header = el("div", "radar-head");
  const selector = document.createElement("select"); selector.setAttribute("aria-label", "选择配置雷达标的池");
  state.universes.forEach((universe) => selector.add(new Option(universe.name, universe.id, false, universe.id === state.selectedUniverse)));
  selector.addEventListener("change", () => {
    state.selectedUniverse = selector.value; state.detail = null; state.snapshot = null; state.backtest = null; state.performance = null; load();
  });
  const meta = snapshot ? `数据截至 ${snapshot.as_of_date} · 池版本 v${snapshot.universe_version}` : "该标的池暂无可用快照";
  const title = el("div"); title.append(el("h2", "", "ETF 配置雷达"), el("p", "radar-meta", meta));
  const refresh = el("button", "radar-refresh", "手动更新"); refresh.type = "button"; refresh.addEventListener("click", () => runRefresh(refresh));
  header.append(selector, title, refresh);
  return header;
}

function renderList() {
  const target = root(); const snapshot = state.snapshot;
  const header = renderListHeader(snapshot);
  target.replaceChildren(header, el("p", "radar-note", snapshot.research_notice || "研究评分，不构成投资建议。"));
  const groups = new Map();
  snapshot.items.forEach((item) => groups.set(item.category, [...(groups.get(item.category) || []), item]));
  for (const [category, items] of groups) {
    const section = el("section", "radar-section"); section.appendChild(el("h3", "", category));
    const table = document.createElement("table"); table.innerHTML = "<thead><tr><th>排名</th><th>标的</th><th>评分</th><th>等级</th><th>数据状态</th></tr></thead>";
    const body = document.createElement("tbody");
    for (const item of items) {
      const row = document.createElement("tr");
      [item.rank || "-", `${item.name} · ${item.symbol}`, item.score == null ? "-" : Number(item.score).toFixed(1), item.grade].forEach((value) => row.appendChild(el("td", "", value)));
      const cell = document.createElement("td"); cell.appendChild(badge(item.status)); row.appendChild(cell);
      row.className = "radar-row"; row.tabIndex = 0;
      row.addEventListener("click", () => openDetail(item));
      row.addEventListener("keydown", (event) => { if (event.key === "Enter") openDetail(item); }); body.appendChild(row);
    }
    table.appendChild(body); section.appendChild(table); target.appendChild(section);
  }
  target.scrollTop = state.listScrollTop;
}

function renderListError(error) {
  const target = root();
  target.replaceChildren(
    renderListHeader(null),
    el("p", "radar-note", "研究评分，不构成投资建议。当前池加载失败不影响切换至其他标的池。"),
    errorCard(error.message, () => load()),
  );
}

function openDetail(item, push = true) {
  state.listScrollTop = root().scrollTop; state.detail = item; state.performance = null; state.backtest = null;
  if (push) history.pushState({ radarDetail: true, symbol: item.symbol }, "", `#radar/${state.selectedUniverse}/${state.snapshot.run_id}/${item.symbol}`);
  renderDetail();
}

function returnToList(fromHistory = false) {
  state.detail = null; state.backtest = null; state.performance = null;
  if (!fromHistory && history.state && history.state.radarDetail) {
    history.back();
    setTimeout(() => { if (!state.detail) renderList(); }, 0);
  } else renderList();
}

function scoreSection(item) {
  const section = el("section", "panel radar-detail-section"); section.appendChild(el("h3", "", "评分摘要"));
  const grid = el("div", "radar-detail-metrics");
  grid.append(metric("综合评分", item.score == null ? "—" : Number(item.score).toFixed(1)), metric("类别排名", item.rank == null ? "—" : `#${item.rank}`), metric("研究等级", item.grade || "—"), metric("最新价格", item.close == null ? "—" : Number(item.close).toFixed(3)));
  section.appendChild(grid); return section;
}

function factorsSection(item) {
  const labels = { trend: "趋势原始值", drawdown: "回撤控制原始值", volatility: "波动控制原始值", liquidity: "流动性原始值", trend_percentile: "趋势类别分位", drawdown_percentile: "回撤类别分位", volatility_percentile: "波动类别分位", liquidity_percentile: "流动性类别分位", trend_contribution: "趋势分数贡献", drawdown_contribution: "回撤分数贡献", volatility_contribution: "波动分数贡献", liquidity_contribution: "流动性分数贡献" };
  const section = el("section", "panel radar-detail-section"); section.append(el("h3", "", "评分依据"), el("p", "radar-meta", `评分配置 ${state.snapshot.score_profile || "—"}`));
  const list = el("dl", "radar-factor-list"); Object.entries(item.factors || {}).forEach(([key, value]) => list.append(el("dt", "", labels[key] || key), el("dd", "", factorValue(key, value)))); section.appendChild(list); return section;
}

function equityCurve(rows, benchmarkId, seriesKey = "strategy_equity", seriesLabel = "策略净值") {
  const strategy = rows.map((row) => Number(row[seriesKey] != null ? row[seriesKey] : row.equity));
  const base = rows.map((row) => Number(row[`${benchmarkId}_equity`]));
  const values = [...strategy, ...base].filter(Number.isFinite);
  if (rows.length < 2 || !base.every(Number.isFinite)) return el("p", "radar-note", "该回测未包含可展示的多基准净值曲线。");
  const width = 760; const height = 250; const pad = 28; const low = Math.min(...values); const span = Math.max(...values) - low || 1;
  const points = (series) => series.map((value, index) => `${pad + index * (width - pad * 2) / (series.length - 1)},${height - pad - (value - low) * (height - pad * 2) / span}`).join(" ");
  const labels = Object.fromEntries(BENCHMARKS);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"); svg.classList.add("radar-equity-curve"); svg.setAttribute("viewBox", `0 0 ${width} ${height}`); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", `${seriesLabel}与${labels[benchmarkId] || "基准"}净值曲线`);
  const strategyLine = document.createElementNS(svg.namespaceURI, "polyline"); strategyLine.setAttribute("points", points(strategy)); strategyLine.classList.add("radar-strategy-line");
  const benchmarkLine = document.createElementNS(svg.namespaceURI, "polyline"); benchmarkLine.setAttribute("points", points(base)); benchmarkLine.classList.add("radar-benchmark-line");
  const legend = document.createElementNS(svg.namespaceURI, "g"); legend.classList.add("radar-curve-legend");
  const entries = [[seriesLabel, "radar-strategy-line"], [labels[benchmarkId] || "基准净值", "radar-benchmark-line"]];
  entries.forEach(([label, className], index) => {
    const y = pad + 14 + index * 20;
    const sample = document.createElementNS(svg.namespaceURI, "line"); sample.setAttribute("x1", String(pad + 8)); sample.setAttribute("x2", String(pad + 30)); sample.setAttribute("y1", String(y)); sample.setAttribute("y2", String(y)); sample.classList.add(className);
    const text = document.createElementNS(svg.namespaceURI, "text"); text.setAttribute("x", String(pad + 37)); text.setAttribute("y", String(y + 4)); text.textContent = label;
    legend.append(sample, text);
  });
  svg.append(strategyLine, benchmarkLine, legend); return svg;
}

function benchmarkSwitch(render) {
  const controls = el("div", "radar-benchmark-switch");
  BENCHMARKS.forEach(([id, label]) => { const button = el("button", id === state.benchmark ? "on" : "", label); button.type = "button"; button.addEventListener("click", () => { state.benchmark = id; render(); }); controls.appendChild(button); });
  return controls;
}

function performancePeriodControl(container) {
  const select = document.createElement("select"); select.className = "radar-backtest-period"; select.setAttribute("aria-label", "选择标的历史表现区间");
  BACKTEST_PERIODS.forEach(([months, label]) => select.add(new Option(label, String(months), false, months === state.performanceMonths)));
  select.addEventListener("change", () => { state.performanceMonths = select.value === "since" ? "since" : Number(select.value); loadPerformance(container); });
  return select;
}

function renderPerformance(container) {
  const result = state.performance; const metrics = (result && result.metrics) || {}; const benchmark = (result && result.benchmarks && result.benchmarks[state.benchmark]) || {};
  const cards = el("div", "radar-detail-metrics");
  cards.append(
    metric("标的累计收益", percent(metrics["累计收益"])), metric("年化收益", percent(metrics["年化收益"])),
    metric("最大回撤", percent(metrics["最大回撤"])), metric("年化波动", percent(metrics["年化波动"])),
    metric("夏普比率", Number.isFinite(Number(metrics["夏普比率"])) ? Number(metrics["夏普比率"]).toFixed(2) : "—"),
    metric("基准累计收益", percent(benchmark["累计收益"])), metric("相对累计收益", percent(benchmark["超额累计收益"])),
    metric("相对净值最大回撤", percent(benchmark["相对净值最大回撤"])),
  );
  container.replaceChildren(
    el("h3", "", "标的历史表现"), performancePeriodControl(container), benchmarkSwitch(() => renderPerformance(container)), cards,
    el("p", "radar-meta", `${state.detail.name} · ${result.start_date} 至 ${result.end_date} · 该结果仅反映此 ETF 自身的历史净值。`),
    equityCurve((result.equity_curve && result.equity_curve.rows) || [], state.benchmark, "instrument_equity", "标的净值"),
    el("p", "radar-note", "历史表现不代表未来收益；基准仅用于比较，不构成推荐。"),
  );
}

function loadPerformance(container) {
  const range = performanceRange();
  container.replaceChildren(el("h3", "", "标的历史表现"), performancePeriodControl(container), el("p", "radar-meta", "正在读取该标的历史行情与基准…"));
  api.radarInstrumentPerformance(state.snapshot.universe_id, state.detail.symbol, range.start, range.end)
    .then((result) => { if (state.detail) { state.performance = result; renderPerformance(container); } })
    .catch((error) => { if (state.detail) container.replaceChildren(el("h3", "", "标的历史表现"), performancePeriodControl(container), el("p", "radar-note", error.message)); });
}

function renderBacktest(container) {
  const result = state.backtest; const summary = (result && result.summary) || {}; const benchmark = (summary.benchmarks && summary.benchmarks[state.benchmark]) || {};
  const metrics = el("div", "radar-detail-metrics");
  metrics.append(
    metric("策略累计收益", percent(summary["累计收益"])), metric("年化收益", percent(summary["年化收益"])),
    metric("年化波动", percent(summary["年化波动"])), metric("夏普比率", Number.isFinite(Number(summary["夏普比率"])) ? Number(summary["夏普比率"]).toFixed(2) : "—"),
    metric("策略最大回撤", percent(summary["最大回撤"])), metric("相对净值最大回撤", percent(benchmark["相对净值最大回撤"])),
    metric("交易次数", Number(summary["交易次数"] || 0).toFixed(0)), metric("换手率", percent(summary["换手率"])),
  );
  const note = result.selection && result.selection.requested_start_date ? "策略可回测以来的连续净值截取，保留区间起点已有持仓，不等同于全现金重新启动。" : "策略可回测以来的连续策略口径，保留区间起点已有持仓。";
  const profile = result.cost_profile || {}; const warnings = result.warnings || [];
  const trades = ((result.trades && result.trades.rows) || []).filter((trade) => trade.symbol === state.detail.symbol);
  const tradeSection = el("div", "radar-trades");
  tradeSection.appendChild(el("h4", "", "该标的实际成交记录"));
  if (!trades.length) tradeSection.appendChild(el("p", "radar-meta", "该标的未出现在本次池级策略的成交记录中。"));
  else {
    const table = document.createElement("table"); table.innerHTML = "<thead><tr><th>成交日</th><th>方向</th><th>成交价</th><th>费用</th></tr></thead>";
    const body = document.createElement("tbody"); trades.forEach((trade) => { const row = document.createElement("tr"); [trade.trade_date, trade.direction === "buy" ? "买入" : "卖出", trade.execution_price, trade.cost].forEach((value) => row.appendChild(el("td", "", value == null ? "—" : String(value))); body.appendChild(row); }); table.appendChild(body); tradeSection.appendChild(table);
  }
  const warningSection = warnings.length ? el("p", "radar-note", `运行警告：${warnings.join("；")}`) : el("p", "radar-meta", "本次运行未记录数据缺失、无法成交或持仓保留警告。");
  const report = result.report || {}; container.replaceChildren(el("h3", "", "同池策略参考"), backtestPeriodControl(container), benchmarkSwitch(() => renderBacktest(container)), metrics, el("p", "radar-meta", `${report.strategy_id || "策略"} · ${report.start_date || "—"} 至 ${report.end_date || "—"} · 池级轮动策略，不是 ${state.detail.name} 的独立历史收益。`), equityCurve((result.equity_curve && result.equity_curve.rows) || [], state.benchmark), el("p", "radar-meta", `成本假设：佣金 ${percent(profile.commission_rate)} · 滑点 ${percent(profile.slippage_rate)}。`), warningSection, tradeSection, el("p", "radar-note", note));
}

function backtestPeriodControl(container) {
  const select = document.createElement("select"); select.className = "radar-backtest-period"; select.setAttribute("aria-label", "选择回测区间");
  BACKTEST_PERIODS.forEach(([months, label]) => select.add(new Option(label, String(months), false, months === state.backtestMonths)));
  select.addEventListener("change", () => { state.backtestMonths = select.value === "since" ? "since" : Number(select.value); loadBacktest(container); });
  return select;
}

function renderBacktestUnavailable(container) {
  const range = backtestRange();
  container.replaceChildren(
    el("h3", "", "同池策略参考"), backtestPeriodControl(container),
    el("p", "radar-note", `正在生成 ${range.start} 至 ${range.end} 的策略回测，完成后将自动展示。`),
  );
  api.startRadarBacktest(state.snapshot.universe_id, range.start, range.end)
    .then((task) => pollBacktest(task.task_id, container))
    .catch((error) => container.replaceChildren(el("h3", "", "同池策略参考"), backtestPeriodControl(container), el("p", "radar-note", error.message)));
}

async function pollBacktest(taskId, container) {
  for (;;) {
    await new Promise((resolve) => setTimeout(resolve, 1200));
    const task = await api.radarBacktestStatus(taskId);
    if (task.status === "completed") { loadBacktest(container); return; }
    if (task.status === "failed") { container.replaceChildren(el("h3", "", "同池策略参考"), backtestPeriodControl(container), el("p", "radar-note", task.error || "回测生成失败")); return; }
  }
}

function loadBacktest(container) {
  const range = backtestRange();
  container.replaceChildren(el("h3", "", "同池策略参考"), backtestPeriodControl(container), el("p", "radar-meta", "正在读取该区间的已完成池级策略回测…"));
  api.radarBacktest(state.snapshot.universe_id, range.start, range.end)
    .then((result) => { if (state.detail) { state.backtest = sliceBacktest(result, range); renderBacktest(container); } })
    .catch((error) => { if (state.detail) { if (error.status === 404) renderBacktestUnavailable(container); else container.replaceChildren(el("h3", "", "同池策略参考"), el("p", "radar-note", error.message)); } });
}

function renderDetail() {
  const target = root(); const item = state.detail; if (!item || !state.snapshot) return;
  const head = el("section", "radar-detail-head"); const back = el("button", "radar-detail-back", "‹"); back.type = "button"; back.setAttribute("aria-label", "返回配置雷达"); back.addEventListener("click", () => returnToList());
  const title = el("div"); title.append(el("h2", "", `${item.name} · ${item.symbol}`), el("p", "radar-meta", `${state.snapshot.asset_type || "ETF"} · ${state.snapshot.universe_id} · 数据截至 ${state.snapshot.as_of_date}`)); head.append(back, title, badge(item.status));
  target.replaceChildren(head, scoreSection(item), factorsSection(item));
  const risk = el("section", "panel radar-detail-section"); risk.append(el("h3", "", "数据质量与风险"), el("p", "radar-meta", `数据状态：${item.status} · 观测时间：${item.observed_at || "—"}`)); if (item.error_summary) risk.appendChild(el("p", "radar-note", item.error_summary)); target.appendChild(risk);
  const performance = el("section", "panel radar-detail-section radar-backtest"); target.appendChild(performance); loadPerformance(performance);
  const reference = document.createElement("details"); reference.className = "panel radar-detail-section radar-backtest";
  reference.appendChild(el("summary", "", "查看同池策略参考（池级，不代表该标的收益）"));
  const backtest = el("div", "radar-backtest");
  reference.appendChild(backtest);
  reference.addEventListener("toggle", () => { if (reference.open && !state.backtest) loadBacktest(backtest); });
  target.appendChild(reference);
}

async function load() {
  const target = root();
  try {
    state.universes = await api.listRadarUniverses(); if (!state.selectedUniverse) state.selectedUniverse = state.universes[0] && state.universes[0].id;
    state.snapshot = null;
    target.replaceChildren(renderListHeader(null), skeleton(7));
    state.snapshot = await api.latestRadarSnapshot(state.selectedUniverse);
    renderList();
  } catch (error) {
    state.snapshot = null;
    if (state.universes.length) renderListError(error);
    else target.replaceChildren(errorCard(error.message, () => load()));
  }
}

async function restoreRoute() {
  const segments = window.location.hash.replace(/^#/, "").split("/");
  if (segments[0] !== "radar") return;
  const [, universeId, , symbol] = segments;
  if (universeId) state.selectedUniverse = universeId;
  switchView("radar");
  await load();
  if (!symbol || !state.snapshot) return;
  const item = state.snapshot.items.find((candidate) => candidate.symbol === symbol);
  if (item) openDetail(item, false);
}

async function runRefresh(button) {
  button.disabled = true; button.textContent = "更新中…";
  try { const task = await api.refreshRadar(state.selectedUniverse); for (;;) { await new Promise((resolve) => setTimeout(resolve, 1200)); const status = await api.radarRefreshStatus(task.task_id); if (status.status === "completed") { state.listScrollTop = 0; await load(); return; } if (status.status === "failed") throw new Error(status.error || "更新失败"); } } catch (error) { button.disabled = false; button.textContent = "重新更新"; alert(error.message); }
}

export function initRadar() {
  const radarNav = document.querySelector('[data-view="radar"]'); if (radarNav) radarNav.addEventListener("click", () => load());
  window.addEventListener("popstate", (event) => {
    const symbol = event.state && event.state.radarDetail ? event.state.symbol : null;
    if (symbol && state.snapshot) {
      const item = state.snapshot.items.find((candidate) => candidate.symbol === symbol);
      if (item) { state.detail = item; state.performance = null; state.backtest = null; renderDetail(); return; }
    }
    if (state.snapshot) { state.detail = null; state.backtest = null; renderList(); }
  });
  restoreRoute().catch((error) => console.error("配置雷达路由恢复失败:", error));
}
