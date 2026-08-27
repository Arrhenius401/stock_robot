# 会话可读性改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让会话页仅在空会话展示建议栏，为新旧会话生成有辨识度的自动标题，并将助手正文与推理分离、可读地渲染。

**Architecture:** 在 API 层引入受限内容块规范化器，统一处理模型响应和历史持久化字符串；会话层依据前两条用户消息更新自动标题并保护手动标题。前端将规范化消息渲染为安全 Markdown，并以 `<details>` 呈现可选推理，同时以会话消息状态控制建议栏。

**Tech Stack:** Python 3.11、FastAPI、SQLite、pytest、原生 ES module、CSS。

**Spec:** `docs/superpowers/specs/2026-08-25-conversation-readability-design.md`

## Global Constraints

- 不覆盖 `title_source == "manual"` 的标题。
- 不执行历史消息中的字符串；Python 旧格式仅用 `ast.literal_eval` 解析。
- 所有用户可见 Markdown 均先转义，链接仅限 HTTP(S)/mailto。
- Python 验证必须使用 `.venv/Scripts/python -m pytest`。
- 不修改用户已有的 `uv.lock` 变更。

---

### Task 1: 内容块规范化器与单元测试

**Files:**
- Create: `src/api/message_content.py`
- Create: `tests/api/test_message_content.py`
- Modify: `src/agent/chat.py`
- Modify: `src/api/app.py`

**Interfaces:**
- Produces: `normalize_message_content(value: Any) -> dict[str, str]`，结果包含非空 `text`，并可包含 `thinking`。
- Consumes: LangChain `AIMessage.content`、Agent `outcome.final_reply` 和历史 `messages.content`。

- [ ] **Step 1: 写失败测试，覆盖字符串、内容块和旧格式。**

```python
def test_normalize_old_content_blocks_separates_text_and_thinking():
    raw = "[{'thinking': '推理', 'type': 'thinking'}, {'text': '# 正文', 'type': 'text'}]"
    assert normalize_message_content(raw) == {"text": "# 正文", "thinking": "推理"}
```

- [ ] **Step 2: 运行失败测试。**

Run: `.venv/Scripts/python -m pytest tests/api/test_message_content.py -q`

Expected: FAIL，提示模块或函数不存在。

- [ ] **Step 3: 实现受限规范化器。**

```python
def normalize_message_content(value: Any) -> dict[str, str]:
    """从字符串、内容块或安全解析的历史字面量提取正文与推理。"""
    blocks = _as_content_blocks(value)
    if blocks is None:
        return {"text": str(value or "")}
    text = "".join(_block_text(block) for block in blocks if block.get("type") == "text")
    thinking = "\n\n".join(_block_thinking(block) for block in blocks if block.get("type") == "thinking")
    return {"text": text, **({"thinking": thinking} if thinking else {})}
```

使用 `json.loads` 后再尝试 `ast.literal_eval`；只接受 `list[dict]`，否则回退为普通文本。

- [ ] **Step 4: 让 ChatResponder 与 SSE/非流式 Agent 回复调用规范化器。**

在模型边界将正文写入 `Memory`；SSE `text` 事件携带 `content` 和可选 `thinking`，避免 `str(list)` 持久化。

- [ ] **Step 5: 运行针对性测试并提交。**

Run: `.venv/Scripts/python -m pytest tests/api/test_message_content.py tests/api/test_app.py -q`

Expected: PASS。

Commit:

```bash
git add src/api/message_content.py src/agent/chat.py src/api/app.py tests/api/test_message_content.py tests/api/test_app.py
git commit -m "fix(会话): 规范化助手正文与推理内容"
```

### Task 2: 两轮标题归纳与历史回填

**Files:**
- Modify: `src/api/session_titles.py`
- Modify: `src/api/sessions.py`
- Modify: `src/api/app.py`
- Modify: `tests/api/test_session_titles.py`
- Modify: `tests/api/test_sessions.py`

**Interfaces:**
- Produces: `derive_session_title_from_messages(messages: list[str]) -> str` 和 `SessionManager.refresh_automatic_title(session_id) -> str | None`。
- Consumes: `SessionStore.get_messages()` 与既有 `maybe_update_title()`。

- [ ] **Step 1: 写失败测试。**

```python
def test_second_user_message_improves_generic_first_title():
    assert derive_session_title_from_messages(["你好", "比较平安银行和招商银行"]) == "平安银行与招商银行比较"

def test_backfill_never_overwrites_manual_title(manager):
    manager.rename("sid", "我的标题")
    assert manager.refresh_automatic_title("sid") is None
```

- [ ] **Step 2: 运行失败测试。**

Run: `.venv/Scripts/python -m pytest tests/api/test_session_titles.py tests/api/test_sessions.py -q`

Expected: FAIL，提示新增标题函数/回填方法不存在。

- [ ] **Step 3: 实现标题质量判断和两轮归纳。**

将“新会话”、问候语和短泛化结果视为低信息量；提取前两条 `role == "user"` 消息，优先使用第二条中可识别的标的与意图。保留 20 字符上限与当前 LLM 润色安全校验。

- [ ] **Step 4: 在会话读取路径回填。**

在 `SessionManager.list_sessions()` 或详情读取路径对非手动、可改善的标题调用受条件更新；新增消息后若标题仍低信息量，也运行一次更新。向前端只发送标题真正变化的事件。

- [ ] **Step 5: 运行测试并提交。**

Run: `.venv/Scripts/python -m pytest tests/api/test_session_titles.py tests/api/test_sessions.py tests/api/test_app.py -q`

Expected: PASS。

Commit:

```bash
git add src/api/session_titles.py src/api/sessions.py src/api/app.py tests/api/test_session_titles.py tests/api/test_sessions.py tests/api/test_app.py
git commit -m "feat(会话): 支持两轮标题归纳与历史回填"
```

### Task 3: 会话页建议栏与结构化消息展示

**Files:**
- Modify: `src/api/static/js/chat.js`
- Modify: `src/api/static/js/sessions.js`
- Modify: `src/api/static/js/markdown.js`
- Modify: `src/api/static/css/style.css`
- Modify: `src/api/static/index.html`（仅在缺少语义容器时）
- Create: `tests/api/test_static_conversation_ui.py`

**Interfaces:**
- Consumes: `message.content` 与可选 `message.thinking`；SSE `{type: "text", content, thinking?}`。
- Produces: 建议栏 `hidden` 状态、正文 HTML、`<details class="message-thinking">` 折叠推理区。

- [ ] **Step 1: 写静态/DOM 行为失败测试。**

```python
def test_chat_ui_hides_suggestions_after_first_user_message():
    source = chat_js.read_text(encoding="utf-8")
    assert "hasUserMessages" in source
    assert "suggestions.hidden" in source

def test_chat_ui_uses_details_for_thinking():
    assert '<details class="message-thinking"' in chat_js.read_text(encoding="utf-8")
```

- [ ] **Step 2: 运行失败测试。**

Run: `.venv/Scripts/python -m pytest tests/api/test_static_conversation_ui.py -q`

Expected: FAIL，提示对应状态/标记缺失。

- [ ] **Step 3: 以当前会话消息控制建议栏。**

```javascript
function syncSuggestionVisibility(messages) {
  const hasUserMessages = messages.some((message) => message.role === "user");
  suggestions.hidden = hasUserMessages;
}
```

在加载会话、提交消息、清空会话和切换会话后调用；CSS 将空会话卡片设为两列、14px 间距、可响应式折叠。

- [ ] **Step 4: 渲染正文和默认折叠推理。**

```javascript
const body = `<div class="message-markdown">${renderMarkdown(message.content)}</div>`;
const thinking = message.thinking
  ? `<details class="message-thinking"><summary>查看分析过程</summary><div>${renderMarkdown(message.thinking)}</div></details>`
  : "";
node.innerHTML = `${body}${thinking}`;
```

历史字符串先在前端轻量兼容，正常情况下由详情 API 提供已经规范化的字段。不得把未经 `renderMarkdown` 的内容写入 `innerHTML`。

- [ ] **Step 5: 扩展 Markdown 测试与运行静态测试。**

Run: `.venv/Scripts/python -m pytest tests/api/test_static.py tests/api/test_static_conversation_ui.py -q`

Expected: PASS。

- [ ] **Step 6: 提交。**

```bash
git add src/api/static/js/chat.js src/api/static/js/sessions.js src/api/static/js/markdown.js src/api/static/css/style.css src/api/static/index.html tests/api/test_static_conversation_ui.py tests/api/test_static.py
git commit -m "feat(前端): 优化会话建议栏与分析过程展示"
```

### Task 4: 端到端回归与手工视觉核验

**Files:**
- Modify: `tests/api/test_app.py`
- Modify: `tests/api/test_sessions.py`

**Interfaces:**
- Consumes: Tasks 1–3 的消息事件、标题回填和页面标记。
- Produces: 覆盖新旧会话、流式响应与清空恢复建议栏的回归证据。

- [ ] **Step 1: 增加 SSE 集成测试。**

```python
assert final_text_event["content"].startswith("##")
assert final_text_event["thinking"] == "整理数据"
assert "[{&#x27;thinking&#x27;" not in stored_assistant_content
```

- [ ] **Step 2: 运行目标测试。**

Run: `.venv/Scripts/python -m pytest tests/api/test_session_titles.py tests/api/test_sessions.py tests/api/test_app.py tests/api/test_static.py tests/api/test_static_conversation_ui.py -q -p no:cacheprovider`

Expected: PASS。

- [ ] **Step 3: 做浏览器手工核验。**

验证：空会话卡片间距、发送首条消息后卡片消失、历史异常消息正常显示、推理默认折叠、清空后卡片恢复、手动标题不变。

- [ ] **Step 4: 提交。**

```bash
git add tests/api/test_app.py tests/api/test_sessions.py
git commit -m "test(会话): 覆盖可读性改造回归场景"
```

## 自审结果

- 覆盖规格中的建议栏、两轮标题、历史回填、内容块分离、安全解析、Markdown 渲染与 SSE 一致性。
- 未使用占位任务；所有新增函数名称和数据字段在前序任务中定义。
- 全部测试命令显式使用项目虚拟环境，不触碰 `uv.lock`。
