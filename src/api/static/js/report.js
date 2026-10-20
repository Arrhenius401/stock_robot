// 个股报告视图：渲染 /api/v1/analyze 完整 JSON（纵向研报流）
import { store, switchView, bus } from "./state.js";
import { api } from "./api.js";
import { el, errorCard, skeleton,
         showEntryError, clearEntryError } from "./components.js";
import { renderStockReport } from "./report-renderer.js?v=20261020-capital-display";

const content = () => document.getElementById("reportContent");

let reqSeq = 0;  // 请求令牌：慢请求期间二次查询时，丢弃迟到响应/错误

export async function openReport(symbol, entryInputId = "stockInput") {
  const seq = ++reqSeq;
  // 已知 ETF 使用跟踪指数入口；动态导入保持报告视图之间无循环依赖。
  if (/^(?:5|1)\d{5}$/.test(String(symbol))) {
    try {
      const { getIndexDirectory, openIndex } = await import("./indexview.js?v=20261020-capital-display");
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
    openReport(symbol);
  });
  box.replaceChildren(renderStockReport(d, {
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
