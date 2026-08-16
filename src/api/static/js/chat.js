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

    const agentBox = appendBubble("agent", el("div", "content"));
    const thinking = el("div", "thinking", "正在分析…");
    agentBox.appendChild(thinking);

    const handlers = {
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
        thinking.remove();
        myPlan = planCard(e);
        agentBox.appendChild(myPlan.card);
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
      // 无 Agent 模式后端发 text 事件，必须渲染否则占位永久停留
      text: (e) => {
        thinking.remove();
        agentBox.appendChild(mdDiv(e.content));
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
  const input = document.getElementById("chatInput");
  const send = () => sendMessage(input.value);
  document.getElementById("sendBtn").addEventListener("click", send);
  input.addEventListener("keydown", (e) => {
    // isComposing：中文等 IME 组合输入的回车仅确认候选词，不应触发发送
    if (e.key === "Enter" && !e.isComposing) send();
  });
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
      if (store.currentSessionId !== target) return;  // 已切换，不动新会话视图
      clearChatScroll();
      bus.dispatchEvent(new Event("chat-done"));
    } catch (err) {
      window.alert(`清空失败: ${err.message}`);
    }
  });
}
