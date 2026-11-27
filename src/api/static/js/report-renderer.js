// 结构化股票报告共享渲染器：不读取业务状态，也不发起网络请求。
import { renderMarkdown } from "./markdown.js?v=20261127-task19-final";
import { el, kv, priceBar, statusBadge } from "./components.js?v=20261127-task19-final";

export const STOCK_NEWS_DISPLAY_LIMIT = 10;

const DIMENSIONS = [
  ["financial", "财务健康"],
  ["technical", "技术趋势"],
  ["valuation", "估值合理"],
  ["industry", "行业对比"],
  ["sentiment", "舆情风险"],
];

// 数据层字段只在内部协议中使用；界面统一显示业务中文名称。
const METRIC_LABELS = {
  basic_eps: "基本每股收益", common_equity: "普通股东权益",
  revenue_period: "营业收入期间", net_profit_period: "净利润期间",
  capital_expenditure: "资本开支", free_cash_flow: "自由现金流",
  capital_expenditure_latest: "最近完整年度资本开支（非 TTM）",
  free_cash_flow_latest: "最近完整年度自由现金流（非 TTM）",
  cash_flow_period: "现金流期间与口径", cash_flow_limitation: "现金流限制",
  cash_flow_latest_period: "最近完整年度现金流期间与币种",
  deducted_profit_growth_yoy: "扣非净利润同比增长", cash_flow_growth_yoy: "经营现金流同比增长",
  net_margin: "净利率", net_margin_basis: "净利率口径",
  dividend_yield_basis: "股息率口径", used_in_score: "是否计入评分",
  amount_currency_reason: "金额币种核验",
  reported_annual_roe: "年度披露 ROE（未年化）",
  reported_annual_roe_period: "年度 ROE 报告期", reported_annual_roe_source: "年度 ROE 来源",
  reported_revenue: "营业收入（SEC，不含投资损益，单季）",
  reported_revenue_ttm: "营业收入（SEC，不含投资损益，TTM）",
  reported_revenue_basis: "SEC 营业收入口径", reported_revenue_source: "SEC 营业收入来源",
  reported_revenue_period: "SEC 营业收入期间", ps_as_of: "PS数据日期",
  extended_valuation_history: "长期估值历史", window: "窗口", start: "起始日期", end: "截止日期",
  reference: "同源参照值", percentile: "历史分位（%）", pe: "PE",

  "net_interest_income_ratio_pct": "净利息收入占比",
  "provision_to_revenue_pct": "拨备／营收",
  "loan_to_deposit_pct": "贷存比",
  "loan_to_assets_pct": "贷款／资产",
  "roe_pct": "银行 ROE",
  "roa_pct": "银行 ROA",
  operating_trends: "经营趋势", trend_note: "趋势分析限制",
  cash_flow_source: "经营现金流来源", deducted_net_profit: "扣非净利润",
  amount_basis: "财务金额口径", gross_margin_basis: "毛利率计算口径",
  valuation_applicability: "估值适用性", applicable: "可用于高低估判断", reason: "原因",
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
  year_high: "采集区间最高价", year_low: "采集区间最低价", price_position: "价格位置",
  latest_quarter: "最新报告期", revenue: "营业收入", net_profit: "净利润",
  total_assets: "总资产", total_equity: "股东权益", operating_cash_flow: "经营活动现金流",
  operating_cash_flow_per_share: "每股经营现金流", revenue_growth_yoy: "营收同比增长",
  profit_growth_yoy: "净利润同比增长", roe: "净资产收益率", gross_margin: "毛利率",
  ma_5: "5 日均线", ma_20: "20 日均线", ma_60: "60 日均线",
  pe_ttm: "市盈率（TTM）", pe_static: "PE（静）", pe_lyr: "PE（静）", pb: "市净率", ps_ttm: "市销率（TTM）",
  pe_percentile: "市盈率分位", pb_percentile: "市净率分位", dividend_yield: "股息率",
  industry: "所属行业", sector: "所属板块", peer_count: "有效同行数",
  peers: "同行业公司", peer_total: "全部同行数", peer_display_note: "同行展示范围",
  peer_scope: "同行范围", peer_industry: "同行所属行业",
  top_peers: "头部同行公司", symbol: "股票代码", name: "公司名称", market_cap: "总市值",
  industry_median_pe: "行业市盈率中位数", industry_median_pb: "行业市净率中位数",
  target_pe_premium: "相对行业市盈率溢价", target_market_cap_rank: "行业市值排名",
  classification_scheme: "分类体系", industry_source: "行业数据来源",
  industry_data_date: "行业数据日期", industry_scheme_note: "分类口径说明",
  target_pe: "目标市盈率（行业池口径）", target_pb: "目标市净率（行业池口径）",
  target_pe_percentile: "相对行业市盈率分位", target_pb_percentile: "相对行业市净率分位",
  peer_pe_sample: "有效市盈率样本数", peer_pb_sample: "有效市净率样本数",
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
  negative_equity: "净资产为负（资不抵债风险）",
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
const MULTIPLE_KEYS = new Set(['pe_ttm','pe_static','pe_lyr','pe_snapshot','pb','ps_ttm','industry_median_pe','industry_median_pb','target_pe','target_pb']);
const RATIO_KEYS = new Set(['reported_annual_roe','roe','gross_margin','net_margin','deducted_profit_growth_yoy','cash_flow_growth_yoy','revenue_growth_yoy','profit_growth_yoy','industry_median_gross_margin']);
const PERCENT_KEYS = new Set(['change_pct','pe_percentile','pb_percentile','target_pe_premium','dividend_yield','shibor_3m','cpi_yoy','shibor_percentile','pmi_percentile','target_pe_percentile','target_pb_percentile']);
const AMOUNT_KEYS = new Set(['reported_revenue','reported_revenue_ttm','revenue','net_profit','deducted_net_profit','total_assets','total_equity','common_equity','operating_cash_flow','net_profit_ttm','capital_expenditure','free_cash_flow','capital_expenditure_latest','free_cash_flow_latest','market_cap']);
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
  if (key === 'operating_trends' && (!Array.isArray(value) || value.length === 0)) return;
  // 适用性结构供机器消费；正文用原因列表和对应指标状态展示，避免重复展开。
  if (key === 'valuation_applicability' || key === 'historical_valuation_applicability' || key === 'valuation_details') return;
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
  return text.replace(/\b(?:roe_low|negative_equity|high_debt|cash_flow_mismatch|high_pe_premium|industry_weak_margin|bearish_ma|volume_bearish|major_negative_news|bull|shake|bear|undervalued|overvalued|invalid|positive|negative|neutral|na)\b/g,
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

function reportTime(value) {
  const text = naturalText(value);
  if (/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(text)) {
    const instant = new Date(text);
    if (!Number.isNaN(instant.getTime())) {
      return `${new Date(instant.getTime() + 8 * 3600000).toISOString().slice(0, 19).replace("T", " ")} UTC+8`;
    }
  }
  const match = text.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})/);
  return match ? `${match[1]} ${match[2]}${text.includes("UTC+8") ? " UTC+8" : ""}` : text;
}

function compactScoreDetail(value) {
  return naturalText(value).replace(/；有效子项 [\d.]+\/[\d.]+ 分，按原相对权重折算为 [\d.]+\/10/g, "");
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
  {
    const industryQuote = el("div", "report-industry-quote");
    industryQuote.appendChild(el("span", "chip ind", readable(overview.industry, "行业未记录")));
    const current = record(overview.current_price);
    const value = current.value ?? overview.latest_close;
    const note = naturalText(current.note) || (value != null ? `最新收盘价回退；截至 ${readable(overview.price_end, "未记录")}` : "暂无数据");
    const price = el("span", "report-latest-close", `当前价格：${formatMetricValue('latest_close', value)}（${note}）`);
    if (current.source) price.title = `当前价格来源：${naturalText(current.source)}`;
    industryQuote.appendChild(price);
    left.appendChild(industryQuote);
  }
  header.appendChild(left);

  const right = el("div", "price-box");
  if (overview.change_pct !== null && overview.change_pct !== undefined) {
    const pct = Number(overview.change_pct);
    const direction = pct > 0 ? "up" : pct < 0 ? "down" : "flat";
    const sign = pct > 0 ? "+" : "";
    right.appendChild(el("span", `chg ${direction}`, `${sign}${formatMetricValue('change_pct',overview.change_pct)}`));
  }
  header.appendChild(right);
  return header;
}

function fiveDimension(report) {
  return report.scoring_method === "five-dimension-v1";
}

function legacyHk(report) {
  return report.market === "hk" && !fiveDimension(report);
}

function summarySection(report, sectionIdPrefix) {
  const card = section("report-summary", "标的基础概况", "panel", sectionIdPrefix);
  card.appendChild(reportHeader(report));
  if (report.analysis_mode === "objective") card.appendChild(el("p", "dim-sum", "客观数据模式"));
  const quality = record(report.data_quality);
  if (quality.status) {
    const badge = statusBadge(quality.status);
    badge.textContent = (legacyHk(report)
      ? {ok: "分析数据可用 · 评分不适用", partial: "部分分析数据可用 · 评分不适用", unavailable: "数据不可用 · 暂无结论"}
      : {ok: "数据完整", partial: "数据不完整", unavailable: "数据不可用 · 暂无结论"})[quality.status] || "数据状态未记录";
    if (report.report_scope === "facts_only") badge.textContent = "事实报告 · 不形成综合评估";
    const assessment = report.assessment_status || quality.assessment_status;
    if (assessment === "partial") badge.textContent = quality.score_label || "部分评估（参考）";
    if (assessment === "facts_only") badge.textContent = "事实报告 · 不形成综合评估";
    card.appendChild(badge);
  }
  const overview = record(report.overview);
  const facts = el("div", "kv report-summary-facts");
  if (overview.year_high !== null && overview.year_high !== undefined
      && overview.year_low !== null && overview.year_low !== undefined) {
    const range = overview.price_start && overview.price_end
      ? `${readable(overview.price_start)} — ${readable(overview.price_end)}` : "区间未记录";
    facts.appendChild(kv("样本区间", `${range}（${overview.price_bars ?? "未记录"} 个交易日）`));
    facts.appendChild(kv("区间最高 / 最低",
      `${formatMetricValue('year_high',overview.year_high)} / ${formatMetricValue('year_low',overview.year_low)}`));
  }
  if (overview.price_position && overview.price_position !== "暂无") {
    const position = el("div");
    position.appendChild(el("div", "k", "采集区间价格位置"));
    const value = el("div", "v");
    value.appendChild(el("span", "", readable(overview.price_position)));
    if (/^\d+%$/.test(overview.price_position)) value.appendChild(priceBar(parseInt(overview.price_position, 10)));
    position.appendChild(value);
    facts.appendChild(position);
  }
  if (facts.children.length) card.appendChild(facts);
  const meta = el("div", "report-summary-meta");
  if (report.generated_at) {
    const stamp = el("div", "report-generation-info");
    stamp.appendChild(el("div", "", `生成时间：${reportTime(report.generated_at)}`));
    stamp.appendChild(el("div", "", "报告定位：以中长期跟踪评估为主，兼容波段参考。"));
    meta.appendChild(stamp);
  }
  if (Number.isFinite(quality.coverage)) {
    const coverage = legacyHk(report) && Number.isInteger(quality.available_dimensions)
      ? `可用维度覆盖：${quality.available_dimensions}/${quality.applicable_dimensions}（${quality.coverage}%），不代表字段完整`
      : `${report.analysis_mode === "objective" || legacyHk(report) ? "分析数据覆盖" : "计分数据覆盖"}：${quality.coverage}%`;
    meta.appendChild(el("span", "", coverage));
  }
  if (fiveDimension(report) && quality.coverage_basis) meta.appendChild(el("span", "", quality.coverage_basis));
  if (meta.children.length) card.appendChild(meta);
  if (report.market === "hk") card.appendChild(el("div", "report-empty", "财务金额按数据源原币口径展示，币种未核实时不作金额趋势比较；不与港币行情混算历史估值。"));
  return card;
}

function scoreSection(report, sectionIdPrefix) {
  const card = section("report-score", report.data_quality?.score_label || "综合评分", "panel", sectionIdPrefix);
  if (legacyHk(report)) {
    card.appendChild(el("div", "report-empty", "不适用：港股不套用 A 股综合评分或交易信号；行业对比使用恒生 HSICS 口径。"));
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
    row.appendChild(el("span", "v", scoreText(rowData.score, report.market === "us" ? "N/A" : "—")));
    const valid = scoreText(rowData.score, "") !== "";
    row.appendChild(priceBar(valid ? Number(rowData.score) * 10 : 0));
    if (fiveDimension(report) && !valid) row.classList.add("score-row-missing");
    row.appendChild(el("span", "wt", readable(rowData.weight, "")));
    rows.appendChild(row);
  }
  if (!fiveDimension(report) && score.risk_deduction) {
    rows.appendChild(el("div", "score-note",
      `风险扣分: -${scoreText(score.risk_deduction)}`));
  }
  if (!rows.children.length) rows.appendChild(el("div", "report-empty", "暂无评分明细"));
  big.appendChild(rows);
  card.appendChild(big);
  if (fiveDimension(report)) {
    for (const row of report.score_rows || []) {
      if (row.detail) card.appendChild(el("p", "report-score-detail", `${readable(row.label)}：${compactScoreDetail(row.detail)}`));
    }
  }
  // 旧存档的评分行可能缺少依据，回退到同维度已有文字，不推算分数。
  const dimensions = record(report.dimensions);
  const scoreRows = Array.isArray(report.score_rows) ? report.score_rows : [];
  for (const [key, label] of DIMENSIONS) {
    const detail = naturalText(record(dimensions[key]).score_detail);
    if (detail && !scoreRows.some(row => record(row).label === label && naturalText(record(row).detail))) {
      card.appendChild(el("p", "report-score-detail", `${label}：${compactScoreDetail(detail)}`));
    }
  }
  return card;
}

function comparisonNumber(value, unit = "") {
  if (value === null || value === undefined || value === "") return "暂无数据";
  if (typeof value !== "number" || !Number.isFinite(value)) return "数据异常";
  return value.toFixed(2) + (unit === "percent" ? "%" : "");
}

function comparisonTable(headers, rows, className, captionText) {
  const table = el("table", className);
  table.appendChild(el("caption", "peer-sr-only", captionText));
  const head = el("thead"), header = el("tr");
  for (const label of headers) {
    const cell = el("th", "", label);
    cell.setAttribute("scope", "col");
    header.appendChild(cell);
  }
  head.appendChild(header);
  table.appendChild(head);
  const body = el("tbody");
  for (const cells of rows) {
    const row = el("tr");
    cells.forEach((value, index) => {
      const cell = el("td", "", value);
      cell.setAttribute("data-label", headers[index]);
      row.appendChild(cell);
    });
    body.appendChild(row);
  }
  table.appendChild(body);
  return table;
}

const REDUNDANT_PEER_NOTES = new Set([
  "分位表示数值位置，采用低于目标比例加相等值比例的一半，不直接等同经营优劣。",
  "各指标独立统计，至少 5 家有效同行；目标不计入分母。",
]);

function comparisonBlock(block, operating = false) {
  const result = el("div", "peer-block");
  const label = readable(block.label, "未记录");
  const start = readable(block.start, "未记录");
  const end = readable(block.end, "未记录");
  const range = `${start} 至 ${end}`;
  const period = operating
    ? `报告期：${label.includes(range) ? label : `${label}；${range}`} · 口径：${readable(block.basis, "未记录")}`
    : `估值日期：${readable(block.as_of, "未记录")}`;
  result.appendChild(el("p", "peer-caption", `${period} · 数据来源：${readable(block.source, "未记录")}`));
  const note = text => {
    const value = readable(text, "").trim();
    if (value) result.appendChild(el("p", "peer-caption", value));
  };
  if (block.reference_only) note("仅参考，不计分。");
  if (block.reason) note(block.reason);
  const metrics = Array.isArray(block.metrics) ? block.metrics : [];
  if (metrics.length) {
    const rows = metrics.map(item => {
      const enough = typeof item.sample_count === "number" && item.sample_count >= 5;
      const label = readable(item.label, item.key || "指标");
      return [label, comparisonNumber(item.target, item.unit),
        enough ? comparisonNumber(item.median, item.unit) : "样本不足",
        enough ? comparisonNumber(item.percentile, "percent") : "—", `${item.sample_count ?? 0} 家`];
    });
    result.appendChild(comparisonTable(["指标", "目标公司", "同行中位数", "数值分位", "有效同行"], rows,
      "peer-summary", operating ? "目标公司的经营指标与同期同行比较" : "目标公司的估值与同行比较"));
    for (const item of metrics) {
      if (item.reason) note(`${readable(item.label, item.key)}：${item.reason}`);
    }
  } else if (!block.reason) {
    result.appendChild(el("p", "peer-caption", "暂无可比较指标"));
  }
  if (!operating && block.show_source_rows) {
    const peers = [record(block.target), ...(Array.isArray(block.peers) ? block.peers : [])];
    const rows = peers.filter(row => row.symbol).map(row => [row.symbol, readable(row.source, "未记录"), readable(row.pb_source, "期间未核实")]);
    result.appendChild(comparisonTable(["代码", "来源", "PB期间（PB_SOURCE）"], rows,
      "peer-sources", "目标与选中同行的数据来源"));
  }
  for (const key of ["notes", "errors"]) {
    for (const value of Array.isArray(block[key]) ? block[key] : []) note(value);
  }
  return result;
}

function industryComparisonSection(dimension, sectionIdPrefix, newScoring = false) {
  const comparison = record(record(dimension.metrics).comparison);
  const card = section("report-industry", "行业对比", "panel peer-comparison", sectionIdPrefix);
  const header = el("div", "dim-head report-dimension-heading");
  const title = card.children[0];
  title.remove();
  header.appendChild(title);
  header.appendChild(statusBadge(dimension.status));
  const value = el("span", "dim-score", newScoring ? scoreText(dimension.score, "暂无评分") : "不计分");
  if (newScoring) value.setAttribute("aria-label", "行业得分");
  header.appendChild(value);
  card.appendChild(header);
  appendRiskFlags(card, dimension);
  if (newScoring) {
    card.appendChild(el("p", "peer-caption", "行业评分仅采用同期经营指标；估值对比在估值维度使用。"));
  }
  card.appendChild(el("p", "peer-caption", `同行范围：${readable(comparison.scope, "未记录")} · 同行总数：${comparison.peer_count ?? 0} 家`));
  const rank = record(comparison.market_cap_rank);
  if (Number.isInteger(rank.rank) && Number.isInteger(rank.total) && rank.rank >= 1 && rank.rank <= rank.total && rank.as_of) {
    const coverage = rank.coverage || `${rank.total}/${rank.pool_total ?? rank.total}`;
    card.appendChild(el("p", "peer-caption", `同行市值排名：第 ${rank.rank}／${rank.total} 家（同日有效覆盖 ${coverage}，${rank.as_of}）`));
  } else if (rank.reason) {
    card.appendChild(el("p", "peer-caption", `同行市值排名暂缺：${rank.reason}`));
  }
  if (comparison.classification_as_of) {
    card.appendChild(el("p", "peer-caption", `分类快照：${String(comparison.classification_as_of).slice(0,10)}（刷新失败时可能为旧快照）`));
  }
  const shownNotes = new Set();
  const addNote = text => {
    const value = readable(text, "").trim();
    if (!value || shownNotes.has(value) || REDUNDANT_PEER_NOTES.has(value)) return;
    shownNotes.add(value);
    card.appendChild(el("p", "peer-caption", value));
  };
  for (const note of [...(Array.isArray(comparison.errors) ? comparison.errors : []), ...(Array.isArray(comparison.notes) ? comparison.notes : [])]) {
    addNote(note);
  }
  card.appendChild(el("h3", "peer-subtitle", "估值对比"));
  const valuation = record(comparison.valuation);
  card.appendChild(comparisonBlock(valuation, false));
  const special = record(comparison.financial_special);
  if (Array.isArray(special.metrics) && special.metrics.length) {
    card.appendChild(el("h3", "peer-subtitle", "银行专项指标"));
    card.appendChild(comparisonBlock(special, true));
  }
  const heading = el("div", "peer-business-heading");
  heading.appendChild(el("h3", "peer-subtitle", "经营表现"));
  const tabs = el("div", "peer-period-tabs");
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", "经营表现期间");
  heading.appendChild(tabs);
  card.appendChild(heading);
  const panel = el("div", "peer-business-panel");
  panel.id = `${card.id}-business`;
  panel.setAttribute("role", "tabpanel");
  panel.setAttribute("aria-live", "polite");
  card.appendChild(panel);
  const methods = el("details", "peer-methods");
  methods.appendChild(el("summary", "", "数据来源与统计口径"));
  const list = el("ul");
  for (const text of ["每项指标独立筛选，至少 5 家有效同行才给出中位数和数值分位；目标公司不计入样本。",
    "PE、PB 仅正有限值参与统计；经营指标保留负值，缺失不填 0；只比较相同期间、累计或单期及计算口径的数据。",
    "分位采用低于目标比例加相等值比例的一半，表示数值位置，不直接表示经营优劣；相对折价不直接等同于低估。",
    `估值来源：${readable(valuation.source, "未记录")}`]) list.appendChild(el("li", "", text));
  methods.appendChild(list);
  card.appendChild(methods);
  const buttons = [];
  function selectPeriod(key) {
    const block = record(record(comparison.periods)[key]);
    for (const button of buttons) {
      const selected = button.dataset.period === key;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
    }
    panel.setAttribute("aria-labelledby", `${card.id}-tab-${key}`);
    panel.replaceChildren(comparisonBlock(block, true));
  }
  const annual = record(record(comparison.periods).annual);
  const referenceAnnual = annual.reference_only === true;
  const usTtm = String(comparison.scope || "").startsWith("东财美股") && !record(comparison.periods).annual;
  const periodChoices = usTtm ? [["latest", "TTM"]] : [
    ["annual", referenceAnnual ? naturalText(annual.label) || "完整年度（参考）" : "完整年度"],
    ["latest", referenceAnnual ? "TTM最新期" : "最新一期"],
  ];
  for (const [key, label] of periodChoices) {
    const button = el("button", "peer-period-button", label);
    button.type = "button";
    button.id = `${card.id}-tab-${key}`;
    button.dataset.period = key;
    button.setAttribute("role", "tab");
    button.setAttribute("aria-controls", panel.id);
    button.addEventListener("click", () => selectPeriod(key));
    button.addEventListener("keydown", event => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? buttons[0] : event.key === "End" ? buttons[buttons.length - 1] : buttons.find(item => item !== button) || button;
      selectPeriod(next.dataset.period);
      next.focus();
    });
    tabs.appendChild(button);
    buttons.push(button);
  }
  selectPeriod(usTtm || referenceAnnual ? "latest" : "annual");
  return card;
}

function dimensionSection(name, label, data, sectionIdPrefix, unscored = false, applicable = true, newScoring = false, objective = false) {
  if (name === "industry" && Object.keys(record(record(record(data).metrics).comparison)).length) {
    const comparison = industryComparisonSection(record(data), sectionIdPrefix, newScoring && !objective);
    if (objective) comparison.querySelectorAll(".dim-score").forEach(node => node.remove());
    return comparison;
  }
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
  if (!objective) header.appendChild(el("span", "dim-score", name === "industry" && !newScoring ? "不计分" : unscored ? "评分不适用" : scoreText(dimension.score, "暂无评分")));
  card.appendChild(header);
  card.appendChild(markdownBlock(naturalText(dimension.display?.summary ?? dimension.summary), "dim-sum md"));

  const display = record(dimension.display);
  if (display.cutoff) card.appendChild(el("div", "dim-sum", display.cutoff));
  const metrics = Object.keys(record(display.metrics)).length ? record(display.metrics) : record(dimension.metrics);
  if (Object.keys(metrics).length) {
    const grid = el("div", "mtr");
    for (const [metric, rawValue] of Object.entries(metrics)) {
      const value = name === "sentiment" && ["headlines", "news_items", "all_items"].includes(metric) && Array.isArray(rawValue)
        ? rawValue.slice(0, STOCK_NEWS_DISPLAY_LIMIT) : rawValue;
      if (metric === "trend_note" && !naturalText(value)) continue;
      if (name === "financial" && (value == null || value === "")) {
        const reason = metric === "common_equity"
          ? "源未提供可核验的普通股东权益；不以股东权益替代"
          : "源未披露可核验值；不作推算";
        grid.appendChild(kv(metricLabel(metric), reason));
        continue;
      }
      if (metric === "peer_names" || metric === "roe_trend") continue;
      if (metric === "trend_note" && !naturalText(value)) continue;
      if (name === "financial" && ["annual_roe", "scoring_basis", "amount_basis", "gross_margin_basis"].includes(metric)) continue;
      if (name === "industry" && ["peers", "top_peers", "peer_display_note"].includes(metric)) continue;
      if (metric === "peers") {
        grid.appendChild(kv(metricLabel(metric), peerText(value, metrics.peer_names)));
        continue;
      }
      if (name === "valuation" && metric === "valuation_notes" && Array.isArray(value)) {
        const rawPs = record(dimension.metrics).ps_ttm;
        const notes = value.filter(note => !(rawPs == null && /^PS\(TTM\)\s*[:：]\s*暂无数据\s*$/.test(String(note))));
        if (notes.length) appendReadableMetric(grid, metric, notes, metrics);
      } else appendReadableMetric(grid, metric, value, metrics);
    }
    card.appendChild(grid);
    if (name === "financial") {
      for (const key of ["amount_basis", "gross_margin_basis"]) {
        if (naturalText(metrics[key])) card.appendChild(el("p", "dim-sum", `${metricLabel(key)}：${naturalText(metrics[key])}`));
      }
    }
  }
  if (name === "valuation" && Array.isArray(display.details)) {
    for (const text of display.details.filter(Boolean)) card.appendChild(el("p", "dim-sum", String(text)));
  }
  appendRiskFlags(card, dimension);
  return card;
}

function hkNewsSection(data, sectionIdPrefix) {
  const card = section("report-sentiment", "相关新闻检索", "panel", sectionIdPrefix);
  const dimension = record(data);
  const metrics = record(dimension.metrics);
  const items = Array.isArray(metrics.news_items) ? metrics.news_items.slice(0, STOCK_NEWS_DISPLAY_LIMIT) : [];
  if (!items.length) {
    card.appendChild(el("p", "report-empty", dimension.summary || "新闻暂时获取失败"));
    return card;
  }
  const rows = items.map(item => {
    const stamp = String(item.published_at || "").replace("T", " ").slice(0, 16);
    const media = [...new Set((Array.isArray(item.links) ? item.links : [])
      .map(link => String(link.media || "来源未标注")))].join("、") || "来源未标注";
    return `${item.title || ""}（${stamp} · ${media}）`;
  });
  const grid = el("div", "mtr");
  appendListMetric(grid, "headlines", rows);
  card.appendChild(grid);
  return card;
}

function safeNewsUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function newsFactsSection(data, sectionIdPrefix) {
  const card = section("report-sentiment", "相关新闻", "panel", sectionIdPrefix);
  const dimension = record(data), metrics = record(dimension.metrics);
  const list = el("ul", "report-metric-list sentiment-source-list");
  const items = Array.isArray(metrics.news_items) && metrics.news_items.length ? metrics.news_items
    : (metrics.headlines || []).map(title => ({title}));
  for (const item of items.slice(0, STOCK_NEWS_DISPLAY_LIMIT)) {
    const row = el("li");
    row.appendChild(el("span", "", `${readable(item.title, "未记录标题")}（${readable(item.publish_date || item.published_at, "日期未记录")}）`));
    const links = Array.isArray(item.links) && item.links.length ? item.links : [{url: item.url, media: item.media || item.source}];
    for (const link of links) {
      const url = safeNewsUrl(link.url), label = readable(link.media, "来源未标注");
      if (url) {
        const source = el("a", "sentiment-source-link", label);
        source.setAttribute("href", url); source.setAttribute("target", "_blank"); source.setAttribute("rel", "noopener noreferrer");
        row.appendChild(source);
      } else row.appendChild(el("span", "peer-caption", ` · ${label}`));
    }
    list.appendChild(row);
  }
  if (list.children.length) card.appendChild(list);
  else card.appendChild(el("p", "dim-sum", dimension.summary || "本次暂无可展示新闻"));
  return card;
}

function sentimentRiskSection(data, sectionIdPrefix) {
  const card = section("report-sentiment", "舆情风险", "panel sentiment-risk", sectionIdPrefix);
  const dimension = record(data), metrics = record(dimension.metrics);
  const risk = scoreText(metrics.risk_score, "无法评估");
  const heading = el("div", "dim-head report-dimension-heading");
  const title = card.children[0];
  title.remove();
  heading.appendChild(title);
  heading.appendChild(el("span", "sentiment-risk-value", `${risk}${risk === "无法评估" ? "" : " /10"} · 越高风险越大`));
  card.appendChild(heading);
  appendRiskFlags(card, dimension);
  card.appendChild(el("p", "peer-caption", `评估窗口：${readable(metrics.window_start, "未记录")} 至 ${readable(metrics.window_end, "未记录")} · 已评估${metrics.evaluated_count ?? 0}条`));
  if (metrics.assessment_reason || dimension.summary) card.appendChild(el("p", "dim-sum", metrics.assessment_reason || dimension.summary));
  const participation = scoreText(metrics.participation_score, "");
  if (participation) card.appendChild(el("p", "report-score-detail", `综合参与分 ${participation} /10 · 权重5%`));
  const assessed = Array.isArray(metrics.all_items) ? metrics.all_items.map(record) : [];
  const news = Array.isArray(metrics.news_items) ? metrics.news_items.map(record) : [];
  const identity = item => JSON.stringify([item.title || "", String(item.publish_date || item.published_at || "").slice(0, 10)]);
  const byId = new Map(assessed.filter(item => item.item_id).map(item => [item.item_id, item]));
  const byDateTitle = new Map(assessed.map(item => [identity(item), item]));
  const identities = new Set(news.map(identity));
  const items = news.length ? [...news.map(item => ({...(byId.get(item.item_id) || byDateTitle.get(identity(item))), ...item})),
    ...assessed.filter(item => !identities.has(identity(item)))] : assessed;
  const list = el("ul", "report-metric-list sentiment-source-list");
  for (const item of items.slice(0, STOCK_NEWS_DISPLAY_LIMIT)) {
    const row = el("li");
    const state = item.related === false ? "与目标无直接关联" : item.assessable === false ? "不可评估"
      : item.related === true && item.assessable === true ? "有效相关样本" : "标注状态未记录";
    row.appendChild(el("span", "", `${readable(item.title, "未记录标题")}（${readable(item.publish_date || item.published_at, "日期未记录")}）`));
    const links = Array.isArray(item.links) && item.links.length ? item.links : [{url: item.url, media: item.media || item.source}];
    for (const link of links) {
      const url = safeNewsUrl(link.url);
      const label = readable(link.media, "来源未标注");
      if (url) {
        const source = el("a", "sentiment-source-link", label);
        source.setAttribute("href", url);
        source.setAttribute("target", "_blank");
        source.setAttribute("rel", "noopener noreferrer");
        row.appendChild(source);
      } else row.appendChild(el("span", "peer-caption", ` · ${label}`));
    }
    row.appendChild(el("span", "peer-caption", ` · ${state}`));
    if (item.evidence) row.appendChild(el("div", "peer-caption", `依据：${item.evidence}`));
    list.appendChild(row);
  }
  if (list.children.length) {
    const details = el("details", "sentiment-sources");
    details.appendChild(el("summary", "", "查看采集样本与来源"));
    details.appendChild(list);
    card.appendChild(details);
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

function appendRiskFlags(card, dimension) {
  const flags = Array.isArray(dimension.risk_flags) ? dimension.risk_flags : [];
  for (const flag of new Set(flags.map(riskText).filter(Boolean))) {
    card.appendChild(el("span", "flag", `⚠ ${flag}`));
  }
}

function researchRows(labels, values) {
  const list = el("dl", "report-research-rows");
  labels.forEach((label, index) => {
    const row = el("div", "report-research-row");
    row.appendChild(el("dt", "", label));
    row.appendChild(el("dd", "", naturalText(values[index]) || "证据不足，暂不能判断。"));
    list.appendChild(row);
  });
  return list;
}

function researchBody(research, state) {
  const box = el("div", "report-research");
  const add = (parent, ...children) => children.forEach(child => parent.appendChild(child));
  const profile = record(research.business_profile);
  const model = state === "ok" ? record(research.model) : {};
  const rawBusiness = naturalText(model.business) || naturalText(profile.main_business);
  const business = naturalText(research.business_text) || (rawBusiness.length > 160 ? "主营资料已取得，简要介绍暂不可用。" : rawBusiness) || "主营业务资料暂不可用。";
  add(box, el("h3", "", "主营业务"), el("p", "", business));
  const sourceLabel = naturalText(profile.source_label)
    || (!research.market || research.market === "a-shares" ? "同花顺主营介绍" : "");
  if (profile.source && sourceLabel) {
    add(box, el("p", "dim-sum", `来源：${sourceLabel}；采集日期：${naturalText(profile.fetched_at) || "未记录"}。`));
  }
  const conclusion = record(model.conclusion);
  if (conclusion.overall) {
    add(box, el("h3", "", "核心结论"), researchRows(["整体判断", "主要支撑", "主要疑问"],
      [conclusion.overall, conclusion.support, conclusion.question]));
  }
  const dims = Array.isArray(research.dimensions) ? research.dimensions.map(record) : [];
  add(box, el("h3", "", "五维解读"), researchRows(dims.map(row => row.label), dims.map(row => row.text)));
  if (conclusion.overall) {
    add(box, el("h3", "", "风险提示"));
    const risks = Array.isArray(model.risks) ? model.risks.map(record) : [];
    if (risks.length) {
      const list = el("ul", "report-research-risks");
      risks.forEach(risk => add(list, el("li", "", naturalText(risk.text))));
      add(box, list);
    } else add(box, el("p", "", "本次未形成有充分依据的独立风险提示，不代表不存在风险。"));
  }
  add(box, el("p", "dim-sum", `解读限制：${naturalText(research.limitation)}`));
  return box;
}

function commentarySection(report, sectionIdPrefix, options) {
  const card = section("report-commentary", "综合研判", "llm", sectionIdPrefix);
  const state = report.commentary_status;
  if (state && state !== "ok") {
    const fallbackLabel = record(report.research_commentary).version === 2 ? "来源资料与程序生成的五维定性" : "量化摘要";
    const messages = {failed: `AI 解读生成失败，以下为${fallbackLabel}。`, disabled: `AI 解读未启用，以下为${fallbackLabel}。`, insufficient: "主要分析数据不足，暂不生成综合判断。", config: `AI 解读尚未配置，以下为${fallbackLabel}。`};
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
  }
  const legacy = naturalText(report.summary) || naturalText(record(report.overview).summary);
  // 旧快照仅含研究摘要时保留其正文；带旧交易动作或仓位的摘要不再展示。
  const safeLegacy = /建议仓位|可考虑建仓|加仓|减仓|买入|卖出|持仓|重仓|轻仓/.test(legacy) ? "" : legacy;
  const rawBody = commentaryText(report) || safeLegacy;
  // 降级状态已在卡片顶部说明，去掉量化摘要正文重复的引导句。
  const body = state && state !== "ok" ? rawBody.replace(/^>\s*[^\n]*(?:\n+|$)/, "").trim() : rawBody;
  if (record(report.research_commentary).version === 2) {
    card.appendChild(researchBody(report.research_commentary, state));
  } else if (body && state !== "insufficient") {
    const nested = body.replace(/^#{1,2}[ \t]+(?=\d+[.、．])/gm, "### ");
    card.appendChild(markdownBlock(nested, "report-commentary-intro dim-sum md"));
  } else if (!state || state === "ok") {
    card.appendChild(el("div", "report-empty", "暂无 AI 解读"));
  }
  const signal = record(report.signal), quality = record(report.data_quality);
  const signalBlock = el("div", "report-signal");
  signalBlock.appendChild(el("h3", "", "模型信号"));
  if (report.market === "hk" && !report.analysis_mode) {
    signalBlock.appendChild(el("p", "dim-sum", "本期港股不提供模型信号。"));
  } else if (quality.signal_allowed !== false && naturalText(signal.label)) {
    signalBlock.appendChild(el("p", "", `模型信号：${naturalText(signal.label)}`));
    signalBlock.appendChild(el("p", "dim-sum", `生成依据：${naturalText(signal.basis) || "历史报告未记录生成规则"}`));
  } else {
    signalBlock.appendChild(el("p", "", "模型信号：暂未生成"));
    signalBlock.appendChild(el("p", "dim-sum", `原因：${naturalText(quality.signal_unavailable_reason) || "当前报告未生成模型信号，请核对数据覆盖。"}`));
  }
  card.appendChild(signalBlock);
  return card;
}

function qualitySection(report, sectionIdPrefix) {
  const card = section("report-quality", "数据覆盖与时效", "panel", sectionIdPrefix);
  const quality = record(report.data_quality);
  const currentPriceSource = record(record(report.overview).current_price).source;
  if (currentPriceSource) card.appendChild(el("p", "dim-sum", `当前价格来源：${naturalText(currentPriceSource)}`));
  const dimensions = Array.isArray(quality.dimensions) ? quality.dimensions.map(record) : [];
  const fallbackRows = dimensions.map(d => {
    const fields = Array.isArray(d.fields) ? d.fields.map(record) : [];
    const raw = label => fields.find(field => field.label === label)?.value;
    const value = label => raw(label) ?? "未记录";
    const sample = [value("样本范围")];
    if (raw("评估窗口")) sample.push(`评估窗口 ${raw("评估窗口")}`);
    if (raw("有效评估")) sample.push(`有效评估 ${raw("有效评估")}`);
    return {key:d.key, title:d.title, status:d.status, sample:sample.join("；"), cutoff:value("数据截止"),
      source:value("完整来源") !== "未记录" ? value("完整来源") : value("数据来源")};
  });
  const rows = Array.isArray(quality.summary_rows) ? quality.summary_rows.map(record) : fallbackRows;
  const qualityRange = (sample, cutoff) => {
    const cleanSample = readable(sample, "未记录");
    const cleanCutoff = readable(cutoff, "未记录");
    if (!cleanCutoff || cleanCutoff === "未记录") return cleanSample;
    if (cleanSample === "未记录") return `截至 ${cleanCutoff}`;
    return cleanSample.includes(cleanCutoff) ? cleanSample : `${cleanSample}；截至 ${cleanCutoff}`;
  };
  if (rows.length) {
    const table = el("table", "report-quality-summary");
    const head = el("thead"), header = el("tr");
    for (const title of ["维度", "状态", "数据范围／截至", "来源"]) header.appendChild(el("th", "", title));
    head.appendChild(header); table.appendChild(head);
    const body = el("tbody");
    const status = {ok:"正常", partial:"部分数据", unavailable:"数据不可用", not_applicable:"不适用",
      not_collected:"未采集", collection_failed:"采集失败", insufficient_sample:"样本不足"};
    for (const row of rows) {
      const tr = el("tr");
      for (const value of [readable(row.title), status[row.status] || readable(row.status, "未记录"), qualityRange(row.sample, row.cutoff), readable(row.source, "未记录")]) tr.appendChild(el("td", "", value));
      body.appendChild(tr);
    }
    table.appendChild(body); card.appendChild(table);
  } else card.appendChild(el("div", "report-empty", "数据范围、来源与截止日期未记录。"));
  if (Number.isFinite(quality.coverage)) {
    const label = report.analysis_mode === "objective" || legacyHk(report) ? "分析数据覆盖" : "计分维度覆盖";
    card.appendChild(el("p", "dim-sum", `${label}：${quality.coverage}%。${naturalText(quality.coverage_basis) || "不表示评分置信度。"}`));
  }
  const fallbackLimits = dimensions.flatMap(d => {
    const fields = Array.isArray(d.fields) ? d.fields.map(record) : [];
    const out = fields.filter(f => ["缺失原因", "分析限制", "采集限制", "评估说明", "依赖说明", "最新报告字段来源", "估值字段来源"].includes(f.label)
      || (f.label === "财务包说明" && f.value && f.value !== "完整来源包")
      || f.reference_only || (f.label === "缓存状态" && String(f.value).includes("过期")))
      .map(f => ({dimension:d.title, label:f.label, value:f.value}));
    if (d.status && !["ok", "not_applicable"].includes(d.status) && !out.some(f => ["缺失原因", "分析限制", "采集限制", "评估说明"].includes(f.label))) {
      out.push({dimension:d.title, label:"原因", value:"未记录"});
    }
    return out;
  });
  const limits = Array.isArray(quality.limitations) ? quality.limitations.map(record) : fallbackLimits;
  const texts = limits.map(item => `${readable(item.dimension)}：${readable(item.label)}：${readable(item.value, "未记录")}`);
  if (texts.length) {
    card.appendChild(el("h3", "", "数据限制"));
    const list = el("ul", "report-pending-list");
    for (const text of [...new Set(texts)]) list.appendChild(el("li", "", text));
    card.appendChild(list);
  }
  return card;
}

function cleanDisclaimer(value) {
  // 只清理已撤下的独立尾注，不改写存档正文或其他章节。
  return String(value).split("\n").filter(line =>
    !/^\s*(?:>\s*)?[（(]部分内容由\s*AI\s*生成[）)]\s*$/.test(line)).join("\n");
}

function archivedCommentaryBody(part) {
  const body = part.replace(/^##\s+.+\n?/, "").replace(/\n\s*---\s*$/, "").trim();
  return body.split("\n").filter(line =>
    !/已包含在上方|本次未生成模型观察视角|该摘要不包含模型主观推演/.test(line)).join("\n").trim();
}

function goldDirectionSummary(report) {
  if (report.report_schema === "gold-observation-v2") {
    const markets = record(report.markets ?? record(report.facts).markets);
    const marketSummary = (label, value) => {
      const market = record(value);
      const price = market.latest_close;
      const validPrice = typeof price === "number" && Number.isFinite(price) && price > 0;
      const formatted = validPrice
        ? `${price.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${readable(market.unit, "单位未记录")}`
        : "价格不可用";
      const date = readable(market.as_of, "日期未记录");
      const stale = market.status === "stale" ? "，旧数据" : "";
      const direction = market.status === "ok" ? readable(record(market.short_term).direction, "无法判断") : "无法判断";
      return `${label}：${formatted}（${date}${stale}），技术${direction}`;
    };
    return `${marketSummary("国内", markets.domestic)}；${marketSummary("国际", markets.international)}`;
  }
  const markets = record(report.markets);
  const direction = value => readable(record(value).direction, "无法判断");
  return `长期：${direction(report.long_term)}；国内短期：${direction(record(markets.domestic).short_term)}；国际短期：${direction(record(markets.international).short_term)}`;
}

function renderGoldObservationMarkdown(markdown, options) {
  // 新版正文按归档顺序展示，不从快照重新生成研判。
  const article = el("article", "stock-report gold-report");
  if (options.className) article.classList.add(String(options.className));
  const prefix = options.sectionIdPrefix || "";
  const source = String(markdown || "").trim();
  const title = source.match(/^#\s+(.+)$/m)?.[1] || "黄金市场观察简报";
  const parts = source.replace(/^#\s+.+\n?/, "").split(/\n(?=##\s+)/);
  const introduction = parts.shift().trim();
  const first = section("gold-observation", title, "panel", prefix);
  if (introduction) first.appendChild(markdownBlock(introduction, "md"));
  article.appendChild(first);
  parts.forEach((part, index) => {
    const match = part.match(/^##\s+(.+)$/m);
    const label = match?.[1] || `报告章节 ${index + 1}`;
    const body = part.replace(/^##\s+.+\n?/, "");
    const card = index === 0 ? first : section(`gold-observation-${index}`, label, "panel", prefix);
    if (label === "数据覆盖与时效") card.classList.add("gold-coverage-panel");
    if (index === 0) card.appendChild(el("h3", "", label));
    card.appendChild(markdownBlock(body, "md"));
    if (index !== 0) article.appendChild(card);
  });
  return article;
}

function renderGoldReport(report, options) {
  // 黄金复用已有 Markdown 章节卡片，不经过股票评分和维度空态。
  const markdown = options.archiveMarkdown || report.markdown || "# 黄金综合研报\n\n报告正文缺失，请重新生成。";
  if (report.report_schema === "gold-observation-v2") return renderGoldObservationMarkdown(markdown, options);
  const article = renderStockMarkdownReport(markdown, {...options, preserveLegacySignal: true});
  article.classList.add("gold-report");
  const summary = article.children[0];
  summary.children[0].textContent = "黄金研判摘要";
  summary.appendChild(el("p", "report-investment-summary", goldDirectionSummary(report)));
  return article;
}

function indexResearchBody(research, state) {
  const box = el("div", "report-research");
  const add = (title, body) => {
    box.appendChild(el("h3", "", title));
    box.appendChild(body);
  };
  const positioning = research.positioning || [];
  add("指数定位", researchRows(positioning.map(row => row.label),
    positioning.map(row => [row.value, row.note].filter(Boolean).join(" "))));
  const model = ["ready", "ok"].includes(state) ? record(research.model) : {};
  const conclusion = record(model.conclusion);
  if (conclusion.overall) {
    add("核心结论", researchRows(["整体判断", "主要支撑", "主要疑问"],
      [conclusion.overall, conclusion.support, conclusion.question]));
  }
  const dimensions = research.dimensions || [];
  add("维度解读", researchRows(dimensions.map(row => row.label), dimensions.map(row => row.text)));
  if (conclusion.overall) {
    const risks = model.risks || [];
    const list = el("ul", "report-research-risks");
    risks.forEach(risk => list.appendChild(el("li", "", naturalText(risk.text))));
    add("风险提示", risks.length ? list : el("p", "", "本次未形成有充分依据的独立风险提示。"));
  }
  add("解读限制", el("p", "dim-sum", naturalText(research.limitation)));
  return box;
}

export function renderIndexReport(report, options = {}) {
  const prefix = options.sectionIdPrefix || "";
  const article = el("article", "stock-report index-report");
  const profile = report.index_profile;
  const visibleSections = profile?.sections ?? report.visible_sections;
  const make = (id, title) => section(id, title, "panel", prefix);
  const addRows = (card, rows) => {
    if (!Array.isArray(rows) || !rows.length) return;
    card.appendChild(comparisonTable(["指标", "数值", "说明"], rows.map(row => [
      readable(row.label), readable(row.value), readable(row.note, "")]), "index-metrics", "指数指标"));
  };
  const overview = make("index-overview", "概览");
  overview.appendChild(reportHeader(report));
    const indexQuality = record(report.data_quality);
    if (indexQuality.status) overview.appendChild(statusBadge(indexQuality.status));
    if (indexQuality.assessment_status) overview.appendChild(el("p", "dim-sum",
      {complete:"完整评估", partial:"部分评估（参考）", facts_only:"事实报告"}[indexQuality.assessment_status] || "评估状态未记录"));
  if (report.analysis_mode === "objective") overview.appendChild(el("p", "dim-sum", "客观数据模式"));
  if (report.generated_at) overview.appendChild(el("p", "dim-sum", `生成时间：${reportTime(report.generated_at)}`));
  addRows(overview, report.overview?.display_rows);
  const instrument = report.requested_instrument;
  if (instrument) overview.appendChild(el("p", "dim-sum", `输入为ETF ${readable(instrument.name)}（${readable(instrument.symbol)}），本报告分析跟踪指数；ETF自身表现单独披露。`));
  article.appendChild(overview);
  const technical = make("index-technical", "历史表现与技术面");
  const indexTags = {bull:"多头",shake:"震荡",bear:"空头",undervalued:"低估",neutral:"中性",overvalued:"高估",invalid:"无效"};
  const indexTagClass = tag => `tag ${{bull:"good",shake:"mid",bear:"bad",undervalued:"good",neutral:"mid",overvalued:"bad",invalid:"gray"}[tag] || "gray"}`;
  if (report.tag_technical) technical.appendChild(el("span", indexTagClass(report.tag_technical), indexTags[report.tag_technical] || report.tag_technical));
  for (const part of [report.section_performance, report.section_technical, report.section_strategy]) {
    if (!part) continue;
    if (part.summary) technical.appendChild(el("p", "dim-sum", part.summary));
    addRows(technical, part.display?.rows);
    const windows = part.display?.windows;
    if (Array.isArray(windows) && windows.length) {
      technical.appendChild(comparisonTable(["区间", "起始", "截止", "交易日", "收益", "最大回撤", "年化波动"], windows.map(w => [
        readable(w.label), readable(w.start_date), readable(w.end_date), readable(w.sample_count), formatMetricValue("return_pct", w.return_pct),
        formatMetricValue("max_drawdown_pct", w.max_drawdown_pct), formatMetricValue("annualized_volatility_pct", w.annualized_volatility_pct)]), "index-performance", "历史表现"));
      for (const window of windows) for (const note of window.notes || []) technical.appendChild(el("p", "dim-sum", `${window.label}：${note}`));
    }
    addRows(technical, part.display?.benchmark_rows);
    if (part.metrics?.benchmark_sample_count >= 2) technical.appendChild(el("p", "dim-sum", `基准 ${readable(part.metrics.benchmark_symbol)}；共同交易日 ${readable(part.metrics.benchmark_start_date)} 至 ${readable(part.metrics.benchmark_end_date)}，${part.metrics.benchmark_sample_count} 日。`));
    addRows(technical, part.display?.etf_rows);
    if (part.metrics?.methodology) technical.appendChild(el("p", "dim-sum", part.metrics.methodology));
    for (const note of part.metrics?.errors || []) technical.appendChild(el("p", "dim-sum", note));
  }
  article.appendChild(technical);
  const valuation = make("index-valuation", "估值面");
  if (report.section_valuation?.summary) valuation.appendChild(el("p", "dim-sum", report.section_valuation.summary));
  addRows(valuation, report.section_valuation?.display?.rows);
  if (report.tag_valuation) valuation.appendChild(el("span", indexTagClass(report.tag_valuation), indexTags[report.tag_valuation] || report.tag_valuation));
  for (const detail of report.section_valuation?.display?.details || []) valuation.appendChild(el("p", "dim-sum", detail));
  for (const note of report.section_valuation?.metrics?.valuation_notes || []) valuation.appendChild(el("p", "dim-sum", note));
  article.appendChild(valuation);
  const background = make("index-background", "资金、宏观与新闻");
  for (const [key, title, part] of [["capital", "资金流向", report.section_capital], ["macro", "宏观环境", report.section_macro], ["sentiment", "相关新闻", report.section_sentiment]]) {
    background.appendChild(el("h3", "", title));
    if ((Array.isArray(visibleSections) && !visibleSections.includes(key)) || !part) {
      background.appendChild(el("p", "dim-sum", profile?.placeholders?.[key]?.web ?? "当前指数不适用或未接入该项数据。"));
      continue;
    }
    if (part.summary) background.appendChild(el("p", "dim-sum", part.summary));
    addRows(background, part.display?.rows);
    const items = part.display?.items || part.metrics?.items || [];
    if (Array.isArray(items) && items.length) {
      const list = el("ul", "report-metric-list sentiment-source-list");
      for (const item of items) {
        const li = el("li");
        li.appendChild(el("span", "", `${readable(item.title, "未记录标题")}（${readable(item.published_at, "日期未记录")}）`));
        const url = safeNewsUrl(item.url);
        const source = readable(item.source, "来源未标注");
        if (url) { const link = el("a", "sentiment-source-link", source); link.setAttribute("href", url); link.setAttribute("target", "_blank"); link.setAttribute("rel", "noopener noreferrer"); li.appendChild(link); }
        else li.appendChild(el("span", "peer-caption", ` · ${source}`));
        list.appendChild(li);
      }
      const details = el("details", "sentiment-sources");
      details.appendChild(el("summary", "", "查看采集样本与来源")); details.appendChild(list); background.appendChild(details);
    }
  }
  article.appendChild(background);
  const commentary = make("report-commentary", "综合研判");
  if (!["ok", "ready"].includes(report.commentary_status)) commentary.appendChild(el("p", "dim-sum", {failed:"AI解读生成失败，以下为量化摘要。",disabled:"AI未启用，以下为量化摘要。",config:"AI尚未配置，以下为量化摘要。",unconfigured:"AI尚未配置，以下为量化摘要。",insufficient:"数据不足，以下为受限摘要。"}[report.commentary_status] || ""));
  if (!options.readOnly && report.commentary_status === "failed" && typeof options.onRetryCommentary === "function") {
    const retry = el("button", "subs-btn report-retry-commentary", options.commentaryBusy ? "正在生成…" : "重试 AI 解读");
    retry.disabled = Boolean(options.commentaryBusy); retry.addEventListener("click", options.onRetryCommentary); commentary.appendChild(retry);
  }
  if (!options.readOnly && ["config", "unconfigured"].includes(report.commentary_status) && typeof options.onConfigure === "function") {
    const configure = el("button", "subs-btn report-configure-commentary", "配置 AI 解读");
    configure.addEventListener("click", options.onConfigure); commentary.appendChild(configure);
  }
  if (options.commentaryError) commentary.appendChild(el("p", "report-commentary-error", options.commentaryError));
  if (report.index_research?.version === 2) {
    commentary.appendChild(indexResearchBody(report.index_research, report.commentary_status));
  } else if (report.commentary) commentary.appendChild(markdownBlock(report.commentary, "md dim-sum"));
  if (report.analysis_mode !== "objective") article.appendChild(commentary);
  if (report.risk_list?.length) {
    const risks = make("index-risks", "风险提示");
    for (const risk of report.risk_list) risks.appendChild(el("p", "dim-sum", riskText(risk)));
    article.appendChild(risks);
  }
  article.appendChild(qualitySection(report, prefix));
  const disclaimer = make("report-disclaimer", "免责声明");
  for (const paragraph of report.disclaimer || []) disclaimer.appendChild(el("p", "dim-sum", paragraph));
  article.appendChild(disclaimer);
  return article;
}

export function renderStockReport(report, options = {}) {
  const data = record(report);
  if (data.report_version >= 2 && data.code && data.section_technical) return renderIndexReport(data, options);
  if (data.report_type === "gold") return renderGoldReport(data, options);
  const sectionIdPrefix = options.sectionIdPrefix || "";
  const article = el("article", "stock-report");
  if (options.className) article.classList.add(String(options.className));
  article.appendChild(summarySection(data, sectionIdPrefix));
  const dimensions = record(data.dimensions);
  const objective = data.analysis_mode === "objective";
  const sentimentPolicy = data.market_profile?.sentiment_policy
    ?? (data.market === "us" ? "not_collected" : "");
  const applicability = Array.isArray(data.data_quality?.dimensions) ? data.data_quality.dimensions : [];
  for (const [name, label] of DIMENSIONS) {
    if (data.report_scope === "facts_only" && name === "technical") continue;
    if (objective && name === "sentiment") {
      article.appendChild(newsFactsSection(dimensions[name], sectionIdPrefix));
      continue;
    }
    if (!objective && sentimentPolicy === "not_collected" && name === "sentiment") {
      const news = newsFactsSection(dimensions[name], sectionIdPrefix);
      article.appendChild(news);
      continue;
    }
    if (!objective && fiveDimension(data) && name === "sentiment") {
      article.appendChild(sentimentRiskSection(dimensions[name], sectionIdPrefix));
      continue;
    }
    if (legacyHk(data) && name === "sentiment") {
      article.appendChild(hkNewsSection(dimensions[name], sectionIdPrefix));
      continue;
    }
    const applicable = applicability.find(item => item.key === name)?.applicable !== false;
    const title = objective ? ({financial: "财务数据", technical: "技术指标", valuation: "估值数据", industry: "行业对比"}[name] || label) : label;
    article.appendChild(dimensionSection(name, title, dimensions[name], sectionIdPrefix, legacyHk(data), applicable, fiveDimension(data), objective));
  }
  if (!objective && data.report_scope !== "facts_only") article.appendChild(scoreSection(data, sectionIdPrefix));
  const commentary = commentarySection(data, sectionIdPrefix, options);
  if (!objective) article.appendChild(commentary);
  const disclaimer = section("report-disclaimer", "免责声明", "panel", sectionIdPrefix);
  let archivedDisclaimer = false;
  if (options.archiveMarkdown) {
    // 原文补充章沿用安全 Markdown 渲染，未知章节仍保留在原文入口。
    const parts = String(options.archiveMarkdown).split(/\n(?=##\s+)/);
    for (const part of parts) {
      const match = part.match(/^##\s+(.+)$/m);
      if (!match || !/多风格观察|工具局限性|免责声明/.test(match[1])) continue;
      if (/工具局限性|免责声明/.test(match[1])) {
        disclaimer.appendChild(markdownBlock(localizeReportMarkdown(cleanDisclaimer(part.replace(/^##\s+.+\n?/, ""))), "md report-archive-body"));
        archivedDisclaimer = true;
        continue;
      }
      const body = archivedCommentaryBody(part);
      if (!body || commentaryText(data).replace(/\s+/g, "").includes(body.replace(/\s+/g, ""))) continue;
      commentary.appendChild(el("h3", "", "多风格观察视角（历史内容）"));
      commentary.appendChild(markdownBlock(localizeReportMarkdown(body), "md report-archive-body"));
    }
  }
  article.appendChild(qualitySection(data, sectionIdPrefix));
  if (!archivedDisclaimer) {
    const paragraphs = Array.isArray(data.disclaimer) && data.disclaimer.length ? data.disclaimer
      : ["本报告不构成任何形式的投资建议。市场有风险，投资需谨慎。"];
    for (const paragraph of paragraphs) {
      const text = cleanDisclaimer(paragraph).trim();
      if (text) disclaimer.appendChild(el("p", "dim-sum", text));
    }
  }
  article.appendChild(disclaimer);
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
  const introduction = parts.shift() || "";
  // 历史 Markdown 原文不改写，报告视图中不展示旧交易动作与仓位。
  const legacySignal = options.preserveLegacySignal ? null : introduction.match(/^>\s*\*\*操作信号：([^*]+)\*\*[^\n]*(?:\n+>\s*原因：[^\n]*)?/m);
  const safeIntro = legacySignal ? introduction.replace(legacySignal[0], "") : introduction;
  heading.appendChild(markdownBlock(safeIntro || "暂无报告摘要", "report-investment-summary md"));
  article.appendChild(heading);

  const disclaimers = [];
  let commentary = null;
  parts.forEach((part, index) => {
    const match = part.match(/^##\s+(.+)$/m);
    const title = match?.[1] || "";
    const isCommentary = /AI.*解读|综合研判/.test(title);
    const isObservation = /多风格观察/.test(title);
    const isSubsection = /^\d+[.、．]\s*(维度分项解读|全维度风险汇总|综合评级|综合评价|现有数据观察|风险与数据限制)/.test(title);
    if (isObservation || (commentary && isSubsection)) {
      const body = archivedCommentaryBody(part);
      if (!body) return;
      if (!commentary) {
        commentary = section("report-commentary", "AI 综合解读", "llm", sectionIdPrefix);
        article.appendChild(commentary);
      }
      commentary.appendChild(el("h3", "", isObservation ? "多风格观察视角（历史内容）" : title));
      commentary.appendChild(markdownBlock(localizeReportMarkdown(body), "md"));
      return;
    }
    const isDisclaimer = /工具局限性|免责声明/.test(match?.[1] || "");
    const label = isCommentary ? (/综合研判/.test(title) ? "综合研判" : "AI 综合解读") : isDisclaimer ? "免责声明" : title || `报告章节 ${index + 1}`;
    const card = section(`report-library-${index}`, label, "panel", sectionIdPrefix);
    const body = part.replace(/^##\s+.+\n?/, "");
    card.appendChild(markdownBlock(localizeReportMarkdown(isDisclaimer ? cleanDisclaimer(body) : body), "md"));
    if (isDisclaimer) disclaimers.push(card);
    else article.appendChild(card);
    if (isCommentary) commentary = card;
  });
  if (legacySignal) {
    if (!commentary) {
      commentary = section("report-commentary", "综合研判", "llm", sectionIdPrefix);
      article.appendChild(commentary);
    }
    const signal = el("div", "report-signal");
    signal.appendChild(el("h3", "", "模型信号"));
    signal.appendChild(el("p", "", `模型信号：${legacySignal[1].trim()}`));
    signal.appendChild(el("p", "dim-sum", "生成依据：历史报告未记录生成规则"));
    commentary.appendChild(signal);
  }
  for (const card of disclaimers) article.appendChild(card);
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
  if (report.report_type !== "gold" && score.final !== null && score.final !== undefined && score.final !== "") {
    header.appendChild(el("span", "report-summary-score", `${scoreText(score.final)}/10`));
  }
  card.appendChild(header);

  card.appendChild(report.report_type === "gold"
    ? el("p", "report-summary-conclusion md", goldDirectionSummary(report))
    : markdownBlock(summaryConclusion(report), "report-summary-conclusion md"));
  const meta = el("div", "report-summary-meta");
  meta.appendChild(el("span", "report-summary-risks", report.analysis_mode === "objective" ? "客观数据模式" : report.report_type === "gold"
    ? (report.report_schema === "gold-observation-v2" ? "行情日期与风险见正文" : "风险与量化重估见正文") : `${riskFlags(report).length} 项风险`));
  meta.appendChild(el("span", "report-summary-time", artifactTime(item, report)));
  card.appendChild(meta);

  const open = el("button", "report-summary-open", "查看完整研报");
  open.type = "button";
  card.appendChild(open);
  return card;
}
