// 配置管理视图：仅呈现受控配置 API 暴露的字段，密钥按需读取。
import { api } from "./api.js";
import { mountCollectorSettings } from "./collector-settings.js?v=20261002-settings-1";
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

SECTIONS.push({ title: "自动采集计划", fields: [
  { path: "radar.collector.enabled", label: "启用自动采集", type: "checkbox" },
  { path: "radar.collector.hour", label: "采集时间 · 时（北京时间）", type: "number", min: 0, max: 23, step: 1 },
  { path: "radar.collector.minute", label: "采集时间 · 分", type: "number", min: 0, max: 59, step: 1 },
] });
const MODULES = [
  { title: "AI 模型", icon: "✦", description: "配置模型连接与生成参数。", tabs: ["模型连接", "生成与重试"] },
  { title: "分析与数据", icon: "◈", description: "配置分析信号与数据缓存。", tabs: ["信号策略", "数据缓存"] },
  { title: "订阅推送", icon: "↗", description: "配置订阅推送与邮件发送。", tabs: ["通用", "邮箱"] },
  { title: "运行与采集", icon: "⚙", description: "配置雷达采集计划与服务运行。", tabs: ["雷达采集", "服务设置"] },
];
let activeModule = 0;
const activeTabs = [0, 0, 0, 0];
const EDIT_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true"><path d="M7 3H5v6c0 2-2 3-3 3 1 0 3 1 3 3v6h2M17 3h2v6c0 2 2 3 3 3-1 0-3 1-3 3v6h-2"/><path d="M8 12h1m2 0h2m2 0h1"/></svg>';
const SAVE_ICON = '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M5 2h12l5 5v13a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h1zm2 2v6h10V4H7zm5 10a3 3 0 1 0 0 6 3 3 0 0 0 0-6z"/></svg>';
let revision;
let fileDraft = null;
let editor = null;
let editorInitial = "";
let saving = false;
let currentPaths = {};

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
let collectorController = null;

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

const FIELD_DESCRIPTIONS = {
  "llm.enabled": "开启后使用模型生成自然语言解读。",
  "llm.provider": "选择模型服务提供方。",
  "llm.model": "配置服务商提供的模型名称。",
  "llm.api_key": "模型服务访问凭据。",
  "llm.base_url": "留空使用服务商默认地址。",
  "llm.temperature": "较低数值使输出更稳定。",
  "llm.max_tokens": "单次生成的长度上限。",
  "llm.retry_times": "请求失败后允许重试的次数。",
  "llm.timeout_seconds": "单次请求的等待上限。",
  "radar.collector.enabled": "关闭后仍可手动采集。",
  "radar.collector.hour": "北京时间，24 小时制。",
  "radar.collector.minute": "每小时的计划分钟。",
};

function fieldLabel(field) {
  const label = el("span", "settings-label", field.label);
  const description = FIELD_DESCRIPTIONS[field.path];
  if (description) label.appendChild(el("small", "settings-field-description", description));
  return label;
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
  state.input.placeholder = visible ? "" : (state.configured ? "************" : "未配置");
  state.button.setAttribute("aria-label", visible ? "隐藏完整密钥" : "显示完整密钥");
  state.button.innerHTML = visible
    ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18M10.6 10.7a3 3 0 0 0 4.2 4.2M9.9 4.2A10.8 10.8 0 0 1 12 4c5.5 0 9.5 4.5 10 8-.2 1.3-1 3-2.3 4.4M6.2 6.2C3.9 7.8 2.4 10.2 2 12c.5 3.5 4.5 8 10 8 1.2 0 2.3-.2 3.3-.6"/></svg>'
    : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
}

function invalidateSecret(path, clearRevealedInput = false) {
  const state = secretDisplays.get(path);
  if (clearRevealedInput && state && !changed.has(path) && state.input.value === state.revealedValue) {
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
  row.appendChild(fieldLabel(field));
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
  row.appendChild(fieldLabel(field));
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
  const label = fieldLabel(field);
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
    configured: Boolean(secret.configured),
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
    if (changed.has(field.path)) {
      revealedSecrets.set(field.path, input.value);
      state.revealedValue = input.value;
      displaySecret(field.path);
      return;
    }
    if (fileDraft !== null) {
      showMessage("文件草稿中的密钥请在配置文件编辑器查看", "info");
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
  panel.appendChild(el("h3", "settings-section-title", section.title));
  if (section.description) panel.appendChild(el("p", "settings-section-description", section.description));
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

function addCollectorAutostart(container) {
  collectorController = mountCollectorSettings(container);
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
  const count = changed.size + (fileDraft !== null ? 1 : 0);
  bar.hidden = count === 0;
  bar.querySelector("[data-role=message]").textContent = `有 ${count} 项配置尚未保存`;
}

function restoreSaveButton() {
  const saveBtn = document.getElementById("settingsSaveBtn");
  if (!saveBtn) return;
  saveBtn.disabled = false;
  saveBtn.innerHTML = SAVE_ICON;
  saveBtn.appendChild(el("span", "settings-action-label", "保存并应用"));
  saveBtn.setAttribute("aria-label", "保存并应用");
  saveBtn.setAttribute("aria-busy", "false");
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

function setSaving(value) {
  saving = value;
  for (const path of fieldsByPath.keys()) {
    const input = document.getElementById(inputId(path));
    if (input) input.disabled = value;
  }
  for (const state of secretDisplays.values()) state.button.disabled = value;
  for (const selector of [".settings-module-button", ".settings-tab-button"]) {
    content()?.querySelectorAll(selector).forEach(button => { button.disabled = value; });
  }
  const edit = document.getElementById("settingsEditFileBtn");
  if (edit) edit.disabled = value;
}

async function saveSettings() {
  if (saving) return;
  const saveBtn = document.getElementById("settingsSaveBtn");
  if (saveBtn) {
    saveBtn.disabled = true;
    saveBtn.setAttribute("aria-label", "正在保存…");
    saveBtn.setAttribute("aria-busy", "true");
  }
  clearMessage();
  const update = collectUpdate();
  if (!Object.keys(update).length && fileDraft === null) {
    restoreSaveButton();
    showMessage("没有需要保存的更改", "info");
    return;
  }
  setSaving(true);
  try {
    const result = fileDraft !== null
      ? await api.updateConfigFile(fileDraft, revision, update)
      : await api.updateConfig(update, revision);
    if (result.revision !== undefined) revision = result.revision;
    if (result.applied || (result.persisted && !result.reload_error && !result.restart_required)) {
      // 热更新已应用：重新读取并重绘，草稿已落盘；同时含监听字段时附加重启提示
      const payload = await api.getConfig();
      fileDraft = null;
      renderSettings(payload);
      showMessage(
        result.restart_required
          ? "配置已保存并应用；监听地址或端口在重启 stock-robot run 后生效"
          : "配置已保存并应用",
        "success",
      );
    } else if (result.restart_required && !result.reload_error) {
      // 仅监听地址/端口变更：已落盘，重启后生效
      const payload = await api.getConfig();
      fileDraft = null;
      fileDraft = null;
      renderSettings(payload);
      showMessage("配置已保存；监听地址或端口在重启 stock-robot run 后生效", "success");
    } else {
      // 已落盘但热更新未应用（含监听字段与热字段混合提交失败）：保留草稿，展示可读错误
      restoreSaveButton();
      const restartNote = result.restart_required ? "监听地址或端口已保存，重启后生效；" : "";
      showMessage(restartNote + (result.reload_error || "配置已保存但运行时应用失败"), "error");
    }
  } catch (error) {
    // 网络/422 校验失败：保留草稿与输入，仅展示错误
    restoreSaveButton();
    showMessage(error.message, "error");
  } finally {
    setSaving(false);
    restoreSaveButton();
  }
}

export function renderSettings(payload) {
  revision = payload.revision ?? revision;
  currentPaths = payload.paths || currentPaths;
  const box = content();
  if (!box) return;
  hideAllSecrets();
  secretDisplays.clear();
  originals.clear();
  changed.clear();
  collectorController?.dispose();
  collectorController = null;
  box.replaceChildren();
  const paths = el("div", "settings-paths");
  paths.appendChild(el("div", "settings-path", `项目状态目录：${payload.paths.state_dir}`));
  paths.appendChild(el("div", "settings-path", `配置文件：${payload.paths.config_file}`));

  const dirtyBar = el("div", "settings-dirty-bar");
  dirtyBar.id = "settingsDirtyBar";
  dirtyBar.hidden = true;
  const dirtyMessage = el("span", "settings-dirty-message", "有 0 项配置尚未保存");
  dirtyMessage.setAttribute("role", "status");
  dirtyMessage.dataset.role = "message";
  const save = el("button", "settings-save");
  save.id = "settingsSaveBtn";
  save.type = "button";
  save.title = "保存并应用";
  save.innerHTML = SAVE_ICON;
  save.appendChild(el("span", "settings-action-label", "保存并应用"));
  save.setAttribute("aria-label", "保存并应用");
  save.addEventListener("click", saveSettings);
  dirtyBar.appendChild(dirtyMessage);
  const actions = el("aside", "settings-actions");
  const edit = el("button", "btn-sm");
  edit.id = "settingsEditFileBtn";
  edit.type = "button";
  edit.title = "编辑配置文件";
  edit.setAttribute("aria-label", "编辑配置文件");
  edit.innerHTML = EDIT_ICON;
  edit.appendChild(el("span", "settings-action-label", "编辑配置文件"));
  edit.addEventListener("click", openFileEditor);
  actions.appendChild(edit);
  actions.appendChild(save);
  box.appendChild(dirtyBar);
  const message = el("div", "settings-message");
  message.id = "settingsMessage";
  message.setAttribute("role", "status");
  message.hidden = true;
  box.appendChild(message);
  const layout = el("div", "settings-layout");
  const nav = el("nav", "settings-module-nav");
  nav.setAttribute("aria-label", "配置模块");
  const body = el("div", "settings-module-body");
  const panels = [], buttons = [];
  function selectModule(index) {
    activeModule = index;
    panels.forEach((panel, i) => { panel.hidden = i !== index; });
    buttons.forEach((button, i) => {
      button.classList.toggle("active", i === index);
      button.setAttribute("aria-current", i === index ? "page" : "false");
    });
    hideAllSecrets();
    if (index === 3 && activeTabs[3] === 0) collectorController?.start();
    else collectorController?.pause();
  }
  MODULES.forEach((module, index) => {
    const button = el("button", "settings-module-button");
    const icon = el("span", "settings-module-icon", module.icon);
    icon.setAttribute("aria-hidden", "true");
    button.appendChild(icon);
    button.appendChild(el("span", "", module.title));
    button.type = "button";
    button.addEventListener("click", () => selectModule(index));
    buttons.push(button); nav.appendChild(button);
    const panel = el("div", "settings-module");
    panel.appendChild(el("h2", "settings-module-title", module.title));
    panel.appendChild(el("p", "settings-module-description", module.description));
    const tabs = el("div", "settings-tabs");
    tabs.setAttribute("role", "tablist");
    tabs.setAttribute("aria-label", module.title);
    panel.appendChild(tabs);
    const tabPanels = [], tabButtons = [];
    const selectTab = (tabIndex) => {
      activeTabs[index] = tabIndex;
      tabPanels.forEach((item, i) => { item.hidden = i !== tabIndex; });
      tabButtons.forEach((item, i) => {
        item.classList.toggle("active", i === tabIndex);
        item.setAttribute("aria-selected", String(i === tabIndex));
        item.tabIndex = i === tabIndex ? 0 : -1;
      });
      hideAllSecrets();
      if (index === 3) {
        if (activeModule === 3 && tabIndex === 0) collectorController?.start();
        else collectorController?.pause();
      }
    };
    module.tabs.forEach((title, tabIndex) => {
      const tab = el("button", "settings-tab-button", title);
      tab.type = "button";
      tab.id = `settings-tab-${index}-${tabIndex}`;
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-controls", `settings-tab-panel-${index}-${tabIndex}`);
      tab.addEventListener("click", () => selectTab(tabIndex));
      tab.addEventListener("keydown", (event) => {
        if (saving || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === "Home" ? 0 : event.key === "End" ? module.tabs.length - 1
          : (tabIndex + (event.key === "ArrowRight" ? 1 : -1) + module.tabs.length) % module.tabs.length;
        selectTab(next);
        tabButtons[next].focus();
      });
      tabs.appendChild(tab); tabButtons.push(tab);
      const group = el("div", "settings-tab-panel");
      group.id = `settings-tab-panel-${index}-${tabIndex}`;
      group.setAttribute("role", "tabpanel");
      group.setAttribute("aria-labelledby", tab.id);
      let section;
      if (index === 0) section = { title,
        description: tabIndex === 0 ? "配置用于研报解读与会话研究的模型。" : "控制生成长度与请求重试策略。",
        fields: tabIndex === 0 ? SECTIONS[0].fields.slice(0, 5) : SECTIONS[0].fields.slice(5) };
      else if (index === 1) section = SECTIONS[tabIndex === 0 ? 4 : 1];
      else if (index === 2) section = { title: tabIndex === 0 ? "推送设置" : "邮箱",
        fields: SECTIONS[3].fields.filter(field => tabIndex === 0 ? !field.group : field.group) };
      else section = SECTIONS[tabIndex === 0 ? 5 : 2];
      addSection(group, section, payload.config);
      if (index === 3 && tabIndex === 0) addCollectorAutostart(group);
      if (index === 3 && tabIndex === 1) group.appendChild(paths);
      tabPanels.push(group); panel.appendChild(group);
    });
    selectTab(activeTabs[index]);
    panels.push(panel); body.appendChild(panel);
  });
  layout.appendChild(nav); layout.appendChild(body); layout.appendChild(actions);
  box.appendChild(layout);
  selectModule(activeModule);
  refreshDirtyBar();
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
  window.addEventListener("beforeunload", (event) => {
    const editorDirty = editor && document.getElementById("settingsFileSource")?.value !== editorInitial;
    if (!changed.size && fileDraft === null && !editorDirty) return;
    event.preventDefault(); event.returnValue = "";
  });
  bus.addEventListener("before-view-change", (event) => {
    const editorDirty = editor && document.getElementById("settingsFileSource")?.value !== editorInitial;
    const notice = editorDirty
      ? "编辑器内容尚未应用，离开将放弃这些修改；表单草稿会保留。确定离开？"
      : "配置尚未保存，确定离开？草稿会保留。";
    if (store.currentView === "settings" && event.detail.view !== "settings"
        && (changed.size || fileDraft !== null || editorDirty)
        && !window.confirm(notice)) event.preventDefault();
  });
  bus.addEventListener("view-change", (event) => {
    if (event.detail.view === "settings") {
      if (!loaded) loadSettings();
      else if (activeModule === 3 && activeTabs[3] === 0) collectorController?.start();
    } else {
      collectorController?.pause();
      editor?.close();
      hideAllSecrets();
    }
  });
}

export function invalidateSettings() {
  loaded = false;
}

// 完整文件始终由受保护的文件接口读取，不从遮罩表单重建。
async function openFileEditor() {
  if (saving || editor) return;
  const trigger = document.getElementById("settingsEditFileBtn");
  setSaving(true);
  try {
    const file = fileDraft === null ? await api.getConfigFile() : { source: fileDraft, revision };
    if (store.currentView !== "settings") return;
    if (fileDraft === null && revision !== undefined && file.revision !== revision) {
      throw new Error("配置已被其他页面修改，请重新加载后编辑；当前草稿已保留");
    }
    const preview = await api.previewConfigFile(file.source, collectUpdate());
    if (store.currentView !== "settings") return;
    const initial = preview.source;
    editorInitial = initial;
    const node = el("dialog", "settings-file-dialog");
    editor = node;
    node.setAttribute("aria-label", "编辑完整配置文件");
    const head = el("div", "settings-file-head");
    const restore = el("button", "btn-sm", "恢复打开时内容");
    const apply = el("button", "settings-save", "应用到表单");
    const close = el("button", "btn-sm", "关闭");
    close.classList.add("settings-file-close");
    close.textContent = "×";
    close.setAttribute("aria-label", "关闭编辑器");
    close.type = "button";
    head.appendChild(close);
    head.appendChild(el("h2", "", "编辑配置文件"));
    for (const button of [restore, apply]) { button.type = "button"; head.appendChild(button); }
    node.appendChild(head);
    node.appendChild(el("p", "settings-file-note", "完整 YAML 可能包含明文密钥，仅本机可编辑。应用只更新草稿，保存后生效；合并表单修改可能重排格式与注释。"));
    const area = el("div", "settings-code-area");
    const lines = el("pre", "settings-code-lines");
    const code = el("div", "settings-code-body");
    const highlight = el("pre", "settings-code-highlight");
    highlight.setAttribute("aria-hidden", "true");
    const input = el("textarea", "settings-code-input");
    input.id = "settingsFileSource"; input.spellcheck = false; input.wrap = "off";
    input.setAttribute("aria-label", "完整 YAML 配置");
    input.value = initial;
    code.appendChild(highlight); code.appendChild(input);
    area.appendChild(lines); area.appendChild(code); node.appendChild(area);
    const errorBox = el("p", "settings-file-error");
    errorBox.setAttribute("role", "alert"); node.appendChild(errorBox);
    const syncScroll = () => {
      highlight.scrollTop = input.scrollTop; highlight.scrollLeft = input.scrollLeft; lines.scrollTop = input.scrollTop;
    };
    const resetPosition = () => {
      input.setSelectionRange?.(0, 0);
      input.scrollTop = 0; input.scrollLeft = 0; syncScroll();
    };
    const paint = () => {
      lines.textContent = input.value.split("\n").map((_, i) => i + 1).join("\n");
      highlight.replaceChildren();
      for (const line of input.value.split("\n")) {
        const row = el("span", "settings-code-line");
        const match = line.match(/^(\s*[\w.-]+:)(.*)$/);
        if (match) { row.appendChild(el("span", "yaml-key", match[1])); row.appendChild(el("span", "yaml-value", match[2])); }
        else row.textContent = line;
        if (line.trimStart().startsWith("#")) row.classList.add("yaml-comment");
        highlight.appendChild(row);
      }
      syncScroll();
    };
    input.addEventListener("input", paint);
    input.addEventListener("scroll", syncScroll);
    restore.onclick = () => { input.value = initial; errorBox.textContent = ""; paint(); input.focus(); resetPosition(); };
    close.onclick = () => {
      if (input.value !== initial && !window.confirm("编辑器内容尚未应用，确定关闭并放弃这些修改？")) return;
      node.close();
    };
    node.addEventListener("cancel", (event) => {
      if (input.value !== initial && !window.confirm("编辑器内容尚未应用，确定关闭并放弃这些修改？")) event.preventDefault();
    });
    node.addEventListener("close", () => { input.value = ""; node.remove(); editor = null; trigger.focus(); });
    apply.onclick = async () => {
      apply.disabled = true; input.disabled = true; restore.disabled = true; errorBox.textContent = "";
      try {
        const result = await api.previewConfigFile(input.value);
        if (editor !== node || store.currentView !== "settings") return;
        fileDraft = result.source;
        renderSettings({ config: result.config, paths: preview.paths || file.paths || currentPaths, revision });
        node.close(); showMessage("文件已应用到表单草稿，请点击保存并应用", "info");
      } catch (error) {
        if (editor !== node || store.currentView !== "settings") return;
        errorBox.textContent = error.message;
        const line = Number(error.detail?.line || error.message.match(/(?:行|line)\s*[:：]?\s*(\d+)/i)?.[1]);
        if (line > 0) {
          const offset = input.value.split("\n").slice(0, line - 1).reduce((n, item) => n + item.length + 1, 0);
          input.disabled = false;
          input.focus(); input.setSelectionRange(offset, offset + (input.value.split("\n")[line - 1]?.length || 0));
          input.scrollTop = (line - 1) * 22; input.dispatchEvent(new Event("scroll"));
        }
      } finally { apply.disabled = false; input.disabled = false; restore.disabled = false; }
    };
    document.body.appendChild(node); paint(); node.showModal(); input.focus(); resetPosition();
  } catch (error) { showMessage(error.message, "error"); }
  finally { setSaving(false); }
}
