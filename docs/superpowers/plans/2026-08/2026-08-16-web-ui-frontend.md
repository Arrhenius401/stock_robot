# Web UI 前端产品化（子项目 B）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Web UI 做成产品形态：聊天 SSE 流式 + 计划/进度展示、个股/指数报告页、会话管理侧边栏，零构建暗色金融终端风格。

**Architecture:** SPA 单页（index.html + ES Modules），FastAPI StaticFiles 挂载不变；4 项后端小改动（结构化 tool_results、会话消息端点、index 多指数+compare、change_pct）先行 TDD 完成，前端消费契约。

**Tech Stack:** 原生 HTML/CSS/JS（无构建链）、FastAPI、pytest。

**Spec:** `docs/superpowers/specs/2026-08-16-web-ui-frontend-design.md`

**相对 spec 的一处微调：** 新增 `js/state.js`（store/bus/switchView 独立成模块），避免入口模块 app.js 与组件模块的循环导入（ESM 循环引用在子代理实现时易踩 TDZ 陷阱）。

**测试命令约定（项目 CLAUDE.md）：** pytest 必须用 `.venv/Scripts/python -m pytest`；ruff 用 VS Code 捆绑版 `"/c/Users/25618/.vscode/extensions/charliermarsh.ruff-2026.72.0-win32-x64/bundled/libs/bin/ruff.exe"`；pyright 直接 `pyright`。前端 JS/CSS 无静态检查工具，以浏览器手工验证为准。

---

## 文件结构

```
src/api/static/
├── index.html          # SPA 壳：顶栏 + 侧边栏 + 三视图容器（重写）
├── css/app.css         # 全部样式（新建）
└── js/
    ├── state.js        # store/bus/switchView（新建，spec 微调）
    ├── app.js          # 入口：导航接线 + 初始化（重写）
    ├── api.js          # fetch 封装 + SSE 消费（新建）
    ├── markdown.js     # 轻量 markdown 渲染（新建）
    ├── components.js   # DOM 工具 + 共享组件（新建）
    ├── sessions.js     # 侧边栏会话列表（新建）
    ├── chat.js         # 聊天视图（新建）
    ├── report.js       # 个股报告视图（新建）
    └── indexview.js    # 指数视图（新建）

src/api/app.py          # 改动 1/2/3/4
src/api/sessions.py     # 改动 2（SessionManager.get_messages）
src/report/scoring.py   # 改动 4（change_pct）
```

**模块依赖方向（单向，无循环）：**
```
state.js → 无依赖
api.js / markdown.js / components.js → 无依赖
report.js / indexview.js → state.js, api.js, markdown.js, components.js
chat.js → 上述 + report.js（报告链接）
sessions.js → state.js, api.js, chat.js, components.js
app.js → 全部
```

---

### Task 1: chat/stream 响应结构化 tool_results（TDD）

**Files:**
- Modify: `src/api/app.py:18-20`（模块级 helper）、`:59-91`（chat）、`:93-150`（chat_stream）
- Modify: `tests/api/test_app.py`（更新 1 处既有断言 + 新增 3 个测试）

- [ ] **Step 1.1: 更新既有测试断言（tool_results 从文本列表变结构化）**

`tests/api/test_app.py` 中 `test_chat_runs_agent_end_to_end` 的断言：

```python
        assert data["plan"]["steps"][0]["status"] == "done"
        assert any("echo" in t for t in data["tool_results"])
```

替换为：

```python
        assert data["plan"]["steps"][0]["status"] == "done"
        tools = data["tool_results"]
        assert len(tools) == 1
        assert tools[0]["tool"] == "echo"
        assert tools[0]["status"] == "done"
        assert tools[0]["symbol"] is None
        assert "echo" in tools[0]["content"]
```

- [ ] **Step 1.2: 新增 symbol 提取测试（需要带 6 位代码描述 + 可匹配的股票工具）**

在 `tests/api/test_app.py` 的 `EchoTool` 类之后新增：

```python
class SymbolLLM:
    """返回含 6 位股票代码步骤描述的 LLM"""

    def generate(self, prompt, system=None, **kwargs):
        return json.dumps({
            "goal": "分析股票估值",
            "complexity": "simple",
            "steps": [{"id": "step-1", "description": "分析 000001 的估值"}],
        }, ensure_ascii=False)


class StockTool:
    name = "analyze_stock"
    description = "分析股票的基本面与估值数据，输入股票代码"
    parameters = {"type": "object", "properties": {"symbol": {"type": "string"}}}
    tags = ["pipeline"]
    source = "pipeline"

    async def execute(self, **kwargs):
        return ToolResult(status="success", data={"symbol": kwargs.get("symbol", "")})


def make_symbol_core():
    """注册 StockTool + SymbolLLM 的核心，验证 tool_args symbol 提取链路"""
    from typing import Any, cast
    registry = ToolRegistry()
    registry.register(StockTool())
    return AgentCore(registry=registry, pipeline=cast(Any, FakePipeline()),
                     index_pipeline=cast(Any, FakeIndexPipeline()),
                     llm=cast(Any, SymbolLLM()))
```

在 `TestChatEndpoint` 内新增：

```python
    @pytest.mark.asyncio
    async def test_chat_tool_results_include_symbol(self, tmp_path):
        store = SessionStore(tmp_path / "sessions_sym.db")
        sessions = SessionManager(store, facts_path=tmp_path / "facts_sym.json")
        app_sym = create_app(core=make_symbol_core(), sessions=sessions)
        transport = ASGITransport(app=app_sym)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/v1/chat",
                                json={"message": "分析 000001 的估值"})
        assert resp.status_code == 200
        tools = resp.json()["tool_results"]
        assert len(tools) == 1
        assert tools[0]["tool"] == "analyze_stock"
        assert tools[0]["symbol"] == "000001"
        assert tools[0]["status"] == "done"
        assert "000001" in tools[0]["content"]
```

- [ ] **Step 1.3: 新增 stream result 事件测试**

在 `TestStreamEndpoint` 内新增：

```python
    @pytest.mark.asyncio
    async def test_stream_result_includes_tool_results(self, client):
        async with client.stream("POST", "/api/v1/chat/stream",
                                 json={"message": "echo 测试"}) as resp:
            assert resp.status_code == 200
            body = ""
            async for line in resp.aiter_lines():
                body += line
        assert '"type": "result"' in body
        assert '"tool_results"' in body
        assert '"tool": "echo"' in body
```

- [ ] **Step 1.4: 运行测试确认失败**

Run: `.venv/Scripts/python -m pytest tests/api/test_app.py -q`
Expected: FAIL（既有断言 `"echo" in dict` 失败 + 两个新测试失败）

- [ ] **Step 1.5: 实现 `_structured_tool_results` 并接线**

`src/api/app.py` 的 `_json_safe` 之后新增模块级 helper：

```python
def _structured_tool_results(plan, memory) -> list[dict]:
    """从计划步骤与 memory 工具消息按执行顺序配对出结构化结果

    Executor 只在步骤成功时写 role=="tool" 消息，且与 DONE 步骤一一对应，
    按顺序 pop 配对即可；失败步骤 content 为 None。
    """
    tool_msgs = [m["content"] for m in memory.messages if m["role"] == "tool"]
    results = []
    for step in plan.steps:
        if not step.tool_name:
            continue
        content = None
        if step.status == TaskStatus.DONE and tool_msgs:
            content = tool_msgs.pop(0)
        results.append({
            "tool": step.tool_name,
            "symbol": (step.tool_args or {}).get("symbol"),
            "status": step.status.value,
            "content": content,
        })
    return results
```

chat 端点中原行：

```python
            tool_results = [m["content"] for m in memory.messages if m["role"] == "tool"]
```

替换为：

```python
            tool_results = _structured_tool_results(plan, memory)
```

chat_stream 的 `run_agent` 内 result 事件，原行：

```python
                    await queue.put({"type": "result",
                                     "summary": f"目标: {plan.goal}\n完成: {done}/{total} 步骤"})
```

替换为：

```python
                    await queue.put({"type": "result",
                                     "summary": f"目标: {plan.goal}\n完成: {done}/{total} 步骤",
                                     "tool_results": _structured_tool_results(plan, memory)})
```

- [ ] **Step 1.6: 运行测试确认通过**

Run: `.venv/Scripts/python -m pytest tests/api/test_app.py -q`
Expected: PASS（全部）

- [ ] **Step 1.7: 提交**

```bash
git add src/api/app.py tests/api/test_app.py
git commit -m "feat(API): chat/stream 响应结构化 tool_results（含 symbol）"
```

---

### Task 2: 会话历史消息端点 GET /api/v1/sessions/{id}/messages（TDD）

**Files:**
- Modify: `src/api/sessions.py:134-139`（SessionManager.get_messages）
- Modify: `src/api/app.py:246-250`（sessions 区新增端点）
- Modify: `tests/api/test_app.py`（新增测试类）

> 注：`SessionStore.get_messages()` 已存在（sessions.py:71-77），无需改存储层。

- [ ] **Step 2.1: 写失败测试**

`tests/api/test_app.py` 的 `TestSessionsEndpoints` 内新增：

```python
    @pytest.mark.asyncio
    async def test_get_messages_returns_history(self, client):
        r = await client.post("/api/v1/chat", json={"message": "echo 测试"})
        sid = r.json()["session_id"]
        resp = await client.get(f"/api/v1/sessions/{sid}/messages")
        assert resp.status_code == 200
        msgs = resp.json()["messages"]
        roles = [m["role"] for m in msgs]
        assert "user" in roles
        assert "tool" in roles
        assert all("content" in m and "role" in m for m in msgs)

    @pytest.mark.asyncio
    async def test_get_messages_unknown_session_returns_404(self, client):
        resp = await client.get("/api/v1/sessions/nope/messages")
        assert resp.status_code == 404
```

`TestNoCoreMode` 内新增：

```python
    @pytest.mark.asyncio
    async def test_messages_returns_503(self, empty_client):
        resp = await empty_client.get("/api/v1/sessions/any/messages")
        assert resp.status_code == 503
```

- [ ] **Step 2.2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/api/test_app.py::TestSessionsEndpoints::test_get_messages_returns_history -q`
Expected: FAIL（404，端点不存在）

- [ ] **Step 2.3: SessionManager 加 get_messages**

`src/api/sessions.py` 的 `SessionManager.list_sessions` 之后新增：

```python
    def get_messages(self, session_id: str) -> list[dict] | None:
        """按会话读回持久化消息；会话不存在返回 None"""
        if not self._store.session_exists(session_id):
            return None
        return self._store.get_messages(session_id)
```

- [ ] **Step 2.4: app.py 新增端点**

`src/api/app.py` 的 `list_sessions` 端点之后新增：

```python
    @app.get("/api/v1/sessions/{session_id}/messages")
    async def get_session_messages(session_id: str):
        if sessions is None:
            raise HTTPException(status_code=503, detail="会话管理未初始化")
        messages = sessions.get_messages(session_id)
        if messages is None:
            raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
        return JSONResponse({"messages": messages})
```

- [ ] **Step 2.5: 运行确认通过**

Run: `.venv/Scripts/python -m pytest tests/api/test_app.py -q`
Expected: PASS

- [ ] **Step 2.6: 提交**

```bash
git add src/api/sessions.py src/api/app.py tests/api/test_app.py
git commit -m "feat(API): 新增会话历史消息端点 GET /sessions/{id}/messages"
```

---

### Task 3: index 端点多指数 + compare 返回（TDD）

**Files:**
- Modify: `src/api/app.py:204-244`（index 端点重写）
- Modify: `tests/api/test_app.py`（FakeIndexPipeline 补 compare、新增 4 测试、扩展 1 断言）

- [ ] **Step 3.1: 更新 FakeIndexPipeline 支持 compare**

`tests/api/test_app.py` 的 `FakeIndexPipeline` 替换为：

```python
class FakeIndexPipeline:
    def run(self, targets, on_progress=None):
        from types import SimpleNamespace
        compare = None
        if len(targets) >= 2:
            compare = SimpleNamespace(
                headers=["指数", "收盘"],
                rows=[{"指数": "000300", "收盘": 3854},
                      {"指数": "000905", "收盘": 5921}],
            )
        return SimpleNamespace(
            reports=[FakeIndexReport() for _ in targets],
            compare=compare, errors=[],
        )
```

- [ ] **Step 3.2: 写失败测试**

`tests/api/test_app.py` 的 `TestIndexEndpoint` 内新增：

```python
    @pytest.mark.asyncio
    async def test_index_multi_symbols_returns_compare(self, client, mocker):
        from types import SimpleNamespace
        mocker.patch("data.index_mapping.IndexMapping.lookup",
                     return_value=SimpleNamespace(
                         name="测试指数", market="a-shares", index_style="broad"))
        resp = await client.post("/api/v1/index",
                                 json={"symbols": ["000300", "000905"]})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["reports"]) == 2
        assert data["compare"]["headers"] == ["指数", "收盘"]
        assert len(data["compare"]["rows"]) == 2
        assert data["errors"] == []

    @pytest.mark.asyncio
    async def test_index_symbols_space_string(self, client, mocker):
        from types import SimpleNamespace
        mocker.patch("data.index_mapping.IndexMapping.lookup",
                     return_value=SimpleNamespace(
                         name="测试指数", market="a-shares", index_style="broad"))
        resp = await client.post("/api/v1/index",
                                 json={"symbols": "000300 000905"})
        assert resp.status_code == 200
        assert len(resp.json()["reports"]) == 2

    @pytest.mark.asyncio
    async def test_index_mixed_valid_invalid(self, client):
        resp = await client.post("/api/v1/index",
                                 json={"symbols": ["000300", "###"]})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["reports"]) == 1
        assert any("无效的指数代码" in e for e in data["errors"])

    @pytest.mark.asyncio
    async def test_index_all_invalid_returns_422(self, client):
        resp = await client.post("/api/v1/index", json={"symbols": ["###"]})
        assert resp.status_code == 422
```

既有 `test_index_returns_report_json` 追加一行断言：

```python
        assert data["errors"] == []
```
之后加：

```python
        assert data["compare"] is None
```

- [ ] **Step 3.3: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/api/test_app.py::TestIndexEndpoint -q`
Expected: FAIL（symbols 参数被忽略/compare 缺失）

- [ ] **Step 3.4: 重写 index 端点**

`src/api/app.py` 的 index 端点整体替换为：

```python
    @app.post("/api/v1/index")
    async def index(request: Request):
        body = await request.json()
        raw_symbols = body.get("symbols")
        if raw_symbols is None:
            raw_symbols = [str(body.get("symbol", "")).strip()]
        elif isinstance(raw_symbols, str):
            raw_symbols = raw_symbols.replace(",", " ").split()
        else:
            raw_symbols = [str(s).strip() for s in raw_symbols]
        symbols = [s for s in raw_symbols if s]
        if not symbols:
            raise HTTPException(status_code=422, detail="symbol 不能为空")
        if core is None:
            raise HTTPException(status_code=503, detail="Agent 核心未注入")

        from data.index_mapping import IndexMapping
        from data.schemas import AnalysisTarget
        from utils.symbols import normalize_index_symbol, validate_index_symbol

        index_style = body.get("index_style")
        mapping = IndexMapping()
        targets: list[AnalysisTarget] = []
        errors: list[str] = []
        for sym in symbols:
            if not validate_index_symbol(sym):
                errors.append(f"无效的指数代码: {sym}")
                continue
            normalized = normalize_index_symbol(sym)
            entry = mapping.lookup(normalized)
            if entry is None:
                if index_style not in ("broad", "sector", "overseas"):
                    errors.append(
                        f"无法识别指数 {normalized}，请指定 index_style (broad/sector/overseas)")
                    continue
                targets.append(AnalysisTarget(
                    target_type="index", symbol=normalized, name=normalized,
                    market="a-shares", index_style=index_style))
            else:
                targets.append(AnalysisTarget(
                    target_type="index", symbol=normalized, name=entry.name,
                    market=entry.market, index_style=entry.index_style))
        if not targets:
            raise HTTPException(status_code=422, detail="；".join(errors))

        try:
            result = await asyncio.to_thread(core.index_pipeline.run, targets)
            compare = None
            if result.compare is not None:
                compare = {"headers": result.compare.headers,
                           "rows": result.compare.rows}
            payload = {
                "reports": [r.model_dump(mode="json") for r in result.reports],
                "compare": compare,
                "errors": result.errors + errors,
            }
            return JSONResponse(_json_safe(payload))
        except Exception as e:  # noqa: BLE001 — HTTP 边界兜底
            logger.error("指数分析失败: %s", e)
            return JSONResponse({"symbol": symbols[0], "error": str(e)}, status_code=500)
```

- [ ] **Step 3.5: 运行确认通过**

Run: `.venv/Scripts/python -m pytest tests/api/test_app.py::TestIndexEndpoint -q`
Expected: PASS

- [ ] **Step 3.6: 提交**

```bash
git add src/api/app.py tests/api/test_app.py
git commit -m "feat(API): index 端点支持多指数输入并返回 compare 对比表"
```

---

### Task 4: compute_price_info 补 change_pct + analyze overview（TDD）

**Files:**
- Modify: `src/report/scoring.py:65-77`（compute_price_info）
- Modify: `src/api/app.py:185-191`（analyze overview）
- Modify: `tests/report/test_scoring.py`、`tests/api/test_app.py`

- [ ] **Step 4.1: 写失败测试**

`tests/report/test_scoring.py` 的 `_price` helper 保持不动，`TestComputePriceInfo` 内新增：

```python
    def test_change_pct_from_latest_price(self):
        from data.schemas import PriceData
        prices = [_price(10.0, 2.0, 4.0),
                  PriceData(symbol="000001", trade_date=date(2026, 1, 3),
                            open=6.0, high=7.0, low=5.0, close=6.0,
                            volume=1000, change_pct=1.15)]
        info = compute_price_info(_ctx(price_data=prices))
        assert info["change_pct"] == 1.15
```

`test_no_price_data` 追加一行：

```python
        assert info["price_position"] == "暂无"
```
之后加：

```python
        assert info["change_pct"] is None
```

`tests/api/test_app.py` 的 `test_analyze_returns_report_json` 追加：

```python
        assert data["commentary"] == "AI 解读"
```
之后加：

```python
        assert "change_pct" in data["overview"]
        assert data["overview"]["change_pct"] is None  # FakePipeline 无价格数据
```

- [ ] **Step 4.2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/report/test_scoring.py tests/api/test_app.py::TestAnalyzeEndpoint::test_analyze_returns_report_json -q`
Expected: FAIL（KeyError / 断言失败）

- [ ] **Step 4.3: 实现**

`src/report/scoring.py` 的 `compute_price_info` 中，原行：

```python
    latest_price = price_data[-1].close if price_data else None
```

之后加：

```python
    change_pct = price_data[-1].change_pct if price_data else None
```

返回字典原行：

```python
    return {"year_high": year_high, "year_low": year_low,
            "latest_price": latest_price, "price_position": price_position}
```

替换为：

```python
    return {"year_high": year_high, "year_low": year_low,
            "latest_price": latest_price, "price_position": price_position,
            "change_pct": change_pct}
```

`src/api/app.py` 的 analyze 端点 overview 字典中，原行：

```python
                    "price_position": price_info["price_position"],
```

之后加：

```python
                    "change_pct": price_info["change_pct"],
```

- [ ] **Step 4.4: 运行确认通过**

Run: `.venv/Scripts/python -m pytest tests/report/test_scoring.py tests/api/test_app.py -q`
Expected: PASS

- [ ] **Step 4.5: 提交**

```bash
git add src/report/scoring.py src/api/app.py tests/report/test_scoring.py tests/api/test_app.py
git commit -m "feat(报告): 价格信息补充涨跌幅 change_pct，analyze 端点透出"
```

---

### Task 5: 前端骨架 — index.html / app.css / state.js / app.js / api.js

**Files:**
- Rewrite: `src/api/static/index.html`
- Create: `src/api/static/css/app.css`
- Create: `src/api/static/js/state.js`、`src/api/static/js/app.js`、`src/api/static/js/api.js`
- Test: `tests/api/test_static.py`（新建）

- [ ] **Step 5.1: 写静态服务冒烟测试**

新建 `tests/api/test_static.py`：

```python
"""静态 Web UI 冒烟测试 — 页面与关键资源可访问"""
import pytest
from httpx import ASGITransport, AsyncClient

from api.app import create_app


@pytest.fixture
async def client():
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestStaticUI:
    @pytest.mark.asyncio
    async def test_index_html_served(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
        assert "Stock Robot" in resp.text

    @pytest.mark.asyncio
    async def test_js_modules_served(self, client):
        for path in ("/js/app.js", "/js/api.js", "/js/state.js",
                     "/js/chat.js", "/js/sessions.js", "/js/report.js",
                     "/js/indexview.js", "/js/markdown.js", "/js/components.js"):
            resp = await client.get(path)
            assert resp.status_code == 200, path

    @pytest.mark.asyncio
    async def test_css_served(self, client):
        resp = await client.get("/css/app.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers.get("content-type", "")
```

- [ ] **Step 5.2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/api/test_static.py -q`
Expected: FAIL（404，文件不存在）

- [ ] **Step 5.3: 重写 index.html**

`src/api/static/index.html` 全文替换：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Stock Robot — AI 股票分析助手</title>
<link rel="stylesheet" href="/css/app.css">
</head>
<body>
<header class="topbar">
  <div class="logo"><span class="dot"></span>Stock Robot</div>
  <div class="entries">
    <div class="entry">
      <label for="stockInput">个股分析</label>
      <input id="stockInput" placeholder="600519">
      <button id="stockBtn">分析</button>
    </div>
    <div class="entry">
      <label for="indexInput">指数分析</label>
      <input id="indexInput" placeholder="000300 000905">
      <button id="indexBtn">分析</button>
    </div>
  </div>
</header>
<div class="layout">
  <aside class="sidebar" id="sidebar">
    <div class="sidebar-top">
      <button class="new-session" id="newSessionBtn"><span class="plus">＋</span><span class="txt"> 新建会话</span></button>
      <button class="collapse-btn" id="collapseBtn" title="折叠/展开侧边栏">«</button>
    </div>
    <div class="session-list" id="sessionList"></div>
    <nav class="nav">
      <div class="nav-item on" data-view="chat">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
        <span>聊天</span>
      </div>
      <div class="nav-item" data-view="report">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 3v18h18"/><path d="M7 14l4-4 4 3 5-6"/></svg>
        <span>个股报告</span>
      </div>
      <div class="nav-item" data-view="index">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 6l-9.5 9.5-5-5L1 18"/><path d="M17 6h6v6"/></svg>
        <span>指数分析</span>
      </div>
    </nav>
  </aside>
  <main class="main">
    <div class="view active" id="view-chat">
      <div class="chat-scroll" id="chatScroll"></div>
      <div class="quick-bar">
        <button id="quickTiming">择时研判</button>
        <button id="quickTools">工具列表</button>
        <button id="quickClear">清空会话</button>
      </div>
      <div class="chat-input">
        <input id="chatInput" placeholder="输入你的投资研究问题...">
        <button id="sendBtn">发送</button>
      </div>
    </div>
    <div class="view" id="view-report">
      <div id="reportContent" class="report-content"></div>
    </div>
    <div class="view" id="view-index">
      <div id="indexContent" class="report-content"></div>
    </div>
  </main>
</div>
<script type="module" src="/js/app.js"></script>
</body>
</html>
```

- [ ] **Step 5.4: 新建 state.js**

`src/api/static/js/state.js`：

```js
// 全局状态与视图切换 — 组件模块唯一共享入口（避免与 app.js 循环导入）
export const store = {
  currentSessionId: null,
  currentView: "chat",
  reportCache: {},        // symbol -> analyze 报告 JSON
  sessionMessages: {},    // sid -> [{role, content}]
};

export const bus = new EventTarget();

export function switchView(name) {
  store.currentView = name;
  document.querySelectorAll(".view").forEach((v) => {
    v.classList.toggle("active", v.id === `view-${name}`);
  });
  document.querySelectorAll(".nav-item").forEach((n) => {
    n.classList.toggle("on", n.dataset.view === name);
  });
}
```

- [ ] **Step 5.5: 新建 api.js**

`src/api/static/js/api.js`：

```js
// HTTP 封装：JSON 请求 + SSE 事件流消费

async function request(path, options = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data.detail || data.error || `HTTP ${resp.status}`);
  }
  return data;
}

export async function consumeSSE(url, body, handlers) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok || !resp.body) {
    throw new Error(`HTTP ${resp.status}`);
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
      for (const line of frame.split("\n")) {
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
```

- [ ] **Step 5.6: 新建 app.js**

`src/api/static/js/app.js`：

```js
// 入口：导航接线 + 各视图初始化
import { switchView } from "./state.js";
import { initChat } from "./chat.js";
import { initReportView } from "./report.js";
import { initIndexView } from "./indexview.js";
import { initSessions, initSessionStartup } from "./sessions.js";

function init() {
  document.querySelectorAll(".nav-item").forEach((n) => {
    n.addEventListener("click", () => switchView(n.dataset.view));
  });
  initChat();
  initReportView();
  initIndexView();
  initSessions();
  initSessionStartup().catch((e) => console.error("会话初始化失败:", e));
}

init();
```

- [ ] **Step 5.7: 新建 app.css**

`src/api/static/css/app.css`（完整样式，见下；覆盖本计划所有组件类名）：

```css
/* ============ 基础 ============ */
* { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #0d1017; --panel: #131722; --panel-2: #20263a; --border: #262b36;
  --border-soft: #1f2430; --accent: #e5484d; --accent-2: #f76b15;
  --text: #e6e8ee; --text-2: #c8ced9; --text-3: #a6adbb; --text-4: #7d8598;
  --ok: #3fb68b; --warn: #f5a623; --blue: #7fb3ff;
}
html, body { height: 100%; }
body {
  background: var(--bg); color: var(--text-2);
  font-family: Inter, -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 13px; display: flex; flex-direction: column; overflow: hidden;
}
button { font-family: inherit; }

/* ============ 顶栏 ============ */
.topbar { background: var(--panel); border-bottom: 1px solid var(--border-soft);
          padding: 10px 16px; display: flex; align-items: center; gap: 12px; flex-shrink: 0; }
.logo { display: flex; align-items: center; gap: 8px; color: var(--text);
        font-weight: 650; font-size: 14px; letter-spacing: .2px; }
.logo .dot { width: 9px; height: 9px; border-radius: 3px;
             background: linear-gradient(135deg, var(--accent), var(--accent-2)); }
.entries { margin-left: auto; display: flex; gap: 10px; }
.entry { display: flex; align-items: center; gap: 6px; background: var(--bg);
         border: 1px solid var(--border); border-radius: 8px; padding: 4px 6px 4px 10px; }
.entry label { color: var(--text-4); font-size: 11px; white-space: nowrap; }
.entry input { background: transparent; border: none; outline: none; color: var(--text);
               width: 72px; font-size: 12px; font-variant-numeric: tabular-nums; }
.entry button { background: var(--accent); color: #fff; border: none; border-radius: 6px;
                padding: 5px 12px; font-size: 11.5px; font-weight: 600; cursor: pointer; }
.entry button:hover { background: #d93a3f; }

/* ============ 布局 ============ */
.layout { flex: 1; display: flex; min-height: 0; }

/* ============ 侧边栏 ============ */
.sidebar { width: 188px; background: var(--panel); border-right: 1px solid var(--border-soft);
           display: flex; flex-direction: column; padding: 12px 10px; flex-shrink: 0;
           transition: width .15s; }
.sidebar.collapsed { width: 56px; padding: 12px 6px; }
.sidebar-top { display: flex; gap: 6px; margin-bottom: 8px; }
.new-session { flex: 1; background: var(--accent); color: #fff; border: none; border-radius: 8px;
               padding: 7px; font-size: 12px; font-weight: 600; cursor: pointer; }
.new-session:hover { background: #d93a3f; }
.collapse-btn { background: var(--bg); color: var(--text-4); border: 1px solid var(--border);
                border-radius: 8px; padding: 0 9px; cursor: pointer; font-size: 13px; }
.collapse-btn:hover { color: var(--text); }
.sidebar.collapsed .new-session .txt { display: none; }
.sidebar.collapsed .new-session { padding: 7px 0; }

.session-list { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; }
.sessions-empty { color: var(--text-4); font-size: 11.5px; padding: 8px 10px; line-height: 1.6; }
.sess { padding: 8px 10px; border-radius: 8px; color: var(--text-3); font-size: 12px;
        cursor: pointer; display: flex; justify-content: space-between; align-items: center;
        gap: 6px; transition: background .12s; }
.sess:hover { background: #1a1f2b; color: var(--text-2); }
.sess.active { background: var(--panel-2); color: var(--text); box-shadow: inset 2px 0 0 var(--accent); }
.sess .t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.sess .x { opacity: 0; color: #5c6470; font-size: 11px; }
.sess:hover .x { opacity: 1; }
.sess .x:hover { color: var(--accent); }
.sidebar.collapsed .sess { justify-content: center; padding: 8px 0; }
.sidebar.collapsed .sess .t { display: none; }
.sidebar.collapsed .sess .x { display: none; }

.nav { margin-top: auto; border-top: 1px solid var(--border-soft); padding-top: 8px;
       display: flex; flex-direction: column; gap: 2px; }
.nav-item { display: flex; align-items: center; gap: 8px; padding: 7px 10px; border-radius: 8px;
            color: var(--text-4); font-size: 12px; cursor: pointer; }
.nav-item:hover { color: var(--text-2); background: #1a1f2b; }
.nav-item.on { color: #fff; background: var(--panel-2); }
.nav-item svg { width: 14px; height: 14px; flex-shrink: 0; }
.sidebar.collapsed .nav-item { justify-content: center; padding: 7px 0; }
.sidebar.collapsed .nav-item span { display: none; }

/* ============ 主区与视图 ============ */
.main { flex: 1; min-width: 0; display: flex; }
.view { display: none; flex: 1; min-width: 0; flex-direction: column; }
.view.active { display: flex; }

/* ============ 聊天视图 ============ */
#view-chat { padding: 16px; gap: 10px; }
.chat-scroll { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 12px;
               padding: 4px 0; }
.msg { border-radius: 12px; padding: 10px 14px; line-height: 1.65; max-width: 88%;
       font-size: 13px; }
.msg.user { background: var(--panel-2); margin-left: auto; color: var(--text);
            border: 1px solid #2a3145; }
.msg.agent { background: var(--panel); border: 1px solid var(--border-soft);
             color: var(--text-2); align-self: flex-start; }
.msg .content { display: flex; flex-direction: column; gap: 4px; }

/* markdown 渲染 */
.md { line-height: 1.7; }
.md h1, .md h2, .md h3, .md h4 { color: var(--text); margin: 8px 0 4px; font-size: 1.05em; }
.md p { margin: 4px 0; }
.md ul { margin: 4px 0; padding-left: 18px; }
.md code { background: var(--bg); border: 1px solid var(--border); border-radius: 4px;
           padding: 1px 5px; font-size: 12px; }
.md pre { background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
          padding: 10px; overflow-x: auto; margin: 6px 0; }
.md pre code { background: none; border: none; padding: 0; }
.md table { border-collapse: collapse; margin: 6px 0; width: 100%; }
.md th { color: var(--text-4); font-size: 11px; font-weight: 600; text-align: left;
         padding: 6px 8px; border-bottom: 1px solid var(--border); }
.md td { color: var(--text-2); font-size: 12px; padding: 7px 8px;
         border-bottom: 1px solid var(--border-soft); font-variant-numeric: tabular-nums; }
.md a { color: var(--accent); }
.md hr { border: none; border-top: 1px solid var(--border-soft); margin: 8px 0; }

/* 计划卡与工具结果卡 */
.card { background: var(--bg); border: 1px solid var(--border); border-radius: 10px;
        padding: 10px 12px; margin-top: 8px; }
.card-title { color: var(--text-4); font-size: 10.5px; font-weight: 650;
              text-transform: uppercase; letter-spacing: .6px; margin-bottom: 6px; }
.goal { color: var(--text); font-size: 12.5px; margin-bottom: 8px; }
.step { display: flex; justify-content: space-between; align-items: center;
        color: var(--text-3); font-size: 12px; padding: 4px 0; }
.step-left { display: flex; align-items: center; gap: 8px; min-width: 0; }
.step-desc { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.dot.wait { background: #3a4152; }
.dot.run { background: var(--warn); box-shadow: 0 0 6px rgba(245,166,35,.6); animation: pulse 1.2s infinite; }
.dot.done { background: var(--ok); box-shadow: 0 0 6px rgba(63,182,139,.6); }
.dot.fail { background: var(--accent); }
@keyframes pulse { 50% { opacity: .35; } }
.step-status { font-size: 11px; color: var(--text-4); flex-shrink: 0; }
.step-status.run { color: var(--warn); }
.step-status.done { color: var(--ok); }
.step-status.fail { color: var(--accent); }
.chip { display: inline-block; background: var(--panel-2); color: var(--blue); border-radius: 5px;
        padding: 1px 7px; font-size: 10.5px; font-weight: 600; margin-bottom: 6px; }
.tooltext { color: var(--text-3); font-size: 12px; }
.link { color: var(--accent); font-size: 12px; font-weight: 600; margin-top: 8px;
        cursor: pointer; display: inline-block; }
.link:hover { color: var(--accent-2); }
.tool-row { display: flex; align-items: baseline; gap: 8px; padding: 4px 0; }
.tool-row .chip { margin-bottom: 0; flex-shrink: 0; }
.tool-desc { color: var(--text-3); font-size: 11.5px; }
.thinking { color: var(--text-4); font-size: 12.5px; animation: pulse 1.5s infinite; }

/* 错误与中断 */
.error-card { border: 1px solid rgba(229,72,77,.4); background: rgba(229,72,77,.08);
              border-radius: 10px; padding: 10px 12px; margin-top: 8px; }
.error-title { color: var(--accent); font-size: 12px; font-weight: 650; margin-bottom: 4px; }
.error-msg { color: var(--text-2); font-size: 12px; line-height: 1.6; word-break: break-all; }
.btn-retry { margin-top: 8px; background: var(--accent); color: #fff; border: none;
             border-radius: 6px; padding: 5px 14px; font-size: 11.5px; cursor: pointer; }

/* 快捷按钮与输入 */
.quick-bar { display: flex; gap: 6px; flex-shrink: 0; }
.quick-bar button { border: 1px solid #2a3145; color: var(--text-4); background: transparent;
                    border-radius: 20px; padding: 4px 12px; font-size: 11px; cursor: pointer;
                    transition: all .12s; }
.quick-bar button:hover { color: var(--text); border-color: #3a4152; background: #1a1f2b; }
.chat-input { display: flex; gap: 8px; align-items: center; flex-shrink: 0; }
.chat-input input { flex: 1; background: var(--bg); border: 1px solid var(--border);
                    color: var(--text); border-radius: 10px; padding: 10px 14px;
                    font-size: 13px; outline: none; transition: border .12s; }
.chat-input input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(229,72,77,.12); }
.chat-input button { background: var(--accent); color: #fff; border: none; border-radius: 10px;
                     padding: 10px 18px; font-size: 13px; font-weight: 600; cursor: pointer; }
.chat-input button:hover { background: #d93a3f; }

/* ============ 报告/指数视图 ============ */
.report-content { max-width: 860px; margin: 0 auto; padding: 18px 16px 40px; flex: 1;
                  overflow-y: auto; display: flex; flex-direction: column; gap: 12px; width: 100%; }
.report-header { display: flex; align-items: baseline; gap: 10px; padding: 4px 2px; }
.stock-name { color: var(--text); font-size: 18px; font-weight: 650; }
.stock-code { color: var(--text-4); font-size: 12px; }
.chip.ind { margin-bottom: 0; }
.price-box { margin-left: auto; display: flex; align-items: baseline; gap: 8px; }
.stock-price { color: var(--text); font-size: 18px; font-weight: 650;
               font-variant-numeric: tabular-nums; }
.chg { font-size: 12px; font-variant-numeric: tabular-nums; }
.chg.up { color: var(--accent); }   /* A 股习惯：红涨 */
.chg.down { color: var(--ok); }     /* 绿跌 */
.chg.flat { color: var(--text-4); }

.panel { background: var(--panel); border: 1px solid var(--border-soft); border-radius: 12px;
         padding: 12px 16px; }
.panel-title { color: var(--text-4); font-size: 10.5px; font-weight: 650;
               text-transform: uppercase; letter-spacing: .6px; margin-bottom: 8px; }
.kv { display: flex; gap: 18px; flex-wrap: wrap; }
.kv .k { color: var(--text-4); font-size: 10.5px; }
.kv .v { color: var(--text); font-size: 13px; font-weight: 600; margin-top: 2px;
         font-variant-numeric: tabular-nums; }
.bar { height: 6px; border-radius: 3px; background: var(--border-soft); overflow: hidden;
       margin-top: 6px; }
.bar i { display: block; height: 100%; border-radius: 3px;
         background: linear-gradient(90deg, var(--accent), var(--accent-2)); }

/* 评分卡 */
.score-big { display: flex; align-items: center; gap: 16px; }
.score-num { font-size: 34px; font-weight: 700; color: var(--text);
             font-variant-numeric: tabular-nums; }
.score-num small { font-size: 13px; color: var(--text-4); font-weight: 500; }
.score-rows { flex: 1; display: flex; flex-direction: column; gap: 5px; }
.score-row { display: flex; align-items: center; gap: 8px; font-size: 11.5px; }
.score-row .lb { width: 64px; color: var(--text-3); flex-shrink: 0; }
.score-row .v { width: 26px; color: var(--text); font-weight: 600; flex-shrink: 0;
                font-variant-numeric: tabular-nums; }
.score-row .wt { width: 34px; color: var(--text-4); text-align: right; flex-shrink: 0; }
.score-row .bar { flex: 1; margin-top: 0; }
.score-note { color: var(--warn); font-size: 11px; margin-top: 2px; }

/* 维度卡 */
.dim-head { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.dim-name { color: var(--text); font-size: 13px; font-weight: 650; }
.dim-score { margin-left: auto; font-size: 16px; font-weight: 700; color: var(--text);
             font-variant-numeric: tabular-nums; }
.dim-sum { color: var(--text-3); line-height: 1.6; font-size: 12px; }
.mtr { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 8px; }
.mtr .k { color: var(--text-4); font-size: 10px; }
.mtr .v { color: var(--text-2); font-size: 11.5px; margin-top: 1px;
          font-variant-numeric: tabular-nums; }
.flag { display: inline-block; background: rgba(229,72,77,.12); color: var(--accent);
        border-radius: 5px; padding: 1px 7px; font-size: 10.5px; margin: 6px 4px 0 0; }

/* 徽章与标签 */
.badge { border-radius: 5px; padding: 1px 7px; font-size: 10px; font-weight: 600; }
.badge.ok { background: rgba(63,182,139,.15); color: var(--ok); }
.badge.part { background: rgba(245,166,35,.15); color: var(--warn); }
.badge.na { background: rgba(90,99,118,.25); color: var(--text-4); }
.tag { border-radius: 5px; padding: 1px 7px; font-size: 10px; font-weight: 600; }
.tag.good { background: rgba(63,182,139,.15); color: var(--ok); }
.tag.mid { background: rgba(245,166,35,.15); color: var(--warn); }
.tag.bad { background: rgba(229,72,77,.15); color: var(--accent); }
.tag.gray { background: rgba(90,99,118,.25); color: var(--text-4); }

/* LLM 解读 */
.llm { background: var(--bg); border-left: 2px solid var(--accent);
       border-radius: 0 10px 10px 0; padding: 12px 16px; }
.llm-title { color: var(--text-4); font-size: 10.5px; font-weight: 650;
             text-transform: uppercase; letter-spacing: .6px; margin-bottom: 6px; }

/* 仓位系数 */
.coeff { display: flex; align-items: center; gap: 10px; margin-top: 8px; }
.coeff-label { color: var(--text-4); font-size: 11px; flex-shrink: 0; }
.coeff .bar { flex: 1; margin-top: 0; }
.coeff-value { color: var(--text); font-weight: 700; font-size: 12.5px; flex-shrink: 0;
               font-variant-numeric: tabular-nums; }

/* 对比表 */
.report-content table { width: 100%; border-collapse: collapse; margin-top: 4px; }
.report-content th { color: var(--text-4); font-size: 10.5px; font-weight: 600; text-align: left;
                     padding: 6px 8px; border-bottom: 1px solid var(--border); }
.report-content td { color: var(--text-2); font-size: 11.5px; padding: 7px 8px;
                     border-bottom: 1px solid var(--border-soft);
                     font-variant-numeric: tabular-nums; }
.report-content tr:last-child td { border-bottom: none; }

/* 骨架屏 */
.skeleton { display: flex; flex-direction: column; gap: 10px; padding: 10px 0; }
.sk-line { height: 14px; border-radius: 7px; background: linear-gradient(90deg,
           var(--panel) 25%, #1a1f2b 50%, var(--panel) 75%);
           background-size: 200% 100%; animation: shimmer 1.4s infinite; }
@keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }

/* 刷新按钮 */
.btn-refresh { align-self: flex-start; background: var(--panel); color: var(--text-3);
               border: 1px solid var(--border); border-radius: 8px; padding: 6px 14px;
               font-size: 12px; cursor: pointer; }
.btn-refresh:hover { color: var(--text); border-color: #3a4152; }
```

- [ ] **Step 5.8: 运行冒烟测试确认通过**

Run: `.venv/Scripts/python -m pytest tests/api/test_static.py -q`
Expected: PASS（3 个测试通过）

- [ ] **Step 5.9: 浏览器初步验证**

启动服务（后台）：`.venv/Scripts/python -c "from stock_robot.cli import api_cmd" ` 不可用；直接用：

```bash
stock-robot api &
```

浏览器打开 http://127.0.0.1:8000，确认：页面加载无 JS 控制台错误（F12 查看）、深色主题渲染、侧边栏/顶栏/聊天输入框可见。此时聊天发送会报错（chat.js 尚未实现），属预期。

- [ ] **Step 5.10: 提交**

```bash
git add src/api/static/ tests/api/test_static.py
git commit -m "feat(Web): SPA 骨架 — 布局/主题/状态/API 封装与静态冒烟测试"
```

---

### Task 6: markdown.js 轻量渲染器

**Files:**
- Create: `src/api/static/js/markdown.js`

- [ ] **Step 6.1: 实现**

`src/api/static/js/markdown.js`：

```js
// 轻量 markdown 渲染：标题/列表/表格/代码块/加粗/行内代码/链接（输入先整体转义）

function esc(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function inline(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g,
             '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

export function renderMarkdown(text) {
  if (!text) return "";
  const lines = esc(String(text)).split("\n");
  const out = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim().startsWith("```")) {
      const buf = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        buf.push(lines[i]);
        i += 1;
      }
      out.push(`<pre><code>${buf.join("\n")}</code></pre>`);
      i += 1;
      continue;
    }
    const tableHead = line.match(/^\s*\|(.+)\|\s*$/);
    if (tableHead && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
      const headers = tableHead[1].split("|").map((h) => h.trim());
      const rows = [];
      i += 2;
      while (i < lines.length) {
        const m = lines[i].match(/^\s*\|(.+)\|\s*$/);
        if (!m) break;
        rows.push(m[1].split("|").map((c) => c.trim()));
        i += 1;
      }
      const thead = `<tr>${headers.map((h) => `<th>${inline(h)}</th>`).join("")}</tr>`;
      const tbody = rows
        .map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`)
        .join("");
      out.push(`<table><thead>${thead}</thead><tbody>${tbody}</tbody></table>`);
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      const level = heading[1].length;
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      i += 1;
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(`<li>${inline(lines[i].replace(/^\s*[-*]\s+/, ""))}</li>`);
        i += 1;
      }
      out.push(`<ul>${items.join("")}</ul>`);
      continue;
    }
    if (/^\s*(---+|\*\*\*+)\s*$/.test(line)) {
      out.push("<hr>");
      i += 1;
      continue;
    }
    if (line.trim() === "") {
      i += 1;
      continue;
    }
    const para = [line];
    i += 1;
    while (i < lines.length && lines[i].trim() !== ""
           && !/^(#{1,4})\s/.test(lines[i])
           && !/^\s*[-*]\s+/.test(lines[i])
           && !lines[i].trim().startsWith("```")
           && !/^\s*\|/.test(lines[i])) {
      para.push(lines[i]);
      i += 1;
    }
    out.push(`<p>${inline(para.join("<br>"))}</p>`);
  }
  return out.join("");
}
```

- [ ] **Step 6.2: 浏览器验证**

服务运行中（Task 5 启动的仍在跑则直接刷新；已停则重新 `stock-robot api` 后打开 http://127.0.0.1:8000）。在浏览器 F12 控制台执行：

```js
import("/js/markdown.js").then((m) => {
  const html = m.renderMarkdown(
    "## 标题\n- 项目一\n- **加粗**\n\n| 列A | 列B |\n|---|---|\n| 1 | 2 |\n\n`code` 和 [链接](https://example.com)");
  console.log(html.includes("<h2>") && html.includes("<table>") && html.includes("<strong>") ? "OK" : "FAIL");
});
```

Expected: 控制台输出 `OK`。

- [ ] **Step 6.3: 提交**

```bash
git add src/api/static/js/markdown.js
git commit -m "feat(Web): 轻量 markdown 渲染器（标题/列表/表格/代码/链接）"
```

---

### Task 7: components.js 共享组件

**Files:**
- Create: `src/api/static/js/components.js`

- [ ] **Step 7.1: 实现**

`src/api/static/js/components.js`：

```js
// DOM 工具与共享组件 — 供 chat/report/indexview/sessions 复用

export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

export function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function statusBadge(status) {
  const map = { ok: ["正常", "ok"], partial: ["部分数据", "part"], unavailable: ["不可用", "na"] };
  const [text, cls] = map[status] || [String(status || "未知"), "na"];
  return el("span", `badge ${cls}`, text);
}

export function tagChip(text, kind) {
  return el("span", `tag ${kind}`, text);
}

export function priceBar(pct) {
  const wrap = el("div", "bar");
  const fill = el("i");
  const num = Number(pct);
  fill.style.width = `${Number.isFinite(num) ? Math.max(0, Math.min(100, num)) : 0}%`;
  wrap.appendChild(fill);
  return wrap;
}

export function kv(label, value) {
  const box = el("div");
  box.appendChild(el("div", "k", label));
  box.appendChild(el("div", "v", value));
  return box;
}

export function errorCard(message, onRetry) {
  const card = el("div", "error-card");
  card.appendChild(el("div", "error-title", "出错了"));
  card.appendChild(el("div", "error-msg", message));
  if (onRetry) {
    const btn = el("button", "btn-retry", "重试");
    btn.addEventListener("click", onRetry);
    card.appendChild(btn);
  }
  return card;
}

export function skeleton(lines = 6) {
  const box = el("div", "skeleton");
  for (let i = 0; i < lines; i += 1) {
    const row = el("div", "sk-line");
    row.style.width = `${60 + ((i * 17) % 40)}%`;
    box.appendChild(row);
  }
  return box;
}
```

- [ ] **Step 7.2: 浏览器验证**

F12 控制台：

```js
import("/js/components.js").then((m) => {
  const badge = m.statusBadge("ok");
  const bar = m.priceBar(65);
  console.log(badge.className === "badge ok" && bar.querySelector("i").style.width === "65%" ? "OK" : "FAIL");
});
```

Expected: 输出 `OK`。

- [ ] **Step 7.3: 提交**

```bash
git add src/api/static/js/components.js
git commit -m "feat(Web): 共享 DOM 组件（徽章/进度条/错误卡/骨架屏）"
```

---

### Task 8: sessions.js 会话侧边栏

**Files:**
- Create: `src/api/static/js/sessions.js`
- Modify: `src/api/static/js/app.js`（启动流程已在 Task 5 引用了 initSessions/initSessionStartup，无需再改）

> 依赖 chat.js 的 `renderMessageHistory`/`clearChatScroll` 导出（Task 9 实现）；本任务实现后浏览器中聊天区为空白但侧边栏可用，Task 9 完成后联动生效。

- [ ] **Step 8.1: 实现**

`src/api/static/js/sessions.js`：

```js
// 侧边栏会话列表：新建/切换/删除/清空 + 历史消息恢复
import { store, bus, switchView } from "./state.js";
import { api } from "./api.js";
import { renderMessageHistory, clearChatScroll } from "./chat.js";
import { el, esc } from "./components.js";

const listEl = () => document.getElementById("sessionList");

export async function refreshSessionList() {
  try {
    const data = await api.listSessions();
    renderList(data.sessions || []);
  } catch (err) {
    renderList([]);
    console.error("获取会话列表失败:", err);
  }
}

function renderList(sessions) {
  const box = listEl();
  box.innerHTML = "";
  if (!sessions.length) {
    box.appendChild(el("div", "sessions-empty", "暂无会话，发送第一条消息后自动创建"));
    return;
  }
  for (const s of sessions) {
    const item = el("div", `sess${s.session_id === store.currentSessionId ? " active" : ""}`);
    const title = el("span", "t", s.title || "新会话");
    title.title = esc(s.title || "新会话");
    item.appendChild(title);
    const del = el("span", "x", "✕");
    del.title = "删除会话";
    del.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      if (!window.confirm(`删除会话「${s.title || "新会话"}」？该操作不可恢复。`)) return;
      try {
        await api.deleteSession(s.session_id);
        if (store.currentSessionId === s.session_id) {
          store.currentSessionId = null;
          delete store.sessionMessages[s.session_id];
          clearChatScroll();
          await ensureSession();
        }
        await refreshSessionList();
      } catch (err) {
        window.alert(`删除失败: ${err.message}`);
      }
    });
    item.appendChild(del);
    item.addEventListener("click", () => selectSession(s.session_id));
    box.appendChild(item);
  }
}

async function selectSession(id) {
  if (id === store.currentSessionId) return;
  store.currentSessionId = id;
  switchView("chat");
  clearChatScroll();
  if (!store.sessionMessages[id]) {
    try {
      const data = await api.getMessages(id);
      store.sessionMessages[id] = data.messages || [];
    } catch (err) {
      store.sessionMessages[id] = [];
      console.error("恢复会话消息失败:", err);
    }
  }
  renderMessageHistory(store.sessionMessages[id]);
  await refreshSessionList();
}

export async function ensureSession() {
  if (store.currentSessionId) return;
  const data = await api.createSession();
  store.currentSessionId = data.session_id;
  store.sessionMessages[data.session_id] = [];
}

export function initSessions() {
  document.getElementById("newSessionBtn").addEventListener("click", async () => {
    try {
      const data = await api.createSession();
      store.currentSessionId = data.session_id;
      store.sessionMessages[data.session_id] = [];
      switchView("chat");
      clearChatScroll();
      await refreshSessionList();
    } catch (err) {
      window.alert(`新建会话失败: ${err.message}`);
    }
  });
  const collapseBtn = document.getElementById("collapseBtn");
  collapseBtn.addEventListener("click", () => {
    const sidebar = document.getElementById("sidebar");
    sidebar.classList.toggle("collapsed");
    collapseBtn.textContent = sidebar.classList.contains("collapsed") ? "»" : "«";
  });
  bus.addEventListener("chat-done", refreshSessionList);
}

export async function initSessionStartup() {
  await refreshSessionList();
  try {
    const data = await api.listSessions();
    const sessions = data.sessions || [];
    if (sessions.length) {
      await selectSession(sessions[0].session_id);
    } else {
      await ensureSession();
    }
  } catch {
    await ensureSession();
  }
}
```

- [ ] **Step 8.2: 浏览器验证**

服务运行中，浏览器打开 http://127.0.0.1:8000。F12 控制台应无报错（Task 9 前 `import { renderMessageHistory } from "./chat.js"` 会因 chat.js 不存在而失败——**先临时验证：确认 Task 9 完成后再一起验证**）。

实际验证放在 Task 9 完成后：侧边栏显示会话列表、新建会话按钮生效、折叠按钮切换 `collapsed` 类。

- [ ] **Step 8.3: 提交（与 Task 9 一并提交亦可，此处单提交需注意 chat.js 缺失导致页面报错）**

**说明：** sessions.js 依赖 chat.js 导出，Task 9 完成前页面会因模块缺失报错。本任务**不单独提交**，与 Task 9 一起提交（见 Task 9 最后一步）。

---

### Task 9: chat.js 聊天视图（SSE 流式 + 计划卡 + 工具结果卡 + 快捷按钮）

**Files:**
- Create: `src/api/static/js/chat.js`

- [ ] **Step 9.1: 实现**

`src/api/static/js/chat.js`：

```js
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
  if (!msg || !store.currentSessionId) return;
  const sid = store.currentSessionId;
  (store.sessionMessages[sid] = store.sessionMessages[sid] || [])
    .push({ role: "user", content: msg });
  appendUser(msg);
  currentPlan = null;

  const agentBox = appendBubble("agent", el("div", "content"));
  const thinking = el("div", "thinking", "正在分析…");
  agentBox.appendChild(thinking);

  const handlers = {
    plan: (e) => {
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
      const resultSid = e.session_id || sid;
      (store.sessionMessages[resultSid] = store.sessionMessages[resultSid] || [])
        .push({ role: "assistant", content: e.summary || "" });
      bus.dispatchEvent(new Event("chat-done"));
    },
    error: (e) => {
      thinking.remove();
      const card = el("div", "error-card");
      card.appendChild(el("div", "error-msg", e.message || "处理请求时出错"));
      agentBox.appendChild(card);
      bus.dispatchEvent(new Event("chat-done"));
    },
  };
  try {
    await api.chatStream(msg, sid, handlers);
  } catch (err) {
    thinking.remove();
    agentBox.appendChild(interruptedCard(() => sendMessage(msg)));
  }
  const input = document.getElementById("chatInput");
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
```

- [ ] **Step 9.2: 浏览器验证（Task 8+9 联合）**

启动服务并打开 http://127.0.0.1:8000：

1. 控制台无报错；侧边栏显示会话列表（或空态提示）
2. 发送"分析 000001 的估值" → 用户气泡 + Agent 区先"正在分析…"，随后计划卡出现，步骤状态点依次点亮，结果摘要 + 工具结果卡（chip 显示 `analyze_stock`）+ "查看完整报告 →"链接
3. 点击"工具列表"按钮 → 工具面板列出 pipeline/rag 工具
4. 点击"清空会话" → confirm 后消息区清空
5. 点击"新建会话" → 侧边栏新增项并高亮
6. 折叠按钮 → 侧边栏收起为窄条，再点恢复

- [ ] **Step 9.3: 提交（含 Task 8 的 sessions.js）**

```bash
git add src/api/static/js/sessions.js src/api/static/js/chat.js
git commit -m "feat(Web): 聊天视图 SSE 流式渲染 + 会话侧边栏"
```

---

### Task 10: report.js 个股报告视图

**Files:**
- Create: `src/api/static/js/report.js`

- [ ] **Step 10.1: 实现**

`src/api/static/js/report.js`：

```js
// 个股报告视图：渲染 /api/v1/analyze 完整 JSON（纵向研报流）
import { store, switchView } from "./state.js";
import { api } from "./api.js";
import { renderMarkdown } from "./markdown.js";
import { el, kv, statusBadge, priceBar, errorCard, skeleton } from "./components.js";

const DIM_LABELS = {
  financial: "财务健康", technical: "技术趋势", valuation: "估值合理",
  industry: "行业对比", sentiment: "舆情风险",
};
const DIM_ORDER = ["financial", "technical", "valuation", "industry", "sentiment"];

const content = () => document.getElementById("reportContent");

export async function openReport(symbol) {
  switchView("report");
  if (store.reportCache[symbol]) {
    renderReport(store.reportCache[symbol]);
    return;
  }
  const box = content();
  box.innerHTML = "";
  box.appendChild(skeleton());
  try {
    const data = await api.analyze(symbol);
    store.reportCache[symbol] = data;
    renderReport(data);
  } catch (err) {
    box.innerHTML = "";
    box.appendChild(errorCard(`分析失败: ${err.message}`, () => openReport(symbol)));
  }
}

function renderReport(d) {
  const box = content();
  box.innerHTML = "";
  box.appendChild(reportHeader(d));
  box.appendChild(overviewCard(d));
  box.appendChild(scoreCard(d));
  for (const dim of DIM_ORDER) {
    const section = (d.dimensions || {})[dim];
    if (section) box.appendChild(dimCard(dim, section));
  }
  if (d.commentary) box.appendChild(llmCard(d.commentary));
  const refresh = el("button", "btn-refresh", "刷新报告");
  refresh.addEventListener("click", () => {
    delete store.reportCache[d.symbol];
    openReport(d.symbol);
  });
  box.appendChild(refresh);
}

function reportHeader(d) {
  const hdr = el("div", "report-header");
  const left = el("div");
  left.appendChild(el("span", "stock-name", d.name || d.symbol));
  left.appendChild(el("span", "stock-code", d.symbol));
  if (d.overview && d.overview.industry) {
    left.appendChild(el("span", "chip ind", d.overview.industry));
  }
  hdr.appendChild(left);
  const right = el("div", "price-box");
  if (d.overview && d.overview.latest_close != null) {
    right.appendChild(el("span", "stock-price", String(d.overview.latest_close)));
  }
  if (d.overview && d.overview.change_pct != null) {
    const pct = d.overview.change_pct;
    const cls = pct > 0 ? "up" : pct < 0 ? "down" : "flat";
    right.appendChild(el("span", `chg ${cls}`, `${pct > 0 ? "+" : ""}${pct}%`));
  }
  hdr.appendChild(right);
  return hdr;
}

function overviewCard(d) {
  const card = el("div", "panel");
  card.appendChild(el("div", "panel-title", "概览"));
  const kvs = el("div", "kv");
  const o = d.overview || {};
  if (o.year_high != null && o.year_low != null) {
    kvs.appendChild(kv("年内最高 / 最低", `${o.year_high} / ${o.year_low}`));
  }
  if (o.price_position && o.price_position !== "暂无") {
    const box = el("div");
    box.appendChild(el("div", "k", "价格位置"));
    const val = el("div", "v");
    val.appendChild(el("span", "", o.price_position));
    val.appendChild(priceBar(parseInt(o.price_position, 10)));
    box.appendChild(val);
    kvs.appendChild(box);
  }
  card.appendChild(kvs);
  return card;
}

function scoreCard(d) {
  const card = el("div", "panel");
  card.appendChild(el("div", "panel-title", "综合评分"));
  const big = el("div", "score-big");
  const s = d.score || {};
  const num = el("div", "score-num");
  num.textContent = s.final != null ? String(s.final) : "—";
  num.appendChild(el("small", "", "/10"));
  big.appendChild(num);
  const rows = el("div", "score-rows");
  for (const r of d.score_rows || []) {
    const row = el("div", "score-row");
    row.appendChild(el("span", "lb", r.label));
    row.appendChild(el("span", "v", r.score));
    row.appendChild(priceBar(parseFloat(r.score)));
    row.appendChild(el("span", "wt", r.weight));
    rows.appendChild(row);
  }
  if (s.risk_deduction) {
    rows.appendChild(el("div", "score-note", `风险扣分: -${s.risk_deduction}`));
  }
  big.appendChild(rows);
  card.appendChild(big);
  return card;
}

function dimCard(name, section) {
  const card = el("div", "panel");
  const head = el("div", "dim-head");
  head.appendChild(el("span", "dim-name", DIM_LABELS[name] || name));
  head.appendChild(statusBadge(section.status));
  const sc = el("span", "dim-score");
  sc.textContent = section.score != null ? String(section.score) : "—";
  head.appendChild(sc);
  card.appendChild(head);
  if (section.summary) {
    const sum = el("div", "dim-sum");
    sum.innerHTML = renderMarkdown(section.summary);
    card.appendChild(sum);
  }
  const metrics = section.metrics || {};
  const keys = Object.keys(metrics);
  if (keys.length) {
    const mtr = el("div", "mtr");
    for (const k of keys) {
      const val = metrics[k];
      const text = val !== null && typeof val === "object" ? JSON.stringify(val) : String(val);
      mtr.appendChild(kv(k, text));
    }
    card.appendChild(mtr);
  }
  for (const flag of section.risk_flags || []) {
    card.appendChild(el("span", "flag", `⚠ ${flag}`));
  }
  return card;
}

function llmCard(commentary) {
  const card = el("div", "llm");
  card.appendChild(el("div", "llm-title", "AI 解读"));
  const p = el("div", "md");
  p.innerHTML = renderMarkdown(commentary);
  card.appendChild(p);
  return card;
}

export function initReportView() {
  const input = document.getElementById("stockInput");
  const btn = document.getElementById("stockBtn");
  btn.addEventListener("click", () => {
    const sym = input.value.trim();
    if (sym) openReport(sym);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") btn.click();
  });
}
```

- [ ] **Step 10.2: 浏览器验证**

1. 顶栏"个股分析"输入 600519 → 点击"分析" → 骨架屏 → 完整报告（头部名称/代码/行业/收盘/涨跌、概览、评分卡、5 维度卡、AI 解读）
2. 聊天中发送"分析 000001 的估值"→ 工具结果卡点"查看完整报告 →" → 切到报告视图并渲染
3. 再次输入同一代码 → 直接渲染（缓存命中，无骨架屏）；点"刷新报告" → 重新拉取
4. 输入非法代码 `abc` → 错误卡显示后端 detail（"无效的股票代码: abc"），页面不切视图

- [ ] **Step 10.3: 提交**

```bash
git add src/api/static/js/report.js
git commit -m "feat(Web): 个股报告视图（纵向研报流 + 缓存 + 聊天联动）"
```

---

### Task 11: indexview.js 指数视图

**Files:**
- Create: `src/api/static/js/indexview.js`

- [ ] **Step 11.1: 实现**

`src/api/static/js/indexview.js`：

```js
// 指数分析视图：多指数对比表 + 逐指数纵向研报
import { switchView } from "./state.js";
import { api } from "./api.js";
import { renderMarkdown } from "./markdown.js";
import { el, kv, tagChip, priceBar, errorCard, skeleton } from "./components.js";

const TAGS = {
  technical: { bull: ["多头", "good"], shake: ["震荡", "mid"], bear: ["空头", "bad"] },
  valuation: { undervalued: ["低估", "good"], neutral: ["中性", "mid"],
               overvalued: ["高估", "bad"], invalid: ["无效", "gray"] },
  capital: { positive: ["流入", "good"], neutral: ["中性", "mid"], negative: ["流出", "bad"] },
  macro: { positive: ["积极", "good"], neutral: ["中性", "mid"],
           negative: ["消极", "bad"], na: ["不适用", "gray"] },
  sentiment: { positive: ["积极", "good"], neutral: ["中性", "mid"], negative: ["消极", "bad"] },
};
const SECTION_META = [
  ["technical", "技术面", "section_technical", "tag_technical"],
  ["valuation", "估值", "section_valuation", "tag_valuation"],
  ["capital", "资金面", "section_capital", "tag_capital"],
  ["macro", "宏观", "section_macro", "tag_macro"],
  ["sentiment", "舆情", "section_sentiment", "tag_sentiment"],
];

const content = () => document.getElementById("indexContent");

export async function openIndex(codes) {
  switchView("index");
  const symbols = String(codes || "").trim().split(/\s+/).filter(Boolean);
  if (!symbols.length) return;
  const box = content();
  box.innerHTML = "";
  box.appendChild(skeleton());
  try {
    const data = await api.index(symbols);
    renderIndex(data);
  } catch (err) {
    box.innerHTML = "";
    box.appendChild(errorCard(`分析失败: ${err.message}`, () => openIndex(codes)));
  }
}

function renderIndex(data) {
  const box = content();
  box.innerHTML = "";
  if (data.compare && data.compare.headers && data.compare.headers.length) {
    box.appendChild(compareCard(data.compare));
  }
  for (const r of data.reports || []) box.appendChild(reportCard(r));
  for (const err of data.errors || []) box.appendChild(errorCard(err));
}

function compareCard(compare) {
  const card = el("div", "panel");
  card.appendChild(el("div", "panel-title", "多指数对比"));
  const table = el("table");
  const thead = el("thead");
  const headRow = el("tr");
  for (const h of compare.headers) headRow.appendChild(el("th", "", String(h)));
  thead.appendChild(headRow);
  table.appendChild(thead);
  const tbody = el("tbody");
  for (const row of compare.rows || []) {
    const tr = el("tr");
    for (const h of compare.headers) {
      tr.appendChild(el("td", "", row[h] != null ? String(row[h]) : "—"));
    }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  card.appendChild(table);
  return card;
}

function reportCard(r) {
  const card = el("div", "report");
  const hdr = el("div", "report-header");
  hdr.appendChild(el("span", "stock-name", r.name || r.code));
  hdr.appendChild(el("span", "stock-code", r.code));
  const o = r.overview || {};
  const priceBox = el("div", "price-box");
  if (o.latest_close != null) {
    priceBox.appendChild(el("span", "stock-price", String(o.latest_close)));
  }
  if (o.change_pct != null) {
    const cls = o.change_pct > 0 ? "up" : o.change_pct < 0 ? "down" : "flat";
    priceBox.appendChild(el("span", `chg ${cls}`, `${o.change_pct > 0 ? "+" : ""}${o.change_pct}%`));
  }
  hdr.appendChild(priceBox);
  card.appendChild(hdr);

  if (o.pe_ttm != null || o.pb != null || o.pe_percentile != null) {
    const panel = el("div", "panel");
    panel.appendChild(el("div", "panel-title", "概览"));
    const kvs = el("div", "kv");
    if (o.pe_ttm != null) kvs.appendChild(kv("PE-TTM", `${o.pe_ttm}x`));
    if (o.pb != null) kvs.appendChild(kv("PB", String(o.pb)));
    if (o.pe_percentile != null) {
      const box = el("div");
      box.appendChild(el("div", "k", `PE 分位（近 ${o.percentile_lookback_years || 5} 年）`));
      const val = el("div", "v");
      val.appendChild(el("span", "", `${o.pe_percentile}%`));
      val.appendChild(priceBar(o.pe_percentile));
      box.appendChild(val);
      kvs.appendChild(box);
    }
    panel.appendChild(kvs);
    card.appendChild(panel);
  }

  const visible = r.visible_sections;
  for (const [key, label, field, tagField] of SECTION_META) {
    const section = r[field];
    if (!section) continue;
    if (Array.isArray(visible) && visible.length && !visible.includes(key)) continue;
    card.appendChild(sectionCard(label, section, tagFor(key, r[tagField])));
  }
  card.appendChild(compositeCard(r));
  for (const risk of r.risk_list || []) {
    card.appendChild(el("span", "flag", `⚠ ${risk}`));
  }
  return card;
}

function tagFor(kind, tag) {
  const map = TAGS[kind] || {};
  const entry = map[tag];
  return entry ? tagChip(entry[0], entry[1]) : null;
}

function sectionCard(label, section, tag) {
  const card = el("div", "panel");
  const head = el("div", "dim-head");
  head.appendChild(el("span", "dim-name", label));
  if (tag) head.appendChild(tag);
  card.appendChild(head);
  if (section.status === "unavailable") {
    card.appendChild(el("div", "dim-sum", section.summary || "数据不可用"));
    return card;
  }
  if (section.summary) {
    const sum = el("div", "dim-sum");
    sum.innerHTML = renderMarkdown(section.summary);
    card.appendChild(sum);
  }
  const metrics = section.metrics || {};
  const keys = Object.keys(metrics).filter((k) => k !== "tag");
  if (keys.length) {
    const mtr = el("div", "mtr");
    for (const k of keys) {
      const val = metrics[k];
      const text = val !== null && typeof val === "object" ? JSON.stringify(val) : String(val);
      mtr.appendChild(kv(k, text));
    }
    card.appendChild(mtr);
  }
  return card;
}

function compositeCard(r) {
  const card = el("div", "llm");
  card.appendChild(el("div", "llm-title", "综合研判"));
  const p = el("div", "md");
  p.innerHTML = renderMarkdown(r.composite_comment || "");
  card.appendChild(p);
  if (r.position_coeff != null) {
    const coeff = el("div", "coeff");
    coeff.appendChild(el("span", "coeff-label", "建议仓位系数"));
    coeff.appendChild(priceBar((Number(r.position_coeff) || 0) * 100));
    coeff.appendChild(el("span", "coeff-value", String(r.position_coeff)));
    card.appendChild(coeff);
  }
  return card;
}

export function initIndexView() {
  const input = document.getElementById("indexInput");
  const btn = document.getElementById("indexBtn");
  btn.addEventListener("click", () => openIndex(input.value));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") btn.click();
  });
}
```

- [ ] **Step 11.2: 浏览器验证**

1. 顶栏"指数分析"输入 `000300` → 单指数研报（概览 PE/PB/分位条 + Section 卡带多空徽章 + 综合研判含仓位系数），无对比表
2. 输入 `000300 000905` → 顶部对比表（headers/rows 渲染）+ 两份报告纵向排列
3. 输入 `000300 ###` → 一份报告 + 底部错误卡（"无效的指数代码: ###"）

- [ ] **Step 11.3: 提交**

```bash
git add src/api/static/js/indexview.js
git commit -m "feat(Web): 指数分析视图（多指数对比 + 纵向研报）"
```

---

### Task 12: 手工验收清单执行 + README 更新

**Files:**
- Modify: `README.md`（Web UI 一节）

- [ ] **Step 12.1: README 更新**

`README.md` 的 `### Web UI` 一节，在现有"浏览器打开 http://127.0.0.1:8000 使用 Web 聊天界面。"之后补充：

```markdown
浏览器打开 http://127.0.0.1:8000 使用 Web 聊天界面。页面功能：

- **聊天**：SSE 流式展示 Agent 执行计划与进度；工具结果卡可一键跳转完整报告
- **个股报告**：顶栏输入代码直达，或从聊天结果跳转；完整维度评分 + AI 解读
- **指数分析**：顶栏支持多指数（空格分隔），自动生成对比表 + 逐指数研报
- **会话管理**：左侧边栏新建/切换/删除/清空会话，历史消息重启后恢复
```

- [ ] **Step 12.2: 全量检查**

```bash
ruff check . && pyright && .venv/Scripts/python -m pytest -q
```

Expected: 全部通过（基线 557 passed + 本计划新增约 14 个测试；3 skipped 维持）。

- [ ] **Step 12.3: 手工验收清单逐项执行**

启动 `stock-robot api`，浏览器 http://127.0.0.1:8000 逐项核对（对应 spec 第 8 节）：

1. ✅ 聊天流式：发"分析 000001 的估值"→ 计划卡逐步骤点亮 → 工具结果卡出现"查看完整报告"链接
2. ✅ 聊天联动：点链接 → 报告视图完整渲染（涨跌徽章有值；红涨绿跌）
3. ✅ 导航直达：顶栏输入 600519 → 报告加载（含骨架屏）
4. ✅ 指数页：输入 000300 000905 → 对比表 + 两份报告；单指数无对比表
5. ✅ 会话：新建/切换/清空/删除；重启服务后切换旧会话历史消息恢复
6. ✅ 快捷按钮：择时研判（发预置消息）、工具列表（面板）、清空会话（confirm + 清空）
7. ✅ 侧边栏折叠/展开正常；无 Agent 模式（`PYTHONPATH=src .venv/Scripts/python -m uvicorn api.app:app --port 8001`）页面不崩、chat 显示"Agent 核心未注入"
8. ✅ 降级路径：无 LLM key（`stock-robot config set llm.api_key ""` 后重启）聊天仍能执行含 6 位代码的分析并出结果

- [ ] **Step 12.4: 提交**

```bash
git add README.md
git commit -m "docs(README): Web UI 功能说明补充报告页/指数页/会话管理"
```

---

## 自审记录

- **Spec 覆盖**：spec 第 2 节范围 4 项 → Task 9/10/11/8；第 6 节后端 4 项 → Task 1/2/3/4；第 7 节错误处理 → 前端各视图错误卡/重试/422 文案透出（Task 10/11 + chat interruptedCard）；第 8 节测试 → Task 1-5 的 pytest + Task 12 手工清单；spec 4.2 视觉规范 → Task 5 CSS 变量与组件类名一一对应（含红涨绿跌修正）
- **占位符**：无 TBD/TODO；所有步骤含完整代码或精确命令
- **类型一致性**：`_structured_tool_results` 字段名（tool/symbol/status/content）在 Task 1 后端与 Task 9 前端消费处一致；`compare.headers/rows` 在 Task 3 后端与 Task 11 前端一致；`overview.change_pct` 在 Task 4 与 Task 10/11 一致；CSS 类名（dot/step-status/badge/tag/panel/dim 等）在 Task 5 定义、Task 7-11 使用处一致
- **spec 微调**：新增 `js/state.js`（循环导入规避）；spec 中"绿涨红跌"笔误已修正为"红涨绿跌"（A 股习惯），CSS 中 `.chg.up` 为红、`.chg.down` 为绿
