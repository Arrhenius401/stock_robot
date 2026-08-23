// 结构化股票报告共享渲染器：不读取业务状态，也不发起网络请求。
import { renderMarkdown } from "./markdown.js";
import { el, kv, priceBar, statusBadge } from "./components.js";

const DIMENSIONS = [
  ["financial", "财务健康"],
  ["technical", "技术趋势"],
  ["valuation", "估值合理"],
  ["industry", "行业对比"],
  ["sentiment", "舆情风险"],
];

function record(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function readable(value, fallback = "暂无数据") {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "object") {
    try {
      return JSON.stringify(value, null, 2) || fallback;
    } catch {
      return fallback;
    }
  }
  return String(value);
}

function naturalText(value) {
  if (typeof value === "string") return value.trim();
  if (Array.isArray(value)) {
    return value.map(naturalText).filter(Boolean).join("\n\n");
  }
  if (value && typeof value === "object") {
    const data = record(value);
    const preferred = naturalText(data.bulk) || naturalText(data.summary);
    if (preferred) return preferred;
    return Object.values(data).map(naturalText).filter(Boolean).join("\n\n");
  }
  return value === null || value === undefined ? "" : String(value);
}

function commentaryText(report) {
  return naturalText(report.commentary) || naturalText(report.comments);
}

function firstParagraph(text) {
  return naturalText(text).split(/\n\s*\n/, 1)[0].trim();
}

function markdownBlock(text, className) {
  const block = el("div", className);
  block.innerHTML = renderMarkdown(text || "暂无数据");
  return block;
}

function section(id, title, className = "panel") {
  const node = el("section", className);
  node.id = id;
  node.dataset.sectionTitle = title;
  node.appendChild(el("h2", "panel-title", title));
  return node;
}

function reportSymbol(report) {
  return readable(report.symbol ?? report.code);
}

function reportHeader(report) {
  const header = el("div", "report-header");
  const left = el("div", "report-identity");
  left.appendChild(el("span", "stock-name", readable(report.name, reportSymbol(report))));
  left.appendChild(el("span", "stock-code", reportSymbol(report)));

  const overview = record(report.overview);
  if (overview.industry !== null && overview.industry !== undefined
      && overview.industry !== "") {
    left.appendChild(el("span", "chip ind", readable(overview.industry)));
  }
  header.appendChild(left);

  const right = el("div", "price-box");
  if (overview.latest_close !== null && overview.latest_close !== undefined) {
    right.appendChild(el("span", "stock-price", readable(overview.latest_close)));
  }
  if (overview.change_pct !== null && overview.change_pct !== undefined) {
    const pct = Number(overview.change_pct);
    const direction = pct > 0 ? "up" : pct < 0 ? "down" : "flat";
    const sign = pct > 0 ? "+" : "";
    right.appendChild(el("span", `chg ${direction}`, `${sign}${readable(overview.change_pct)}%`));
  }
  header.appendChild(right);
  return header;
}

function investmentSummary(report) {
  const overview = record(report.overview);
  const signal = record(report.signal);
  const explicit = naturalText(report.summary) || naturalText(overview.summary);
  if (explicit) return explicit;

  const signalParts = [signal.label, signal.action].map(naturalText).filter(Boolean);
  if (signal.position !== null && signal.position !== undefined && signal.position !== "") {
    signalParts.push(`建议仓位 ${readable(signal.position)}`);
  }
  if (signalParts.length) return signalParts.join(" — ");

  const dimensions = record(report.dimensions);
  for (const [name] of DIMENSIONS) {
    const summary = naturalText(record(dimensions[name]).summary);
    if (summary) return summary;
  }
  return "暂无数据";
}

function summarySection(report) {
  const card = section("report-summary", "投资摘要");
  card.appendChild(reportHeader(report));
  card.appendChild(markdownBlock(investmentSummary(report), "report-investment-summary md"));

  const overview = record(report.overview);
  const facts = el("div", "kv");
  if (overview.year_high !== null && overview.year_high !== undefined
      && overview.year_low !== null && overview.year_low !== undefined) {
    facts.appendChild(kv("年内最高 / 最低",
      `${readable(overview.year_high)} / ${readable(overview.year_low)}`));
  }
  if (overview.price_position && overview.price_position !== "暂无") {
    const position = el("div");
    position.appendChild(el("div", "k", "价格位置"));
    const value = el("div", "v");
    value.appendChild(el("span", "", readable(overview.price_position)));
    value.appendChild(priceBar(parseInt(overview.price_position, 10)));
    position.appendChild(value);
    facts.appendChild(position);
  }
  if (report.generated_at) facts.appendChild(kv("生成时间", readable(report.generated_at)));
  if (facts.children.length) card.appendChild(facts);
  return card;
}

function scoreSection(report) {
  const card = section("report-score", "综合评分");
  const score = record(report.score);
  const big = el("div", "score-big");
  const number = el("div", "score-num", readable(score.final, "—"));
  number.appendChild(el("small", "", "/10"));
  big.appendChild(number);

  const rows = el("div", "score-rows");
  for (const item of Array.isArray(report.score_rows) ? report.score_rows : []) {
    const rowData = record(item);
    const row = el("div", "score-row");
    row.appendChild(el("span", "lb", readable(rowData.label)));
    row.appendChild(el("span", "v", readable(rowData.score, "—")));
    row.appendChild(priceBar(Number(rowData.score) * 10));
    row.appendChild(el("span", "wt", readable(rowData.weight, "")));
    rows.appendChild(row);
  }
  if (score.risk_deduction) {
    rows.appendChild(el("div", "score-note",
      `风险扣分: -${readable(score.risk_deduction)}`));
  }
  if (!rows.children.length) rows.appendChild(el("div", "report-empty", "暂无评分明细"));
  big.appendChild(rows);
  card.appendChild(big);
  return card;
}

function dimensionSection(name, label, data) {
  const card = section(`report-${name}`, label);
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    card.appendChild(el("div", "report-empty", "暂无数据"));
    return card;
  }

  const dimension = record(data);
  const header = el("div", "dim-head");
  header.appendChild(el("span", "dim-name", label));
  header.appendChild(statusBadge(dimension.status));
  header.appendChild(el("span", "dim-score", readable(dimension.score, "—")));
  card.appendChild(header);
  card.appendChild(markdownBlock(naturalText(dimension.summary), "dim-sum md"));

  const metrics = record(dimension.metrics);
  if (Object.keys(metrics).length) {
    const grid = el("div", "mtr");
    for (const [metric, value] of Object.entries(metrics)) {
      grid.appendChild(kv(metric, readable(value)));
    }
    card.appendChild(grid);
  }
  for (const flag of Array.isArray(dimension.risk_flags) ? dimension.risk_flags : []) {
    card.appendChild(el("span", "flag", `⚠ ${readable(flag)}`));
  }
  return card;
}

function riskFlags(report) {
  const unique = new Set();
  const dimensions = record(report.dimensions);
  for (const data of Object.values(dimensions)) {
    for (const flag of Array.isArray(record(data).risk_flags) ? data.risk_flags : []) {
      const text = readable(flag, "");
      if (text) unique.add(text);
    }
  }
  return [...unique];
}

function risksSection(report) {
  const card = section("report-risks", "汇总风险");
  const flags = riskFlags(report);
  if (!flags.length) {
    card.appendChild(el("div", "report-empty", "暂无风险提示"));
    return card;
  }
  const list = el("ul", "report-risk-list");
  for (const flag of flags) list.appendChild(el("li", "report-risk-item", flag));
  card.appendChild(list);
  return card;
}

function commentarySection(report) {
  const card = section("report-commentary", "AI 解读", "llm");
  card.appendChild(markdownBlock(commentaryText(report), "report-commentary md"));
  return card;
}

export function renderStockReport(report, options = {}) {
  const data = record(report);
  const article = el("article", "stock-report");
  if (options.className) article.classList.add(String(options.className));
  article.appendChild(summarySection(data));
  article.appendChild(scoreSection(data));

  const dimensions = record(data.dimensions);
  for (const [name, label] of DIMENSIONS) {
    article.appendChild(dimensionSection(name, label, dimensions[name]));
  }
  article.appendChild(risksSection(data));
  article.appendChild(commentarySection(data));
  return article;
}

function summaryConclusion(report) {
  const commentary = firstParagraph(commentaryText(report));
  if (commentary) return commentary;
  const dimensions = record(report.dimensions);
  for (const [name] of DIMENSIONS) {
    const summary = firstParagraph(record(dimensions[name]).summary);
    if (summary) return summary;
  }
  return "暂无数据";
}

function artifactTime(artifact, report) {
  const value = report.generated_at ?? artifact.updated_at ?? artifact.created_at;
  if (typeof value === "number") {
    const milliseconds = value < 1e12 ? value * 1000 : value;
    const date = new Date(milliseconds);
    if (!Number.isNaN(date.getTime())) return date.toLocaleString("zh-CN");
  }
  return readable(value);
}

export function renderReportSummary(artifact) {
  const item = record(artifact);
  const payload = record(item.payload);
  const report = Object.keys(payload).length ? { ...item, ...payload } : item;
  const card = el("article", "report-summary-card");
  card.dataset.artifactId = readable(item.artifact_id ?? item.id, "");

  const header = el("div", "report-summary-header");
  header.appendChild(el("span", "report-summary-name",
    readable(report.name, reportSymbol(report))));
  header.appendChild(el("span", "report-summary-code", reportSymbol(report)));
  const score = record(report.score);
  if (score.final !== null && score.final !== undefined && score.final !== "") {
    header.appendChild(el("span", "report-summary-score", `${readable(score.final)}/10`));
  }
  card.appendChild(header);

  card.appendChild(markdownBlock(summaryConclusion(report),
    "report-summary-conclusion md"));
  const meta = el("div", "report-summary-meta");
  meta.appendChild(el("span", "report-summary-risks", `${riskFlags(report).length} 项风险`));
  meta.appendChild(el("span", "report-summary-time", artifactTime(item, report)));
  card.appendChild(meta);

  const open = el("button", "report-summary-open", "查看完整研报");
  open.type = "button";
  card.appendChild(open);
  return card;
}
