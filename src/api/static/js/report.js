// 个股报告视图：渲染 /api/v1/analyze 完整 JSON（纵向研报流）
import { store, switchView } from "./state.js";
import { api } from "./api.js";
import { renderMarkdown } from "./markdown.js";
import { el, kv, statusBadge, priceBar, errorCard, skeleton } from "./components.js";

const DIM_LABELS = {
  financial: "财务健康", technical: "技术趋势", valuation: "估值合理",
  industry: "行业对比", sentiment: "舆情风险",
};
const DIM_ORDER = ["financial", "technical", "valuation", "industry", "sentiment"];

const content = () => document.getElementById("reportContent");

let reqSeq = 0;  // 请求令牌：慢请求期间二次查询时，丢弃迟到响应/错误

export async function openReport(symbol) {
  const seq = ++reqSeq;
  switchView("report");
  if (store.reportCache[symbol]) {
    renderReport(store.reportCache[symbol]);
    return;
  }
  const box = content();
  box.innerHTML = "";
  box.appendChild(skeleton());
  try {
    const data = await api.analyze(symbol);
    store.reportCache[symbol] = data;
    if (seq !== reqSeq) return;  // 已有更新请求，迟到响应不覆盖新视图
    renderReport(data);
  } catch (err) {
    if (seq !== reqSeq) return;
    box.innerHTML = "";
    box.appendChild(errorCard(`分析失败: ${err.message}`, () => openReport(symbol)));
  }
}

function renderReport(d) {
  const box = content();
  box.innerHTML = "";
  box.appendChild(reportHeader(d));
  box.appendChild(overviewCard(d));
  box.appendChild(scoreCard(d));
  for (const dim of DIM_ORDER) {
    const section = (d.dimensions || {})[dim];
    if (section) box.appendChild(dimCard(dim, section));
  }
  if (d.commentary) box.appendChild(llmCard(d.commentary));
  const refresh = el("button", "btn-refresh", "刷新报告");
  refresh.addEventListener("click", () => {
    delete store.reportCache[d.symbol];
    openReport(d.symbol);
  });
  box.appendChild(refresh);
}

function reportHeader(d) {
  const hdr = el("div", "report-header");
  const left = el("div");
  left.appendChild(el("span", "stock-name", d.name || d.symbol));
  left.appendChild(el("span", "stock-code", d.symbol));
  if (d.overview && d.overview.industry) {
    left.appendChild(el("span", "chip ind", d.overview.industry));
  }
  hdr.appendChild(left);
  const right = el("div", "price-box");
  if (d.overview && d.overview.latest_close != null) {
    right.appendChild(el("span", "stock-price", String(d.overview.latest_close)));
  }
  if (d.overview && d.overview.change_pct != null) {
    const pct = d.overview.change_pct;
    const cls = pct > 0 ? "up" : pct < 0 ? "down" : "flat";
    right.appendChild(el("span", `chg ${cls}`, `${pct > 0 ? "+" : ""}${pct}%`));
  }
  hdr.appendChild(right);
  return hdr;
}

function overviewCard(d) {
  const card = el("div", "panel");
  card.appendChild(el("div", "panel-title", "概览"));
  const kvs = el("div", "kv");
  const o = d.overview || {};
  if (o.year_high != null && o.year_low != null) {
    kvs.appendChild(kv("年内最高 / 最低", `${o.year_high} / ${o.year_low}`));
  }
  if (o.price_position && o.price_position !== "暂无") {
    const box = el("div");
    box.appendChild(el("div", "k", "价格位置"));
    const val = el("div", "v");
    val.appendChild(el("span", "", o.price_position));
    val.appendChild(priceBar(parseInt(o.price_position, 10)));
    box.appendChild(val);
    kvs.appendChild(box);
  }
  card.appendChild(kvs);
  return card;
}

function scoreCard(d) {
  const card = el("div", "panel");
  card.appendChild(el("div", "panel-title", "综合评分"));
  const big = el("div", "score-big");
  const s = d.score || {};
  const num = el("div", "score-num");
  num.textContent = s.final != null ? String(s.final) : "—";
  num.appendChild(el("small", "", "/10"));
  big.appendChild(num);
  const rows = el("div", "score-rows");
  for (const r of d.score_rows || []) {
    const row = el("div", "score-row");
    row.appendChild(el("span", "lb", r.label));
    row.appendChild(el("span", "v", r.score));
    // 修正 1：score 是 0-10 制，priceBar 收 0-100 百分比，需 ×10
    row.appendChild(priceBar(parseFloat(r.score) * 10));
    row.appendChild(el("span", "wt", r.weight));
    rows.appendChild(row);
  }
  if (s.risk_deduction) {
    rows.appendChild(el("div", "score-note", `风险扣分: -${s.risk_deduction}`));
  }
  big.appendChild(rows);
  card.appendChild(big);
  return card;
}

function dimCard(name, section) {
  const card = el("div", "panel");
  const head = el("div", "dim-head");
  head.appendChild(el("span", "dim-name", DIM_LABELS[name] || name));
  head.appendChild(statusBadge(section.status));
  const sc = el("span", "dim-score");
  sc.textContent = section.score != null ? String(section.score) : "—";
  head.appendChild(sc);
  card.appendChild(head);
  if (section.summary) {
    const sum = el("div", "dim-sum");
    sum.innerHTML = renderMarkdown(section.summary);
    card.appendChild(sum);
  }
  const metrics = section.metrics || {};
  const keys = Object.keys(metrics);
  if (keys.length) {
    const mtr = el("div", "mtr");
    for (const k of keys) {
      const val = metrics[k];
      const text = val !== null && typeof val === "object" ? JSON.stringify(val) : String(val);
      mtr.appendChild(kv(k, text));
    }
    card.appendChild(mtr);
  }
  for (const flag of section.risk_flags || []) {
    card.appendChild(el("span", "flag", `⚠ ${flag}`));
  }
  return card;
}

function llmCard(commentary) {
  const card = el("div", "llm");
  card.appendChild(el("div", "llm-title", "AI 解读"));
  const p = el("div", "md");
  p.innerHTML = renderMarkdown(commentary);
  card.appendChild(p);
  return card;
}

export function initReportView() {
  const input = document.getElementById("stockInput");
  const btn = document.getElementById("stockBtn");
  btn.addEventListener("click", () => {
    const sym = input.value.trim();
    if (sym) openReport(sym);
  });
  input.addEventListener("keydown", (e) => {
    // 修正 2：IME 组合输入回车不触发（中文输入法候选确认）
    if (e.key === "Enter" && !e.isComposing) btn.click();
  });
}
