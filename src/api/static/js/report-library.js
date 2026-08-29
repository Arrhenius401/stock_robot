import { api } from "./api.js";
import { el, errorCard, skeleton } from "./components.js";
import { renderMarkdown } from "./markdown.js";
import { bus } from "./state.js";

const TYPE_LABELS = {
  stock: "个股",
  index: "指数",
  backtest: "回测",
};

let initialized = false;
let loaded = false;
let requestSeq = 0;

const state = {
  reports: [],
  total: 0,
  type: "",
  query: "",
  selected: null,
  tab: "markdown",
};

function root() {
  return document.getElementById("reportLibraryContent");
}

function reportTypeLabel(type) {
  return TYPE_LABELS[type] || "报告";
}

function formatDate(value) {
  const timestamp = Number(value);
  if (!Number.isFinite(timestamp) || timestamp <= 0) return "未知时间";
  return new Date(timestamp * 1000).toLocaleString("zh-CN", {
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatPercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const display = `${(number * 100).toFixed(2)}%`;
  return number > 0 ? `+${display}` : display;
}

function formatValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (Number.isFinite(number)) return number.toFixed(2);
  return String(value);
}

function renderBadge(report) {
  return el("span", `badge report-library-badge report-library-badge-${report.type}`,
            reportTypeLabel(report.type));
}

function renderMeta(report) {
  const parts = [formatDate(report.generated_at), report.path];
  if (report.strategy_id) parts.push(`策略 ${report.strategy_id}`);
  if (report.start_date || report.end_date) {
    parts.push(`${report.start_date || "?"} 至 ${report.end_date || "?"}`);
  }
  return parts.join(" · ");
}

function renderHeader() {
  const heading = el("section", "report-library-heading");
  const copy = el("div", "report-library-heading-copy");
  copy.appendChild(el("div", "report-library-title", "已保存报告"));
  copy.appendChild(el("div", "report-library-subtitle", `共 ${state.total} 份 · 按生成时间倒序展示`));

  const tools = el("div", "report-library-heading-tools");
  tools.appendChild(el("span", "pill", "个股分析"));
  tools.appendChild(el("span", "pill", "指数分析"));
  tools.appendChild(el("span", "pill", "回测复盘"));

  heading.appendChild(copy);
  heading.appendChild(tools);
  return heading;
}

function renderToolbar() {
  const toolbar = el("section", "report-library-toolbar");
  const input = el("input");
  input.type = "search";
  input.placeholder = "搜索股票、指数、策略或文件名";
  input.value = state.query;
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      state.query = input.value.trim();
      loadReports();
    }
  });

  const filters = el("div", "report-library-filters");
  [
    ["", "全部"],
    ["stock", "个股"],
    ["index", "指数"],
    ["backtest", "回测"],
  ].forEach(([value, label]) => {
    const button = el("button", value === state.type ? "on" : "", label);
    button.type = "button";
    button.addEventListener("click", () => {
      state.type = value;
      state.query = input.value.trim();
      loadReports();
    });
    filters.appendChild(button);
  });

  const refresh = el("button", "primary-btn", "刷新");
  refresh.type = "button";
  refresh.addEventListener("click", () => {
    state.query = input.value.trim();
    loadReports();
  });

  toolbar.appendChild(input);
  toolbar.appendChild(filters);
  toolbar.appendChild(refresh);
  return toolbar;
}

function reportCard(report) {
  const card = el("article", "report-library-card");
  const head = el("div", "report-library-card-head");
  head.appendChild(el("h3", "report-library-card-title", report.title));
  head.appendChild(renderBadge(report));

  const button = el("button", "report-library-open", "打开详情 →");
  button.type = "button";
  button.addEventListener("click", () => openDetail(report.id));

  card.appendChild(head);
  card.appendChild(el("div", "report-library-card-meta", renderMeta(report)));
  card.appendChild(button);
  return card;
}

function renderList() {
  const target = root();
  if (!target) return;

  target.replaceChildren(renderToolbar(), renderHeader());
  if (!state.reports.length) {
    target.appendChild(el("div", "panel report-library-empty",
                          "还没有匹配的报告。生成分析或回测后，报告会自动出现在这里。"));
    return;
  }

  const grid = el("section", "report-library-grid");
  state.reports.forEach((report) => grid.appendChild(reportCard(report)));
  target.appendChild(grid);
}

async function loadReports() {
  const target = root();
  if (!target) return;

  const currentSeq = ++requestSeq;
  target.replaceChildren(skeleton("正在读取 reports 文件夹中的报告…"));
  try {
    const data = await api.listReports({
      type: state.type || undefined,
      query: state.query || undefined,
    });
    if (currentSeq !== requestSeq) return;
    state.reports = data.reports || [];
    state.total = Number(data.total || state.reports.length);
    loaded = true;
    renderList();
  } catch (error) {
    if (currentSeq !== requestSeq) return;
    target.replaceChildren(errorCard("报告库加载失败", error.message || String(error)));
  }
}

function metric(label, value) {
  const item = el("div", "report-library-metric");
  item.appendChild(el("div", "k", label));
  item.appendChild(el("div", "v", value));
  return item;
}

function renderMetrics(detail) {
  const metrics = detail.summary?.metrics || {};
  const trades = detail.trades?.rows || [];
  const wrap = el("section", "report-library-metrics");
  wrap.appendChild(metric("累计收益", formatPercent(metrics.total_return)));
  wrap.appendChild(metric("最大回撤", formatPercent(metrics.max_drawdown)));
  wrap.appendChild(metric("交易次数", formatValue(metrics.trades_count ?? trades.length)));
  wrap.appendChild(metric("夏普比率", formatValue(metrics.sharpe)));
  return wrap;
}

function tabButton(value, label) {
  const button = el("button", state.tab === value ? "on" : "", label);
  button.type = "button";
  button.addEventListener("click", () => {
    state.tab = value;
    renderDetail();
  });
  return button;
}

function renderTabs(report) {
  const tabs = el("nav", "report-library-tabs");
  tabs.appendChild(tabButton("markdown", "报告正文"));
  if (report.type === "backtest") {
    tabs.appendChild(tabButton("curve", "净值曲线"));
    tabs.appendChild(tabButton("trades", "交易明细"));
  }
  return tabs;
}

function numericColumn(columns, keyword) {
  const lower = keyword.toLowerCase();
  return columns.find((column) => String(column).toLowerCase().includes(lower));
}

function pathFor(rows, xColumn, yColumn, width, height, padding, min, max) {
  if (!yColumn || rows.length < 2) return "";
  const points = rows.map((row, index) => {
    const value = Number(row[yColumn]);
    if (!Number.isFinite(value)) return null;
    const x = padding + (index / (rows.length - 1)) * (width - padding * 2);
    const y = padding + (1 - ((value - min) / (max - min || 1))) * (height - padding * 2);
    return { x, y };
  }).filter(Boolean);

  if (points.length < 2) return "";
  return points.map((point, index) => `${index === 0 ? "M" : "L"}${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(" ");
}

function renderEquityCurve(data) {
  const rows = data?.rows || [];
  const columns = data?.columns || [];
  if (rows.length < 2) {
    return el("div", "report-library-empty", "暂无可展示的净值曲线数据。");
  }

  const xColumn = columns[0];
  const strategyColumn = numericColumn(columns, "strategy")
    || numericColumn(columns, "equity")
    || numericColumn(columns, "portfolio")
    || columns.find((column) => column !== xColumn);
  const benchmarkColumn = numericColumn(columns, "benchmark") || numericColumn(columns, "base");
  const values = rows
    .flatMap((row) => [Number(row[strategyColumn]), Number(row[benchmarkColumn])])
    .filter(Number.isFinite);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const width = 760;
  const height = 280;
  const padding = 28;

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "净值曲线");
  svg.innerHTML = `
    <line class="report-library-axis" x1="${padding}" y1="${height - padding}" x2="${width - padding}" y2="${height - padding}"></line>
    <line class="report-library-axis" x1="${padding}" y1="${padding}" x2="${padding}" y2="${height - padding}"></line>
    <line class="report-library-grid-line" x1="${padding}" y1="${padding}" x2="${width - padding}" y2="${padding}"></line>
    <line class="report-library-grid-line" x1="${padding}" y1="${height / 2}" x2="${width - padding}" y2="${height / 2}"></line>
    <path class="report-library-line-main" d="${pathFor(rows, xColumn, strategyColumn, width, height, padding, min, max)}"></path>
    <path class="report-library-line-base" d="${pathFor(rows, xColumn, benchmarkColumn, width, height, padding, min, max)}"></path>
    <text x="${padding}" y="${height - 7}">${rows[0][xColumn] || ""}</text>
    <text x="${width - padding - 86}" y="${height - 7}">${rows[rows.length - 1][xColumn] || ""}</text>
    <text x="${padding + 4}" y="${padding - 8}">${max.toFixed(2)}</text>
    <text x="${padding + 4}" y="${height - padding - 8}">${min.toFixed(2)}</text>
  `;

  const wrap = el("div", "report-library-curve");
  wrap.appendChild(svg);
  return wrap;
}

function renderTrades(data) {
  const rows = data?.rows || [];
  const columns = data?.columns || [];
  if (!rows.length || !columns.length) {
    return el("div", "report-library-empty", "暂无交易明细数据。");
  }

  const table = el("table", "report-library-table");
  const thead = el("thead");
  const headRow = el("tr");
  columns.forEach((column) => headRow.appendChild(el("th", "", column)));
  thead.appendChild(headRow);

  const tbody = el("tbody");
  rows.forEach((row) => {
    const tr = el("tr");
    columns.forEach((column) => tr.appendChild(el("td", "", formatValue(row[column]))));
    tbody.appendChild(tr);
  });
  table.appendChild(thead);
  table.appendChild(tbody);

  const wrap = el("div", "report-library-table-wrap");
  wrap.appendChild(table);
  return wrap;
}

function renderMarkdownTab(detail) {
  const markdown = el("article", "md report-library-markdown");
  markdown.innerHTML = renderMarkdown(detail.markdown || "暂无报告正文");
  return markdown;
}

function renderActiveTab(detail) {
  if (state.tab === "curve") return renderEquityCurve(detail.equity_curve);
  if (state.tab === "trades") return renderTrades(detail.trades);
  return renderMarkdownTab(detail);
}

function renderDetail() {
  const target = root();
  const detail = state.selected;
  if (!target || !detail) return;

  const report = detail.report;
  const back = el("button", "report-library-back");
  back.type = "button";
  back.setAttribute("aria-label", "返回报告库");
  back.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 18l-6-6 6-6"></path></svg>`;
  back.addEventListener("click", () => renderList());

  const title = el("div", "report-library-detail-title");
  const titleText = el("div", "report-library-title-text");
  titleText.appendChild(el("h2", "", report.title));
  titleText.appendChild(el("div", "report-library-card-meta", renderMeta(report)));
  title.appendChild(back);
  title.appendChild(titleText);

  const actions = el("div", "report-library-detail-actions");
  actions.appendChild(renderBadge(report));
  const download = el("a", "btn-sm", "下载 Markdown");
  download.href = api.downloadReportUrl(report.id);
  download.target = "_blank";
  download.rel = "noopener";
  actions.appendChild(download);

  const head = el("section", "report-library-detail-head");
  head.appendChild(title);
  head.appendChild(actions);

  const panel = el("section", "panel report-library-detail-panel");
  panel.appendChild(renderMetrics(detail));
  panel.appendChild(renderTabs(report));

  const body = el("div", "report-library-detail-body");
  body.appendChild(renderActiveTab(detail));
  panel.appendChild(body);

  target.replaceChildren(head, panel);
}

async function openDetail(id) {
  const target = root();
  if (!target) return;

  target.replaceChildren(skeleton("正在打开报告详情…"));
  try {
    state.selected = await api.getReport(id);
    state.tab = "markdown";
    renderDetail();
  } catch (error) {
    target.replaceChildren(errorCard("报告已不存在或无法读取", error.message || String(error)));
  }
}

export function initReportLibrary() {
  if (initialized) return;
  initialized = true;
  bus.addEventListener("view-change", (event) => {
    if (event.detail?.view === "report-library" && !loaded) {
      loadReports();
    }
  });
}
