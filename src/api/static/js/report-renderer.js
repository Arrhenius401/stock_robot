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

// 数据层字段只在内部协议中使用；界面统一显示业务中文名称。
const METRIC_LABELS = {
  amount_basis: "财务金额口径", valuation_applicability: "估值适用性", applicable: "可用于高低估判断", reason: "原因",
  ma_5: "5 日均线", ma_20: "20 日均线", ma_60: "60 日均线",
  volume_ratio: "近5日与此前20日均量比",
  macd_dif: "MACD DIF", macd_dea: "MACD DEA", macd_bar: "MACD 柱值",
  pe_snapshot_basis: "单张PE计算口径", pe_snapshot_source_url: "单张PE官方来源",
  pe_snapshot_as_of: "单张PE数据日期",
  pe_snapshot: "官方单张市盈率",
  pe_as_of: "PE数据日期",
  pb_as_of: "PB数据日期",
  pe_basis: "PE计算口径",
  pb_basis: "PB计算口径",
  pe_source_url: "PE官方来源",
  pb_source_url: "PB官方来源",
  pe_sample_count: "PE有效交易日样本数",
  pb_sample_count: "PB有效交易日样本数",
  valuation_notes: "估值数据覆盖说明",

  weight_as_of: "成分权重日期", market_cap_dates: "市值采样日期",
  benchmark_aligned_index_return_pct: "同日对齐指数收益率",
  etf_start_date: "ETF区间起始日期", etf_end_date: "ETF区间结束日期",
  etf_sample_count: "ETF交易日样本数", etf_unavailable_reason: "ETF表现不可用原因",
  latest_close: "最新收盘价", latest_price: "最新价格", change_pct: "涨跌幅",
  year_high: "近一年最高价", year_low: "近一年最低价", price_position: "价格位置",
  latest_quarter: "最新报告期", revenue: "营业收入", net_profit: "净利润",
  total_assets: "总资产", total_equity: "股东权益", operating_cash_flow: "经营活动现金流",
  operating_cash_flow_per_share: "每股经营现金流", revenue_growth_yoy: "营收同比增长",
  profit_growth_yoy: "净利润同比增长", roe: "净资产收益率", gross_margin: "毛利率",
  ma_5: "5 日均线", ma_20: "20 日均线", ma_60: "60 日均线",
  pe_ttm: "市盈率（TTM）", pb: "市净率", ps_ttm: "市销率（TTM）",
  pe_percentile: "市盈率分位", pb_percentile: "市净率分位", dividend_yield: "股息率",
  industry: "所属行业", sector: "所属板块", peer_count: "有效同行数",
  peers: "同行业公司", peer_scope: "同行范围", peer_industry: "同行所属行业",
  top_peers: "头部同行公司", symbol: "股票代码", name: "公司名称", market_cap: "总市值",
  industry_median_pe: "行业市盈率中位数", industry_median_pb: "行业市净率中位数",
  target_pe_premium: "相对行业市盈率溢价", target_market_cap_rank: "行业市值排名",
  headline_count: "新闻数量", north_bound: "北向资金净流入", main_net_inflow: "主力资金净流入",
  headlines: "近期新闻", top_headlines: "近期要闻", roe_trend: "净资产收益率走势",
  quarter: "报告期", valuation_valid: "估值样本有效", percentile_lookback_years: "分位回看年限",
  sample_start: "样本起始日期", sample_end: "样本结束日期", tag: "信号标签",
  margin_balance: "融资余额", data_date: "数据日期", date: "数据日期",
};

Object.assign(METRIC_LABELS, {
  start_date: "区间起始日期", end_date: "区间结束日期", sample_count: "行情样本数",
  return_pct: "指数区间收益率", annualized_volatility_pct: "年化波动率", max_drawdown_pct: "最大回撤",
  benchmark_symbol: "对照基准代码", benchmark_return_pct: "同日基准收益率", excess_return_pct: "同日超额收益率",
  benchmark_sample_count: "基准交集样本数", benchmark_start_date: "基准交集起始日期", benchmark_end_date: "基准交集结束日期",
  return_basis: "指数收益口径", etf_symbol: "ETF 代码", etf_return_pct: "ETF 区间收益率", etf_return_basis: "ETF 收益口径",
  strategy_kind: "策略类型", source: "数据来源", as_of: "采样日期", member_count: "成分数量",
  member_weight_coverage_pct: "成分权重覆盖率", top10_weight_pct: "前十大权重集中度", industry_coverage_pct: "行业权重覆盖率",
  financial_years: "财报年度", errors: "数据缺失原因", methodology: "计算口径",
});

const RISK_LABELS = {
  roe_low: "净资产收益率偏低",
  high_debt: "资产负债率偏高",
  cash_flow_mismatch: "经营现金流与净利润不匹配",
  high_pe_premium: "市盈率分位偏高",
  industry_weak_margin: "毛利率弱于行业",
  bearish_ma: "均线呈空头排列",
  volume_bearish: "放量下跌风险",
  major_negative_news: "存在重大负面舆情",
};

function record(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

export function normalizeArtifactReport(artifact) {
  const item = record(artifact);
  const payload = record(item.payload);
  const presentPayload = Object.fromEntries(
    Object.entries(payload).filter(([, value]) => value !== null && value !== undefined),
  );
  return { ...item, ...presentPayload };
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

export function metricLabel(key) {
  const raw = String(key);
  return METRIC_LABELS[raw] || raw.replaceAll("_", " ");
}

function scoreText(value, fallback = "—") {
  if (value === null || value === undefined || value === "") return fallback;
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(1) : fallback;
}

export function riskText(value) {
  const raw = readable(value, "");
  return RISK_LABELS[raw] || raw;
}

export function metricText(value) {
  if (Array.isArray(value)) return value.map(metricText).filter(Boolean).join("、") || "暂无数据";
  if (value && typeof value === "object") {
    return Object.values(value).map(metricText).filter(Boolean).join("、") || "暂无数据";
  }
  return readable(value);
}

function peerText(value, peerNames) {
  const names = record(peerNames);
  const peers = Array.isArray(value) ? value : Object.entries(record(value)).map(([symbol, name]) => ({ symbol, name }));
  const labels = peers.map((peer) => {
    const item = record(peer);
    const symbol = readable(item.symbol ?? peer, "");
    const name = readable(item.name ?? names[symbol], "");
    return name ? `${name}（${symbol}）` : symbol;
  }).filter(Boolean);
  return labels.join("、") || "暂无数据";
}

function marketCapText(value) {
  return formatMetricValue("market_cap", value);
}

function appendTopPeers(grid, value, peerNames) {
  const names = record(peerNames);
  const peers = Array.isArray(value) ? value : [];
  const wrap = el("div", "report-metric-list-wrap");
  wrap.appendChild(el("div", "k", metricLabel("top_peers")));
  const list = el("ul", "report-metric-list report-peer-list");
  for (const peer of peers) {
    const item = record(peer);
    const symbol = readable(item.symbol, "");
    const name = readable(item.name ?? names[symbol], "");
    const title = name ? `${name}（${symbol}）` : symbol;
    if (!title) continue;
    const details = [
      ["市值", "market_cap", item.market_cap], ["PE(TTM)", "pe_ttm", item.pe_ttm], ["PB", "pb", item.pb],
    ].filter(([, , metric]) => metric !== null && metric !== undefined && metric !== "")
      .map(([label, key, metric]) => `${label} ${key === "market_cap" ? marketCapText(metric) : formatMetricValue(key, metric)}`);
    list.appendChild(el("li", "", details.length ? `${title}：${details.join(" · ")}` : title));
  }
  if (!list.children.length) list.appendChild(el("li", "", "暂无数据"));
  wrap.appendChild(list);
  grid.appendChild(wrap);
}

function appendListMetric(grid, key, value) {
  const wrap = el("div", "report-metric-list-wrap");
  wrap.appendChild(el("div", "k", metricLabel(key)));
  const list = el("ul", "report-metric-list");
  const items = Array.isArray(value) ? value : [];
  for (const item of items) list.appendChild(el("li", "", metricText(item)));
  if (!list.children.length) list.appendChild(el("li", "", "暂无数据"));
  wrap.appendChild(list);
  grid.appendChild(wrap);
}

const PRICE_KEYS = new Set(['latest_close','latest_price','year_high','year_low','ma_5','ma_20','ma_60']);
const MULTIPLE_KEYS = new Set(['pe_ttm','pe_snapshot','pb','ps_ttm','industry_median_pe','industry_median_pb']);
const RATIO_KEYS = new Set(['roe','gross_margin','revenue_growth_yoy','profit_growth_yoy','industry_median_gross_margin']);
const PERCENT_KEYS = new Set(['change_pct','pe_percentile','pb_percentile','target_pe_premium','dividend_yield','shibor_3m','cpi_yoy','shibor_percentile','pmi_percentile']);
const AMOUNT_KEYS = new Set(['revenue','net_profit','deducted_net_profit','total_assets','total_equity','common_equity','operating_cash_flow','market_cap']);
const ONE_DIGIT_KEYS = new Set(['score','base_score','final_score','risk_deduction']);
const TWO_DIGIT_KEYS = new Set(['volume_ratio','operating_cash_flow_per_share','basic_eps','pmi','usd_cny']);

function fixedNumber(value, digits) {
  const normalized = Number(value.toFixed(digits)) === 0 ? 0 : value;
  return normalized.toLocaleString('en-US', {minimumFractionDigits:digits, maximumFractionDigits:digits});
}

export function formatMetricValue(key, value) {
  if (value == null || value === 'N/A') return '暂无数据';
  if (typeof value !== 'number') return value;
  if (!Number.isFinite(value)) return '数据异常';
  const flowNumber = () => {
    if (value===0 || Number(value.toFixed(2))!==0) return fixedNumber(value,2);
    const compact = Number(value.toPrecision(8));
    return Math.abs(compact)<0.0001
      ? compact.toExponential().replace(/e([+-])(\d)$/, (_,sign,digit)=>`e${sign}0${digit}`)
      : String(compact);
  };
  if (key === 'north_bound') return flowNumber()+' 亿';
  if (['main_net_inflow','margin_balance'].includes(key)) return flowNumber()+'（原始单位未记录）';
  if (AMOUNT_KEYS.has(key)) {
    const [divisor,unit] = Math.abs(value)>=1e8 ? [1e8,' 亿'] : Math.abs(value)>=1e4 ? [1e4,' 万'] : [1,''];
    return fixedNumber(value/divisor,2)+unit;
  }
  if (RATIO_KEYS.has(key)) return fixedNumber(value*100,2)+'%';
  if (PERCENT_KEYS.has(key) || key.endsWith('_pct')) return fixedNumber(value,2)+'%';
  if (MULTIPLE_KEYS.has(key)) return fixedNumber(value,2)+(value<=0?'（不适用于高低估判断）':'');
  if (PRICE_KEYS.has(key)) return value!==0 && Number(value.toFixed(2))===0 ? String(Number(value.toPrecision(8))) : fixedNumber(value,2);
  if (['macd_dif','macd_dea','macd_bar'].includes(key)) return fixedNumber(value,3);
  if (ONE_DIGIT_KEYS.has(key)) return fixedNumber(value,1);
  if (TWO_DIGIT_KEYS.has(key)) return fixedNumber(value,2);
  return value;
}

function appendMetrics(grid, key, value, prefix = "") {
  const label = prefix ? `${prefix} · ${metricLabel(key)}` : metricLabel(key);
  if (Array.isArray(value)) {
    const wrap = el('div', 'report-metric-list-wrap');
    wrap.appendChild(el('div','k',label));
    const list = el('ul','report-metric-list');
    for (const item of value) {
      const row = item && typeof item === 'object'
        ? Object.entries(item).map(([field,cell])=>`${metricLabel(field)}：${formatMetricValue(field,cell)}`).join(' · ')
        : String(formatMetricValue(key,item));
      list.appendChild(el('li','',row));
    }
    wrap.appendChild(list); grid.appendChild(wrap); return;
  }
  if (key.endsWith("_source_url") && typeof value === "string" && /^https:\/\//i.test(value)) {
    // 官方来源使用短链接文案，避免长网址撑出窄屏卡片。
    const item = el("div");
    item.appendChild(el("div", "k", label));
    const content = el("div", "v");
    const link = el("a", "", "查看官方来源");
    link.href = value;
    link.title = value;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    content.appendChild(link);
    item.appendChild(content);
    grid.appendChild(item);
    return;
  }
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const entries = Object.entries(value);
    if (!entries.length) {
      grid.appendChild(kv(label, "暂无数据"));
      return;
    }
    for (const [nestedKey, nestedValue] of entries) {
      appendMetrics(grid, nestedKey, nestedValue, label);
    }
    return;
  }
  let text = metricText(formatMetricValue(key,value));
  if (typeof value === 'boolean') text = value ? '是' : '否';
  if (key === "strategy_kind") text = {dividend: "红利", dividend_low_volatility: "红利低波动", free_cash_flow: "自由现金流"}[value] || text;
  grid.appendChild(kv(label, text));
}

export function appendReadableMetric(grid, key, value, context = {}) {
  // 适用性结构供机器消费；正文用原因列表和对应指标状态展示，避免重复展开。
  if (key === 'valuation_applicability') return;
  const rules = context.valuation_applicability;
  const snapshot = {pe_percentile:'pe_ttm',pb_percentile:'pb',target_pe_premium:'pe_ttm'}[key];
  if (rules && snapshot && value == null) {
    const rule = rules[snapshot] || {};
    const text = !rule.applicable ? (context[snapshot] != null ? '不适用' : rule.reason || '暂无数据')
      : key === 'target_pe_premium' ? '暂无可比数据' : '历史样本不足';
    grid.appendChild(kv(metricLabel(key),text)); return;
  }
  if (key === "headlines" || key === "top_headlines") {
    appendListMetric(grid, key, value);
    return;
  }
  if (key === "top_peers") {
    appendTopPeers(grid, value, context.peer_names);
    return;
  }
  appendMetrics(grid, key, value);
}

const REPORT_TOKEN_LABELS = {
  ...RISK_LABELS,
  bull: "多头", shake: "震荡", bear: "空头",
  undervalued: "低估", overvalued: "高估", invalid: "无效",
  positive: "积极", negative: "消极", neutral: "中性", na: "不适用",
};

export function localizeReportMarkdown(markdown) {
  const text = String(markdown || "");
  return text.replace(/\b(?:roe_low|high_debt|cash_flow_mismatch|high_pe_premium|industry_weak_margin|bearish_ma|volume_bearish|major_negative_news|bull|shake|bear|undervalued|overvalued|invalid|positive|negative|neutral|na)\b/g,
    (token) => REPORT_TOKEN_LABELS[token] || token);
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

function section(id, title, className = "panel", sectionIdPrefix = "") {
  const node = el("section", className);
  node.id = `${sectionIdPrefix}${id}`;
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
    right.appendChild(el("span", "stock-price", formatMetricValue('latest_close',overview.latest_close)));
  }
  if (overview.change_pct !== null && overview.change_pct !== undefined) {
    const pct = Number(overview.change_pct);
    const direction = pct > 0 ? "up" : pct < 0 ? "down" : "flat";
    const sign = pct > 0 ? "+" : "";
    right.appendChild(el("span", `chg ${direction}`, `${sign}${formatMetricValue('change_pct',overview.change_pct)}`));
  }
  header.appendChild(right);
  return header;
}

function investmentSummary(report) {
  const overview = record(report.overview);
  const signal = record(report.signal);
  const quality = record(report.data_quality);
  if (report.market === "hk") {
    return quality.has_analysis_data
      ? "依据可用港股指标进行观察；A 股综合评分、申万同行比较与交易信号不适用。"
      : "主要分析数据不可用，暂无分析结论；港股不适用 A 股综合评分或交易信号。";
  }
  if (quality.signal_allowed === false) {
    return quality.status === "unavailable"
      ? "主要分析数据不可用，暂无分析结论。"
      : "计分数据不完整，暂无操作信号和仓位建议。";
  }
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

function summarySection(report, sectionIdPrefix) {
  const card = section("report-summary", "投资摘要", "panel", sectionIdPrefix);
  card.appendChild(reportHeader(report));
  const quality = record(report.data_quality);
  if (quality.status) {
    const badge = statusBadge(quality.status);
    badge.textContent = (report.market === "hk"
      ? {ok: "分析数据可用 · 评分不适用", partial: "部分分析数据可用 · 评分不适用", unavailable: "数据不可用 · 暂无结论"}
      : {ok: "数据完整", partial: "数据不完整 · 暂无操作信号", unavailable: "数据不可用 · 暂无结论"})[quality.status] || "数据状态未记录";
    card.appendChild(badge);
  }
  card.appendChild(markdownBlock(investmentSummary(report), "report-investment-summary md"));

  const overview = record(report.overview);
  const facts = el("div", "kv report-summary-facts");
  if (overview.year_high !== null && overview.year_high !== undefined
      && overview.year_low !== null && overview.year_low !== undefined) {
    const range = overview.price_start && overview.price_end
      ? `${readable(overview.price_start)} — ${readable(overview.price_end)}` : "区间未记录";
    facts.appendChild(kv("样本区间", range));
    facts.appendChild(kv("区间最高 / 最低",
      `${formatMetricValue('year_high',overview.year_high)} / ${formatMetricValue('year_low',overview.year_low)}`));
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
  if (facts.children.length) card.appendChild(facts);
  const meta = el("div", "report-summary-meta");
  if (report.generated_at) meta.appendChild(el("span", "", `生成时间：${readable(report.generated_at)}`));
  if (Number.isFinite(quality.coverage)) {
    const coverage = report.market === "hk" && Number.isInteger(quality.available_dimensions)
      ? `可用维度覆盖：${quality.available_dimensions}/${quality.applicable_dimensions}（${quality.coverage}%），不代表字段完整`
      : `${report.market === "hk" ? "分析数据覆盖" : "计分数据覆盖"}：${quality.coverage}%`;
    meta.appendChild(el("span", "", coverage));
  }
  if (meta.children.length) card.appendChild(meta);
  if (report.market === "hk") card.appendChild(el("div", "report-empty", "财务金额为财报原币，币种未记录，未换汇；不与港币行情混算历史估值。"));
  return card;
}

function scoreSection(report, sectionIdPrefix) {
  const card = section("report-score", "综合评分", "panel", sectionIdPrefix);
  if (report.market === "hk") {
    card.appendChild(el("div", "report-empty", "不适用：港股不套用 A 股综合评分、申万同行比较或交易信号。"));
    return card;
  }
  const score = record(report.score);
  const big = el("div", "score-big");
  const number = el("div", "score-num", scoreText(score.final, "暂无评分"));
  if (score.final !== null && score.final !== undefined && score.final !== "") {
    number.appendChild(el("small", "", "/10"));
  } else {
    number.classList.add("report-score-unavailable");
  }
  big.appendChild(number);

  const rows = el("div", "score-rows");
  for (const item of Array.isArray(report.score_rows) ? report.score_rows : []) {
    const rowData = record(item);
    const row = el("div", "score-row");
    row.appendChild(el("span", "lb", readable(rowData.label)));
    row.appendChild(el("span", "v", scoreText(rowData.score)));
    row.appendChild(priceBar(Number(rowData.score) * 10));
    row.appendChild(el("span", "wt", readable(rowData.weight, "")));
    rows.appendChild(row);
  }
  if (score.risk_deduction) {
    rows.appendChild(el("div", "score-note",
      `风险扣分: -${scoreText(score.risk_deduction)}`));
  }
  if (!rows.children.length) rows.appendChild(el("div", "report-empty", "暂无评分明细"));
  big.appendChild(rows);
  card.appendChild(big);
  return card;
}

function dimensionSection(name, label, data, sectionIdPrefix, unscored = false, applicable = true) {
  const card = section(`report-${name}`, label, "panel", sectionIdPrefix);
  if (!applicable) {
    card.appendChild(el("div", "report-empty", "不适用：本期港股分析不支持此维度，不计入覆盖率。"));
    return card;
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    card.appendChild(el("div", "report-empty", "暂无数据"));
    return card;
  }

  const dimension = record(data);
  const header = el("div", "dim-head report-dimension-heading");
  const title = card.children[0];
  title.remove();
  header.appendChild(title);
  header.appendChild(statusBadge(dimension.status));
  header.appendChild(el("span", "dim-score", unscored ? "评分不适用" : scoreText(dimension.score, "暂无评分")));
  card.appendChild(header);
  card.appendChild(markdownBlock(naturalText(dimension.summary), "dim-sum md"));
  if (!unscored && naturalText(dimension.score_detail)) {
    card.appendChild(el("div", "report-score-detail", `评分依据：${naturalText(dimension.score_detail)}`));
  }

  const metrics = record(dimension.metrics);
  if (Object.keys(metrics).length) {
    const grid = el("div", "mtr");
    for (const [metric, value] of Object.entries(metrics)) {
      if (metric === "peer_names") continue;
      if (metric === "peers") {
        grid.appendChild(kv(metricLabel(metric), peerText(value, metrics.peer_names)));
        continue;
      }
      appendReadableMetric(grid, metric, value, metrics);
    }
    card.appendChild(grid);
  }
  for (const flag of Array.isArray(dimension.risk_flags) ? dimension.risk_flags : []) {
    card.appendChild(el("span", "flag", `⚠ ${riskText(flag)}`));
  }
  return card;
}

function riskFlags(report) {
  const unique = new Set();
  const dimensions = record(report.dimensions);
  for (const data of Object.values(dimensions)) {
    for (const flag of Array.isArray(record(data).risk_flags) ? data.risk_flags : []) {
      const text = riskText(flag);
      if (text) unique.add(text);
    }
  }
  return [...unique];
}

function risksSection(report, sectionIdPrefix) {
  const card = section("report-risks", "汇总风险", "panel", sectionIdPrefix);
  const flags = riskFlags(report);
  if (!flags.length) {
    card.appendChild(el("div", "report-empty", "暂无风险提示"));
    return card;
  }
  const list = el("div", "report-detail-list report-risk-list");
  flags.forEach((flag, index) => {
    const row = el("div", "report-detail-row report-risk-item");
    row.appendChild(el("span", "k", `风险 ${String(index + 1).padStart(2, "0")}`));
    row.appendChild(el("span", "v", flag));
    list.appendChild(row);
  });
  card.appendChild(list);
  return card;
}

function commentaryParts(report) {
  const paragraphs = commentaryText(report)
    .split(/\n\s*\n/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean);
  if (!paragraphs.length) return { intro: "", points: [] };

  const intro = paragraphs[0];
  const points = [];
  for (const paragraph of paragraphs.slice(1)) {
    const lines = paragraph.split("\n").map((line) => line.trim()).filter(Boolean);
    const numbered = lines.filter((line) => /^\d+[.、)]\s+/.test(line));
    if (numbered.length) {
      points.push(...numbered.map((line) => line.replace(/^\d+[.、)]\s+/, "")));
    } else {
      points.push(paragraph);
    }
  }
  return { intro, points };
}

function commentarySection(report, sectionIdPrefix, options) {
  const card = section("report-commentary", "AI 解读", "llm", sectionIdPrefix);
  const state = report.commentary_status;
  if (state && state !== "ok") {
    const messages = {failed: "AI 解读生成失败，数据报告已保留。", disabled: "AI 解读未启用。", insufficient: "主要分析数据不足，未生成 AI 解读。", config: "AI 解读尚未配置。"};
    card.appendChild(el("div", "report-empty", messages[state] || "暂无 AI 解读"));
    const configure = state === "config";
    const callback = configure ? options.onConfigure : options.onRetryCommentary;
    if (!options.readOnly && (configure || state === "failed") && typeof callback === "function") {
      const button = el("button", configure ? "subs-btn report-configure-commentary" : "subs-btn report-retry-commentary",
        configure ? "配置 AI 解读" : options.commentaryBusy ? "正在生成…" : "重试 AI 解读");
      button.type = "button";
      button.disabled = Boolean(options.commentaryBusy);
      button.addEventListener("click", () => callback());
      card.appendChild(button);
    }
    if (options.commentaryError) {
      const error = el("div", "report-commentary-error", String(options.commentaryError));
      error.setAttribute("role", "alert");
      card.appendChild(error);
    }
    return card;
  }
  const { intro, points } = commentaryParts(report);
  if (intro) {
    card.appendChild(markdownBlock(intro, "report-commentary-intro dim-sum md"));
  }
  if (!points.length) {
    card.appendChild(el("div", "report-empty", "暂无 AI 解读"));
    return card;
  }
  const list = el("div", "report-detail-list report-commentary-list");
  points.forEach((point, index) => {
    const row = el("div", "report-detail-row report-commentary-item");
    row.appendChild(el("span", "k", `要点 ${String(index + 1).padStart(2, "0")}`));
    row.appendChild(markdownBlock(point, "v report-commentary-value md"));
    list.appendChild(row);
  });
  card.appendChild(list);
  return card;
}

function pendingSection(report, sectionIdPrefix) {
  const card = section("report-pending", "待验证事项", "panel", sectionIdPrefix);
  const items = Array.isArray(report.pending_verifications) ? report.pending_verifications : [];
  if (!items.length) {
    card.appendChild(el("div", "report-empty", "暂无待验证事项"));
    return card;
  }
  const list = el("ul", "report-pending-list");
  for (const item of items) list.appendChild(el("li", "", readable(item)));
  card.appendChild(list);
  return card;
}

function qualitySection(report, sectionIdPrefix) {
  const card = section("report-quality", "数据覆盖与时效", "panel", sectionIdPrefix);
  const quality = record(report.data_quality);
  if (quality.status) {
    const heading = el("div", "report-section-heading");
    const title = card.children[0];
    title.remove();
    heading.appendChild(title);
    const badge = statusBadge(quality.status);
    badge.textContent = (report.market === "hk"
      ? {ok: "分析数据可用", partial: "分析数据部分可用", unavailable: "数据不可用"}
      : {ok: "计分维度完整", partial: "计分维度不完整", unavailable: "数据不可用"})[quality.status] || "未记录";
    heading.appendChild(badge);
    card.appendChild(heading);
  }
  const supplied = Array.isArray(quality.dimensions) ? quality.dimensions : [];
  const qualityTitles = {financial: "财务", technical: "技术", valuation: "估值", industry: "行业", sentiment: "舆情"};
  const dimensions = supplied.length ? supplied : DIMENSIONS.map(([key]) => ({key, title: qualityTitles[key], fields: []}));
  for (const value of dimensions) {
    const dimension = record(value);
    const row = el("div", "report-quality-row");
    const heading = el("div", "report-quality-heading");
    heading.appendChild(el("h3", "", readable(dimension.title)));
    const dimensionBadge = statusBadge(dimension.status);
    if (dimension.status === "not_applicable") dimensionBadge.textContent = "不适用";
    heading.appendChild(dimensionBadge);
    row.appendChild(heading);
    const fields = Array.isArray(dimension.fields) ? dimension.fields.map(record) : [];
    const fixed = dimension.status === "not_applicable" ? [] : ["样本范围", "数据来源", "数据截止"];
    const ordered = fixed.map(label => ({label, value: fields.find(field => field.label === label)?.value ?? "未记录"}));
    ordered.push(...fields.filter(field => !fixed.includes(field.label)));
    const grid = el("dl", "report-quality-fields");
    for (const field of ordered) {
      const item = el("div", "report-quality-field");
      if (field.label === "数据来源") item.classList.add("report-quality-source");
      else if (field.label === "数据截止") item.classList.add("report-quality-date");
      else if (field.label !== "样本范围") item.classList.add("report-quality-wide");
      if (field.label === "缺失原因" || field.label === "分析限制") item.classList.add("report-quality-limitation");
      item.appendChild(el("dt", "", readable(field.label)));
      item.appendChild(el("dd", "", readable(field.value, "未记录")));
      grid.appendChild(item);
    }
    row.appendChild(grid);
    card.appendChild(row);
  }
  return card;
}

export function renderStockReport(report, options = {}) {
  const data = record(report);
  const sectionIdPrefix = options.sectionIdPrefix || "";
  const article = el("article", "stock-report");
  if (options.className) article.classList.add(String(options.className));
  article.appendChild(summarySection(data, sectionIdPrefix));
  article.appendChild(scoreSection(data, sectionIdPrefix));

  const dimensions = record(data.dimensions);
  const applicability = Array.isArray(data.data_quality?.dimensions) ? data.data_quality.dimensions : [];
  for (const [name, label] of DIMENSIONS) {
    const applicable = applicability.find(item => item.key === name)?.applicable !== false;
    article.appendChild(dimensionSection(name, label, dimensions[name], sectionIdPrefix, data.market === "hk", applicable));
  }
  article.appendChild(risksSection(data, sectionIdPrefix));
  article.appendChild(commentarySection(data, sectionIdPrefix, options));
  if (options.archiveMarkdown) {
    // 原文补充章沿用安全 Markdown 渲染，未知章节仍保留在原文入口。
    const parts = String(options.archiveMarkdown).split(/\n(?=##\s+)/);
    for (const [index, part] of parts.entries()) {
      const match = part.match(/^##\s+(.+)$/m);
      if (!match || !/多风格观察|工具局限性/.test(match[1])) continue;
      const title = match[1].replace(/^[一二三四五六七八九十]+[、.．]\s*/, "");
      const card = section(`report-archive-${index}`, title, "panel", sectionIdPrefix);
      card.appendChild(markdownBlock(localizeReportMarkdown(part.replace(/^##\s+.+\n?/, "")), "md report-archive-body"));
      article.appendChild(card);
    }
  }
  article.appendChild(pendingSection(data, sectionIdPrefix));
  article.appendChild(qualitySection(data, sectionIdPrefix));
  return article;
}

export function renderStockMarkdownReport(markdown, options = {}) {
  // 将报告库的历史 Markdown 放入与实时个股报告一致的章节卡片体系。
  const article = el("article", "stock-report");
  if (options.className) article.classList.add(String(options.className));
  const sectionIdPrefix = options.sectionIdPrefix || "";
  const source = String(markdown || "").trim();
  const titleMatch = source.match(/^#\s+(.+)$/m);
  const title = titleMatch?.[1] || "个股分析报告";
  const parts = source.replace(/^#\s+.+\n?/, "").split(/\n(?=##\s+)/);

  const heading = section("report-summary", "投资摘要", "panel", sectionIdPrefix);
  heading.appendChild(el("div", "stock-name", title));
  heading.appendChild(markdownBlock(parts.shift() || "暂无报告摘要", "report-investment-summary md"));
  article.appendChild(heading);

  parts.forEach((part, index) => {
    const match = part.match(/^##\s+(.+)$/m);
    const label = match?.[1] || `报告章节 ${index + 1}`;
    const card = section(`report-library-${index}`, label, "panel", sectionIdPrefix);
    card.appendChild(markdownBlock(localizeReportMarkdown(
      part.replace(/^##\s+.+\n?/, "")), "md"));
    article.appendChild(card);
  });
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
  const report = normalizeArtifactReport(item);
  const card = el("article", "report-summary-card");
  card.dataset.artifactId = readable(item.artifact_id ?? item.id, "");

  const header = el("div", "report-summary-header");
  header.appendChild(el("span", "report-summary-name",
    readable(report.name, reportSymbol(report))));
  header.appendChild(el("span", "report-summary-code", reportSymbol(report)));
  const score = record(report.score);
  if (score.final !== null && score.final !== undefined && score.final !== "") {
    header.appendChild(el("span", "report-summary-score", `${scoreText(score.final)}/10`));
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
