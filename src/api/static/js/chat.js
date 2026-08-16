// 聊天视图：SSE 流式渲染、执行计划卡、工具结果卡、快捷按钮
import { store, bus } from "./state.js";
import { api } from "./api.js";
import { renderMarkdown } from "./markdown.js";
import { el } from "./components.js";
import { openReport } from "./report.js";

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

let currentPlan = null;

function planCard(evt) {
  const card = el("div", "card");
  card.appendChild(el("div", "card-title", "执行计划"));
  card.appendChild(el("div", "goal", `目标：${evt.goal || "执行任务"}`));
  currentPlan = { el: card, stepEls: [] };
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
    currentPlan.stepEls.push({ dot, status });
  }
  return card;
}

function setStep(step, state, text) {
  step.dot.className = `dot ${state}`;
  step.status.textContent = text;
  step.status.className = `step-status ${state}`;
}

function updatePlan(evt) {
  if (!currentPlan) return;
  const idx = (evt.current || 1) - 1;   // current 从 1 起，步骤索引 = current-1
  currentPlan.stepEls.forEach((s, i) => {
    if (i < idx) {
      setStep(s, "done", "完成");
    } else if (i === idx && evt.stage !== "complete") {
      setStep(s, "run", "进行中");
    }
  });
  if (evt.stage === "complete") {
    currentPlan.stepEls.forEach((s) => setStep(s, "done", "完成"));
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
  body.innerHTML = renderMarkdown(t.content);
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

export function renderMessageHistory(messages) {
  clearChatScroll();
  for (const m of messages) {
    if (m.role === "user") {
      appendUser(m.content);
    } else if (m.role === "tool") {
      appendBubble("agent", toolResultCard(parseToolMessage(m.content)));
    } else if (m.role === "assistant") {
      appendBubble("agent", mdDiv(m.content));
    }
    // system 消息不展示
  }
}

export async function sendMessage(text) {
  const msg = String(text || "").trim();
  if (!msg) return;
  // 无 Agent 调试模式下 session 为 null，后端流接口的 text 事件分支仍可用
  const sid = store.currentSessionId;
  if (sid) {
    (store.sessionMessages[sid] = store.sessionMessages[sid] || [])
      .push({ role: "user", content: msg });
  }
  appendUser(msg);
  currentPlan = null;

  // 【补充 1：发送期间禁用发送按钮，防同会话并发请求交错】
  const input = document.getElementById("chatInput");
  const sendBtn = document.getElementById("sendBtn");
  const finish = () => { sendBtn.disabled = false; input.focus(); };
  sendBtn.disabled = true;

  const agentBox = appendBubble("agent", el("div", "content"));
  const thinking = el("div", "thinking", "正在分析…");
  agentBox.appendChild(thinking);

  const handlers = {
    plan: (e) => {
      // 无会话发送时（正常模式冷启动兜底），采纳后端新建的 session
      if (!store.currentSessionId && e.session_id) {
        store.currentSessionId = e.session_id;
        store.sessionMessages[e.session_id] = [{ role: "user", content: msg }];
      }
      thinking.remove();
      agentBox.appendChild(planCard(e));
    },
    progress: (e) => updatePlan(e),
    result: (e) => {
      thinking.remove();
      if (currentPlan) currentPlan.stepEls.forEach((s) => setStep(s, "done", "完成"));
      if (e.summary) agentBox.appendChild(mdDiv(e.summary));
      for (const t of e.tool_results || []) {
        const card = toolResultCard({ tool: t.tool, content: t.content || "" });
        if (t.symbol && t.status === "done") {
          const link = el("span", "link", "查看完整报告 →");
          link.addEventListener("click", () => openReport(t.symbol));
          card.appendChild(link);
        }
        agentBox.appendChild(card);
      }
      const resultSid = store.currentSessionId || sid;
      if (resultSid) {
        (store.sessionMessages[resultSid] = store.sessionMessages[resultSid] || [])
          .push({ role: "assistant", content: e.summary || "" });
      }
      bus.dispatchEvent(new Event("chat-done"));
    },
    error: (e) => {
      thinking.remove();
      const card = el("div", "error-card");
      card.appendChild(el("div", "error-msg", e.message || "处理请求时出错"));
      agentBox.appendChild(card);
      bus.dispatchEvent(new Event("chat-done"));
    },
    // 【补充 2：无 Agent 模式后端发 text 事件，必须渲染否则占位永久停留】
    text: (e) => {
      thinking.remove();
      agentBox.appendChild(mdDiv(e.content));
    },
    // 【补充 3：done 事件清理占位（无 Agent 模式或异常路径兜底）】
    done: () => {
      thinking.remove();
      finish();
    },
  };
  try {
    await api.chatStream(msg, sid, handlers);
  } catch (err) {
    thinking.remove();
    agentBox.appendChild(interruptedCard(() => sendMessage(msg)));
  }
  finish();
  input.value = "";
  input.focus();
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
  const input = document.getElementById("chatInput");
  const send = () => sendMessage(input.value);
  document.getElementById("sendBtn").addEventListener("click", send);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") send();
  });
  document.getElementById("quickTiming").addEventListener(
    "click", () => sendMessage("大盘现在适合入场吗？"));
  document.getElementById("quickTools").addEventListener("click", showToolsPanel);
  document.getElementById("quickClear").addEventListener("click", async () => {
    if (!store.currentSessionId) return;
    if (!window.confirm("清空当前会话的全部消息？")) return;
    try {
      await api.clearSession(store.currentSessionId);
      store.sessionMessages[store.currentSessionId] = [];
      clearChatScroll();
      bus.dispatchEvent(new Event("chat-done"));
    } catch (err) {
      window.alert(`清空失败: ${err.message}`);
    }
  });
}
