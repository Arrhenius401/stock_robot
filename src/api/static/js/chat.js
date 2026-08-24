// 聊天视图：SSE 流式渲染、执行计划卡、工具结果卡、快捷按钮
import { store, bus } from "./state.js";
import { api } from "./api.js";
import { renderMarkdown } from "./markdown.js";
import { el } from "./components.js";
import { openReport } from "./report.js";
import { openReportDrawer, closeReportDrawer } from "./report-drawer.js";
import { renderReportSummary } from "./report-renderer.js";

const scrollEl = () => document.getElementById("chatScroll");

export function clearChatScroll() {
  scrollEl().innerHTML = "";
}

function appendBubble(role, node) {
  const wrap = el("div", `msg ${role}`);
  wrap.appendChild(node);
  scrollEl().appendChild(wrap);
  scrollEl().scrollTop = scrollEl().scrollHeight;
  return wrap;
}

function mdDiv(text) {
  const div = el("div", "md");
  div.innerHTML = renderMarkdown(text);
  return div;
}

function appendUser(text) {
  appendBubble("user", mdDiv(text));
}

const RESEARCH_SUGGESTIONS = [
  "分析 600519 贵州茅台的基本面与估值",
  "比较 000001 平安银行与 600036 招商银行",
  "大盘现在适合入场吗？",
];

function renderEmptyChat() {
  const empty = el("section", "chat-empty");
  empty.appendChild(el("div", "chat-empty-title", "从一个常用研究问题开始"));
  for (const prompt of RESEARCH_SUGGESTIONS) {
    const button = el("button", "research-suggestion", prompt);
    button.type = "button";
    button.addEventListener("click", () => {
      const input = document.getElementById("chatInput");
      input.value = prompt;
      input.focus();
    });
    empty.appendChild(button);
  }
  scrollEl().appendChild(empty);
}

function artifactMessageId(artifact) {
  return artifact?.message_id ?? artifact?.messageId ?? null;
}

function messageId(message) {
  return message?.message_id ?? message?.id ?? null;
}

function artifactId(artifact) {
  return artifact?.artifact_id ?? artifact?.id ?? null;
}

function cacheArtifact(sessionId, artifact, persisted = true) {
  if (!sessionId || !artifact) return null;
  const cached = { ...artifact, session_id: artifact.session_id ?? sessionId, persisted };
  const artifacts = store.sessionArtifacts[sessionId] || [];
  const id = artifactId(cached);
  const index = id == null ? -1 : artifacts.findIndex((item) => artifactId(item) === id);
  if (index >= 0) artifacts[index] = cached;
  else artifacts.push(cached);
  store.sessionArtifacts[sessionId] = artifacts;
  return cached;
}

function reportSummary(artifact) {
  const card = renderReportSummary(artifact);
  if (artifact?.persisted === false) {
    card.appendChild(el("div", "report-summary-persist-warning",
      "本轮报告可查看，但未保存到历史记录"));
  }
  const button = card.querySelector(".report-summary-open");
  if (button) {
    button.addEventListener("click", () => openReportDrawer(artifact, button));
  }
  return card;
}

function appendReportSummary(artifact, parent = null) {
  const card = reportSummary(artifact);
  if (parent) parent.appendChild(card);
  else appendBubble("agent", card);
  return card;
}

// 并发守卫：流进行中禁止再次发送（模块级，防同会话并发请求交错）
let sending = false;

// 返回 { card, stepEls }：计划状态随发送闭包持有，不落模块级变量，避免串会话
function planCard(evt) {
  const card = el("div", "card");
  card.appendChild(el("div", "card-title", "执行计划"));
  card.appendChild(el("div", "goal", `目标：${evt.goal || "执行任务"}`));
  const plan = { card, stepEls: [] };
  for (const desc of evt.steps || []) {
    const row = el("div", "step");
    const left = el("div", "step-left");
    const dot = el("span", "dot wait");
    left.appendChild(dot);
    left.appendChild(el("span", "step-desc", desc));
    const status = el("span", "step-status", "等待");
    row.appendChild(left);
    row.appendChild(status);
    card.appendChild(row);
    plan.stepEls.push({ dot, status });
  }
  return plan;
}

function setStep(step, state, text) {
  step.dot.className = `dot ${state}`;
  step.status.textContent = text;
  step.status.className = `step-status ${state}`;
}

function updatePlan(plan, evt) {
  if (!plan) return;
  const idx = (evt.current || 1) - 1;   // current 从 1 起，步骤索引 = current-1
  plan.stepEls.forEach((s, i) => {
    if (i < idx) {
      setStep(s, "done", "完成");
    } else if (i === idx && evt.stage !== "complete") {
      setStep(s, "run", "进行中");
    }
  });
  if (evt.stage === "complete") {
    plan.stepEls.forEach((s) => setStep(s, "done", "完成"));
  }
}

function parseToolMessage(content) {
  const m = /^\[([^\]]+)\]\s*(.*)$/s.exec(content || "");
  if (!m) return { tool: "tool", content: content || "" };
  return { tool: m[1], content: m[2] };
}

function toolResultCard(t) {
  const card = el("div", "card");
  card.appendChild(el("div", "card-title", "工具结果"));
  card.appendChild(el("span", "chip", t.tool));
  const body = el("div", "md tooltext");
  // 流式 result 事件的 content 为原始 memory 消息，含 "[工具名] " 前缀，
  // 剥离后与历史恢复路径（parseToolMessage 已剥）渲染一致
  body.innerHTML = renderMarkdown(String(t.content || "").replace(/^\[[^\]]+\]\s*/, ""));
  card.appendChild(body);
  return card;
}

function interruptedCard(retry) {
  const card = el("div", "error-card");
  card.appendChild(el("div", "error-title", "连接中断"));
  const btn = el("button", "btn-retry", "重试");
  btn.addEventListener("click", () => {
    card.remove();
    retry();
  });
  card.appendChild(btn);
  return card;
}

function isDuplicateReportTool(message, artifacts) {
  if (!artifacts.length) return false;
  const id = messageId(message);
  if (id != null && artifacts.some((artifact) => artifactMessageId(artifact) === id)) return true;
  const { tool } = parseToolMessage(message.content);
  return /(?:analy[sz]e_stock|stock_analy[sz]e|个股分析)/i.test(tool);
}

export function renderMessageHistory(messages, artifacts = []) {
  clearChatScroll();
  const linked = new Map();
  for (const artifact of artifacts) {
    const id = artifactMessageId(artifact);
    if (id == null) continue;
    const key = String(id);
    const values = linked.get(key) || [];
    values.push(artifact);
    linked.set(key, values);
  }
  const rendered = new Set();
  for (const m of messages) {
    if (m.role === "user") {
      appendUser(m.content);
    } else if (m.role === "tool") {
      if (!isDuplicateReportTool(m, artifacts)) {
        appendBubble("agent", toolResultCard(parseToolMessage(m.content)));
      }
    } else if (m.role === "assistant") {
      appendBubble("agent", mdDiv(m.content));
      const id = messageId(m);
      for (const artifact of id == null ? [] : (linked.get(String(id)) || [])) {
        appendReportSummary(artifact);
        rendered.add(artifact);
      }
    }
    // system 消息不展示
  }
  const remaining = artifacts.filter((artifact) => !rendered.has(artifact));
  if (remaining.length) {
    const section = el("section", "artifact-history-orphans");
    section.appendChild(el("div", "artifact-history-title", "研究成果"));
    for (const artifact of remaining) appendReportSummary(artifact, section);
    scrollEl().appendChild(section);
  }
  if (!messages.length && !artifacts.length) renderEmptyChat();
}

export async function sendMessage(text) {
  const msg = String(text || "").trim();
  if (!msg || sending) return;
  sending = true;
  try {
    // 无 Agent 调试模式下 session 为 null，后端流接口的 text 事件分支仍可用
    let streamSid = store.currentSessionId;
    if (streamSid) {
      (store.sessionMessages[streamSid] = store.sessionMessages[streamSid] || [])
        .push({ role: "user", content: msg });
    }
    appendUser(msg);
    const input = document.getElementById("chatInput");
    input.value = "";   // 发送时即清空，流结束不再清（避免吞掉期间新输入）
    const sendBtn = document.getElementById("sendBtn");
    const finish = () => { sendBtn.disabled = false; input.focus(); };
    sendBtn.disabled = true;
    // 计划状态随本次发送闭包持有，中途切换会话也不会串扰其他会话的计划卡
    let myPlan = null;
    // 自主循环工具卡片：按 run_id 映射（单条消息可并行多工具调用，
    // tool_result 需回落到发起调用的那张卡片）
    const myToolCards = {};

    const agentBox = appendBubble("agent", el("div", "content"));
    const thinking = el("div", "thinking", "正在分析…");
    agentBox.appendChild(thinking);

    const handlers = {
      session_title: (e) => {
        const sessionId = e.session_id || streamSid;
        if (!sessionId || sessionId !== streamSid) return;
        store.sessionDetails[sessionId] = {
          ...(store.sessionDetails[sessionId] || { session_id: sessionId }),
          title: e.title || "新会话",
          updated_at: Date.now() / 1000,
        };
        const event = new Event("session-title");
        event.sessionId = sessionId;
        event.title = e.title || "新会话";
        bus.dispatchEvent(event);
      },
      artifact: (e) => {
        const artifact = e.artifact;
        const sessionId = artifact?.session_id || streamSid;
        if (!artifact || !sessionId || sessionId !== streamSid) return;
        const cached = cacheArtifact(sessionId, artifact, e.persisted !== false);
        if (store.currentSessionId !== streamSid || !cached) return;
        appendReportSummary(cached, agentBox);
      },
      plan: (e) => {
        // 无会话发送时（正常模式冷启动兜底），采纳后端新建的 session。
        // 用户消息无条件写入（缓存与 result 的 assistant 写保持对称），
        // 仅 currentSessionId 接管以不劫持用户新选的会话为条件
        if (!streamSid && e.session_id) {
          streamSid = e.session_id;
          (store.sessionMessages[e.session_id] = store.sessionMessages[e.session_id] || [])
            .push({ role: "user", content: msg });
          if (!store.currentSessionId) {
            store.currentSessionId = e.session_id;
          }
        }
        // agent 模式 plan 事件步骤为空，占位保留供 thinking 片段追加；
        // 仅 plan 模式（有步骤）移除占位
        if ((e.steps || []).length) thinking.remove();
        myPlan = planCard(e);
        agentBox.appendChild(myPlan.card);
      },
      thinking: (e) => {
        // 模型推理片段追加到占位元素（后端已节流，仅非空片段）
        thinking.textContent += e.content || "";
      },
      tool_call: (e) => {
        if (store.currentSessionId !== streamSid) return;
        const card = toolResultCard({ tool: e.tool, content: "调用中…" });
        myToolCards[e.run_id || "default"] = card;
        agentBox.appendChild(card);
      },
      tool_result: (e) => {
        if (store.currentSessionId !== streamSid) return;
        const card = myToolCards[e.run_id || "default"];
        if (!card) return;
        const body = card.querySelector(".tooltext");
        body.innerHTML = renderMarkdown(String(e.content || ""));
        delete myToolCards[e.run_id || "default"];
      },
      progress: (e) => {
        // 会话已切换时跳过：气泡已脱离视图，且避免驱动新会话的计划卡。
        // 取舍：无 Agent 模式 streamSid 为 null，若期间其他流程把 currentSessionId
        // 置为非 null，进度更新会被跳过；但该模式后端不发 plan 事件、myPlan 恒为
        // null，此分支实际不会触发；即便触发，updatePlan 只改本气泡（已脱离视图）
        // 的节点，最多是极端角落下的进度停更——宁可丢进度也不污染新会话视图。
        if (!myPlan || store.currentSessionId !== streamSid) return;
        updatePlan(myPlan, e);
      },
      result: (e) => {
        thinking.remove();
        const isCurrent = store.currentSessionId === streamSid;
        if (myPlan) myPlan.stepEls.forEach((s) => setStep(s, "done", "完成"));
        if (e.summary && isCurrent) agentBox.appendChild(mdDiv(e.summary));
        for (const t of e.tool_results || []) {
          if (!isCurrent) continue;
          const card = toolResultCard({ tool: t.tool, content: t.content || "" });
          if (t.symbol && t.status === "done") {
            const link = el("span", "link", "查看完整报告 →");
            link.addEventListener("click", () => openReport(t.symbol));
            card.appendChild(link);
          }
          agentBox.appendChild(card);
        }
        if (streamSid) {
          (store.sessionMessages[streamSid] = store.sessionMessages[streamSid] || [])
            .push({ role: "assistant", content: e.summary || "" });
        }
        bus.dispatchEvent(new Event("chat-done"));
      },
      error: (e) => {
        thinking.remove();
        // chat-done 先派发：后端可能已建新会话，侧边栏需刷新；视图渲染才需守卫
        bus.dispatchEvent(new Event("chat-done"));
        if (store.currentSessionId !== streamSid) return;
        const card = el("div", "error-card");
        card.appendChild(el("div", "error-msg", e.message || "处理请求时出错"));
        agentBox.appendChild(card);
      },
      text: (e) => {
        thinking.remove();
        agentBox.appendChild(mdDiv(e.content));
        // agent 模式最终回答写入会话缓存并触发侧边栏刷新
        if (streamSid) {
          (store.sessionMessages[streamSid] = store.sessionMessages[streamSid] || [])
            .push({ role: "assistant", content: e.content || "" });
        }
        bus.dispatchEvent(new Event("chat-done"));
      },
      // done 事件清理占位（无 Agent 模式或异常路径兜底）
      done: () => {
        thinking.remove();
        finish();
      },
    };
    try {
      await api.chatStream(msg, streamSid, handlers);
    } catch (err) {
      thinking.remove();
      if (store.currentSessionId === streamSid) {
        agentBox.appendChild(interruptedCard(() => sendMessage(msg)));
      }
    }
    finish();
    input.focus();
  } finally {
    sending = false;
  }
}

async function showToolsPanel() {
  const agentBox = appendBubble("agent", el("div", "content"));
  const card = el("div", "card");
  card.appendChild(el("div", "card-title", "可用工具"));
  try {
    const data = await api.listTools();
    const tools = data.tools || [];
    if (!tools.length) {
      card.appendChild(el("div", "tooltext", "（无可用工具）"));
    } else {
      for (const t of tools) {
        const row = el("div", "tool-row");
        row.appendChild(el("span", "chip", t.name));
        row.appendChild(el("span", "tool-desc", t.description || ""));
        card.appendChild(row);
      }
    }
  } catch (err) {
    card.appendChild(el("div", "tooltext", `获取工具列表失败: ${err.message}`));
  }
  agentBox.appendChild(card);
}

export function initChat() {
  if (initChat.initialized) return;
  initChat.initialized = true;
  const input = document.getElementById("chatInput");
  const send = () => sendMessage(input.value);
  document.getElementById("sendBtn").addEventListener("click", send);
  let composing = false;
  input.addEventListener("compositionstart", () => { composing = true; });
  input.addEventListener("compositionend", () => { composing = false; });
  input.addEventListener("keydown", (e) => handleChatInputKeydown(e, send, composing));
  document.getElementById("quickTiming").addEventListener(
    "click", () => sendMessage("大盘现在适合入场吗？"));
  document.getElementById("quickTools").addEventListener("click", showToolsPanel);
  document.getElementById("quickClear").addEventListener("click", async () => {
    const target = store.currentSessionId;   // await 期间可能切换会话，先捕获
    if (!target) return;
    if (!window.confirm("清空当前会话的全部消息？")) return;
    try {
      await api.clearSession(target);
      store.sessionMessages[target] = [];  // 服务端已清空，本地缓存先同步
      store.sessionArtifacts[target] = [];
      if (store.currentSessionId !== target) return;  // 已切换，不动新会话视图
      closeReportDrawer();
      renderMessageHistory([], []);
      bus.dispatchEvent(new Event("chat-done"));
    } catch (err) {
      window.alert(`清空失败: ${err.message}`);
    }
  });
}

export function handleChatInputKeydown(event, send, composing = false) {
  if (event.key !== "Enter" || event.shiftKey) return;
  // 某些浏览器在 IME 提交期间仅暴露 keyCode=229，三重守卫避免误发送。
  if (composing || event.isComposing || event.keyCode === 229) return;
  event.preventDefault();
  send();
}
