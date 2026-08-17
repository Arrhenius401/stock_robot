// HTTP 封装：JSON 请求 + SSE 事件流消费
import { bus } from "./state.js";

// 包装 fetch：网络层失败（fetch reject，通常为 "Failed to fetch"）派发 conn-down，
// 成功则派发 conn-up，供顶栏全局连接状态条消费
async function safeFetch(url, options) {
  try {
    const resp = await fetch(url, options);
    bus.dispatchEvent(new Event("conn-up"));
    return resp;
  } catch (err) {
    bus.dispatchEvent(new Event("conn-down"));
    throw err;
  }
}

async function request(path, options = {}) {
  const resp = await safeFetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const err = new Error(data.detail || data.error || `HTTP ${resp.status}`);
    err.status = resp.status;  // 附带状态码：422 输入校验错误由视图层特殊处理
    throw err;
  }
  return data;
}

export async function consumeSSE(url, body, handlers) {
  const resp = await safeFetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok || !resp.body) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.detail || data.error || `HTTP ${resp.status}`);
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      for (const rawLine of frame.split("\n")) {
        const line = rawLine.replace(/\r$/, "");
        if (!line.startsWith("data: ")) continue;
        let event;
        try {
          event = JSON.parse(line.slice(6));
        } catch {
          continue; // 半帧 JSON 不应出现（按 \n\n 分帧），防御性跳过
        }
        const handler = handlers[event.type];
        if (handler) handler(event);
      }
    }
  }
}

export const api = {
  chatStream(message, sessionId, handlers) {
    return consumeSSE("/api/v1/chat/stream",
                      { message, session_id: sessionId }, handlers);
  },
  analyze(symbol) {
    return request("/api/v1/analyze", { method: "POST", body: JSON.stringify({ symbol }) });
  },
  index(symbols) {
    return request("/api/v1/index", { method: "POST", body: JSON.stringify({ symbols }) });
  },
  listSessions() {
    return request("/api/v1/sessions");
  },
  createSession() {
    return request("/api/v1/sessions", { method: "POST" });
  },
  deleteSession(id) {
    return request(`/api/v1/sessions/${id}`, { method: "DELETE" });
  },
  clearSession(id) {
    return request(`/api/v1/sessions/${id}/clear`, { method: "POST" });
  },
  getMessages(id) {
    return request(`/api/v1/sessions/${id}/messages`);
  },
  listTools() {
    return request("/api/v1/tools");
  },
};
