// 配置雷达基础视图：使用浏览器兼容语法直接消费完成快照。
var allocationState = { universes: [], universeId: null, snapshot: null, detail: null };

function allocationRoot() { return document.getElementById("radarContent"); }

function allocationElement(tagName, className, text) {
  var node = document.createElement(tagName);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function allocationRequest(url) {
  return fetch(url).then(function (response) {
    if (!response.ok) throw new Error("请求失败（" + response.status + "）");
    return response.json();
  });
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
    allocationLoad();
  });
  var title = allocationElement("div", "");
  title.appendChild(allocationElement("h2", "", "ETF 配置雷达"));
  title.appendChild(allocationElement("p", "radar-meta", allocationState.snapshot ? "数据截至 " + allocationState.snapshot.as_of_date : "正在读取完成快照…"));
  header.append(selector, title);
  return header;
}

function allocationShowList() {
  var root = allocationRoot();
  var snapshot = allocationState.snapshot;
  root.replaceChildren(allocationHeader(), allocationElement("p", "radar-note", snapshot.research_notice || "研究评分，不构成投资建议。"));
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
      [item.rank || "-", item.name + " · " + item.symbol, item.score == null ? "-" : Number(item.score).toFixed(1), item.grade || "-", item.status || "-"].forEach(function (value) {
        row.appendChild(allocationElement("td", "", value));
      });
      row.addEventListener("click", function () { allocationShowDetail(item); });
      body.appendChild(row);
    });
    table.appendChild(body);
    section.appendChild(table);
    root.appendChild(section);
  });
}

function allocationShowDetail(item) {
  allocationState.detail = item;
  var root = allocationRoot();
  var back = allocationElement("button", "radar-detail-back", "‹ 返回配置雷达");
  back.type = "button";
  back.addEventListener("click", allocationShowList);
  var head = allocationElement("section", "radar-detail-head");
  head.append(back, allocationElement("h2", "", item.name + " · " + item.symbol));
  var summary = allocationElement("section", "panel radar-detail-section");
  summary.appendChild(allocationElement("h3", "", "评分摘要"));
  summary.appendChild(allocationElement("p", "radar-meta", "综合评分：" + (item.score == null ? "—" : Number(item.score).toFixed(1)) + " · 类别排名：#" + (item.rank || "—") + " · 研究等级：" + (item.grade || "—")));
  var factors = allocationElement("section", "panel radar-detail-section");
  factors.appendChild(allocationElement("h3", "", "评分依据"));
  var list = document.createElement("dl");
  Object.keys(item.factors || {}).forEach(function (key) {
    list.append(allocationElement("dt", "", key), allocationElement("dd", "", String(item.factors[key])));
  });
  factors.appendChild(list);
  root.replaceChildren(head, summary, factors, allocationElement("p", "radar-note", "完整的历史表现与池级策略回测正在恢复兼容层；当前页面仅展示已完成快照的客观评分数据。"));
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
    .then(function (snapshot) { allocationState.snapshot = snapshot; allocationShowList(); })
    .catch(function (error) { root.replaceChildren(allocationHeader(), allocationElement("p", "radar-note", error.message)); });
}

export function initRadar() {
  var nav = document.querySelector('[data-view="radar"]');
  if (nav) nav.addEventListener("click", allocationLoad);
  if (window.location.hash.indexOf("#radar") === 0) {
    allocationActivate();
    allocationLoad();
  }
}
