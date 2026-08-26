// 配置管理视图：仅呈现受控配置 API 暴露的字段，密钥按需读取。
import { api } from "./api.js";
import { el, errorCard, skeleton } from "./components.js";
import { bus } from "./state.js";

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
const changed = new Set();
const originals = new Map();
const secretDisplays = new Map();
const revealedSecrets = new Map();
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
  state.value.textContent = visible ? value : (state.masked || "未配置");
  state.button.setAttribute("aria-label", visible ? "隐藏完整密钥" : "显示完整密钥");
  state.button.innerHTML = visible
    ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18M10.6 10.7a3 3 0 0 0 4.2 4.2M9.9 4.2A10.8 10.8 0 0 1 12 4c5.5 0 9.5 4.5 10 8-.2 1.3-1 3-2.3 4.4M6.2 6.2C3.9 7.8 2.4 10.2 2 12c.5 3.5 4.5 8 10 8 1.2 0 2.3-.2 3.3-.6"/></svg>'
    : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
}

function hideSecret(path) {
  revealedSecrets.delete(path);
  displaySecret(path);
}

function hideAllSecrets() {
  for (const path of revealedSecrets.keys()) hideSecret(path);
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
  row.appendChild(el("div", "settings-label", field.label));
  const control = el("div", "settings-control settings-secret-control");
  const current = el("span", "settings-secret-value", secret.masked || "未配置");
  const toggle = el("button", "settings-secret-toggle");
  toggle.type = "button";
  const replacement = document.createElement("input");
  replacement.id = inputId(field.path);
  replacement.type = "password";
  replacement.placeholder = "输入新值以覆盖";
  replacement.autocomplete = "new-password";
  originals.set(field.path, "");
  secretDisplays.set(field.path, { value: current, button: toggle, masked: secret.masked || "" });
  displaySecret(field.path);
  toggle.addEventListener("click", async () => {
    if (revealedSecrets.has(field.path)) {
      hideSecret(field.path);
      return;
    }
    try {
      const response = await api.getCredential(field.path);
      revealedSecrets.set(field.path, response.value);
      displaySecret(field.path);
    } catch (error) {
      showMessage(`无法读取完整密钥: ${error.message}`, "error");
    }
  });
  replacement.addEventListener("input", () => {
    hideSecret(field.path);
    if (replacement.value && replacement.value !== secret.masked) changed.add(field.path);
    else changed.delete(field.path);
  });
  control.appendChild(current);
  control.appendChild(toggle);
  control.appendChild(replacement);
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
  clearMessage();
  const update = collectUpdate();
  if (!Object.keys(update).length) {
    showMessage("没有需要保存的更改", "info");
    return;
  }
  try {
    const result = await api.updateConfig(update);
    const payload = await api.getConfig();
    renderSettings(payload);
    showMessage(
      result.restart_required
        ? "服务地址或端口已保存，重启 stock-robot run 后生效"
        : "配置已保存",
    );
  } catch (error) {
    showMessage(error.message, "error");
  }
}

export function renderSettings(payload) {
  const box = content();
  if (!box) return;
  revealedSecrets.clear();
  secretDisplays.clear();
  originals.clear();
  changed.clear();
  box.replaceChildren();
  const paths = el("div", "settings-paths");
  paths.appendChild(el("div", "settings-path", `项目状态目录：${payload.paths.state_dir}`));
  paths.appendChild(el("div", "settings-path", `配置文件：${payload.paths.config_file}`));
  box.appendChild(paths);
  const message = el("div", "settings-message");
  message.id = "settingsMessage";
  message.setAttribute("role", "status");
  message.hidden = true;
  box.appendChild(message);
  for (const section of SECTIONS) addSection(box, section, payload.config);
  const actions = el("div", "settings-actions");
  const save = el("button", "settings-save", "保存配置");
  save.id = "settingsSaveBtn";
  save.type = "button";
  save.addEventListener("click", saveSettings);
  actions.appendChild(save);
  box.appendChild(actions);
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
