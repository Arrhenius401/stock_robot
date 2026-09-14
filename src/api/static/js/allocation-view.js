// 配置雷达基础视图：使用浏览器兼容语法直接消费完成快照。
var allocationState = { universes: [], universeId: null, snapshot: null, detail: null, benchmark: "csi_300", backtestPeriod: "inception", performancePeriod: "inception", performanceCache: {}, refreshMessage: "" };

function allocationRoot() { return document.getElementById("radarContent"); }

function allocationElement(tagName, className, text) {
  var node = document.createElement(tagName);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function allocationRequest(url, options) {
  return fetch(url, options).then(function (response) {
    return response.json().catch(function () { return {}; }).then(function (payload) {
      if (!response.ok) {
        var detail = payload.detail || payload.message || ("请求失败（" + response.status + "）");
        var error = new Error(detail);
        error.status = response.status;
        throw error;
      }
      return payload;
    });
  });
}

function allocationPercent(value) {
  var number = Number(value);
  return isFinite(number) ? (number * 100).toFixed(1) + "%" : "—";
}

function allocationRate(value) {
  var number = Number(value);
  return isFinite(number) ? (number * 100).toFixed(2) + "%" : "—";
}

function allocationMetric(label, value) {
  var card = allocationElement("div", "radar-detail-metric");
  var display = allocationElement("strong", "v", value);
  var numeric = Number(String(value).replace("%", ""));
  if (isFinite(numeric) && /收益|超额|回撤/.test(label)) {
    display.classList.add(numeric > 0 ? "positive" : (numeric < 0 ? "negative" : ""));
  }
  card.append(allocationElement("span", "k", label), display);
  return card;
}

function allocationBenchmarkName(id) {
  return { money_fund: "货币基金", csi_300: "沪深300", csi_all_bond: "中证全债" }[id] || "基准";
}

function allocationSvgElement(name) {
  return document.createElementNS("http://www.w3.org/2000/svg", name);
}

function allocationCurve(rows, valueKey, benchmarkKey, valueLabel, benchmarkLabel) {
  if (!rows || rows.length < 2) return allocationElement("p", "radar-note", "该区间暂无可展示的净值曲线。");
  var primaryBase = Number(rows[0][valueKey]);
  var benchmarkBase = Number(rows[0][benchmarkKey]);
  if (!isFinite(primaryBase) || primaryBase <= 0 || !isFinite(benchmarkBase) || benchmarkBase <= 0) return allocationElement("p", "radar-note", "该区间暂无可展示的净值曲线。");
  var series = rows.map(function (row) {
    return {
      date: String(row.date || ""),
      primary: Number(row[valueKey]) / primaryBase - 1,
      benchmark: Number(row[benchmarkKey]) / benchmarkBase - 1
    };
  }).filter(function (row) { return isFinite(row.primary) && isFinite(row.benchmark); });
  if (series.length < 2) return allocationElement("p", "radar-note", "该区间暂无可展示的净值曲线。");
  var values = [];
  series.forEach(function (row) { values.push(row.primary, row.benchmark, 0); });
  var low = Math.min.apply(Math, values);
  var high = Math.max.apply(Math, values);
  var step = Math.max(0.02, Math.pow(10, Math.floor(Math.log(Math.max(high - low, 0.02)) / Math.LN10)) / 2);
  low = Math.floor(low / step) * step;
  high = Math.ceil(high / step) * step;
  if (low === high) { low -= step; high += step; }
  var width = 760; var height = 270; var left = 54; var right = 16; var top = 30; var bottom = 40; var range = high - low;
  function xFor(index) { return left + index * (width - left - right) / (series.length - 1); }
  function yFor(value) { return top + (high - value) * (height - top - bottom) / range; }
  function points(key) { return series.map(function (row, index) { return xFor(index) + "," + yFor(row[key]); }).join(" "); }
  var container = allocationElement("div", "radar-curve");
  var legend = allocationElement("div", "radar-curve-legend");
  legend.append(allocationElement("span", "radar-legend-primary", "实线：" + valueLabel), allocationElement("span", "radar-legend-benchmark", "虚线：" + benchmarkLabel));
  var svg = allocationSvgElement("svg");
  svg.classList.add("radar-equity-curve"); svg.setAttribute("viewBox", "0 0 " + width + " " + height);
  svg.setAttribute("role", "img"); svg.setAttribute("aria-label", valueLabel + "与" + benchmarkLabel + "收益率曲线");
  var axisLabel = allocationSvgElement("text");
  axisLabel.setAttribute("x", "14"); axisLabel.setAttribute("y", String(top + 4)); axisLabel.textContent = "收益率"; axisLabel.classList.add("radar-axis-label"); svg.appendChild(axisLabel);
  for (var tick = 0; tick <= 4; tick += 1) {
    var tickValue = low + range * tick / 4;
    var y = yFor(tickValue);
    var grid = allocationSvgElement("line"); grid.setAttribute("x1", String(left)); grid.setAttribute("x2", String(width - right)); grid.setAttribute("y1", String(y)); grid.setAttribute("y2", String(y)); grid.classList.add("radar-grid-line"); svg.appendChild(grid);
    var tickText = allocationSvgElement("text"); tickText.setAttribute("x", String(left - 8)); tickText.setAttribute("y", String(y + 4)); tickText.setAttribute("text-anchor", "end"); tickText.textContent = (tickValue * 100).toFixed(0) + "%"; tickText.classList.add("radar-axis-text"); svg.appendChild(tickText);
  }
  [0, Math.floor((series.length - 1) / 2), series.length - 1].forEach(function (index) {
    var dateText = allocationSvgElement("text"); dateText.setAttribute("x", String(xFor(index))); dateText.setAttribute("y", String(height - 12)); dateText.setAttribute("text-anchor", index === 0 ? "start" : (index === series.length - 1 ? "end" : "middle")); dateText.textContent = series[index].date; dateText.classList.add("radar-axis-text"); svg.appendChild(dateText);
  });
  [["primary", "radar-strategy-line"], ["benchmark", "radar-benchmark-line"]].forEach(function (entry) {
    var line = allocationSvgElement("polyline");
    line.setAttribute("points", points(entry[0])); line.classList.add(entry[1]); svg.appendChild(line);
  });
  var guide = allocationSvgElement("line"); guide.classList.add("radar-hover-guide"); guide.setAttribute("y1", String(top)); guide.setAttribute("y2", String(height - bottom)); guide.setAttribute("visibility", "hidden"); svg.appendChild(guide);
  var primaryDot = allocationSvgElement("circle"); primaryDot.classList.add("radar-hover-dot", "primary"); primaryDot.setAttribute("r", "4"); primaryDot.setAttribute("visibility", "hidden"); svg.appendChild(primaryDot);
  var benchmarkDot = allocationSvgElement("circle"); benchmarkDot.classList.add("radar-hover-dot", "benchmark"); benchmarkDot.setAttribute("r", "4"); benchmarkDot.setAttribute("visibility", "hidden"); svg.appendChild(benchmarkDot);
  var tooltip = allocationElement("div", "radar-curve-tooltip"); tooltip.hidden = true;
  function hideTooltip() { guide.setAttribute("visibility", "hidden"); primaryDot.setAttribute("visibility", "hidden"); benchmarkDot.setAttribute("visibility", "hidden"); tooltip.hidden = true; }
  function showTooltip(event) {
    var rect = svg.getBoundingClientRect();
    var ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    var index = Math.round(ratio * (series.length - 1)); var row = series[index]; var x = xFor(index);
    guide.setAttribute("x1", String(x)); guide.setAttribute("x2", String(x)); guide.setAttribute("visibility", "visible");
    primaryDot.setAttribute("cx", String(x)); primaryDot.setAttribute("cy", String(yFor(row.primary))); primaryDot.setAttribute("visibility", "visible");
    benchmarkDot.setAttribute("cx", String(x)); benchmarkDot.setAttribute("cy", String(yFor(row.benchmark))); benchmarkDot.setAttribute("visibility", "visible");
    tooltip.replaceChildren(allocationElement("strong", "", row.date), allocationElement("span", "primary", valueLabel + " " + allocationPercent(row.primary)), allocationElement("span", "benchmark", benchmarkLabel + " " + allocationPercent(row.benchmark)));
    tooltip.style.left = Math.max(8, Math.min(rect.width - 158, event.clientX - rect.left + 12)) + "px"; tooltip.style.top = Math.max(8, event.clientY - rect.top + 10) + "px"; tooltip.hidden = false;
  }
  svg.addEventListener("mousemove", showTooltip); svg.addEventListener("mouseleave", hideTooltip); svg.addEventListener("focus", function () { showTooltip({ clientX: svg.getBoundingClientRect().left + svg.getBoundingClientRect().width / 2, clientY: svg.getBoundingClientRect().top + 20 }); }); svg.addEventListener("blur", hideTooltip); svg.tabIndex = 0;
  container.append(legend, svg, tooltip);
  return container;
}

function allocationBenchmarkSwitch(onChange) {
  var control = allocationElement("div", "radar-benchmark-switch");
  ["money_fund", "csi_300", "csi_all_bond"].forEach(function (benchmark) {
    var button = allocationElement("button", "", allocationBenchmarkName(benchmark));
    button.type = "button";
    button.classList.toggle("on", benchmark === allocationState.benchmark);
    button.addEventListener("click", function () {
      if (allocationState.benchmark === benchmark) return;
      allocationState.benchmark = benchmark;
      onChange();
    });
    control.appendChild(button);
  });
  return control;
}

function allocationBacktestPeriodLabel(period) {
  return { three_months: "近三月", six_months: "近半年", one_year: "近一年", two_years: "近2年", three_years: "近3年", five_years: "近5年", ten_years: "近10年", inception: "成立以来" }[period] || "成立以来";
}

function allocationBacktestPeriodStart(period) {
  if (period === "inception") return null;
  var months = { three_months: 3, six_months: 6, one_year: 12, two_years: 24, three_years: 36, five_years: 60, ten_years: 120 }[period];
  var end = new Date(allocationState.snapshot.as_of_date + "T12:00:00");
  end.setMonth(end.getMonth() - months);
  return end.getFullYear() + "-" + String(end.getMonth() + 1).padStart(2, "0") + "-" + String(end.getDate()).padStart(2, "0");
}

function allocationBacktestPeriodControl(onChange) {
  var control = allocationElement("div", "radar-backtest-period");
  ["three_months", "six_months", "one_year", "two_years", "three_years", "five_years", "ten_years", "inception"].forEach(function (period) {
    var button = allocationElement("button", "", allocationBacktestPeriodLabel(period));
    button.type = "button";
    button.classList.toggle("on", period === allocationState.backtestPeriod);
    button.addEventListener("click", function () {
      if (period === allocationState.backtestPeriod) return;
      allocationState.backtestPeriod = period;
      onChange(period);
    });
    control.appendChild(button);
  });
  return control;
}

function allocationPerformancePeriodControl(onChange) {
  var control = allocationElement("div", "radar-backtest-period");
  ["three_months", "six_months", "one_year", "two_years", "three_years", "five_years", "ten_years", "inception"].forEach(function (period) {
    var button = allocationElement("button", "", allocationBacktestPeriodLabel(period));
    button.type = "button";
    button.classList.toggle("on", period === allocationState.performancePeriod);
    button.addEventListener("click", function () {
      if (period === allocationState.performancePeriod) return;
      allocationState.performancePeriod = period;
      onChange(period);
    });
    control.appendChild(button);
  });
  return control;
}

function allocationPerformanceSection(item) {
  var section = allocationElement("section", "panel radar-detail-section");
  section.appendChild(allocationElement("h3", "", "标的历史表现"));
  section.appendChild(allocationElement("p", "radar-meta", "单只 ETF 的历史净值与基准比较，不等同于池级轮动策略。"));
  if (item.status === "failed") {
    section.appendChild(allocationElement("p", "radar-note", "本次刷新未取得该标的的有效行情，暂不请求历史表现。" + allocationItemStatusDetail(item)));
    return section;
  }
  section.appendChild(allocationElement("p", "radar-note", "正在读取该标的历史表现…"));
  allocationLoadPerformance(section, item);
  return section;
}

function allocationRenderPerformance(section, payload, item) {
  if (allocationState.detail !== item) return;
  section.replaceChildren(allocationElement("h3", "", "标的历史表现"));
  section.appendChild(allocationElement("p", "radar-meta", payload.start_date + " 至 " + payload.end_date + " · 单只 ETF 净值表现"));
  var metrics = payload.metrics || {};
  var benchmark = (payload.benchmarks || {})[allocationState.benchmark] || {};
  var cards = allocationElement("div", "radar-detail-metrics");
  [["自身累计收益", allocationPercent(metrics["累计收益"])], ["自身年化收益", allocationPercent(metrics["年化收益"])], ["自身最大回撤", allocationPercent(metrics["最大回撤"])], ["相对" + allocationBenchmarkName(allocationState.benchmark) + "超额", allocationPercent(benchmark["超额累计收益"])]].forEach(function (pair) {
    cards.appendChild(allocationMetric(pair[0], pair[1]));
  });
  section.appendChild(cards);
  section.appendChild(allocationBenchmarkSwitch(function () { allocationRenderPerformance(section, payload, item); }));
  var curve = payload.equity_curve || {};
  section.appendChild(allocationCurve(curve.rows || [], "instrument_equity", allocationState.benchmark + "_equity", item.name, allocationBenchmarkName(allocationState.benchmark)));
  section.appendChild(allocationPerformancePeriodControl(function (period) { allocationLoadPerformance(section, item, period); }));
  section.appendChild(allocationElement("p", "radar-note", "实线：" + item.name + "；虚线：" + allocationBenchmarkName(allocationState.benchmark) + "。"));
  if (payload.research_notice) section.appendChild(allocationElement("p", "radar-note", payload.research_notice));
}

function allocationLoadPerformance(section, item, requestedPeriod) {
  var period = requestedPeriod || allocationState.performancePeriod;
  var snapshot = allocationState.snapshot;
  var requestedStart = allocationBacktestPeriodStart(period);
  var cacheKey = item.symbol + "|" + snapshot.as_of_date + "|" + period;
  if (allocationState.performanceCache[cacheKey]) {
    allocationRenderPerformance(section, allocationState.performanceCache[cacheKey], item);
    return;
  }
  section.replaceChildren(allocationElement("h3", "", "标的历史表现"), allocationElement("p", "radar-note", "正在读取" + allocationBacktestPeriodLabel(period) + "标的历史表现…"));
  var url = "/api/v1/radar/performance?universe_id=" + encodeURIComponent(allocationState.universeId) + "&symbol=" + encodeURIComponent(item.symbol) + "&end_date=" + encodeURIComponent(snapshot.as_of_date);
  if (requestedStart) url += "&start_date=" + encodeURIComponent(requestedStart);
  allocationRequest(url).then(function (payload) {
    if (allocationState.performancePeriod !== period) return;
    allocationState.performanceCache[cacheKey] = payload;
    allocationRenderPerformance(section, payload, item);
  }).catch(function (error) {
    if (allocationState.detail !== item || allocationState.performancePeriod !== period) return;
    section.replaceChildren(allocationElement("h3", "", "标的历史表现"), allocationElement("p", "radar-note", "历史表现暂不可用：" + error.message));
  });
}

function allocationBacktestSection(item, isPoolOverview) {
  var details = document.createElement("details");
  details.className = "panel radar-detail-section";
  details.dataset.context = isPoolOverview ? "pool" : "detail";
  details.open = !isPoolOverview;
  var summary = allocationElement("summary", "", "同池策略参考");
  details.appendChild(summary);
  details.appendChild(allocationElement("p", "radar-meta", isPoolOverview ? "该标的池的轮动策略研究结果，用于观察池级规则，不代表任一单只 ETF 的业绩。" : "池级轮动策略的研究结果，仅作同池策略参考，并非该 ETF 的独立业绩。"));
  details.dataset.loaded = isPoolOverview ? "false" : "true";
  if (!isPoolOverview) allocationLoadBacktest(details, item);
  details.addEventListener("toggle", function () {
    if (details.open && !details.dataset.loaded) {
      details.dataset.loaded = "true";
      allocationLoadBacktest(details, item);
    }
  });
  return details;
}

function allocationBacktestSectionIsCurrent(section, item) {
  if (!allocationRoot().contains(section)) return false;
  return section.dataset.context === "pool" ? allocationState.detail === null : allocationState.detail === item;
}

function allocationRenderBacktest(section, payload, item) {
  if (!allocationBacktestSectionIsCurrent(section, item)) return;
  section.replaceChildren(allocationElement("summary", "", "同池策略参考"));
  var metrics = payload.summary || payload.metrics || {};
  var startDate = metrics.start_date || payload.start_date || (payload.report || {}).start_date || "—";
  var endDate = metrics.end_date || payload.end_date || (payload.report || {}).end_date || "—";
  var overview = section.dataset.context === "pool";
  section.appendChild(allocationElement("p", "radar-meta", startDate + " 至 " + endDate + (overview ? " · 标的池轮动策略研究结果。" : " · 池级轮动策略，并非该 ETF 的独立业绩。")));
  var benchmark = (payload.benchmarks || {})[allocationState.benchmark] || {};
  if (!Object.keys(benchmark).length) benchmark = (metrics.benchmarks || {})[allocationState.benchmark] || {};
  var cards = allocationElement("div", "radar-detail-metrics");
  [["策略累计收益", allocationPercent(metrics["累计收益"])], ["策略年化收益", allocationPercent(metrics["年化收益"])], ["策略最大回撤", allocationPercent(metrics["最大回撤"])], ["相对" + allocationBenchmarkName(allocationState.benchmark) + "超额", allocationPercent(benchmark["超额累计收益"])], ["交易次数", metrics["交易次数"] == null ? "—" : String(metrics["交易次数"])], ["换手率", allocationPercent(metrics["换手率"])], ["佣金费率", allocationRate((payload.cost_profile || {}).commission_rate)], ["滑点费率", allocationRate((payload.cost_profile || {}).slippage_rate)]].forEach(function (pair) {
    cards.appendChild(allocationMetric(pair[0], pair[1]));
  });
  section.appendChild(cards);
  section.appendChild(allocationBenchmarkSwitch(function () { allocationRenderBacktest(section, payload, item); }));
  var curve = payload.equity_curve || {};
  section.appendChild(allocationCurve(curve.rows || [], "strategy_equity", allocationState.benchmark + "_equity", "同池策略", allocationBenchmarkName(allocationState.benchmark)));
  section.appendChild(allocationBacktestPeriodControl(function (period) { allocationLoadBacktest(section, item, false, period); }));
  var cache = payload.cache || {};
  var cacheNote = cache.status === "derived" ? "该区间由覆盖它的已完成回测缓存截取并重算窗口指标，未重新运行策略。" : "已命中完整回测缓存，未重新运行策略。";
  section.appendChild(allocationElement("p", "radar-note", cacheNote));
  section.appendChild(allocationElement("p", "radar-note", "实线：同池策略；虚线：" + allocationBenchmarkName(allocationState.benchmark) + "。成本、换手与成交限制以回测产物记录为准。"));
  if (allocationState.backtestPeriod === "inception") section.appendChild(allocationElement("p", "radar-note", "“成立以来”指策略可回测以来，以当前已完成产物的数据覆盖为准。"));
  if (!overview) {
    var trades = ((payload.trades || {}).rows || []).filter(function (trade) { return trade.symbol === item.symbol; });
    section.appendChild(allocationElement("p", "radar-note", trades.length ? ("该 ETF 在本回测中实际出现 " + trades.length + " 笔成交记录。") : "该 ETF 未出现在本回测产物的成交记录中。"));
  }
  (payload.warnings || metrics.warnings || []).forEach(function (warning) {
    section.appendChild(allocationElement("p", "radar-note", "回测警告：" + warning));
  });
  if (payload.research_notice) section.appendChild(allocationElement("p", "radar-note", payload.research_notice));
}

function allocationBacktestTaskStart(period) {
  return allocationBacktestPeriodStart(period) || "2005-01-01";
}

function allocationPollBacktest(taskId, section, item, period) {
  window.setTimeout(function () {
    allocationRequest("/api/v1/radar/backtests/tasks/" + encodeURIComponent(taskId)).then(function (task) {
      if (!allocationBacktestSectionIsCurrent(section, item) || allocationState.backtestPeriod !== period) return;
      if (task.status === "completed") {
        allocationLoadBacktest(section, item, true, period);
      } else if (task.status === "failed") {
        section.appendChild(allocationElement("p", "radar-note", "策略回测未完成：" + (task.error || "后端未返回原因。")));
      } else {
        allocationPollBacktest(taskId, section, item, period);
      }
    }).catch(function (error) {
      if (allocationBacktestSectionIsCurrent(section, item)) section.appendChild(allocationElement("p", "radar-note", "无法获取回测任务状态：" + error.message));
    });
  }, 1200);
}

function allocationLoadBacktest(section, item, retried, requestedPeriod) {
  var period = requestedPeriod || allocationState.backtestPeriod;
  var requestedStart = allocationBacktestPeriodStart(period);
  var url = "/api/v1/radar/backtests?universe_id=" + encodeURIComponent(allocationState.universeId) + "&end_date=" + encodeURIComponent(allocationState.snapshot.as_of_date);
  if (requestedStart) url += "&start_date=" + encodeURIComponent(requestedStart);
  section.appendChild(allocationElement("p", "radar-note", "正在读取已完成的同池策略回测…"));
  allocationRequest(url).then(function (payload) {
    if (!allocationBacktestSectionIsCurrent(section, item) || allocationState.backtestPeriod !== period) return;
    allocationRenderBacktest(section, payload, item);
  }).catch(function (error) {
    if (!allocationBacktestSectionIsCurrent(section, item) || allocationState.backtestPeriod !== period) return;
    if (error.status !== 404 || retried) {
      section.appendChild(allocationElement("p", "radar-note", "策略回测暂不可用：" + error.message));
      return;
    }
    section.appendChild(allocationElement("p", "radar-note", "尚无覆盖该区间的缓存，正在由后端生成" + allocationBacktestPeriodLabel(period) + "同池策略参考…"));
    allocationRequest("/api/v1/radar/backtests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ universe_id: allocationState.universeId, start_date: allocationBacktestTaskStart(period), end_date: allocationState.snapshot.as_of_date })
    }).then(function (task) {
      allocationPollBacktest(task.task_id, section, item, period);
    }).catch(function (startError) {
      if (allocationBacktestSectionIsCurrent(section, item)) section.appendChild(allocationElement("p", "radar-note", "无法启动策略回测：" + startError.message));
    });
  });
}

function allocationItemStatusDetail(item) {
  return item.error_summary ? " 原因：" + item.error_summary : "";
}

function allocationDataAvailability(snapshot) {
  var summary = snapshot.status_summary || {};
  var fresh = Number(summary.fresh || 0);
  var stale = Number(summary.stale || 0);
  var failed = Number(summary.failed || 0);
  var section = allocationElement("section", "panel radar-data-availability");
  section.appendChild(allocationElement("h3", "", "数据可用性"));
  var message = failed ? (failed + " 个标的本次未取得有效行情，未参与评分；") : "本次快照均已生成评分；";
  message += stale ? (stale + " 个标的沿用上次健康数据。") : "其余标的使用本次行情。";
  section.appendChild(allocationElement("p", "radar-meta", message));
  var metrics = allocationElement("div", "radar-detail-metrics");
  [["最新数据", fresh], ["沿用历史数据", stale], ["本次不可用", failed]].forEach(function (pair) {
    metrics.appendChild(allocationMetric(pair[0], String(pair[1])));
  });
  section.appendChild(metrics);
  var affected = snapshot.items.filter(function (item) { return item.status !== "fresh"; });
  affected.forEach(function (item) {
    var state = item.status === "stale" ? "沿用上次健康数据" : "本次不可用，未参与评分";
    section.appendChild(allocationElement("p", "radar-note", item.name + " · " + item.symbol + "：" + state + allocationItemStatusDetail(item)));
  });
  return section;
}

function allocationActivate() {
  Array.prototype.forEach.call(document.querySelectorAll(".view"), function (view) {
    view.classList.toggle("active", view.id === "view-radar");
  });
  Array.prototype.forEach.call(document.querySelectorAll(".nav-item"), function (item) {
    item.classList.toggle("on", item.dataset.view === "radar");
  });
  var title = document.getElementById("currentViewTitle");
  if (title) title.textContent = "配置雷达";
}

function allocationHeader() {
  var header = allocationElement("div", "radar-head");
  var selector = document.createElement("select");
  selector.setAttribute("aria-label", "选择配置雷达标的池");
  allocationState.universes.forEach(function (universe) {
    selector.add(new Option(universe.name, universe.id, false, universe.id === allocationState.universeId));
  });
  selector.addEventListener("change", function () {
    allocationState.universeId = selector.value;
    allocationState.detail = null;
    allocationState.refreshMessage = "";
    allocationLoad();
  });
  var title = allocationElement("div", "radar-head-title");
  title.appendChild(allocationElement("h2", "", "ETF 配置雷达"));
  title.appendChild(allocationElement("p", "radar-meta", allocationSnapshotMeta(allocationState.snapshot)));
  var controls = allocationElement("div", "radar-head-controls");
  var actions = allocationElement("div", "radar-head-actions");
  var refresh = allocationElement("button", "radar-refresh", "更新数据");
  refresh.type = "button";
  refresh.addEventListener("click", function () { allocationStartRefresh(refresh, feedback); });
  actions.appendChild(refresh);
  var feedback = allocationElement("p", "radar-meta");
  feedback.setAttribute("role", "status");
  feedback.textContent = allocationState.refreshMessage;
  actions.appendChild(feedback);
  controls.append(selector, actions);
  header.append(title, controls);
  return header;
}

function allocationSnapshotMeta(snapshot) {
  if (!snapshot) return "正在读取完成快照…";
  var parts = ["数据截至 " + snapshot.as_of_date];
  if (snapshot.completed_at) parts.push("刷新完成 " + String(snapshot.completed_at).replace("T", " ").replace(/([+-]\d\d:\d\d)$/, ""));
  if (snapshot.provider) parts.push("采集通道 " + snapshot.provider);
  return parts.join(" · ");
}

function allocationRefreshFeedback(button, feedback, message) {
  allocationState.refreshMessage = message;
  button.disabled = false;
  button.textContent = "更新数据";
  feedback.textContent = message;
}

function allocationPollRefresh(taskId, button, feedback) {
  window.setTimeout(function () {
    allocationRequest("/api/v1/radar/refresh/" + encodeURIComponent(taskId)).then(function (task) {
      if (task.status === "completed") {
        allocationState.performanceCache = {};
        allocationState.refreshMessage = "数据更新完成，已载入最新快照。";
        allocationLoad();
      } else if (task.status === "failed") {
        allocationRefreshFeedback(button, feedback, "数据更新失败：" + (task.error || "后端未返回原因。"));
      } else {
        allocationPollRefresh(taskId, button, feedback);
      }
    }).catch(function (error) {
      allocationRefreshFeedback(button, feedback, "无法获取更新状态：" + error.message);
    });
  }, 1200);
}

function allocationStartRefresh(button, feedback) {
  if (!allocationState.universeId) return;
  button.disabled = true;
  button.textContent = "更新中…";
  feedback.textContent = "正在后台更新当前标的池，完成后自动刷新榜单。";
  allocationRequest("/api/v1/radar/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ universe_id: allocationState.universeId })
  }).then(function (task) {
    allocationPollRefresh(task.task_id, button, feedback);
  }).catch(function (error) {
    allocationRefreshFeedback(button, feedback, "无法启动数据更新：" + error.message);
  });
}

function allocationShowList() {
  allocationState.detail = null;
  var root = allocationRoot();
  var snapshot = allocationState.snapshot;
  root.replaceChildren(allocationHeader(), allocationDataAvailability(snapshot), allocationBacktestSection(null, true), allocationElement("p", "radar-note", snapshot.research_notice || "研究评分，不构成投资建议。"));
  var groups = {};
  snapshot.items.forEach(function (item) {
    var category = item.category || "其他";
    if (!groups[category]) groups[category] = [];
    groups[category].push(item);
  });
  Object.keys(groups).forEach(function (category) {
    var section = allocationElement("section", "radar-section");
    section.appendChild(allocationElement("h3", "", category));
    var table = document.createElement("table");
    table.innerHTML = "<thead><tr><th>排名</th><th>标的</th><th>评分</th><th>等级</th><th>数据状态</th></tr></thead>";
    var body = document.createElement("tbody");
    groups[category].forEach(function (item) {
      var row = document.createElement("tr");
      row.className = "radar-row";
      row.tabIndex = 0;
      [item.rank || "-", item.name + " · " + item.symbol, item.score == null ? "-" : Number(item.score).toFixed(1), item.grade || "-", item.status === "stale" ? "沿用历史" : (item.status === "failed" ? "不可用" : "最新")].forEach(function (value) {
        row.appendChild(allocationElement("td", "", value));
      });
      row.addEventListener("click", function () { allocationShowDetail(item); });
      row.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          allocationShowDetail(item);
        }
      });
      body.appendChild(row);
    });
    table.appendChild(body);
    section.appendChild(table);
    root.appendChild(section);
  });
}

function allocationShowDetail(item, fromHistory) {
  allocationState.detail = item;
  if (!fromHistory && allocationState.snapshot) {
    var detailHash = "#radar/" + encodeURIComponent(allocationState.universeId) + "/" + encodeURIComponent(allocationState.snapshot.run_id) + "/" + encodeURIComponent(item.symbol);
    window.history.pushState({ radarDetail: item.symbol }, "", detailHash);
  }
  var root = allocationRoot();
  var back = allocationElement("button", "radar-detail-back", "‹");
  back.type = "button";
  back.setAttribute("aria-label", "返回配置雷达");
  back.addEventListener("click", function () {
    if (window.history.state && window.history.state.radarDetail) {
      window.history.back();
      return;
    }
    var listHash = "#radar/" + encodeURIComponent(allocationState.universeId) + "/" + encodeURIComponent(allocationState.snapshot.run_id);
    window.history.pushState({}, "", listHash);
    allocationShowList();
  });
  var head = allocationElement("section", "radar-detail-head");
  var heading = allocationElement("div", "");
  heading.appendChild(allocationElement("h2", "", item.name + " · " + item.symbol));
  heading.appendChild(allocationElement("p", "radar-meta", "ETF · 数据截至 " + allocationState.snapshot.as_of_date));
  head.append(back, heading);
  var summary = allocationElement("section", "panel radar-detail-section");
  summary.appendChild(allocationElement("h3", "", "评分摘要"));
  var metrics = allocationElement("div", "radar-detail-metrics");
  [["综合评分", item.score == null ? "—" : Number(item.score).toFixed(1)], ["类别排名", item.rank ? "#" + item.rank : "—"], ["研究等级", item.grade || "—"], ["数据状态", item.status || "—"]].forEach(function (pair) {
    var card = allocationElement("div", "radar-detail-metric");
    card.append(allocationElement("span", "k", pair[0]), allocationElement("strong", "v", pair[1]));
    metrics.appendChild(card);
  });
  summary.appendChild(metrics);
  if (item.status !== "fresh") {
    var statusNote = item.status === "stale" ? "本次刷新未获得最新行情；评分依据沿用上次健康快照。" : "本次刷新未获得有效行情；该标的不参与当前评分。";
    summary.appendChild(allocationElement("p", "radar-note", statusNote + allocationItemStatusDetail(item)));
  }
  var factors = allocationElement("section", "panel radar-detail-section");
  factors.appendChild(allocationElement("h3", "", "评分依据"));
  factors.appendChild(allocationElement("p", "radar-meta", "评分配置由趋势、回撤、波动与流动性共同构成。"));
  var list = allocationElement("dl", "radar-factor-list");
  var labels = { trend: "趋势原始值", trend_percentile: "趋势类别分位", trend_contribution: "趋势分数贡献", drawdown: "回撤控制原始值", drawdown_percentile: "回撤类别分位", drawdown_contribution: "回撤分数贡献", volatility: "波动控制原始值", volatility_percentile: "波动类别分位", volatility_contribution: "波动分数贡献", liquidity: "流动性原始值", liquidity_percentile: "流动性类别分位", liquidity_contribution: "流动性分数贡献" };
  Object.keys(item.factors || {}).forEach(function (key) {
    var value = Number(item.factors[key]);
    var display = String(item.factors[key]);
    if (isFinite(value)) {
      if (key.indexOf("percentile") > -1 || key === "trend" || key === "drawdown" || key === "volatility") display = (value * 100).toFixed(2) + "%";
      else if (key === "liquidity") display = (value / 100000000).toFixed(2) + " 亿元";
      else if (key.indexOf("contribution") > -1) display = value.toFixed(1) + " 分";
      else display = value.toFixed(4);
    }
    list.append(allocationElement("dt", "", labels[key] || key), allocationElement("dd", "", display));
  });
  factors.appendChild(list);
  var performance = allocationPerformanceSection(item);
  var backtest = allocationBacktestSection(item);
  root.replaceChildren(head, summary, factors, performance, backtest, allocationElement("p", "radar-note", "评分和历史数据均为研究用途，不构成投资建议。"));
}

function allocationRestoreDetail() {
  var parts = window.location.hash.split("/");
  var symbol = parts.length >= 4 ? decodeURIComponent(parts[3]) : null;
  if (!symbol || !allocationState.snapshot) return;
  var item = allocationState.snapshot.items.filter(function (candidate) { return candidate.symbol === symbol; })[0];
  if (item) allocationShowDetail(item, true);
}

function allocationLoad() {
  var root = allocationRoot();
  root.replaceChildren(allocationElement("p", "radar-meta", "正在读取配置雷达快照…"));
  allocationRequest("/api/v1/radar/universes")
    .then(function (universes) {
      allocationState.universes = universes;
      if (!allocationState.universeId && universes.length) allocationState.universeId = universes[0].id;
      return allocationRequest("/api/v1/radar/snapshots/latest?universe_id=" + encodeURIComponent(allocationState.universeId));
    })
    .then(function (snapshot) {
      allocationState.snapshot = snapshot;
      allocationShowList();
      allocationRestoreDetail();
    })
    .catch(function (error) { root.replaceChildren(allocationHeader(), allocationElement("p", "radar-note", error.message)); });
}

export function initRadar() {
  var nav = document.querySelector('[data-view="radar"]');
  if (nav) nav.addEventListener("click", allocationLoad);
  if (window.location.hash.indexOf("#radar") === 0) {
    allocationActivate();
    allocationLoad();
  }
  window.addEventListener("popstate", function () {
    if (!allocationState.snapshot || window.location.hash.indexOf("#radar") !== 0) return;
    allocationActivate();
    allocationShowList();
    allocationRestoreDetail();
  });
}
