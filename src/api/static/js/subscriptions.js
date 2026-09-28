// 订阅页：列表、配置弹窗、运行详情。
import { api } from "./api.js";
import { bus, store } from "./state.js";
import { invalidateSettings } from "./settings.js";

const ACTIVE = new Set(["queued", "running"]);
const RUN_STATUS = { queued: "排队中", running: "运行中", succeeded: "成功",
  partial: "部分成功", failed: "失败", interrupted: "已中断" };
let requestToken = 0;
let modalToken = 0;
let selectedId = null;
let pollTimer = null;
let returnFocus = null;
let initialized = false;

const get = (id) => document.getElementById(id);
function element(tag, className = "", value = "") {
  const result = document.createElement(tag);
  result.className = className;
  result.textContent = value;
  return result;
}
function action(label, className, handler) {
  const result = element("button", className, label);
  result.type = "button";
  result.addEventListener("click", handler);
  return result;
}
function notice(message, error = false) {
  const box = get("subsNotice");
  box.textContent = message;
  box.className = `subs-notice ${error ? "error" : "success"}`;
  box.hidden = !message;
}
function symbolChip(item, remove = null) {
  const chip = element("span", "subs-symbol-chip");
  const kind = item.kind === "index" ? "index" : "stock";
  chip.append(element("span", `subs-kind subs-kind-${kind}`, kind === "index" ? "指数" : "股票"),
    element("span", "subs-symbol-label",
      `${item.symbol}${item.display_name ? `（${item.display_name}）` : ""}`));
  if (remove) {
    const close = action("×", "subs-symbol-remove", remove);
    close.setAttribute("aria-label", `移除 ${kind === "index" ? "指数" : "股票"} ${item.symbol}`);
    chip.append(close);
  }
  return chip;
}
function timeLabel(value) {
  if (!value) return "—";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric",
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
}
function runLabel(run) {
  return run ? `${RUN_STATUS[run.status] || run.status} · ${run.ok || 0}/${run.total || 0} 成功` : "尚未推送";
}
function stopPoll() {
  if (pollTimer !== null) clearTimeout(pollTimer);
  pollTimer = null;
}
function schedulePoll(run) {
  stopPoll();
  if (!run || !ACTIVE.has(run.status) || store.currentView !== "subscriptions" || !selectedId) return;
  const id = selectedId;
  pollTimer = setTimeout(async () => {
    if (selectedId !== id || store.currentView !== "subscriptions") return;
    try {
      const current = await api.getSubscriptionRun(id, run.id);
      if (selectedId !== id || store.currentView !== "subscriptions") return;
      const card = [...get("subsDetail").querySelectorAll(".subs-run")]
        .find((item) => item.dataset.runId === String(run.id));
      if (card) {
        updateRunCard(card, current);
        const trigger = get("subsRunNow");
        if (trigger) {
          trigger.disabled = ACTIVE.has(current.status);
          trigger.title = trigger.disabled ? "该订阅已有推送任务正在运行" : "";
        }
      } else {
        await openDetail(id, true);
        return;
      }
      schedulePoll(current);
    } catch (error) {
      if (selectedId !== id || store.currentView !== "subscriptions") return;
      notice(`运行进度读取失败：${error.message}；稍后自动重试。`, true);
      schedulePoll(run);
    }
  }, 2500);
}
function closeModal() {
  if (get("subsModal").hidden) return;
  ++modalToken;
  get("subsModal").hidden = true;
  get("subsModalBody").replaceChildren();
  document.body.classList.remove("subs-modal-open");
  returnFocus?.focus();
  returnFocus = null;
}
function openModal(title, trigger) {
  returnFocus = trigger || document.activeElement;
  get("subsModalTitle").textContent = title;
  get("subsModalBody").replaceChildren();
  get("subsModal").hidden = false;
  document.body.classList.add("subs-modal-open");
  get("subsModalClose").focus();
  return ++modalToken;
}
function field(label, control) {
  const row = element("label", "subs-field");
  row.append(element("span", "subs-field-label", label), control);
  return row;
}
function formMessage() {
  const message = element("div", "subs-form-message");
  message.setAttribute("role", "status");
  return message;
}
function modalActions() {
  const row = element("div", "subs-modal-actions");
  row.append(action("取消", "subs-btn", closeModal));
  return row;
}
function showFormError(box, message) {
  box.textContent = message;
  box.className = "subs-form-message error";
}

async function openEmailSettings(trigger) {
  const token = openModal("邮箱设置", trigger);
  const body = get("subsModalBody");
  body.append(element("p", "subs-modal-intro", "保存 SMTP 配置后，可试发不含研报的测试邮件。"));
  const message = formMessage();
  body.append(message);
  try {
    const payload = await api.getConfig();
    if (token !== modalToken) return;
    const config = payload.config?.push?.email || {};
    const definitions = [["smtp_host", "SMTP 主机", "text"], ["smtp_port", "SMTP 端口", "number"],
      ["smtp_user", "SMTP 用户名", "text"], ["smtp_password", "SMTP 密码", "password"],
      ["to_addr", "收件人地址", "email"]];
    const form = element("form", "subs-modal-form");
    const controls = {};
    let passwordConfigured = config.smtp_password?.configured === true;
    let passwordDirty = false;
    let passwordRevealed = false;
    let passwordToggle = null;
    const passwordState = element("p", "subs-field-help",
      passwordConfigured ? "SMTP 密码已保存；点击“显示”可查看。" : "尚未保存 SMTP 密码。");
    for (const [key, label, type] of definitions) {
      const input = document.createElement("input");
      input.type = type;
      input.required = type !== "password";
      if (type === "number") { input.min = "1"; input.max = "65535"; }
      if (type === "password") {
        input.autocomplete = "new-password";
        input.placeholder = passwordConfigured ? "********" : "请输入 SMTP 密码";
      } else input.value = config[key] ?? "";
      controls[key] = input;
      if (type === "password") {
        const row = element("div", "subs-field");
        const caption = element("label", "subs-field-label", label);
        input.id = "subsSmtpPassword";
        caption.htmlFor = input.id;
        const control = element("div", "subs-password-control");
        passwordToggle = action("显示", "subs-password-toggle", async () => {
          if (passwordRevealed) {
            passwordRevealed = false;
            input.type = "password";
            if (!passwordDirty) input.value = "";
            passwordToggle.textContent = "显示";
            passwordToggle.setAttribute("aria-label", "显示 SMTP 密码");
            passwordToggle.setAttribute("aria-pressed", "false");
            return;
          }
          if (!passwordDirty && passwordConfigured) {
            passwordToggle.disabled = true;
            passwordToggle.textContent = "读取中…";
            try {
              const credential = await api.getCredential("push.email.smtp_password");
              if (token !== modalToken) return;
              input.value = String(credential.value || "");
            } catch (error) {
              if (token === modalToken) showFormError(message, `读取 SMTP 密码失败：${error.message}`);
              return;
            } finally {
              if (token === modalToken) {
                passwordToggle.disabled = false;
                passwordToggle.textContent = "显示";
              }
            }
          }
          if (token !== modalToken) return;
          passwordRevealed = true;
          input.type = "text";
          passwordToggle.textContent = "隐藏";
          passwordToggle.setAttribute("aria-label", "隐藏 SMTP 密码");
          passwordToggle.setAttribute("aria-pressed", "true");
        });
        passwordToggle.setAttribute("aria-label", "显示 SMTP 密码");
        passwordToggle.setAttribute("aria-pressed", "false");
        control.append(input, passwordToggle);
        row.append(caption, control);
        form.append(row, passwordState);
      } else form.append(field(label, input));
    }
    const buttons = modalActions();
    const inputSnapshot = () => JSON.stringify(definitions.map(([key]) =>
      key === "smtp_password" && !passwordDirty ? null : controls[key].value.trim()));
    let savedSnapshot = inputSnapshot();
    let runtimeApplied = true;
    const test = action("发送测试邮件", "subs-btn", async () => {
      if (!runtimeApplied) {
        showFormError(message, "配置已保存，但当前服务尚未应用；请重启服务后再试发邮件。");
        return;
      }
      if (inputSnapshot() !== savedSnapshot) {
        showFormError(message, "请先保存邮箱配置，再发送测试邮件。");
        return;
      }
      test.disabled = true;
      message.textContent = "正在发送测试邮件…";
      message.className = "subs-form-message";
      try {
        await api.testPushEmail();
        if (token !== modalToken) return;
        message.textContent = "SMTP 服务已接受测试邮件，请检查收件箱。";
        message.className = "subs-form-message success";
      } catch (error) {
        if (token !== modalToken) return;
        showFormError(message, `试发失败：${error.message}`);
      } finally { if (token === modalToken) test.disabled = inputSnapshot() !== savedSnapshot || !runtimeApplied; }
    });
    const syncTest = () => {
      const dirty = inputSnapshot() !== savedSnapshot;
      test.disabled = dirty || !runtimeApplied;
      if (dirty) showFormError(message, "配置尚未保存；请先保存，再发送测试邮件。");
    };
    for (const [key, input] of Object.entries(controls)) input.addEventListener("input", () => {
      if (key === "smtp_password") passwordDirty = true;
      syncTest();
    });
    const save = element("button", "subs-btn primary", "保存配置");
    save.type = "submit";
    buttons.append(test, save);
    form.append(buttons);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const email = {};
      for (const [key, , type] of definitions) {
        if (type === "password" && !passwordDirty) continue;
        const value = controls[key].value.trim();
        if (type === "password" && !value) continue;
        email[key] = type === "number" ? Number(value) : value;
      }
      save.disabled = true;
      message.textContent = "正在保存…";
      message.className = "subs-form-message";
      try {
        const result = await api.updateConfig({ push: { email } });
        if (token !== modalToken) return;
        invalidateSettings();
        if (result.persisted !== true) throw new Error("配置未保存，请重试");
        passwordConfigured = result.config?.push?.email?.smtp_password?.configured === true;
        passwordDirty = false;
        passwordRevealed = false;
        controls.smtp_password.type = "password";
        controls.smtp_password.value = "";
        controls.smtp_password.placeholder = passwordConfigured ? "********" : "请输入 SMTP 密码";
        passwordToggle.textContent = "显示";
        passwordToggle.setAttribute("aria-label", "显示 SMTP 密码");
        passwordToggle.setAttribute("aria-pressed", "false");
        passwordState.textContent = passwordConfigured
          ? "SMTP 密码已保存；点击“显示”可查看。" : "尚未保存 SMTP 密码。";
        savedSnapshot = inputSnapshot();
        runtimeApplied = result.applied === true;
        test.disabled = !runtimeApplied;
        message.textContent = result.applied
          ? "邮箱配置已保存并生效，可以发送测试邮件。"
          : `邮箱配置已保存，但当前服务尚未应用：${result.reload_error || "请重启服务后再试发邮件"}`;
        message.className = `subs-form-message ${result.applied ? "success" : "error"}`;
      } catch (error) {
        if (token !== modalToken) return;
        showFormError(message, `保存失败：${error.message}`);
      } finally { if (token === modalToken) save.disabled = false; }
    });
    body.append(form);
    controls.smtp_host.focus();
  } catch (error) {
    if (token === modalToken) showFormError(message, `读取配置失败：${error.message}`);
  }
}

export function isCodeToken(value) {
  return /^(?:(?:sh|sz)?\d{5,6}|[a-z]{2,10})$/i.test(value);
}

function lookupQuery(value) {
  return /^(?:sh|sz)\d{5,6}$/i.test(value) ? value.slice(2) : value;
}

export function exactMatches(candidates, query) {
  const bare = query.replace(/^(sh|sz)(?=\d)/i, "").toUpperCase();
  const normalized = /^\d+$/.test(bare) ? bare.padStart(6, "0") : bare;
  return candidates.filter((candidate) => candidate.symbol.toUpperCase() === normalized);
}

export function suggestedName(symbols) {
  if (!symbols.length) return "每日研报";
  const names = symbols.slice(0, 2).map((item) => item.display_name || item.symbol);
  return `${names.join("、")}${symbols.length > 2 ? `等${symbols.length}个标的` : ""}每日研报`;
}

function openSubscriptionForm(sub = null, trigger = document.activeElement) {
  const token = openModal(sub ? "编辑订阅" : "新建订阅", trigger);
  const form = element("form", "subs-modal-form");
  const selected = (sub?.symbols || []).map((item) => ({ symbol: item.symbol,
    kind: item.kind, index_style: item.index_style, display_name: item.display_name || "" }));
  const search = document.createElement("textarea");
  search.rows = 2;
  search.placeholder = "搜索公司、指数名称或代码；可粘贴多个代码";
  search.setAttribute("aria-label", "搜索并添加标的");
  search.setAttribute("role", "combobox");
  search.setAttribute("aria-autocomplete", "list");
  search.setAttribute("aria-controls", "subsSymbolCandidates");
  search.setAttribute("aria-expanded", "false");
  const results = element("div", "subs-candidates");
  results.id = "subsSymbolCandidates";
  results.setAttribute("role", "listbox");
  results.setAttribute("aria-label", "标的搜索候选");
  const searchState = element("p", "subs-search-state", "输入名称或代码，从候选中添加。");
  searchState.setAttribute("role", "status");
  const chosen = element("div", "subs-chosen");
  chosen.setAttribute("aria-label", "已选标的");
  const count = element("p", "subs-field-help");
  const namePreview = element("span", "subs-name-preview");
  const editName = action("修改名称", "subs-name-edit", () => {
    nameRow.hidden = false;
    editName.hidden = true;
    name.value = sub?.name || suggestedName(selected);
    name.focus();
  });
  const nameRow = element("div", "subs-name-row");
  nameRow.hidden = true;
  const name = document.createElement("input");
  name.maxLength = 80;
  name.placeholder = "订阅名称";
  name.setAttribute("aria-label", "订阅名称");
  nameRow.append(name);
  const nameLine = element("div", "subs-name-line");
  nameLine.append(namePreview, editName);
  const time = document.createElement("input");
  time.type = "time";
  time.required = true;
  time.value = sub?.time || "08:00";
  const message = formMessage();
  const buttons = modalActions();
  const save = element("button", "subs-btn primary", sub ? "保存修改" : "创建订阅");
  save.type = "submit";
  buttons.append(save);
  let queryTimer = null;
  let searchSeq = 0;
  let batch = [];
  let batchActive = false;
  let saving = false;
  const current = () => token === modalToken;
  const setSearchState = (text, error = false) => {
    searchState.textContent = text;
    searchState.className = `subs-search-state${error ? " error" : ""}`;
  };
  const refreshSelected = () => {
    chosen.replaceChildren();
    for (const item of selected) chosen.append(symbolChip(item, () => {
      const index = selected.indexOf(item);
      if (index >= 0) selected.splice(index, 1);
      refreshSelected();
      search.focus();
    }));
    count.textContent = `已选 ${selected.length} 个标的`;
    namePreview.textContent = sub?.name || suggestedName(selected);
    save.disabled = !selected.length || batchActive || saving;
  };
  const addCandidate = (candidate) => {
    if (!["stock", "index"].includes(candidate.kind)) return;
    if (selected.some((item) => item.kind === candidate.kind && item.symbol === candidate.symbol)) {
      setSearchState("此标的已添加。");
      return;
    }
    selected.push({ symbol: candidate.symbol, kind: candidate.kind,
      index_style: candidate.index_style || null, display_name: candidate.display_name || "" });
    refreshSelected();
    setSearchState(`已添加 ${candidate.symbol}${candidate.display_name ? `（${candidate.display_name}）` : ""}`);
  };
  const clearResults = () => {
    results.replaceChildren();
    search.setAttribute("aria-expanded", "false");
  };
  const showCandidates = (candidates, onChoose) => {
    clearResults();
    if (!candidates.length) return;
    candidates.forEach((candidate, index) => {
      const option = action("", "subs-candidate", () => onChoose(candidate));
      option.id = `subsSymbolOption${index}`;
      option.setAttribute("role", "option");
      const market = { hk: "香港", us: "美国", "a-shares": "A股" }[candidate.market] || "";
      option.append(symbolChip(candidate),
        element("span", "subs-candidate-market", market));
      results.append(option);
    });
    search.setAttribute("aria-expanded", "true");
  };
  const batchNext = async () => {
    if (!current()) return;
    if (!batch.length) {
      batchActive = false;
      search.disabled = false;
      clearResults();
      refreshSelected();
      search.focus();
      return;
    }
    const query = batch[0];
    setSearchState(`正在识别 ${query}（剩余 ${batch.length} 个）…`);
    try {
      const payload = await api.searchPushSymbols(lookupQuery(query));
      if (!current() || !batchActive || batch[0] !== query) return;
      const exact = exactMatches(payload.candidates || [], query);
      const fallback = payload.stocks_available ? "" : " 股票名称目录不可用，股票候选可能暂缺名称。";
      if (exact.length === 1) {
        addCandidate(exact[0]);
        batch.shift();
        await batchNext();
        return;
      }
      setSearchState(exact.length
        ? `${query} 对应多个标的，请选择股票或指数。${fallback}`
        : `未找到 ${query}，不能直接添加；可跳过后继续。${fallback}`, !exact.length);
      showCandidates(exact, (candidate) => {
        addCandidate(candidate);
        batch.shift();
        void batchNext();
      });
      results.append(action(`跳过 ${query}`, "subs-candidate-skip", () => {
        batch.shift();
        void batchNext();
      }));
      search.setAttribute("aria-expanded", "true");
    } catch (error) {
      if (!current()) return;
      setSearchState(`查询 ${query} 失败：${error.message}`, true);
      clearResults();
      results.append(action("重试查询", "subs-candidate-skip", () => void batchNext()),
        action(`跳过 ${query}`, "subs-candidate-skip", () => {
          batch.shift();
          void batchNext();
        }));
      search.setAttribute("aria-expanded", "true");
    }
  };
  const searchOne = async (query, attempt = 0) => {
    const seq = ++searchSeq;
    if (!query) { clearResults(); setSearchState("输入名称或代码，从候选中添加。"); return; }
    setSearchState("正在搜索…");
    try {
      const payload = await api.searchPushSymbols(lookupQuery(query));
      if (!current() || seq !== searchSeq || batchActive) return;
      const candidates = payload.candidates || [];
      showCandidates(candidates, (candidate) => {
        addCandidate(candidate);
        search.value = "";
        clearResults();
        search.focus();
      });
      const availability = payload.stocks_available ? "" : " 股票名称目录不可用，可输入股票代码查找；名称可能暂缺。";
      setSearchState(candidates.length
        ? `找到 ${candidates.length} 个候选，请选择。${availability}`
        : `没有找到标的；未收录的代码不能直接添加。${availability}`, !candidates.length);
      if (!payload.stocks_available && !candidates.length && /[\u3400-\u9fff]/.test(query) && attempt < 3) {
        setSearchState("股票名称目录正在加载，稍后自动重试…");
        setTimeout(() => {
          if (current() && seq === searchSeq && search.value.trim() === query) void searchOne(query, attempt + 1);
        }, 1500);
      }
    } catch (error) {
      if (!current() || seq !== searchSeq || batchActive) return;
      clearResults();
      setSearchState(`搜索失败：${error.message}，请重试。`, true);
    }
  };
  const dismissCandidates = () => {
    clearTimeout(queryTimer);
    queryTimer = null;
    ++searchSeq;
    clearResults();
  };
  search.addEventListener("input", () => {
    clearTimeout(queryTimer);
    queryTimer = null;
    ++searchSeq;
    clearResults();
    const query = search.value.trim();
    const parts = query.split(/[\s,，]+/).filter(Boolean);
    if (parts.length > 1 && parts.every(isCodeToken)) {
      batch = parts;
      batchActive = true;
      search.value = "";
      search.disabled = true;
      clearResults();
      refreshSelected();
      void batchNext();
      return;
    }
    queryTimer = setTimeout(() => { queryTimer = null; void searchOne(query); }, 220);
  });
  search.addEventListener("keydown", (event) => {
    if (event.isComposing || event.keyCode === 229) return;
    if (event.key === "ArrowDown" && results.querySelector("button")) {
      event.preventDefault();
      results.querySelector("button").focus();
    } else if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      const options = [...results.querySelectorAll(".subs-candidate")];
      if (options.length === 1) options[0].click();
      else if (options.length > 1) options[0].focus();
      else void searchOne(search.value.trim());
    } else if (event.key === "Escape" && (search.getAttribute("aria-expanded") === "true" || queryTimer !== null)) {
      event.stopPropagation();
      dismissCandidates();
    }
  });
  results.addEventListener("keydown", (event) => {
    const options = [...results.querySelectorAll("button")];
    const index = options.indexOf(document.activeElement);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      options[(index + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length]?.focus();
    } else if (event.key === "Escape") {
      event.stopPropagation();
      dismissCandidates();
      search.focus();
    }
  });
  form.append(field("添加标的", search), searchState, results,
    element("div", "subs-field-label", "已选标的"), chosen, count,
    nameLine, nameRow, field("每日发送时间（北京时间）", time), message, buttons);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (batchActive || search.value.trim()) {
      showFormError(message, "请先从候选列表选择标的，或处理完粘贴的代码。");
      return;
    }
    if (!selected.length) { showFormError(message, "请先添加至少一个标的。"); return; }
    const title = nameRow.hidden ? (sub?.name || suggestedName(selected)) : name.value.trim();
    if (!title) { showFormError(message, "请输入订阅名称。"); name.focus(); return; }
    const payload = { name: title, time: time.value, symbols: selected,
      channel: "email", enabled: sub?.enabled ?? true };
    saving = true;
    save.disabled = true;
    message.textContent = "正在保存…";
    message.className = "subs-form-message";
    try {
      if (sub) await api.updateSubscription(sub.id, payload);
      else await api.createSubscription(payload);
      if (!current()) return;
      closeModal();
      notice(sub ? "订阅已更新" : "订阅已创建");
      if (sub) await openDetail(sub.id);
      else { selectedId = null; await loadList(); }
    } catch (error) {
      if (current()) showFormError(message, error.message);
    } finally {
      saving = false;
      if (current()) refreshSelected();
    }
  });
  refreshSelected();
  get("subsModalBody").append(form);
  search.focus();
}

function listCard(sub) {
  const card = action("", "subs-list-card", () => openDetail(sub.id));
  const top = element("div", "subs-list-top");
  top.append(element("strong", "subs-list-name", sub.name),
    element("span", `subs-state ${sub.enabled ? "enabled" : "paused"}`, sub.enabled ? "启用" : "暂停"));
  const symbols = element("div", "subs-list-symbols");
  for (const item of (sub.symbols || []).slice(0, 2)) symbols.append(symbolChip(item));
  const remaining = (sub.symbols || []).length - Math.min((sub.symbols || []).length, 2);
  if (remaining) symbols.append(element("span", "subs-symbol-more", `另有 ${remaining} 个`));
  if (!sub.symbols?.length) symbols.append(element("span", "", "暂无标的"));
  card.append(top, symbols);
  const bottom = element("div", "subs-list-bottom");
  bottom.append(element("span", "", `每日 ${sub.time}（北京时间）`),
    element("span", "", `最近：${runLabel(sub.last_run)}`));
  card.append(bottom);
  return card;
}

async function loadList(quiet = false) {
  const token = ++requestToken;
  const root = get("subsList");
  if (!quiet) root.replaceChildren(element("p", "subs-loading", "正在读取订阅…"));
  try {
    const payload = await api.listSubscriptions();
    if (token !== requestToken || store.currentView !== "subscriptions" || selectedId) return;
    root.replaceChildren();
    const list = payload.subscriptions || [];
    if (!list.length) {
      const empty = element("div", "subs-empty panel");
      empty.append(element("strong", "", "尚无订阅"),
        element("p", "", "点击右上角“新建订阅”，设置每日发送的标的与时间。"));
      root.append(empty);
    } else for (const sub of list) root.append(listCard(sub));
  } catch (error) {
    if (token !== requestToken || selectedId) return;
    root.replaceChildren(element("p", "subs-loading error", `订阅读取失败：${error.message}`),
      action("重试", "subs-btn", () => loadList()));
  }
}

function runCard(run, id) {
  const card = element("section", "subs-run");
  card.dataset.runId = String(run.id);
  const head = element("div", "subs-run-head");
  head.append(element("strong", "subs-run-status", `${RUN_STATUS[run.status] || run.status} · ${run.trigger === "manual" ? "手动" : "定时"}`),
    element("span", "", timeLabel(run.queued_at)));
  const current = element("p", "subs-run-current");
  card.append(head, element("p", "subs-run-progress"), current);
  const detail = element("details", "subs-run-failures");
  detail.append(element("summary"));
  const detailsBody = element("div", "subs-run-detail");
  detail.append(detailsBody);
  card.append(detail);
  updateRunCard(card, run);
  detail.addEventListener("toggle", async () => {
    if (!detail.open || detail.dataset.loaded) return;
    try {
      const latest = await api.getSubscriptionRun(id, run.id);
      detail.dataset.loaded = "true";
      updateRunCard(card, latest);
    } catch (error) { notice(`运行详情读取失败：${error.message}`, true); }
  });
  return card;
}

function updateRunCard(card, run) {
  card.querySelector(".subs-run-status").textContent =
    `${RUN_STATUS[run.status] || run.status} · ${run.trigger === "manual" ? "手动" : "定时"}`;
  card.querySelector(".subs-run-progress").textContent =
    `已处理 ${run.processed || 0}/${run.total || 0} · 成功 ${run.ok || 0}`;
  const current = card.querySelector(".subs-run-current");
  current.hidden = !(run.current_symbol && ACTIVE.has(run.status));
  current.textContent = current.hidden ? "" : `正在处理：${run.current_symbol}`;
  const detail = card.querySelector(".subs-run-failures");
  detail.querySelector("summary").textContent =
    run.failures?.length ? `失败原因（${run.failures.length}）` : "运行详情";
  const body = card.querySelector(".subs-run-detail");
  body.replaceChildren(element("p", "", `开始：${timeLabel(run.started_at)} · 结束：${timeLabel(run.finished_at)}`));
  if (run.failures?.length) {
    const failures = element("ul");
    for (const failure of run.failures) failures.append(element("li", "", String(failure)));
    body.append(failures);
  }
}

function renderDetail(sub, runs) {
  const root = get("subsDetail");
  root.replaceChildren(action("← 返回订阅列表", "subs-back", () => {
    ++requestToken;
    selectedId = null;
    stopPoll();
    root.hidden = true;
    get("subsList").hidden = false;
    loadList();
  }));
  const panel = element("section", "subs-detail-card panel");
  const title = element("div", "subs-detail-title");
  title.append(element("h2", "", sub.name),
    element("span", `subs-state ${sub.enabled ? "enabled" : "paused"}`, sub.enabled ? "启用" : "暂停"));
  panel.append(title, element("p", "subs-detail-meta",
    `每日 ${sub.time}（北京时间） · 下次计划：${timeLabel(sub.next_run_at)}`));
  const symbols = element("div", "subs-detail-symbols");
  for (const item of sub.symbols || []) symbols.append(symbolChip(item));
  panel.append(symbols);
  const active = runs.find((run) => ACTIVE.has(run.status)) ||
    (ACTIVE.has(sub.last_run?.status) ? sub.last_run : null);
  const actions = element("div", "subs-detail-actions");
  const run = action("立即推送", "subs-btn primary", async () => {
    run.disabled = true;
    try {
      const result = await api.triggerSubscription(sub.id);
      notice(`推送已排队，运行编号 ${result.run_id}`);
      await openDetail(sub.id, true);
    } catch (error) {
      notice(error.status === 409 ? "该订阅已有推送任务正在运行。" : `推送失败：${error.message}`, true);
      run.disabled = false;
    }
  });
  run.id = "subsRunNow";
  run.disabled = Boolean(active);
  if (active) run.title = "该订阅已有推送任务正在运行";
  actions.append(run, action("编辑", "subs-btn", (event) => openSubscriptionForm(sub, event.currentTarget)),
    action(sub.enabled ? "暂停" : "启用", "subs-btn", async (event) => {
      const target = event.currentTarget;
      target.disabled = true;
      try {
        await api.updateSubscription(sub.id, { name: sub.name, symbols: sub.symbols,
          channel: "email", time: sub.time, enabled: !sub.enabled });
        notice(sub.enabled ? "已暂停未来的定时推送" : "已恢复定时推送");
        await openDetail(sub.id);
      } catch (error) { notice(`状态修改失败：${error.message}`, true); target.disabled = false; }
    }), action("删除", "subs-btn danger", async () => {
      if (!window.confirm(`确认删除订阅“${sub.name}”？`)) return;
      try {
        await api.deleteSubscription(sub.id);
        selectedId = null;
        stopPoll();
        root.hidden = true;
        get("subsList").hidden = false;
        notice("订阅已删除");
        await loadList();
      } catch (error) {
        notice(error.status === 409 ? "运行中的订阅不能删除。" : `删除失败：${error.message}`, true);
      }
    }));
  panel.append(actions);
  root.append(panel);
  const history = element("section", "subs-history");
  history.append(element("h2", "", "运行记录"));
  if (!runs.length) history.append(element("p", "subs-muted", "尚无推送记录。"));
  else for (const item of runs) history.append(runCard(item, sub.id));
  root.append(history);
  schedulePoll(active);
}

async function openDetail(id, quiet = false) {
  selectedId = id;
  const token = ++requestToken;
  get("subsList").hidden = true;
  const root = get("subsDetail");
  root.hidden = false;
  if (!quiet) root.replaceChildren(element("p", "subs-loading", "正在读取订阅详情…"));
  try {
    const [subscription, payload] = await Promise.all([api.getSubscription(id), api.listSubscriptionRuns(id)]);
    if (token !== requestToken || selectedId !== id || store.currentView !== "subscriptions") return;
    renderDetail(subscription.subscription || subscription, payload.runs || []);
  } catch (error) {
    if (token !== requestToken || selectedId !== id) return;
    root.replaceChildren(element("p", "subs-loading error", `订阅详情读取失败：${error.message}`),
      action("返回列表", "subs-btn", () => {
        selectedId = null;
        root.hidden = true;
        get("subsList").hidden = false;
        loadList();
      }));
    stopPoll();
  }
}

export function initSubscriptions() {
  if (initialized) return;
  initialized = true;
  get("subEmailBtn").addEventListener("click", (event) => openEmailSettings(event.currentTarget));
  get("subNewBtn").addEventListener("click", (event) => openSubscriptionForm(null, event.currentTarget));
  get("subsModalClose").addEventListener("click", closeModal);
  get("subsModal").querySelector("[data-close-modal]").addEventListener("click", closeModal);
  get("subsModal").addEventListener("keydown", (event) => {
    if (event.key === "Escape") { event.preventDefault(); closeModal(); return; }
    if (event.key !== "Tab") return;
    const focusable = [...get("subsModal").querySelectorAll("button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])")]
      .filter((item) => !item.hidden);
    if (!focusable.length) return;
    const first = focusable[0], last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  bus.addEventListener("view-change", (event) => {
    ++requestToken;
    if (event.detail.view !== "subscriptions") { stopPoll(); closeModal(); return; }
    api.getPushStatus().then((status) => {
      if (store.currentView === "subscriptions" && !status.enabled && get("subsNotice").hidden) {
        notice("自动推送已在全局配置中关闭；仍可手动推送和试发邮箱。");
      }
    }).catch((error) => console.debug("推送状态暂不可读取", error));
    if (selectedId) openDetail(selectedId);
    else loadList();
  });
  if (store.currentView === "subscriptions") loadList();
}
