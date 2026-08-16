// 指数分析视图：多指数对比表 + 逐指数纵向研报
import { store, switchView } from "./state.js";
import { api } from "./api.js";
import { renderMarkdown } from "./markdown.js";
import { el, kv, tagChip, priceBar, errorCard, skeleton,
         showEntryError, clearEntryError } from "./components.js";

let reqSeq = 0;  // 请求令牌：慢请求期间二次查询时，丢弃迟到响应/错误

const TAGS = {
  technical: { bull: ["多头", "good"], shake: ["震荡", "mid"], bear: ["空头", "bad"] },
  valuation: { undervalued: ["低估", "good"], neutral: ["中性", "mid"],
               overvalued: ["高估", "bad"], invalid: ["无效", "gray"] },
  capital: { positive: ["流入", "good"], neutral: ["中性", "mid"], negative: ["流出", "bad"] },
  macro: { positive: ["积极", "good"], neutral: ["中性", "mid"],
           negative: ["消极", "bad"], na: ["不适用", "gray"] },
  sentiment: { positive: ["积极", "good"], neutral: ["中性", "mid"], negative: ["消极", "bad"] },
};
const SECTION_META = [
  ["technical", "技术面", "section_technical", "tag_technical"],
  ["valuation", "估值", "section_valuation", "tag_valuation"],
  ["capital", "资金面", "section_capital", "tag_capital"],
  ["macro", "宏观", "section_macro", "tag_macro"],
  ["sentiment", "舆情", "section_sentiment", "tag_sentiment"],
];

// 对比表表头为中文展示标签、行内 key 为英文内部名（与 CLI 一致），
// 渲染时必须按键映射取值，否则真实数据全部渲染 "—"
const COMPARE_KEYS = {
  "指数名称": "name", "最新点位": "latest", "涨跌幅": "change",
  "PE 分位": "pe_pct", "PB 分位": "pb_pct", "趋势": "trend",
  "估值": "valuation", "资金": "capital", "综合评级": "composite",
};

const content = () => document.getElementById("indexContent");

export async function openIndex(codes) {
  const seq = ++reqSeq;
  const prevView = store.currentView;  // 记录原视图：422 校验失败时回退
  switchView("index");
  const symbols = String(codes || "").trim().split(/\s+/).filter(Boolean);
  if (!symbols.length) return;
  const box = content();
  box.innerHTML = "";
  box.appendChild(skeleton());
  try {
    const data = await api.index(symbols);
    if (seq !== reqSeq) return;  // 已有更新请求，迟到响应不覆盖新视图
    renderIndex(data);
  } catch (err) {
    if (seq !== reqSeq) return;
    if (err.status === 422) {
      // 输入校验失败：回原视图 + 输入框旁红字，不渲染错误卡
      showEntryError("indexInput", err.message);
      switchView(prevView);
      return;
    }
    box.innerHTML = "";
    box.appendChild(errorCard(`分析失败: ${err.message}`, () => openIndex(codes)));
  }
}

function renderIndex(data) {
  const box = content();
  box.innerHTML = "";
  if (data.compare && data.compare.headers && data.compare.headers.length) {
    box.appendChild(compareCard(data.compare));
  }
  for (const r of data.reports || []) box.appendChild(reportCard(r));
  for (const err of data.errors || []) box.appendChild(errorCard(err));
}

function compareCell(header, row) {
  // 键映射取值；趋势/估值/资金三列为多空标签，渲染徽章；综合评级长文本截断
  const key = COMPARE_KEYS[header] || header;
  const val = row[key];
  if (val === undefined || val === null) return el("td", "", "—");
  const text = String(val);
  if (header === "趋势") {
    return tdWithTag(TAGS.technical[text]);
  }
  if (header === "估值") {
    return tdWithTag(TAGS.valuation[text]);
  }
  if (header === "资金") {
    return tdWithTag(TAGS.capital[text]);
  }
  if (header === "综合评级") {
    return el("td", "", text.length > 12 ? `${text.slice(0, 12)}…` : text);
  }
  return el("td", "", text);
}

function tdWithTag(entry) {
  const td = el("td");
  if (entry) td.appendChild(tagChip(entry[0], entry[1]));
  else td.textContent = "—";
  return td;
}

function compareCard(compare) {
  const card = el("div", "panel");
  card.appendChild(el("div", "panel-title", "多指数对比"));
  const table = el("table");
  const thead = el("thead");
  const headRow = el("tr");
  for (const h of compare.headers) headRow.appendChild(el("th", "", String(h)));
  thead.appendChild(headRow);
  table.appendChild(thead);
  const tbody = el("tbody");
  for (const row of compare.rows || []) {
    const tr = el("tr");
    for (const h of compare.headers) {
      tr.appendChild(compareCell(h, row));
    }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  card.appendChild(table);
  return card;
}

function reportCard(r) {
  const card = el("div", "report");
  const hdr = el("div", "report-header");
  hdr.appendChild(el("span", "stock-name", r.name || r.code));
  hdr.appendChild(el("span", "stock-code", r.code));
  const o = r.overview || {};
  const priceBox = el("div", "price-box");
  if (o.latest_close != null) {
    priceBox.appendChild(el("span", "stock-price", String(o.latest_close)));
  }
  if (o.change_pct != null) {
    const cls = o.change_pct > 0 ? "up" : o.change_pct < 0 ? "down" : "flat";
    priceBox.appendChild(el("span", `chg ${cls}`, `${o.change_pct > 0 ? "+" : ""}${o.change_pct}%`));
  }
  hdr.appendChild(priceBox);
  card.appendChild(hdr);

  if (o.pe_ttm != null || o.pb != null || o.pe_percentile != null) {
    const panel = el("div", "panel");
    panel.appendChild(el("div", "panel-title", "概览"));
    const kvs = el("div", "kv");
    if (o.pe_ttm != null) kvs.appendChild(kv("PE-TTM", `${o.pe_ttm}x`));
    if (o.pb != null) kvs.appendChild(kv("PB", String(o.pb)));
    if (o.pe_percentile != null) {
      const box = el("div");
      box.appendChild(el("div", "k", `PE 分位（近 ${o.percentile_lookback_years || 5} 年）`));
      const val = el("div", "v");
      val.appendChild(el("span", "", `${o.pe_percentile}%`));
      val.appendChild(priceBar(o.pe_percentile));
      box.appendChild(val);
      kvs.appendChild(box);
    }
    panel.appendChild(kvs);
    card.appendChild(panel);
  }

  const visible = r.visible_sections;
  for (const [key, label, field, tagField] of SECTION_META) {
    const section = r[field];
    if (!section) continue;
    if (Array.isArray(visible) && visible.length && !visible.includes(key)) continue;
    card.appendChild(sectionCard(label, section, tagFor(key, r[tagField])));
  }
  card.appendChild(compositeCard(r));
  for (const risk of r.risk_list || []) {
    card.appendChild(el("span", "flag", `⚠ ${risk}`));
  }
  return card;
}

function tagFor(kind, tag) {
  const map = TAGS[kind] || {};
  const entry = map[tag];
  return entry ? tagChip(entry[0], entry[1]) : null;
}

function sectionCard(label, section, tag) {
  const card = el("div", "panel");
  const head = el("div", "dim-head");
  head.appendChild(el("span", "dim-name", label));
  if (tag) head.appendChild(tag);
  card.appendChild(head);
  if (section.status === "unavailable") {
    card.appendChild(el("div", "dim-sum", section.summary || "数据不可用"));
    return card;
  }
  if (section.summary) {
    const sum = el("div", "dim-sum");
    sum.innerHTML = renderMarkdown(section.summary);
    card.appendChild(sum);
  }
  const metrics = section.metrics || {};
  const keys = Object.keys(metrics).filter((k) => k !== "tag");
  if (keys.length) {
    const mtr = el("div", "mtr");
    for (const k of keys) {
      const val = metrics[k];
      const text = val !== null && typeof val === "object" ? JSON.stringify(val) : String(val);
      mtr.appendChild(kv(k, text));
    }
    card.appendChild(mtr);
  }
  return card;
}

function compositeCard(r) {
  const card = el("div", "llm");
  card.appendChild(el("div", "llm-title", "综合研判"));
  const p = el("div", "md");
  p.innerHTML = renderMarkdown(r.composite_comment || "");
  card.appendChild(p);
  if (r.position_coeff != null) {
    const coeff = el("div", "coeff");
    coeff.appendChild(el("span", "coeff-label", "建议仓位系数"));
    coeff.appendChild(priceBar((Number(r.position_coeff) || 0) * 100));
    coeff.appendChild(el("span", "coeff-value", String(r.position_coeff)));
    card.appendChild(coeff);
  }
  return card;
}

export function initIndexView() {
  const input = document.getElementById("indexInput");
  const btn = document.getElementById("indexBtn");
  btn.addEventListener("click", () => {
    clearEntryError("indexInput");  // 重新分析前清除上次校验红字
    // 空输入不触发：否则令牌自增会丢弃在途响应、骨架屏永久残留
    if (input.value.trim()) openIndex(input.value);
  });
  input.addEventListener("keydown", (e) => {
    // IME 组合输入回车不触发（中文输入法候选确认）
    if (e.key === "Enter" && !e.isComposing) btn.click();
  });
}
