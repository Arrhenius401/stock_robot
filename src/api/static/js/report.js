// 个股报告视图：渲染 /api/v1/analyze 完整 JSON（纵向研报流）
import { store, switchView, bus } from "./state.js";
import { api } from "./api.js?v=20261029-index-report-refinement";
import { el, errorCard, skeleton,
         showEntryError, clearEntryError } from "./components.js";
import { renderStockReport } from "./report-renderer.js?v=20261029-index-report-refinement";

const content = () => document.getElementById("reportContent");

let reqSeq = 0;  // 请求令牌：慢请求期间二次查询时，丢弃迟到响应/错误
const receivedAt = new WeakMap();
const hkDate = (stamp) => new Date(stamp + 8 * 3600000).toISOString().slice(0, 10);

function reusableReport(report, now = Date.now()) {
  if (report.report_type === "gold") {
    const stamp = receivedAt.get(report) ?? Date.parse(report.generated_at);
    return Number.isFinite(stamp) && stamp <= now && now - stamp < 6 * 3600000;
  }
  if (report.market !== "hk") return true;
  const received = receivedAt.get(report) ?? Date.parse(report.generated_at);
  const fetched = Date.parse(report.dimensions?.sentiment?.metrics?.news_fetched_at);
  // 没有新闻快照时按接收时间重试；旧快照不能被新报告生成时间续期。
  const anchor = Number.isFinite(fetched) ? Math.min(fetched, received) : received;
  return Number.isFinite(anchor) && anchor <= now && now - anchor < 3600000
    && hkDate(anchor) === hkDate(now);
}

function currentHkReport(report, now = Date.now()) {
  // 新评分的窗口与样本是报告证据，不能只删新闻而留下原评分。
  if (report.market !== "hk" || report.scoring_method === "five-dimension-v1") return report;
  const news = report.dimensions?.sentiment;
  const metrics = news?.metrics;
  if (!Array.isArray(metrics?.news_items)) return report;
  const cutoff = hkDate(now - 29 * 86400000);
  const items = metrics.news_items.filter(item => {
    const stamp = Date.parse(item.published_at);
    return Number.isFinite(stamp) && stamp <= now && hkDate(stamp) >= cutoff;
  }).slice(0, 20);
  const summary = (items.length ? `近30天关键词检索结果，展示最新${items.length}条`
    : news.status === "unavailable" || news.summary?.includes("获取失败")
      ? news.summary : "本次检索未获得近30天有效记录");
  const dimensions = {...report.dimensions, sentiment: {...news, summary,
    metrics: {...metrics, news_items: items}}};
  const quality = report.data_quality;
  const qualityDimensions = Array.isArray(quality?.dimensions) ? quality.dimensions.map(row =>
    row.key !== "sentiment" ? row : {...row, fields: (row.fields || []).map(field =>
      field.label === "样本范围" ? {...field, value: `展示最新${items.length}条，最近30天`}
      : field.label === "数据截止" ? {...field, value: items[0]?.published_at || "无有效新闻发布日期"}
      : field.label === "检索状态" ? {...field, value: summary} : field)}) : null;
  return {...report, dimensions,
    ...(qualityDimensions ? {data_quality: {...quality, dimensions: qualityDimensions}} : {})};
}

export async function openReport(symbol, entryInputId = "stockInput", refreshCache = false) {
  const seq = ++reqSeq;
  // 已知 ETF 使用跟踪指数入口；动态导入保持报告视图之间无循环依赖。
  if (/^(?:5|1)\d{5}$/.test(String(symbol))) {
    try {
const { getIndexDirectory, openIndex } = await import("./indexview.js?v=20261029-index-report-refinement");
      const directory = await getIndexDirectory();
      if (seq !== reqSeq) return;
      const etf = directory.etfs.find((item) => item.symbol === String(symbol));
      if (etf) {
        clearEntryError(entryInputId);
        const input = document.getElementById("indexInput");
        if (input) input.value = etf.symbol;
        return openIndex(etf.symbol);
      }
    } catch (error) {
      if (seq !== reqSeq) return;
      console.warn("ETF 目录读取失败，继续原分析入口：", error.message);
    }
  }
  const prevView = store.currentView;  // 记录原视图：422 校验失败时回退
  switchView("report");
  if (!refreshCache && store.reportCache[symbol] && reusableReport(store.reportCache[symbol])) {
    renderReport(store.reportCache[symbol]);
    return;
  }
  const box = content();
  box.innerHTML = "";
  box.appendChild(skeleton());
  try {
    const data = await api.analyze(symbol, refreshCache);
    receivedAt.set(data, Date.now());
    store.reportCache[symbol] = data;
    if (seq !== reqSeq) return;  // 已有更新请求，迟到响应不覆盖新视图
    renderReport(data);
  } catch (err) {
    if (seq !== reqSeq) return;
    if (err.status === 422) {
      // 输入校验失败：回原视图 + 输入框旁红字，不渲染错误卡
      showEntryError(entryInputId, err.message);
      switchView(prevView);
      return;
    }
    box.innerHTML = "";
    box.appendChild(errorCard(`分析失败: ${err.message}`, () => openReport(symbol, entryInputId)));
  }
}

function renderReport(d, commentaryState = {}) {
  const box = content();
  const refresh = el("button", "btn-refresh", "刷新报告");
  refresh.addEventListener("click", () => {
    const symbol = d.symbol || d.code;
    if (!symbol) return;
    delete store.reportCache[symbol];
    openReport(symbol, "stockInput", true);
  });
  box.replaceChildren(renderStockReport(currentHkReport(d), {
    sectionIdPrefix: "page-",
    ...commentaryState,
    onConfigure: () => switchView("config"),
    onRetryCommentary: d.commentary_retry_id ? () => retryCommentary(d) : null,
  }), refresh);
}

async function retryCommentary(report) {
  const sequence = ++reqSeq;
  const symbol = report.symbol || report.code;
  renderReport(report, { commentaryBusy: true });
  try {
    const updated = await api.retryCommentary(report.commentary_retry_id);
    // 只更新原批次的缓存；新分析、换页或换标的不会被迟到结果覆盖。
    if (store.reportCache[symbol] !== report) return;
    store.reportCache[symbol] = updated;
    // 重试AI不属于新闻刷新，保留原报告的接收时点。
    receivedAt.set(updated, receivedAt.get(report) ?? Date.parse(report.generated_at));
    if (sequence !== reqSeq || store.currentView !== "report") return;
    renderReport(updated);
  } catch (error) {
    if (sequence !== reqSeq || store.currentView !== "report") return;
    renderReport(report, { commentaryError: error.message });
  }
}

bus.addEventListener("view-change", (event) => {
  if (event.detail.view !== "report") reqSeq += 1;
});

export function initReportView() {
  const input = document.getElementById("stockInput");
  const btn = document.getElementById("stockBtn");
  btn.addEventListener("click", () => {
    clearEntryError("stockInput");  // 重新分析前清除上次校验红字
    const sym = input.value.trim();
    if (sym) openReport(sym);
  });
  input.addEventListener("keydown", (e) => {
    // 修正 2：IME 组合输入回车不触发（中文输入法候选确认）
    if (e.key === "Enter" && !e.isComposing) btn.click();
  });
}
