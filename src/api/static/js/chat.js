// 聊天视图：SSE 流式渲染、执行计划卡、工具结果卡、快捷按钮
import {
  store, bus, invalidateSessionDetail, markSessionListMutation, reviveSession,
  invalidateSessionRuns, sessionRunEpoch, markSessionDetailStale,
} from "./state.js";
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

function thinkingDetails(text) {
  if (!text) return null;
  const details = el("details", "message-thinking");
  details.appendChild(el("summary", "", "查看分析过程"));
  details.appendChild(mdDiv(text));
  return details;
}

function assistantMessage(content, thinking = "") {
  const node = el("div", "assistant-message");
  node.appendChild(mdDiv(content));
  const details = thinkingDetails(thinking);
  if (details) node.appendChild(details);
  return node;
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

function syncSuggestionVisibility(messages) {
  const empty = scrollEl().querySelector(".chat-empty");
  if (empty) empty.hidden = messages.some((message) => message.role === "user");
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

// 并发守卫绑定到具体 run；clear/delete 可释放旧 run，且旧 finally 不会释放新 run。
let activeSendingRun = null;
let localRunSequence = 0;

function releaseSending(run, focus = false) {
  if (activeSendingRun !== run) return;
  activeSendingRun = null;
  const sendBtn = document.getElementById("sendBtn");
  const input = document.getElementById("chatInput");
  if (sendBtn) sendBtn.disabled = false;
  if (focus && input) input.focus();
}

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

function toolHasArtifact(tool, artifacts) {
  const id = messageId(tool);
  return id != null && artifacts.some(
    (artifact) => String(artifactMessageId(artifact)) === String(id),
  );
}

function runIsValid(run) {
  if (run.cancelled || !run.sessionId) return false;
  return !store.sessionTombstones[run.sessionId]
    && run.epoch === sessionRunEpoch(run.sessionId);
}

function removeRun(run) {
  run.cancelled = true;
  run.done = true;
  run.mount?.remove();
  if (run.sessionId) {
    store.sessionRuns[run.sessionId] = (store.sessionRuns[run.sessionId] || [])
      .filter((item) => item !== run);
  }
}

export function cancelSessionRuns(sessionId) {
  invalidateSessionRuns(sessionId);
  for (const run of store.sessionRuns[sessionId] || []) {
    releaseSending(run);
    removeRun(run);
  }
  store.sessionRuns[sessionId] = [];
}

function buildRunBubble(run) {
  const wrap = el("div", "msg agent");
  wrap.dataset.runId = run.id;
  const content = el("div", "content");
  wrap.appendChild(content);

  if (run.plan) {
    const renderedPlan = planCard(run.plan);
    if (run.progress) updatePlan(renderedPlan, run.progress);
    if (run.answers.length) {
      renderedPlan.stepEls.forEach((step) => setStep(step, "done", "完成"));
    }
    content.appendChild(renderedPlan.card);
  }
  const details = thinkingDetails(run.thinking === "正在分析…" ? "" : run.thinking);
  if (details) content.appendChild(details);
  for (const answer of run.answers) content.appendChild(mdDiv(answer));

  const tools = [...Object.values(run.toolCalls), ...run.resultTools];
  for (const tool of tools) {
    if (toolHasArtifact(tool, run.artifacts)) continue;
    const card = toolResultCard(tool);
    if (tool.symbol && tool.status === "done") {
      const link = el("span", "link", "查看完整报告 →");
      link.addEventListener("click", () => openReport(tool.symbol));
      card.appendChild(link);
    }
    content.appendChild(card);
  }
  for (const artifact of run.artifacts) appendReportSummary(artifact, content);
  if (run.error) {
    const card = el("div", "error-card");
    card.appendChild(el("div", "error-msg", run.error));
    content.appendChild(card);
  }
  if (run.interrupted) {
    content.appendChild(interruptedCard(() => {
      removeRun(run);
      sendMessage(run.message);
    }));
  }
  return wrap;
}

function renderRun(run) {
  if (!runIsValid(run) || store.currentSessionId !== run.sessionId) return;
  const scroll = scrollEl();
  if (run.mount && Array.from(scroll.children).includes(run.mount)) run.mount.remove();
  const bubble = buildRunBubble(run);
  scroll.appendChild(bubble);
  scroll.scrollTop = scroll.scrollHeight;
  run.mount = bubble;
}

export function renderSessionRuns(sessionId) {
  for (const run of store.sessionRuns[sessionId] || []) {
    if (run.done) continue;
    run.mount = null;
    renderRun(run);
  }
}

function isDuplicateReportTool(message, artifacts) {
  return toolHasArtifact(message, artifacts);
}

export function renderMessageHistory(messages, artifacts = []) {
  clearChatScroll();
  const activeRuns = (store.sessionRuns[store.currentSessionId] || [])
    .filter((run) => !run.done);
  const liveArtifacts = activeRuns.flatMap((run) => run.artifacts);
  const liveArtifactIds = new Set(liveArtifacts
    .map((artifact) => artifactId(artifact)).filter((id) => id != null));
  const restoredArtifacts = artifacts.filter(
    (artifact) => !liveArtifacts.includes(artifact)
      && !liveArtifactIds.has(artifactId(artifact)),
  );
  const linked = new Map();
  for (const artifact of restoredArtifacts) {
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
      const id = messageId(m);
      const matched = id == null ? [] : (linked.get(String(id)) || []);
      if (!isDuplicateReportTool(m, restoredArtifacts)) {
        appendBubble("agent", toolResultCard(parseToolMessage(m.content)));
      }
      for (const artifact of matched) {
        appendReportSummary(artifact);
        rendered.add(artifact);
      }
    } else if (m.role === "assistant") {
      appendBubble("agent", assistantMessage(m.content, m.thinking || ""));
      const id = messageId(m);
      for (const artifact of id == null ? [] : (linked.get(String(id)) || [])) {
        appendReportSummary(artifact);
        rendered.add(artifact);
      }
    }
    // system 消息不展示
  }
  const remaining = restoredArtifacts.filter((artifact) => !rendered.has(artifact));
  if (remaining.length) {
    const section = el("section", "artifact-history-orphans");
    section.appendChild(el("div", "artifact-history-title", "研究成果"));
    for (const artifact of remaining) appendReportSummary(artifact, section);
    scrollEl().appendChild(section);
  }
  renderSessionRuns(store.currentSessionId);
  const hasRuns = (store.sessionRuns[store.currentSessionId] || []).some((run) => !run.done);
  if (!messages.length && !restoredArtifacts.length && !hasRuns) renderEmptyChat();
  syncSuggestionVisibility(messages);
}

export async function sendMessage(text) {
  const msg = String(text || "").trim();
  if (!msg || activeSendingRun) return;
  const empty = scrollEl().querySelector(".chat-empty");
  if (empty) empty.hidden = true;
  let run = null;
  try {
    const initialSessionId = store.currentSessionId;
    let streamSid = initialSessionId;
    let userCached = false;
    run = {
      id: `local-run-${++localRunSequence}`,
      sessionId: null,
      message: msg,
      thinking: "正在分析…",
      plan: null,
      progress: null,
      toolCalls: {},
      resultTools: [],
      answers: [],
      artifacts: [],
      error: "",
      interrupted: false,
      done: false,
      cancelled: false,
      epoch: null,
      committed: false,
      mount: null,
    };
    activeSendingRun = run;
    const attachRun = (sessionId) => {
      if (run.sessionId === sessionId) return;
      if (store.sessionTombstones[sessionId]) return;
      run.sessionId = sessionId;
      run.epoch = sessionRunEpoch(sessionId);
      const runs = store.sessionRuns[sessionId] || [];
      if (!runs.includes(run)) runs.push(run);
      store.sessionRuns[sessionId] = runs;
    };
    const adoptSession = (sessionId) => {
      if (!sessionId || (streamSid && streamSid !== sessionId)) return false;
      if (store.sessionTombstones[sessionId] || run.cancelled) return false;
      if (run.sessionId && !runIsValid(run)) return false;
      if (!streamSid) streamSid = sessionId;
      if (!userCached) {
        if (initialSessionId === null) reviveSession(sessionId);
        else invalidateSessionDetail(sessionId);
        markSessionListMutation();
        (store.sessionMessages[sessionId] = store.sessionMessages[sessionId] || [])
          .push({ role: "user", content: msg });
        userCached = true;
      }
      attachRun(sessionId);
      if (!runIsValid(run)) return false;
      if (store.currentSessionId === initialSessionId) store.currentSessionId = sessionId;
      return true;
    };
    if (streamSid) adoptSession(streamSid);
    appendUser(msg);
    const input = document.getElementById("chatInput");
    input.value = "";   // 发送时即清空，流结束不再清（避免吞掉期间新输入）
    const sendBtn = document.getElementById("sendBtn");
    const finish = () => releaseSending(run, true);
    sendBtn.disabled = true;
    if (streamSid) {
      renderRun(run);
    } else {
      run.mount = buildRunBubble(run);
      scrollEl().appendChild(run.mount);
    }

    const handlers = {
      session_title: (e) => {
        const sessionId = e.session_id || streamSid;
        if (!adoptSession(sessionId) || sessionId !== streamSid) return;
        const existing = store.sessionDetails[sessionId] || {};
        if (existing.title_source === "manual") return;
        markSessionListMutation();
        store.sessionDetails[sessionId] = {
          ...existing,
          session_id: sessionId,
          title: e.title || "新会话",
          title_source: e.title_source || "llm",
          titleRevision: Number(existing.titleRevision || 0) + 1,
          updated_at: Date.now() / 1000,
        };
        const event = new Event("session-title");
        event.sessionId = sessionId;
        event.title = e.title || "新会话";
        bus.dispatchEvent(event);
        renderRun(run);
      },
      artifact: (e) => {
        const artifact = e.artifact;
        const sessionId = artifact?.session_id || streamSid;
        if (!artifact || !adoptSession(sessionId) || sessionId !== streamSid) return;
        const cached = cacheArtifact(sessionId, artifact, e.persisted !== false);
        if (!cached) return;
        const id = artifactId(cached);
        const index = id == null ? -1
          : run.artifacts.findIndex((item) => artifactId(item) === id);
        if (index >= 0) run.artifacts[index] = cached;
        else run.artifacts.push(cached);
        renderRun(run);
      },
      plan: (e) => {
        if (!adoptSession(e.session_id || streamSid)) return;
        run.plan = e;
        if ((e.steps || []).length) run.thinking = "";
        renderRun(run);
      },
      thinking: (e) => {
        if (!runIsValid(run)) return;
        run.thinking += e.content || "";
        renderRun(run);
      },
      tool_call: (e) => {
        if (!runIsValid(run)) return;
        const key = e.run_id || "default";
        run.toolCalls[key] = {
          tool: e.tool,
          args: e.args,
          symbol: e.symbol ?? e.args?.symbol,
          content: "调用中…",
          status: "running",
        };
        renderRun(run);
      },
      tool_result: (e) => {
        if (!runIsValid(run)) return;
        const key = e.run_id || "default";
        run.toolCalls[key] = {
          ...(run.toolCalls[key] || { tool: e.tool || "tool" }),
          content: e.content || "",
          status: e.status || "done",
          symbol: e.symbol ?? run.toolCalls[key]?.symbol,
          message_id: e.message_id ?? run.toolCalls[key]?.message_id,
        };
        renderRun(run);
      },
      progress: (e) => {
        if (!runIsValid(run)) return;
        run.progress = e;
        renderRun(run);
      },
      result: (e) => {
        if (!runIsValid(run)) return;
        run.thinking = "";
        if (e.summary) run.answers.push(e.summary);
        run.resultTools = e.tool_results || [];
        renderRun(run);
        bus.dispatchEvent(new Event("chat-done"));
      },
      error: (e) => {
        if (!runIsValid(run)) return;
        run.thinking = "";
        run.error = e.message || "处理请求时出错";
        renderRun(run);
        bus.dispatchEvent(new Event("chat-done"));
      },
      text: (e) => {
        if (!runIsValid(run)) return;
        run.thinking = "";
        if (e.thinking) run.thinking = e.thinking;
        if (e.content) run.answers.push(e.content);
        renderRun(run);
        bus.dispatchEvent(new Event("chat-done"));
      },
      done: () => {
        if (!runIsValid(run)) return;
        run.thinking = "";
        run.done = true;
        if (streamSid && !run.committed && run.answers.length) {
          (store.sessionMessages[streamSid] = store.sessionMessages[streamSid] || [])
            .push({ role: "assistant", content: run.answers.join("\n\n") });
          run.committed = true;
        }
        if (streamSid) markSessionDetailStale(streamSid);
        renderRun(run);
        if (streamSid) {
          store.sessionRuns[streamSid] = (store.sessionRuns[streamSid] || [])
            .filter((item) => item !== run);
        }
        finish();
      },
    };
    try {
      await api.chatStream(msg, streamSid, handlers);
    } catch {
      if (!runIsValid(run)) {
        finish();
        return;
      }
      run.thinking = "";
      run.interrupted = true;
      renderRun(run);
    }
    finish();
    input.focus();
  } finally {
    if (run) releaseSending(run);
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
      markSessionListMutation();
      invalidateSessionDetail(target);
      store.sessionMessages[target] = [];  // 服务端已清空，本地缓存先同步
      store.sessionArtifacts[target] = [];
      delete store.sessionDetailStale[target];
      cancelSessionRuns(target);
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
