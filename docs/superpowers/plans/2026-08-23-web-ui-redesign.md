# Stock Robot Web 前端重设计实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 Web UI 改造成专业投研工作台，提供智能会话标题、可恢复的报告成果、聊天内摘要卡和可随时关闭的右侧完整报告抽屉。

**Architecture:** 保留 FastAPI、SQLite 与原生 ES Module 架构；后端为会话标题和报告成果提供持久化与 REST/SSE 接口，前端以共享报告渲染器连接独立报告页和右侧抽屉。应用外壳采用左侧导航、中间工作区和按需出现的右侧抽屉，移动端把导航与报告转换为覆盖层。

**Tech Stack:** Python 3.11+、FastAPI、SQLite、Pydantic v2、pytest、原生 HTML/CSS/JavaScript ES Modules

**Spec:** `docs/superpowers/specs/2026-08-23-web-ui-redesign-design.md`

## Global Constraints

- 代码注释、文档和 Git 提交信息使用中文。
- 不引入 React、Vue、前端构建工具或新的部署链路。
- SQLite 迁移必须保留现有会话和消息；新增列提供安全默认值。
- 用户手动标题不得被自动标题覆盖；标题生成失败不得影响聊天。
- 报告持久化失败不得把整轮聊天标记为失败。
- Markdown 内容必须先转义，并只允许 `http`、`https`、`mailto` 安全链接。
- Python 命令只使用 `.venv/Scripts/python`；优先运行改动文件的 Ruff、Pyright 和定向 pytest。
- 保留工作区中与本计划无关的用户改动，不修改 `data/industry_mapping.csv` 和现有未跟踪 `scripts/`。

## 文件结构

- `src/api/session_titles.py`：本地标题提取、标题校验和 LLM 润色器。
- `src/api/sessions.py`：会话元数据迁移、重命名、标题状态和报告成果 SQLite 存储。
- `src/api/app.py`：会话标题/成果 REST 接口，以及聊天 SSE 的标题和成果事件编排。
- `src/api/static/js/report-renderer.js`：结构化个股报告 DOM 渲染器，独立报告页与抽屉共用。
- `src/api/static/js/report-drawer.js`：右侧抽屉状态、报告加载、章节定位、关闭和刷新。
- `src/api/static/js/chat.js`：聊天消息、报告摘要卡和成果事件渲染。
- `src/api/static/js/sessions.js`：丰富会话项、重命名菜单和历史成果恢复。
- `src/api/static/js/markdown.js`：安全 Markdown 块级解析增强。
- `src/api/static/js/api.js`：重命名、成果读取及带会话上下文的报告刷新请求。
- `src/api/static/js/state.js`：当前报告成果、抽屉和会话详情缓存状态。
- `src/api/static/js/report.js`：独立报告页改为调用共享报告渲染器。
- `src/api/static/js/app.js`：应用外壳初始化、导航覆盖层和全局搜索。
- `src/api/static/index.html`：新的应用外壳、抽屉及移动端遮罩语义结构。
- `src/api/static/css/app.css`：设计令牌、三栏布局、会话、聊天、报告和响应式样式。
- `tests/api/test_session_titles.py`：标题规则和 LLM 降级测试。
- `tests/api/test_sessions.py`：数据库迁移、标题状态和成果生命周期测试。
- `tests/api/test_app.py`：新增 REST/SSE 合约与历史恢复测试。
- `tests/api/test_static.py`：新增静态模块、外壳关键元素和安全渲染契约测试。

---

### Task 1: 会话标题领域逻辑与持久化

**Files:**
- Create: `src/api/session_titles.py`
- Modify: `src/api/sessions.py:24-69,117-145`
- Create: `tests/api/test_session_titles.py`
- Modify: `tests/api/test_sessions.py`

**Interfaces:**
- Consumes: 现有 `SessionStore.create_session()`、`SessionManager.get_or_create()` 和注入的 LangChain 模型 `ainvoke(messages)`。
- Produces: `derive_session_title(message: str) -> str`、`normalize_generated_title(value: str) -> str | None`、`SessionTitleRefiner.refine(message: str, fallback: str) -> str`；`SessionManager.rename(session_id: str, title: str, manual: bool = True) -> bool`；`SessionManager.maybe_update_title(session_id: str, title: str, source: str) -> bool`。

- [ ] **Step 1: 为本地标题器写失败测试**

```python
from api.session_titles import derive_session_title, normalize_generated_title


def test_derive_title_keeps_symbols_and_research_intent():
    assert derive_session_title("帮我分析 600519 近期估值和技术趋势") == "600519估值与趋势"


def test_derive_title_compacts_comparison_request():
    assert derive_session_title("请比较沪深300和中证红利最近表现") == "沪深300与中证红利比较"


def test_generated_title_rejects_multiline_or_overlong_value():
    assert normalize_generated_title("第一行\n第二行") is None
    assert normalize_generated_title("过" * 21) is None
```

- [ ] **Step 2: 运行标题器测试并确认失败**

Run: `.venv/Scripts/python -m pytest tests/api/test_session_titles.py -q`

Expected: FAIL，提示 `api.session_titles` 不存在。

- [ ] **Step 3: 实现纯本地标题器和 LLM 润色器**

```python
TITLE_MAX_LENGTH = 20


def normalize_generated_title(value: str) -> str | None:
    title = " ".join(value.strip().strip('"“”').split())
    if not title or "\n" in value or len(title) > TITLE_MAX_LENGTH:
        return None
    return title


class SessionTitleRefiner:
    def __init__(self, model=None):
        self._model = model

    async def refine(self, message: str, fallback: str) -> str:
        if self._model is None:
            return fallback
        try:
            response = await self._model.ainvoke([
                {"role": "system", "content": "将用户请求概括为8至20字中文投研会话标题，只输出标题。"},
                {"role": "user", "content": message},
            ])
            return normalize_generated_title(str(response.content)) or fallback
        except Exception as exc:  # noqa: BLE001 — LLM 边界失败保留本地标题
            logger.warning("会话标题润色失败: %s", exc)
            return fallback
```

`derive_session_title()` 按“标的提取 → 意图提取 → 去口语化回退”顺序实现；只覆盖设计文档列出的研究意图，不引入股票名称远程查询。

- [ ] **Step 4: 为会话表增量迁移和手动标题保护写失败测试**

```python
def test_manual_title_is_not_overwritten(store):
    store.create_session("s1", "新会话")
    assert store.update_title("s1", "我的自选池", source="manual") is True
    assert store.update_title("s1", "模型标题", source="llm", only_if_automatic=True) is False
    assert store.list_sessions()[0]["title"] == "我的自选池"
    assert store.list_sessions()[0]["title_source"] == "manual"


def test_existing_database_gets_title_source_column(tmp_path):
    # 先用 sqlite3 建立旧版 sessions/messages 表，再构造 SessionStore。
    migrated = SessionStore(tmp_path / "legacy.db")
    assert migrated.list_sessions()[0]["title_source"] == "legacy"
```

- [ ] **Step 5: 实现 SQLite 增量迁移和标题更新方法**

在 `_init_db()` 完成建表后读取 `PRAGMA table_info(sessions)`；缺少 `title_source` 时执行：

```sql
ALTER TABLE sessions ADD COLUMN title_source TEXT NOT NULL DEFAULT 'legacy';
```

新增 `SessionStore.update_title()`，用单条带条件的 `UPDATE` 实现手动标题保护；`SessionManager.get_or_create()` 使用 `derive_session_title(first_message)` 并写入 `source="local"`，显式空消息仍写“新会话”。

- [ ] **Step 6: 运行定向测试和静态检查**

Run: `.venv/Scripts/python -m pytest tests/api/test_session_titles.py tests/api/test_sessions.py -q`

Expected: PASS。

Run: `pyright src/api/session_titles.py src/api/sessions.py`

Expected: 0 errors。

- [ ] **Step 7: 提交标题领域改动**

```bash
git add src/api/session_titles.py src/api/sessions.py tests/api/test_session_titles.py tests/api/test_sessions.py
git commit -m "feat(会话): 添加智能标题生成与手动标题保护"
```

### Task 2: 报告成果持久化

**Files:**
- Modify: `src/api/sessions.py`
- Modify: `tests/api/test_sessions.py`

**Interfaces:**
- Consumes: Task 1 的 `SessionStore` 迁移模式和 `SessionManager` 锁。
- Produces: `SessionArtifact` 字典字段 `artifact_id/session_id/message_id/kind/symbol/payload/created_at/updated_at`；`save_artifact()`、`list_artifacts()`、`get_artifact()`；`get_session_detail()` 返回 `{"messages": ..., "artifacts": ...}`。

- [ ] **Step 1: 为成果生命周期写失败测试**

```python
def test_artifact_round_trip_and_session_cleanup(store):
    store.create_session("s1", "平安银行估值")
    artifact = store.save_artifact(
        session_id="s1", message_id=None, kind="stock_report",
        symbol="000001", payload={"symbol": "000001", "score": {"final": 7.2}},
    )
    assert store.get_artifact(artifact["artifact_id"])["payload"]["score"]["final"] == 7.2
    assert store.list_artifacts("s1")[0]["symbol"] == "000001"
    store.clear_messages("s1")
    assert store.list_artifacts("s1") == []
```

- [ ] **Step 2: 运行测试并确认缺少成果接口**

Run: `.venv/Scripts/python -m pytest tests/api/test_sessions.py -q`

Expected: FAIL，提示 `save_artifact` 不存在。

- [ ] **Step 3: 建立成果表与 JSON 序列化方法**

```sql
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    message_id INTEGER,
    kind TEXT NOT NULL,
    symbol TEXT,
    payload_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts(session_id, created_at);
```

所有连接启用 `PRAGMA foreign_keys=ON`。`payload_json` 使用 `json.dumps(..., ensure_ascii=False, default=str)`；读取时解析为 `dict`。`clear_messages()` 显式删除成果，确保旧数据库连接行为一致。

- [ ] **Step 4: 在 SessionManager 暴露线程安全成果接口**

```python
def save_artifact(self, session_id: str, *, kind: str, symbol: str | None,
                  payload: dict, message_id: int | None = None) -> dict: ...

def get_session_detail(self, session_id: str) -> dict | None:
    if not self._store.session_exists(session_id):
        return None
    return {"messages": self._store.get_messages(session_id),
            "artifacts": self._store.list_artifacts(session_id)}
```

- [ ] **Step 5: 运行会话测试和检查**

Run: `.venv/Scripts/python -m pytest tests/api/test_sessions.py -q`

Expected: PASS。

Run: `pyright src/api/sessions.py`

Expected: 0 errors。

- [ ] **Step 6: 提交成果存储**

```bash
git add src/api/sessions.py tests/api/test_sessions.py
git commit -m "feat(会话): 持久化结构化报告成果"
```

### Task 3: 会话标题与成果 API/SSE 编排

**Files:**
- Modify: `src/api/app.py:220-316,468-520`
- Modify: `tests/api/test_app.py`

**Interfaces:**
- Consumes: Task 1 的 `SessionTitleRefiner`、Task 2 的成果接口、现有 `_structured_tool_results()`。
- Produces: `PATCH /api/v1/sessions/{session_id}`；`GET /api/v1/sessions/{session_id}/artifacts/{artifact_id}`；历史响应 `messages + artifacts`；SSE `session_title` 与 `artifact` 事件。

- [ ] **Step 1: 为重命名和成果读取端点写失败测试**

```python
@pytest.mark.asyncio
async def test_session_can_be_renamed_and_artifact_loaded(client):
    created = await client.post("/api/v1/sessions")
    sid = created.json()["session_id"]
    renamed = await client.patch(f"/api/v1/sessions/{sid}", json={"title": "银行股估值"})
    assert renamed.json()["title"] == "银行股估值"
    missing = await client.get(f"/api/v1/sessions/{sid}/artifacts/missing")
    assert missing.status_code == 404
```

- [ ] **Step 2: 为 SSE 标题和报告成果事件写失败测试**

扩展测试工具桩，使 `analyze_stock` 返回可 JSON 化的完整报告字典，然后断言流中依次存在：

```python
assert any(event["type"] == "session_title" and event["title"] for event in events)
artifact_event = next(event for event in events if event["type"] == "artifact")
assert artifact_event["artifact"]["kind"] == "stock_report"
assert artifact_event["artifact"]["payload"]["symbol"] == "000001"
```

- [ ] **Step 3: 实现 REST 端点和输入校验**

`PATCH` 请求只接受去首尾空格后 1 至 20 个字符的标题，空值或超长返回 422；不存在会话返回 404。成果读取必须同时校验成果所属会话，避免跨会话访问。

- [ ] **Step 4: 提取并持久化个股报告成果**

新增纯函数。当前 Executor 把工具字典写成 Python `repr` 文本，使用
`ast.literal_eval()` 解析去掉 `[analyze_stock] success:` 前缀后的内容；解析失败
视为普通工具文本，不得使用 `eval()`：

```python
def _extract_stock_report(tool_results: list[dict]) -> tuple[str, dict] | None:
    for item in tool_results:
        if item.get("tool") != "analyze_stock" or item.get("status") != "done":
            continue
        content = item.get("content")
        if isinstance(content, str):
            raw = re.sub(r"^\[analyze_stock\]\s+success:\s*", "", content)
            try:
                content = ast.literal_eval(raw)
            except (SyntaxError, ValueError):
                continue
        if isinstance(content, dict) and (content.get("symbol") or content.get("code")):
            symbol = str(content.get("symbol") or content["code"])
            payload = {
                "symbol": symbol,
                "name": content.get("name") or symbol,
                "overview": content.get("overview") or {},
                "score": content.get("score") or {},
                "score_rows": content.get("score_rows") or [],
                "dimensions": content.get("dimensions") or {},
                "commentary": content.get("commentary") or "\n\n".join(content.get("comments") or []),
                "generated_at": content.get("generated_at"),
            }
            return symbol, _json_safe(payload)
    return None
```

在 plan 和 agent 路径得到 `tool_results` 后调用统一 `_persist_artifact_if_present()`。存储边界使用带日志的异常隔离；失败时继续发送本轮内存 payload，并在事件中增加 `persisted: false`。

- [ ] **Step 5: 编排非阻塞标题润色**

会话创建后先发送 `session_title` 本地标题。首轮完成后创建标题润色协程；在 SSE `done` 之前最多等待一个短超时并发出更新事件，但不得延迟正文/成果事件。超时后取消任务并保留本地标题，避免脱离请求生命周期的泄漏任务。

- [ ] **Step 6: 运行 API 定向测试和检查**

Run: `.venv/Scripts/python -m pytest tests/api/test_app.py tests/api/test_sessions.py tests/api/test_session_titles.py -q`

Expected: PASS。

Run: `pyright src/api/app.py src/api/sessions.py src/api/session_titles.py`

Expected: 0 errors。

- [ ] **Step 7: 提交 API 编排**

```bash
git add src/api/app.py tests/api/test_app.py
git commit -m "feat(api): 提供会话标题与报告成果接口"
```

### Task 4: 共享结构化报告渲染器与右侧抽屉

**Files:**
- Create: `src/api/static/js/report-renderer.js`
- Create: `src/api/static/js/report-drawer.js`
- Modify: `src/api/static/js/report.js`
- Modify: `src/api/static/js/api.js`
- Modify: `src/api/static/js/state.js`
- Modify: `src/api/static/index.html`
- Modify: `tests/api/test_static.py`

**Interfaces:**
- Consumes: 个股报告 JSON 字段 `symbol/name/overview/score/score_rows/dimensions/commentary/signal/generated_at` 和 Task 3 的成果读取端点。
- Produces: `renderStockReport(report: object, options?: object) -> HTMLElement`；`renderReportSummary(artifact: object) -> HTMLElement`；`openReportDrawer(artifact: object)`、`closeReportDrawer()`、`initReportDrawer()`。

- [ ] **Step 1: 为静态模块和抽屉语义写失败测试**

```python
def test_report_drawer_assets_are_wired(client):
    html = client.get("/").text
    assert 'id="reportDrawer"' in html
    assert 'aria-label="完整研报"' in html
    assert 'id="reportDrawerClose"' in html
    assert client.get("/js/report-renderer.js").status_code == 200
    assert client.get("/js/report-drawer.js").status_code == 200
```

- [ ] **Step 2: 运行静态测试并确认失败**

Run: `.venv/Scripts/python -m pytest tests/api/test_static.py -q`

Expected: FAIL，缺少抽屉标记或静态模块。

- [ ] **Step 3: 创建无业务状态的共享报告渲染器**

`renderStockReport()` 只接收数据并返回 DOM，不读取全局 store。为每个章节生成稳定 ID（如 `report-financial`），缺失字段显示“暂无数据”或省略可选行；所有自然语言字段通过 `renderMarkdown()` 输出。把 `report.js` 中 `reportHeader/overviewCard/scoreCard/dimCard/llmCard` 移入该模块并保留中文字段映射。

- [ ] **Step 4: 创建报告抽屉控制器**

```javascript
export function openReportDrawer(artifact) {
  store.currentArtifact = artifact;
  document.getElementById("appLayout").classList.add("drawer-open");
  const drawer = document.getElementById("reportDrawer");
  drawer.hidden = false;
  drawer.setAttribute("aria-hidden", "false");
  renderDrawerContent(artifact);
}

export function closeReportDrawer() {
  store.currentArtifact = null;
  document.getElementById("appLayout").classList.remove("drawer-open");
  const drawer = document.getElementById("reportDrawer");
  drawer.hidden = true;
  drawer.setAttribute("aria-hidden", "true");
}
```

抽屉关闭后把焦点还给触发摘要卡；刷新使用请求序号，旧响应不得覆盖新报告；失败时保留旧 DOM 并在抽屉内显示局部错误。

- [ ] **Step 5: 改造独立报告页并扩展 API 客户端**

`report.js` 只负责请求、缓存和调用 `renderStockReport()`；`api.js` 增加 `renameSession()`、`getArtifact()`，并让刷新报告支持传入会话/成果上下文但保持旧 `analyze(symbol)` 可用。

- [ ] **Step 6: 运行静态和 API 测试**

Run: `.venv/Scripts/python -m pytest tests/api/test_static.py tests/api/test_app.py -q`

Expected: PASS。

- [ ] **Step 7: 提交共享报告视图**

```bash
git add src/api/static/js/report-renderer.js src/api/static/js/report-drawer.js src/api/static/js/report.js src/api/static/js/api.js src/api/static/js/state.js src/api/static/index.html tests/api/test_static.py
git commit -m "feat(报告): 添加共享渲染器与右侧研报抽屉"
```

### Task 5: 聊天报告卡、历史恢复与智能会话列表

**Files:**
- Modify: `src/api/static/js/chat.js`
- Modify: `src/api/static/js/sessions.js`
- Modify: `src/api/static/js/api.js`
- Modify: `src/api/static/js/state.js`
- Modify: `src/api/static/index.html`
- Modify: `tests/api/test_static.py`

**Interfaces:**
- Consumes: Task 3 的 `session_title/artifact` SSE 事件和历史 `messages/artifacts`；Task 4 的 `renderReportSummary()`、`openReportDrawer()`、`closeReportDrawer()`。
- Produces: 历史可恢复的报告摘要卡、会话重命名菜单、相对更新时间与会话切换抽屉一致性。

- [ ] **Step 1: 为前端事件合约写失败测试**

在 `test_static.py` 读取模块文本并断言关键导入和 handler 名称，防止静态页面漏接线：

```python
def test_chat_wires_artifact_and_title_events():
    source = STATIC.joinpath("js/chat.js").read_text(encoding="utf-8")
    assert "session_title:" in source
    assert "artifact:" in source
    assert "openReportDrawer" in source
```

- [ ] **Step 2: 接入 SSE 标题与成果事件**

`session_title` 更新当前会话缓存并触发列表重绘；`artifact` 写入 `store.sessionArtifacts[sessionId]`，在当前助手回复后插入摘要卡。报告持久化失败时卡片仍可打开本轮 payload，并显示“刷新后可能不可恢复”的非阻塞说明。

- [ ] **Step 3: 恢复历史消息与成果**

`selectSession()` 请求会话详情后分别保存 `messages` 和 `artifacts`。`renderMessageHistory(messages, artifacts)` 按 `message_id` 关联；没有关联 ID 的旧成果按创建顺序放在消息末尾的“研究成果”组。切换会话时调用 `closeReportDrawer()`。

- [ ] **Step 4: 改造会话列表与重命名菜单**

会话项使用真实 `<button>` 作为主点击区域，显示标题、标的标签和格式化更新时间。省略号按钮打开包含“重命名/删除”的小菜单；重命名使用内联输入，Enter 保存、Escape 取消、失焦不自动提交。保存成功后更新缓存，失败时恢复旧标题并显示局部错误。

- [ ] **Step 5: 改造聊天空状态与多行输入**

把单行 `input#chatInput` 改为 `textarea`。Enter 发送、Shift+Enter 换行、IME 组合期间不发送。空会话显示三个研究建议按钮，点击只填入输入框，不自动发送。

- [ ] **Step 6: 运行静态与 API 测试**

Run: `.venv/Scripts/python -m pytest tests/api/test_static.py tests/api/test_app.py -q`

Expected: PASS。

- [ ] **Step 7: 提交聊天与会话交互**

```bash
git add src/api/static/js/chat.js src/api/static/js/sessions.js src/api/static/js/api.js src/api/static/js/state.js src/api/static/index.html tests/api/test_static.py
git commit -m "feat(前端): 恢复会话报告并优化会话管理"
```

### Task 6: 安全 Markdown 渲染增强

**Files:**
- Modify: `src/api/static/js/markdown.js`
- Modify: `tests/api/test_static.py`

**Interfaces:**
- Consumes: 未可信 Markdown 字符串。
- Produces: `renderMarkdown(text: string) -> string`，支持标题、段落、有序/无序列表、引用、表格、代码块、强调与安全链接。

- [ ] **Step 1: 增加可执行的 Markdown 安全契约测试**

在 `test_static.py` 使用 `subprocess.run([node, "--input-type=module", "--eval", script])` 动态导入 `markdown.js`；若 Node 不可用则明确 `pytest.skip("Node.js 不可用")`。断言：

```javascript
const html = renderMarkdown(`# 标题\n\n1. 第一项\n2. 第二项\n\n> 风险提示\n\n<script>alert(1)</script>\n[坏链接](javascript:alert(1))`);
if (!html.includes("<ol>")) throw new Error("缺少有序列表");
if (!html.includes("<blockquote>")) throw new Error("缺少引用");
if (html.includes("<script>")) throw new Error("未转义脚本");
if (html.includes("javascript:")) throw new Error("放行危险链接");
```

- [ ] **Step 2: 运行测试并确认语法能力失败**

Run: `.venv/Scripts/python -m pytest tests/api/test_static.py -q`

Expected: FAIL，缺少 `<ol>` 或 `<blockquote>`。

- [ ] **Step 3: 扩展块级状态机**

保持 `esc(String(text))` 在任何 Markdown 识别之前执行；增加连续有序列表和连续引用块解析；链接过滤在转义后执行且仅允许 `http(s)`/`mailto`。代码块内部不再运行行内格式化。段落终止条件加入有序列表和引用起始符。

- [ ] **Step 4: 运行静态测试**

Run: `.venv/Scripts/python -m pytest tests/api/test_static.py -q`

Expected: PASS。

- [ ] **Step 5: 提交 Markdown 改动**

```bash
git add src/api/static/js/markdown.js tests/api/test_static.py
git commit -m "feat(前端): 增强安全 Markdown 报告渲染"
```

### Task 7: 投研工作台视觉外壳与响应式布局

**Files:**
- Modify: `src/api/static/index.html`
- Modify: `src/api/static/css/app.css`
- Modify: `src/api/static/js/app.js`
- Modify: `tests/api/test_static.py`

**Interfaces:**
- Consumes: Tasks 4–6 已有 DOM ID、类名和初始化函数。
- Produces: 左侧工作台、中间主视图、右侧抽屉；桌面、中等宽度和移动端布局；全局股票搜索。

- [ ] **Step 1: 为新应用外壳关键语义写失败测试**

```python
def test_workspace_shell_has_accessible_landmarks(client):
    html = client.get("/").text
    assert 'id="appLayout"' in html
    assert 'aria-label="主要导航"' in html
    assert 'id="globalStockSearch"' in html
    assert 'id="mobileNavToggle"' in html
    assert 'id="workspaceBackdrop"' in html
```

- [ ] **Step 2: 运行测试并确认旧外壳失败**

Run: `.venv/Scripts/python -m pytest tests/api/test_static.py -q`

Expected: FAIL，缺少工作台语义元素。

- [ ] **Step 3: 重组 HTML 应用外壳**

顶部只放移动导航按钮、当前视图标题、全局股票搜索和连接状态；个股页面内部保留专用分析输入，指数输入移动到指数页面。左侧先放会话，再放功能导航；报告抽屉置于主区域同级。遮罩按钮负责关闭移动导航或全屏报告。

- [ ] **Step 4: 建立设计令牌和桌面布局**

在 `:root` 定义背景、表面、文字、弱文字、边框、主色、涨跌色、风险色、间距、圆角和阴影令牌。应用外壳使用 CSS Grid：展开侧栏约 272px，中间 `minmax(0, 1fr)`，抽屉打开时增加 `minmax(420px, 45%)`；折叠侧栏保留 72px 图标导航。

- [ ] **Step 5: 完成会话、聊天和报告排版**

去除重复面板重边框；摘要、评分和风险使用排版与浅色表面分组。报告正文限制可读行宽，表格包裹层允许局部横向滚动。所有按钮有可见焦点，状态不只依赖颜色。

- [ ] **Step 6: 添加响应式行为**

在约 1024px 以下收窄侧栏；约 760px 以下让侧栏成为覆盖层、报告抽屉占满主区域。禁止页面级横向滚动；长标题、代码和表格分别使用省略、换行或局部滚动。`app.js` 统一处理 Escape、遮罩点击和视图切换后的覆盖层复位。

- [ ] **Step 7: 运行静态测试并人工检查页面**

Run: `.venv/Scripts/python -m pytest tests/api/test_static.py -q`

Expected: PASS。

启动：`.venv/Scripts/python -m stock_robot.cli api --host 127.0.0.1 --port 25618`

人工检查：1280px 桌面三栏、900px 收窄、390px 移动端；长会话标题、长表格、打开/关闭抽屉、Escape、侧栏覆盖层与聊天滚动位置。

- [ ] **Step 8: 提交视觉外壳**

```bash
git add src/api/static/index.html src/api/static/css/app.css src/api/static/js/app.js tests/api/test_static.py
git commit -m "feat(前端): 重构投研工作台视觉与响应式布局"
```

### Task 8: 端到端回归与交付检查

**Files:**
- Modify: `tests/api/test_app.py`
- Modify: `tests/api/test_static.py`

**Interfaces:**
- Consumes: Tasks 1–7 的完整功能。
- Produces: 可重复验证的会话标题、报告持久化、历史恢复和响应式交付证据。

- [ ] **Step 1: 增加完整历史恢复回归测试**

测试流程必须真实经过 API：创建会话 → 发送产生报告的聊天 → 获取会话详情 → 读取成果 → 手动重命名 → 再次聊天 → 确认标题不变 → 清空会话 → 确认消息与成果均为空。

- [ ] **Step 2: 运行后端定向测试**

Run: `.venv/Scripts/python -m pytest tests/api/test_session_titles.py tests/api/test_sessions.py tests/api/test_app.py tests/api/test_static.py -q`

Expected: 全部 PASS。

- [ ] **Step 3: 运行改动文件 Ruff 与 Pyright**

Run: `& (Get-ChildItem "$env:USERPROFILE/.vscode/extensions/charliermarsh.ruff-*/bundled/libs/bin/ruff.exe" | Select-Object -First 1 -ExpandProperty FullName) check src/api/session_titles.py src/api/sessions.py src/api/app.py tests/api/test_session_titles.py tests/api/test_sessions.py tests/api/test_app.py tests/api/test_static.py`

Expected: 0 errors。

Run: `pyright src/api/session_titles.py src/api/sessions.py src/api/app.py`

Expected: 0 errors。

- [ ] **Step 4: 完成浏览器验收矩阵**

逐项验证：

- 模型已配置和未配置时标题都可用；
- 手动重命名后不被覆盖；
- 新报告摘要卡可打开抽屉；
- 切换会话和刷新页面后报告可恢复；
- 刷新失败保留旧报告；
- 多份报告切换无迟到覆盖；
- 桌面和移动端抽屉均可通过按钮、遮罩和 Escape 关闭；
- 危险 Markdown 不产生脚本或危险链接。

- [ ] **Step 5: 检查提交范围并提交回归补充**

Run: `git status --short`

Expected: 只出现本计划文件或原先已存在的用户改动；不得暂存 `data/industry_mapping.csv` 和 `scripts/`。

```bash
git add tests/api/test_app.py tests/api/test_static.py
git commit -m "test(前端): 补充工作台端到端回归验证"
```
