// 配置管理视图：仅呈现受控配置 API 暴露的字段，密钥按需读取。
import { api } from "./api.js";
import { el, errorCard, skeleton } from "./components.js";
import { bus, store } from "./state.js";

const SECTIONS = [
  {
    title: "LLM 设置",
    fields: [
      { path: "llm.enabled", label: "启用 LLM", type: "checkbox" },
      { path: "llm.provider", label: "服务商", type: "select", options: [
        ["openai", "OpenAI"], ["claude", "Claude"],
      ] },
      { path: "llm.model", label: "模型", type: "text" },
      { path: "llm.api_key", label: "API Key", type: "secret" },
      { path: "llm.base_url", label: "Base URL", type: "text" },
      { path: "llm.temperature", label: "温度", type: "number", min: 0, max: 2, step: 0.1 },
      { path: "llm.max_tokens", label: "最大 Token 数", type: "number", min: 1, max: 128000, step: 1 },
      { path: "llm.retry_times", label: "重试次数", type: "number", min: 0, max: 10, step: 1 },
      { path: "llm.timeout_seconds", label: "超时秒数", type: "number", min: 1, max: 600, step: 1 },
    ],
  },
  {
    title: "数据与缓存",
    fields: [
      { path: "data.disclaimer_accepted", label: "已确认数据免责声明", type: "checkbox" },
      { path: "data.cache_ttl.daily", label: "日线缓存秒数", type: "number", min: 1, step: 1 },
      { path: "data.cache_ttl.quarterly", label: "季报缓存秒数", type: "number", min: 1, step: 1 },
      { path: "data.cache_ttl.news", label: "新闻缓存秒数", type: "number", min: 1, step: 1 },
    ],
  },
  {
    title: "服务设置",
    fields: [
      { path: "api.host", label: "监听地址", type: "text" },
      { path: "api.port", label: "监听端口", type: "number", min: 1, max: 65535, step: 1 },
    ],
  },
  {
    title: "推送设置",
    fields: [
      { path: "push.enabled", label: "启用推送", type: "checkbox" },
      { path: "push.max_symbols_per_subscription", label: "每个订阅最大标的数", type: "number", min: 1, step: 1 },
      { path: "push.email.smtp_host", label: "SMTP 主机", type: "text", group: "邮箱" },
      { path: "push.email.smtp_port", label: "SMTP 端口", type: "number", min: 1, step: 1, group: "邮箱" },
      { path: "push.email.smtp_user", label: "SMTP 用户名", type: "text", group: "邮箱" },
      { path: "push.email.smtp_password", label: "SMTP 密码", type: "secret", group: "邮箱" },
      { path: "push.email.to_addr", label: "收件人地址", type: "text", group: "邮箱" },
      { path: "push.wecom.corp_id", label: "企业 ID", type: "text", group: "企业微信" },
      { path: "push.wecom.agent_id", label: "应用 Agent ID", type: "text", group: "企业微信" },
      { path: "push.wecom.secret", label: "应用 Secret", type: "secret", group: "企业微信" },
      { path: "push.wecom.to_user", label: "接收用户", type: "text", group: "企业微信" },
    ],
  },
  {
    title: "信号策略",
    fields: [
      { path: "signal.thresholds.attack", label: "进攻阈值", type: "number", min: 0, max: 10, step: 0.1 },
      { path: "signal.thresholds.watch", label: "观察阈值", type: "number", min: 0, max: 10, step: 0.1 },
      { path: "signal.actions.attack.action", label: "进攻操作", type: "text", group: "进攻信号" },
      { path: "signal.actions.attack.position", label: "进攻仓位", type: "text", group: "进攻信号" },
      { path: "signal.actions.watch.action", label: "观察操作", type: "text", group: "观察信号" },
      { path: "signal.actions.watch.position", label: "观察仓位", type: "text", group: "观察信号" },
      { path: "signal.actions.defend.action", label: "防御操作", type: "text", group: "防御信号" },
      { path: "signal.actions.defend.position", label: "防御仓位", type: "text", group: "防御信号" },
    ],
  },
];

const fieldsByPath = new Map(SECTIONS.flatMap((section) => section.fields)
  .map((field) => [field.path, field]));
const secretPaths = [...fieldsByPath.values()]
  .filter((field) => field.type === "secret")
  .map((field) => field.path);
const changed = new Set();
const originals = new Map();
const secretDisplays = new Map();
const revealedSecrets = new Map();
const revealGenerations = new Map();
const pendingReveals = new Map();
let initialized = false;
let loaded = false;

function content() {
  return document.getElementById("settingsContent");
}

function getValue(data, path) {
  return path.split(".").reduce((value, key) => value?.[key], data);
}

function setValue(target, path, value) {
  const keys = path.split(".");
  let node = target;
  for (const key of keys.slice(0, -1)) node = node[key] ||= {};
  node[keys.at(-1)] = value;
}

function inputId(path) {
  return `settings-${path.replaceAll(".", "-")}`;
}

function displaySecret(path) {
  const state = secretDisplays.get(path);
  if (!state) return;
  const value = revealedSecrets.get(path);
  const visible = value !== undefined;
  state.input.type = visible ? "text" : "password";
  state.input.placeholder = visible ? "" : (state.masked || "未配置");
  state.button.setAttribute("aria-label", visible ? "隐藏完整密钥" : "显示完整密钥");
  state.button.innerHTML = visible
    ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18M10.6 10.7a3 3 0 0 0 4.2 4.2M9.9 4.2A10.8 10.8 0 0 1 12 4c5.5 0 9.5 4.5 10 8-.2 1.3-1 3-2.3 4.4M6.2 6.2C3.9 7.8 2.4 10.2 2 12c.5 3.5 4.5 8 10 8 1.2 0 2.3-.2 3.3-.6"/></svg>'
    : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
}

function invalidateSecret(path, clearRevealedInput = false) {
  const state = secretDisplays.get(path);
  if (clearRevealedInput && state && state.input.value === state.revealedValue) {
    state.input.value = "";
  }
  if (state) state.revealedValue = undefined;
  revealedSecrets.delete(path);
  revealGenerations.set(path, (revealGenerations.get(path) || 0) + 1);
  displaySecret(path);
}

function hideAllSecrets() {
  for (const path of secretPaths) invalidateSecret(path, true);
}

function isCurrentSettingsView(state) {
  const view = document.getElementById("view-settings");
  return store.currentView === "settings"
    && (!view || view.classList.contains("active"))
    && secretDisplays.get(state.path) === state;
}

function addLabeledField(container, field, value) {
  const row = el("label", "settings-field");
  row.appendChild(el("span", "settings-label", field.label));
  const control = el("div", "settings-control");
  const input = document.createElement("input");
  input.id = inputId(field.path);
  input.type = field.type === "number" ? "number" : field.type;
  if (field.type === "checkbox") {
    input.checked = Boolean(value);
    control.classList.add("settings-checkbox-control");
  } else {
    input.value = value ?? "";
  }
  if (field.min !== undefined) input.min = String(field.min);
  if (field.max !== undefined) input.max = String(field.max);
  if (field.step !== undefined) input.step = String(field.step);
  originals.set(field.path, field.type === "checkbox" ? Boolean(value) : String(value ?? ""));
  const markChanged = () => {
    const next = field.type === "checkbox" ? Boolean(input.checked) : input.value;
    if (next === originals.get(field.path)) changed.delete(field.path);
    else changed.add(field.path);
    refreshDirtyBar();
  };
  input.addEventListener("input", markChanged);
  input.addEventListener("change", markChanged);
  control.appendChild(input);
  row.appendChild(control);
  container.appendChild(row);
}

function addSelectField(container, field, value) {
  const row = el("label", "settings-field");
  row.appendChild(el("span", "settings-label", field.label));
  const select = document.createElement("select");
  select.id = inputId(field.path);
  for (const [optionValue, optionLabel] of field.options) {
    const option = document.createElement("option");
    option.value = optionValue;
    option.textContent = optionLabel;
    select.appendChild(option);
  }
  select.value = value;
  originals.set(field.path, String(value ?? ""));
  const markChanged = () => {
    if (select.value === originals.get(field.path)) changed.delete(field.path);
    else changed.add(field.path);
    refreshDirtyBar();
  };
  select.addEventListener("input", markChanged);
  select.addEventListener("change", markChanged);
  const control = el("div", "settings-control");
  control.appendChild(select);
  row.appendChild(control);
  container.appendChild(row);
}

function addSecretField(container, field, secret) {
  const row = el("div", "settings-field settings-secret-field");
  const label = el("div", "settings-label", field.label);
  label.id = `${inputId(field.path)}-label`;
  row.appendChild(label);
  const control = el("div", "settings-control settings-secret-control");
  const toggle = el("button", "settings-secret-toggle");
  toggle.type = "button";
  const input = document.createElement("input");
  input.id = inputId(field.path);
  input.type = "password";
  input.autocomplete = "new-password";
  input.setAttribute("aria-labelledby", label.id);
  originals.set(field.path, "");
  const state = {
    path: field.path,
    input,
    button: toggle,
    masked: secret.masked || "",
    revealedValue: undefined,
  };
  secretDisplays.set(field.path, state);
  displaySecret(field.path);
  toggle.addEventListener("click", async () => {
    if (revealedSecrets.has(field.path)) {
      invalidateSecret(field.path, true);
      return;
    }
    if (pendingReveals.has(field.path)) {
      invalidateSecret(field.path, true);
      return;
    }
    const generation = revealGenerations.get(field.path) || 0;
    const request = api.getCredential(field.path);
    pendingReveals.set(field.path, request);
    toggle.setAttribute("aria-label", "隐藏完整密钥");
    try {
      const response = await request;
      if (pendingReveals.get(field.path) !== request
          || revealGenerations.get(field.path) !== generation
          || !isCurrentSettingsView(state)) return;
      revealedSecrets.set(field.path, response.value);
      state.revealedValue = response.value;
      input.value = response.value;
      displaySecret(field.path);
    } catch (error) {
      if (pendingReveals.get(field.path) === request
          && revealGenerations.get(field.path) === generation
          && isCurrentSettingsView(state)) {
        showMessage(`无法读取完整密钥: ${error.message}`, "error");
      }
    } finally {
      if (pendingReveals.get(field.path) === request) pendingReveals.delete(field.path);
      if (secretDisplays.get(field.path) === state) toggle.disabled = false;
    }
  });
  input.addEventListener("input", () => {
    invalidateSecret(field.path);
    if (input.value && input.value !== secret.masked) changed.add(field.path);
    else changed.delete(field.path);
    refreshDirtyBar();
  });
  control.appendChild(input);
  control.appendChild(toggle);
  row.appendChild(control);
  container.appendChild(row);
}

function addSection(container, section, config) {
  const panel = el("section", "panel settings-section");
  panel.appendChild(el("h2", "settings-section-title", section.title));
  const grid = el("div", "settings-grid");
  let activeGroup = "";
  for (const field of section.fields) {
    if (field.group && field.group !== activeGroup) {
      activeGroup = field.group;
      const heading = el("div", "settings-group-title", activeGroup);
      heading.style.gridColumn = "1 / -1";
      grid.appendChild(heading);
    }
    const value = getValue(config, field.path);
    if (field.type === "secret") addSecretField(grid, field, value || {});
    else if (field.type === "select") addSelectField(grid, field, value);
    else addLabeledField(grid, field, value);
  }
  panel.appendChild(grid);
  container.appendChild(panel);
}

function messageBox() {
  return document.getElementById("settingsMessage");
}

function showMessage(text, kind = "success") {
  const box = messageBox();
  if (!box) return;
  box.textContent = text;
  box.className = `settings-message ${kind}`;
  box.hidden = false;
}

function clearMessage() {
  const box = messageBox();
  if (box) {
    box.textContent = "";
    box.hidden = true;
  }
}

function refreshDirtyBar() {
  const bar = document.getElementById("settingsDirtyBar");
  if (!bar) return;
  const count = changed.size;
  bar.hidden = count === 0;
  bar.querySelector("[data-role=message]").textContent = `有 ${count} 项配置尚未保存`;
}

function restoreSaveButton() {
  const saveBtn = document.getElementById("settingsSaveBtn");
  if (!saveBtn) return;
  saveBtn.disabled = false;
  saveBtn.textContent = "保存并应用";
}

function collectUpdate() {
  const update = {};
  for (const path of changed) {
    const field = fieldsByPath.get(path);
    const input = document.getElementById(inputId(path));
    if (!field || !input) continue;
    if (field.type === "secret"
        && (!input.value || input.value === secretDisplays.get(path)?.masked)) continue;
    let value;
    if (field.type === "checkbox") value = Boolean(input.checked);
    else if (field.type === "number") value = Number(input.value);
    else value = input.value;
    setValue(update, path, value);
  }
  return update;
}

async function saveSettings() {
  const saveBtn = document.getElementById("settingsSaveBtn");
  if (saveBtn) {
    saveBtn.disabled = true;
    saveBtn.textContent = "正在保存…";
  }
  clearMessage();
  const update = collectUpdate();
  if (!Object.keys(update).length) {
    restoreSaveButton();
    showMessage("没有需要保存的更改", "info");
    return;
  }
  try {
    const result = await api.updateConfig(update);
    if (result.persisted && result.applied) {
      // 热更新已应用：重新读取并重绘，草稿已落盘
      const payload = await api.getConfig();
      renderSettings(payload);
      showMessage("配置已保存并应用", "success");
    } else if (result.persisted && result.restart_required) {
      // 监听地址/端口需重启生效，其余字段已落盘
      const payload = await api.getConfig();
      renderSettings(payload);
      showMessage("配置已保存；监听地址或端口在重启 stock-robot run 后生效", "success");
    } else {
      // 已落盘但运行时未应用（applied=false 且无需重启）：保留草稿，展示可读错误
      restoreSaveButton();
      showMessage(result.reload_error || "配置已保存但运行时应用失败", "error");
    }
  } catch (error) {
    // 网络/422 校验失败：保留草稿与输入，仅展示错误
    restoreSaveButton();
    showMessage(error.message, "error");
  }
}

export function renderSettings(payload) {
  const box = content();
  if (!box) return;
  hideAllSecrets();
  secretDisplays.clear();
  originals.clear();
  changed.clear();
  box.replaceChildren();
  const paths = el("div", "settings-paths");
  paths.appendChild(el("div", "settings-path", `项目状态目录：${payload.paths.state_dir}`));
  paths.appendChild(el("div", "settings-path", `配置文件：${payload.paths.config_file}`));
  box.appendChild(paths);
  const dirtyBar = el("div", "settings-dirty-bar");
  dirtyBar.id = "settingsDirtyBar";
  dirtyBar.hidden = true;
  const dirtyMessage = el("span", "settings-dirty-message", "有 0 项配置尚未保存");
  dirtyMessage.setAttribute("role", "status");
  dirtyMessage.dataset.role = "message";
  const save = el("button", "settings-save", "保存并应用");
  save.id = "settingsSaveBtn";
  save.type = "button";
  save.addEventListener("click", saveSettings);
  dirtyBar.appendChild(dirtyMessage);
  dirtyBar.appendChild(save);
  box.appendChild(dirtyBar);
  const message = el("div", "settings-message");
  message.id = "settingsMessage";
  message.setAttribute("role", "status");
  message.hidden = true;
  box.appendChild(message);
  for (const section of SECTIONS) addSection(box, section, payload.config);
}

async function loadSettings() {
  const box = content();
  if (!box) return;
  box.replaceChildren(skeleton(5));
  try {
    const payload = await api.getConfig();
    renderSettings(payload);
    loaded = true;
  } catch (error) {
    box.replaceChildren(errorCard("无法加载配置，请检查服务连接后重试", () => {
      loadSettings();
    }));
  }
}

export function initSettings() {
  if (initialized) return;
  initialized = true;
  bus.addEventListener("view-change", (event) => {
    if (event.detail.view === "settings") {
      if (!loaded) loadSettings();
    } else {
      hideAllSecrets();
    }
  });
}
