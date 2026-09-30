# 报告库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有静态单页应用中新增“报告库”列表页与独立详情页，读取 `reports/` 中已持久化的个股、指数和回测报告，并展示回测净值曲线与交易明细。

**Architecture:** 后端新增只读报告库服务模块，负责枚举、校验、读取和转换 `reports/` 产物；`api.app.create_app()` 只挂载 HTTP 路由并转换错误。前端新增 `report-library.js` 作为独立视图状态机，列表页和详情页都在 `view-report-library` 内切换，样式复用当前浅色工作台、左侧导航、`analysis-entry`、`panel`、表格和报告正文风格。

**Tech Stack:** Python 3.11+、FastAPI、Pydantic/标准库 dataclass、csv/json/pathlib、原生 ES module、DOM API、SVG、pytest、httpx ASGITransport、Node DOM stub。

**Spec:** `docs/superpowers/specs/2026-08-29-report-library-design.md`

## Global Constraints

- 报告库只读本地 `reports/` 历史产物，不修改、不删除、不触发分析或回测。
- 不把 `reports/` 直接作为任意静态目录暴露；所有读取必须经过受控 API。
- API 仅识别 `reports/stock/`、`reports/index/`、`reports/backtests/`，并兼容历史根目录 `<代码>/<YYYY-MM>/*.md` 个股报告。
- 报告 ID 由服务端基于相对路径编码生成；客户端不得提交文件系统路径。
- 从 ID 还原路径时必须 `resolve()` 并验证仍位于 `reports/` 根目录，拒绝 `..`、绝对路径、符号链接逃逸和任意文件读取。
- 列表按生成时间倒序；列表刷新由用户显式触发。
- 列表页不嵌入详情；详情页是同一静态应用内的独立视图状态。
- 顶部首行比现有紧凑栏略高，保留 `Stock Robot` 与当前视图标题，不在右上角展示全局“分析”搜索模块。
- 详情页标题行最左侧使用仅含左向 Chevron 的返回按钮，旁边展示标题与元信息，右侧保留下载入口。
- 回测详情页提供“报告正文、净值曲线、交易明细”三个页签；缺失附属 CSV 时正文仍可阅读。
- Markdown 继续复用现有受控渲染函数；前端不拼接不可信 HTML。
- Python 测试使用 `.venv/Scripts/python -m pytest`；日常验证运行聚焦测试，不默认跑全量检查。
- 前端、会话或静态页面改动最终必须通过 `./.venv/Scripts/stock-robot.exe api` 真实启动路径完成浏览器验收。

---

## File Structure

- Create `src/api/report_library.py`: 报告库服务。定义报告摘要/详情数据结构、ID 编解码、目录枚举、详情读取、CSV/JSON 解析和下载路径解析。
- Modify `src/api/app.py`: 在 `create_app()` 中挂载 `/api/v1/reports`、`/api/v1/reports/{report_id}`、`/api/v1/reports/{report_id}/download`。
- Modify `src/api/static/index.html`: 左侧导航新增“报告库”，主区新增 `view-report-library` 容器；顶部不增加任何右上角分析模块。
- Modify `src/api/static/js/api.js`: 新增 `listReports()`、`getReport()`、`downloadReportUrl()`。
- Modify `src/api/static/js/app.js`: 导入并初始化 `initReportLibrary()`，把 `report-library` 纳入现有视图切换。
- Create `src/api/static/js/report-library.js`: 报告库前端状态、列表页、详情页、回测页签、SVG 曲线、交易表、错误和空状态。
- Modify `src/api/static/css/app.css`: 新增报告库列表网格、详情头部、Chevron 返回按钮、摘要指标、页签、曲线和交易表样式。
- Test `tests/api/test_report_library.py`: 服务层目录识别、安全边界和解析行为。
- Test `tests/api/test_report_library_api.py`: HTTP 列表、详情、下载和错误码。
- Modify `tests/api/test_static.py`: 静态 HTML/JS 模块存在、导航入口、报告库 DOM 交互。

---

### Task 1: 后端报告库服务

**Files:**
- Create: `src/api/report_library.py`
- Test: `tests/api/test_report_library.py`

**Interfaces:**
- Produces:
  - `ReportLibraryError(message: str, status_code: int = 400)`
  - `ReportSummary`
  - `ReportDetail`
  - `list_reports(reports_root: Path, report_type: Literal["stock", "index", "backtest"] | None = None, query: str | None = None) -> list[ReportSummary]`
  - `get_report_detail(reports_root: Path, report_id: str) -> ReportDetail`
  - `resolve_download_path(reports_root: Path, report_id: str) -> Path`
- Consumes: `reports/` 目录布局、标准库 `Path.resolve()`、`json`、`csv`、`base64.urlsafe_b64encode`。

- [ ] **Step 1: Write failing service tests**

Create `tests/api/test_report_library.py` with fixtures that write these exact structures:

```python
from pathlib import Path

import pytest

from api.report_library import (
    ReportLibraryError,
    get_report_detail,
    list_reports,
    resolve_download_path,
)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_reports(root: Path) -> None:
    write(root / "stock" / "000001" / "2026-08" / "000001_20260829.md",
          "# 平安银行\n\n个股正文")
    write(root / "index" / "000300" / "2026-08" / "000300_20260829.md",
          "# 沪深300\n\n指数正文")
    run = root / "backtests" / "report_technical" / "000001" / "2026-08" / "run-1"
    write(run / "report.md", "# 回测报告\n\n策略正文")
    write(run / "summary.json",
          '{"symbol":"000001","strategy_id":"report_technical","strategy_version":"v1",'
          '"run_id":"run-1","start_date":"2025-01-02","end_date":"2026-08-28",'
          '"metrics":{"total_return":0.1842,"max_drawdown":-0.0786,'
          '"sharpe":1.21},"trades_count":26}')
    write(run / "manifest.json", '{"strategy":{"name":"技术策略"}}')
    write(run / "equity_curve.csv", "净值日期,策略净值,基准净值\n2026-01-01,1.0,1.0\n2026-01-02,1.1,1.02\n")
    write(run / "trades.csv", "trade_date,side,price,return_pct\n2026-03-18,buy,10.42,\n2026-04-26,sell,11.31,0.0854\n")
    write(root / "600519" / "2026-08" / "600519_20260829.md",
          "# 贵州茅台\n\n旧版个股正文")


def test_list_reports_reads_all_supported_products(tmp_path):
    reports = tmp_path / "reports"
    make_reports(reports)

    summaries = list_reports(reports)

    assert [item.type for item in summaries] == ["stock", "index", "backtest", "stock"]
    assert any(item.title == "000001 技术策略回测报告" and item.has_equity_curve
               and item.has_trades for item in summaries)
    assert any(item.title == "600519 个股分析报告" and item.legacy for item in summaries)


def test_list_reports_filters_type_and_query(tmp_path):
    reports = tmp_path / "reports"
    make_reports(reports)

    assert [item.type for item in list_reports(reports, report_type="index")] == ["index"]
    assert [item.symbol for item in list_reports(reports, query="茅台")] == ["600519"]


def test_detail_reads_backtest_payload_and_missing_csv_is_nonfatal(tmp_path):
    reports = tmp_path / "reports"
    make_reports(reports)
    summary = next(item for item in list_reports(reports) if item.type == "backtest")
    (reports / "backtests" / "report_technical" / "000001" / "2026-08"
     / "run-1" / "trades.csv").unlink()

    detail = get_report_detail(reports, summary.id)

    assert detail.markdown.startswith("# 回测报告")
    assert detail.summary["symbol"] == "000001"
    assert detail.equity_curve["columns"] == ["净值日期", "策略净值", "基准净值"]
    assert detail.trades["rows"] == []
    assert detail.missing_artifacts == ["trades.csv"]


def test_rejects_unknown_id_and_path_escape(tmp_path):
    reports = tmp_path / "reports"
    make_reports(reports)

    with pytest.raises(ReportLibraryError) as unknown:
        get_report_detail(reports, "not-a-valid-report-id")
    assert unknown.value.status_code == 404

    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    escaped_id = "Li4vb3V0c2lkZS5tZA"
    with pytest.raises(ReportLibraryError) as escaped:
        resolve_download_path(reports, escaped_id)
    assert escaped.value.status_code == 404
```

- [ ] **Step 2: Run service tests and verify they fail**

Run: `.venv/Scripts/python -m pytest tests/api/test_report_library.py -q`

Expected: import failure for `api.report_library`.

- [ ] **Step 3: Implement report library service**

Create `src/api/report_library.py` with these concrete shapes:

```python
from __future__ import annotations

import base64
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

ReportType = Literal["stock", "index", "backtest"]
MAX_TRADE_ROWS = 1000


class ReportLibraryError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ReportSummary:
    id: str
    type: ReportType
    title: str
    symbol: str
    path: str
    generated_at: float
    strategy_id: str | None = None
    strategy_version: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    has_equity_curve: bool = False
    has_trades: bool = False
    legacy: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReportDetail:
    report: ReportSummary
    markdown: str
    summary: dict[str, Any] | None = None
    equity_curve: dict[str, Any] | None = None
    trades: dict[str, Any] | None = None
    missing_artifacts: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

Implement helpers:

```python
def _encode_id(relative_path: Path) -> str:
    raw = relative_path.as_posix().encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_id(report_id: str) -> Path:
    try:
        padded = report_id + "=" * (-len(report_id) % 4)
        value = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (UnicodeDecodeError, ValueError, binascii.Error) as exc:
        raise ReportLibraryError("报告不存在", 404) from exc
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ReportLibraryError("报告不存在", 404)
    return candidate
```

Implementation notes:

- Import `binascii` for `_decode_id`.
- `list_reports()` returns `[]` when `reports_root` is missing.
- Stock/index title format: `"{symbol} 个股分析报告"` and `"{symbol} 指数分析报告"` unless the first Markdown heading exists, in which case append type only if heading is empty.
- Backtest title format: `"{symbol} {strategy_id} 回测报告"`; if `manifest.json` has `strategy.name`, use that display name.
- `_read_csv(path, max_rows)` returns `{"columns": [...], "rows": [{...}]}` and converts empty cells to `None`.
- Invalid `summary.json` or CSV raises `ReportLibraryError("报告文件已损坏或不完整", 422)`.
- `resolve_download_path()` returns the Markdown file for valid stock/index/backtest reports and rejects any decoded path whose resolved target is not below `reports_root.resolve()`.

- [ ] **Step 4: Run service tests and verify they pass**

Run: `.venv/Scripts/python -m pytest tests/api/test_report_library.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit service task**

```bash
git add src/api/report_library.py tests/api/test_report_library.py
git commit -m "feat(报告库): 添加持久化报告读取服务"
```

---

### Task 2: API 路由

**Files:**
- Modify: `src/api/app.py`
- Test: `tests/api/test_report_library_api.py`

**Interfaces:**
- Consumes: `list_reports()`, `get_report_detail()`, `resolve_download_path()` from Task 1.
- Produces:
  - `GET /api/v1/reports?type=&query=`
  - `GET /api/v1/reports/{report_id}`
  - `GET /api/v1/reports/{report_id}/download`

- [ ] **Step 1: Write failing API tests**

Create `tests/api/test_report_library_api.py`:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from api.app import create_app
from tests.api.test_report_library import make_reports


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    make_reports(tmp_path / "reports")
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://test") as item:
        yield item


@pytest.mark.asyncio
async def test_list_reports_endpoint(client):
    resp = await client.get("/api/v1/reports")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 4
    assert payload["reports"][0]["type"] == "stock"
    assert any(report["type"] == "backtest" and report["has_equity_curve"]
               for report in payload["reports"])


@pytest.mark.asyncio
async def test_report_detail_and_download(client):
    listing = (await client.get("/api/v1/reports", params={"type": "backtest"})).json()
    report_id = listing["reports"][0]["id"]

    detail = await client.get(f"/api/v1/reports/{report_id}")
    download = await client.get(f"/api/v1/reports/{report_id}/download")

    assert detail.status_code == 200
    assert detail.json()["markdown"].startswith("# 回测报告")
    assert detail.json()["equity_curve"]["rows"][0]["策略净值"] == "1.0"
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    assert download.text.startswith("# 回测报告")


@pytest.mark.asyncio
async def test_report_errors_are_http_statuses(client):
    missing = await client.get("/api/v1/reports/not-a-valid-report-id")

    assert missing.status_code == 404
    assert missing.json()["detail"] == "报告不存在"
```

- [ ] **Step 2: Run API tests and verify they fail**

Run: `.venv/Scripts/python -m pytest tests/api/test_report_library_api.py -q`

Expected: 404 for `/api/v1/reports`.

- [ ] **Step 3: Add API routes in `create_app()`**

Inside `create_app()` after `/api/v1/tools`, add:

```python
    def _reports_root() -> Path:
        return Path.cwd() / "reports"

    def _raise_report_error(error: ReportLibraryError) -> None:
        raise HTTPException(status_code=error.status_code, detail=str(error))

    @app.get("/api/v1/reports")
    async def reports(report_type: str | None = None, query: str | None = None):
        if report_type not in (None, "stock", "index", "backtest"):
            raise HTTPException(status_code=422, detail="报告类型无效")
        items = list_reports(_reports_root(), report_type=report_type, query=query)
        return JSONResponse({"reports": [item.to_dict() for item in items],
                             "total": len(items)})

    @app.get("/api/v1/reports/{report_id}")
    async def report_detail(report_id: str):
        try:
            detail = get_report_detail(_reports_root(), report_id)
        except ReportLibraryError as error:
            _raise_report_error(error)
        return JSONResponse(detail.to_dict())

    @app.get("/api/v1/reports/{report_id}/download")
    async def report_download(report_id: str):
        try:
            path = resolve_download_path(_reports_root(), report_id)
        except ReportLibraryError as error:
            _raise_report_error(error)
        return FileResponse(path, media_type="text/markdown",
                            filename=path.name)
```

Add imports near the top:

```python
from pathlib import Path
from fastapi.responses import FileResponse
from api.report_library import (
    ReportLibraryError,
    get_report_detail,
    list_reports,
    resolve_download_path,
)
```

If `Path` or `FileResponse` already exists, reuse the existing import.

- [ ] **Step 4: Run API tests and verify they pass**

Run: `.venv/Scripts/python -m pytest tests/api/test_report_library_api.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit API task**

```bash
git add src/api/app.py tests/api/test_report_library_api.py
git commit -m "feat(报告库): 暴露报告列表详情与下载接口"
```

---

### Task 3: 静态页面接入

**Files:**
- Modify: `src/api/static/index.html`
- Modify: `src/api/static/js/api.js`
- Modify: `src/api/static/js/app.js`
- Modify: `tests/api/test_static.py`

**Interfaces:**
- Consumes: API routes from Task 2.
- Produces: `view-report-library` 容器、左侧 `data-view="report-library"` 导航、前端 API 方法。

- [ ] **Step 1: Write failing static shell tests**

Append to `tests/api/test_static.py`:

```python
@pytest.mark.asyncio
async def test_index_contains_report_library_view(client):
    html = (await client.get("/")).text

    assert 'data-view="report-library"' in html
    assert "<span>报告库</span>" in html
    assert 'id="view-report-library"' in html
    assert 'id="reportLibraryContent"' in html
    assert 'id="globalStockSearch"' not in html


@pytest.mark.asyncio
async def test_report_library_modules_served(client):
    app_js = (await client.get("/js/app.js")).text
    api_js = (await client.get("/js/api.js")).text

    assert 'import { initReportLibrary } from "./report-library.js";' in app_js
    assert "initReportLibrary();" in app_js
    assert "listReports(" in api_js
    assert "getReport(" in api_js
    assert "downloadReportUrl(" in api_js
    assert (await client.get("/js/report-library.js")).status_code == 200
```

- [ ] **Step 2: Run static shell tests and verify they fail**

Run: `.venv/Scripts/python -m pytest tests/api/test_static.py::TestStaticUI::test_index_contains_report_library_view tests/api/test_static.py::TestStaticUI::test_report_library_modules_served -q`

Expected: missing navigation/view/module assertions fail.

- [ ] **Step 3: Modify `index.html`**

Add a nav button after `指数分析`:

```html
<button class="nav-item" type="button" data-view="report-library">
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M4 4.5A2.5 2.5 0 0 1 6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5z"/></svg>
  <span>报告库</span>
</button>
```

Add a main view before subscriptions:

```html
<div class="view" id="view-report-library">
  <div id="reportLibraryContent" class="report-library-content"></div>
</div>
```

Remove the current topbar global form:

```html
<form class="global-stock-search" role="search">...</form>
```

Keep `.workspace-title` because `switchView()` updates it.

- [ ] **Step 4: Modify `api.js`**

Add methods inside `export const api = { ... }`:

```javascript
  listReports(params = {}) {
    const search = new URLSearchParams();
    if (params.type) search.set("type", params.type);
    if (params.query) search.set("query", params.query);
    const suffix = search.toString() ? `?${search.toString()}` : "";
    return request(`/api/v1/reports${suffix}`);
  },
  getReport(id) {
    return request(`/api/v1/reports/${encodeURIComponent(id)}`);
  },
  downloadReportUrl(id) {
    return `/api/v1/reports/${encodeURIComponent(id)}/download`;
  },
```

- [ ] **Step 5: Modify `app.js`**

Add import:

```javascript
import { initReportLibrary } from "./report-library.js";
```

Call it in `init()` beside other view initializers:

```javascript
initReportLibrary();
```

If `switchView()` has a title map in `state.js` instead of `app.js`, add `"report-library": "报告库"` there; otherwise add it to the existing title map where view labels are defined.

- [ ] **Step 6: Run static shell tests and verify they pass**

Run: `.venv/Scripts/python -m pytest tests/api/test_static.py::TestStaticUI::test_index_contains_report_library_view tests/api/test_static.py::TestStaticUI::test_report_library_modules_served -q`

Expected: both tests pass.

- [ ] **Step 7: Commit static shell task**

```bash
git add src/api/static/index.html src/api/static/js/api.js src/api/static/js/app.js tests/api/test_static.py
git commit -m "feat(报告库): 接入静态页面导航入口"
```

---

### Task 4: 报告库前端交互与样式

**Files:**
- Create: `src/api/static/js/report-library.js`
- Modify: `src/api/static/css/app.css`
- Modify: `tests/api/test_static.py`

**Interfaces:**
- Consumes: `api.listReports(params)`, `api.getReport(id)`, `api.downloadReportUrl(id)`, `renderMarkdown(text)`, `el()`, `errorCard()`, `skeleton()`, `bus`.
- Produces:
  - `initReportLibrary()`
  - 列表页状态：搜索、筛选、刷新、卡片网格、空状态。
  - 详情页状态：左 Chevron 返回、下载、摘要指标、Markdown 正文、回测页签、SVG 曲线、交易表。

- [ ] **Step 1: Write failing DOM behavior test**

Append to `tests/api/test_static.py`:

```python
def test_report_library_renders_list_detail_and_back_button(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js 不可用")

    library_url = json.dumps(_module_url("src/api/static/js/report-library.js"))
    api_url = json.dumps(_module_url("src/api/static/js/api.js"))
    state_url = json.dumps(_module_url("src/api/static/js/state.js"))
    script = _DOM_STUB + r"""
const root = makeElement("reportLibraryContent");
const title = makeElement("currentViewTitle", "h1");
const { api } = await import(__API_URL__);
const { bus } = await import(__STATE_URL__);
const { initReportLibrary } = await import(__LIBRARY_URL__);

api.listReports = async () => ({ total: 1, reports: [{
  id: "abc", type: "backtest", title: "000001 技术策略回测报告",
  symbol: "000001", path: "backtests/report_technical/000001/2026-08/run-1/report.md",
  generated_at: 1787994863, strategy_id: "report_technical",
  strategy_version: "v1", start_date: "2025-01-02", end_date: "2026-08-28",
  has_equity_curve: true, has_trades: true, legacy: false,
}] });
api.getReport = async () => ({
  report: { id: "abc", type: "backtest", title: "000001 技术策略回测报告",
    symbol: "000001", path: "backtests/report_technical/000001/2026-08/run-1/report.md",
    generated_at: 1787994863, strategy_id: "report_technical" },
  markdown: "# 回测报告\n\n正文",
  summary: { metrics: { total_return: 0.1842, max_drawdown: -0.0786, sharpe: 1.21 },
    trades_count: 26 },
  equity_curve: { columns: ["净值日期", "策略净值", "基准净值"],
    rows: [{ "净值日期": "2026-01-01", "策略净值": "1.0", "基准净值": "1.0" },
           { "净值日期": "2026-01-02", "策略净值": "1.1", "基准净值": "1.02" }] },
  trades: { columns: ["trade_date", "side", "price", "return_pct"],
    rows: [{ trade_date: "2026-04-26", side: "sell", price: "11.31", return_pct: "0.0854" }] },
  missing_artifacts: [],
});
api.downloadReportUrl = () => "/api/v1/reports/abc/download";

initReportLibrary();
const event = new Event("view-change");
Object.defineProperty(event, "detail", { value: { view: "report-library" } });
bus.dispatchEvent(event);
await new Promise((resolve) => setTimeout(resolve, 0));

if (!root.textContent.includes("已保存报告") || !root.textContent.includes("打开详情")) {
  throw new Error("未渲染报告库列表页");
}
await byClass(root, "report-library-open")[0].click();
await new Promise((resolve) => setTimeout(resolve, 0));
if (!root.textContent.includes("累计收益") || !root.textContent.includes("+18.42%")
    || !root.textContent.includes("净值曲线") || !root.textContent.includes("交易明细")) {
  throw new Error("未渲染报告详情页的摘要与回测页签");
}
const back = byClass(root, "report-library-back")[0];
if (!back || back.getAttribute("aria-label") !== "返回报告库" || back.textContent.trim()) {
  throw new Error("返回按钮必须是仅含可访问名称的 Chevron 图标按钮");
}
await back.click();
if (!root.textContent.includes("已保存报告")) {
  throw new Error("返回按钮未回到列表页");
}
""".replace("__LIBRARY_URL__", library_url)
    script = script.replace("__API_URL__", api_url).replace("__STATE_URL__", state_url)
    _run_node(tmp_path, script)
```

- [ ] **Step 2: Run DOM behavior test and verify it fails**

Run: `.venv/Scripts/python -m pytest tests/api/test_static.py::test_report_library_renders_list_detail_and_back_button -q`

Expected: import failure for `report-library.js`.

- [ ] **Step 3: Implement `report-library.js`**

Create `src/api/static/js/report-library.js`:

```javascript
import { api } from "./api.js";
import { bus } from "./state.js";
import { el, errorCard, skeleton } from "./components.js";
import { renderMarkdown } from "./markdown.js";

let initialized = false;
let loaded = false;
let reqSeq = 0;
const state = { reports: [], type: "", query: "", selected: null, tab: "markdown" };

const root = () => document.getElementById("reportLibraryContent");

export function initReportLibrary() {
  if (initialized) return;
  initialized = true;
  bus.addEventListener("view-change", (event) => {
    if (event.detail.view === "report-library" && !loaded) loadReports();
  });
}
```

Implement these functions in the same file:

- `loadReports()`: calls `api.listReports({ type: state.type, query: state.query })`, renders skeleton, stores `state.reports`, sets `loaded = true`, calls `renderList()`, handles error with `errorCard("无法加载报告库，请检查服务连接后重试", loadReports)`.
- `renderList()`: renders `.report-library-toolbar` with search input, refresh button and type filters; renders `.report-library-summary` with `已保存报告` and `共 N 份`; renders `.report-library-grid`; empty list renders `.report-library-empty`.
- `reportCard(report)`: returns a `.report-library-card` with badge and button `.report-library-open`; click calls `openDetail(report.id)`.
- `openDetail(id)`: calls `api.getReport(id)`, stores `state.selected`, sets `state.tab = "markdown"`, calls `renderDetail()`.
- `renderDetail()`: renders `.report-library-detail-head`, `.report-library-back` button with inline Chevron SVG and `aria-label="返回报告库"`; renders download link using `api.downloadReportUrl(report.id)`; renders summary metrics and tabs.
- `renderActiveTab(detail)`: markdown tab uses `renderMarkdown(detail.markdown || "暂无报告正文")`; curve tab uses `renderEquityCurve(detail.equity_curve)`; trades tab uses `renderTrades(detail.trades)`.
- `renderEquityCurve(data)`: native SVG with strategy/base lines when at least two rows exist, otherwise `.report-library-empty` text `该次运行未包含净值曲线`。
- `renderTrades(data)`: table from `data.columns` and `data.rows`; empty state text `该次运行未包含交易明细`。
- `formatPercent(value)`: `0.1842 -> "+18.42%"`, `-0.0786 -> "-7.86%"`, invalid -> `"—"`。

- [ ] **Step 4: Add CSS**

Append to `src/api/static/css/app.css`:

```css
#view-report-library { overflow: hidden; }
.report-library-content {
  flex: 1; min-height: 0; overflow: auto;
  padding: 16px clamp(16px, 4vw, 40px) 48px;
}
.report-library-toolbar {
  display: flex; align-items: center; gap: var(--space-3); flex-wrap: wrap;
  margin-bottom: var(--space-3);
}
.report-library-toolbar input {
  min-width: 220px; flex: 1; border: 1px solid var(--border); border-radius: var(--radius-sm);
  background: var(--bg); color: var(--text); padding: 8px 10px; outline: 0;
}
.report-library-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: var(--space-3);
}
.report-library-card {
  display: grid; gap: var(--space-2); min-height: 118px;
  padding: 14px 16px; border: 1px solid var(--border); border-radius: var(--radius-md);
  background: var(--panel);
}
.report-library-card-head,
.report-library-detail-head,
.report-library-detail-actions,
.report-library-tabs {
  display: flex; align-items: center; gap: var(--space-2);
}
.report-library-card-title {
  min-width: 0; flex: 1; overflow: hidden; color: var(--text);
  font-weight: 650; text-overflow: ellipsis; white-space: nowrap;
}
.report-library-open {
  justify-self: start; border: 0; background: transparent; color: var(--accent);
  font-size: 12px; font-weight: 650; padding: 0;
}
.report-library-detail-head {
  justify-content: space-between; align-items: flex-start; margin-bottom: var(--space-3);
  padding: 16px; border: 1px solid var(--border); border-radius: var(--radius-md);
  background: var(--panel);
}
.report-library-detail-title { display: flex; align-items: flex-start; gap: 10px; min-width: 0; }
.report-library-back {
  display: grid; place-items: center; flex: 0 0 auto; width: 32px; height: 32px;
  border: 1px solid var(--border); border-radius: var(--radius-sm); background: #fff;
  color: var(--text-3);
}
.report-library-back svg { width: 18px; height: 18px; }
.report-library-title-text h2 { margin: 0; color: var(--text); font-size: 19px; }
.report-library-metrics {
  display: grid; grid-template-columns: repeat(4, minmax(90px, 1fr));
  gap: var(--space-3); padding: 14px 16px; border-bottom: 1px solid var(--border-soft);
}
.report-library-tabs { padding: 10px 16px 0; border-bottom: 1px solid var(--border-soft); }
.report-library-tabs button {
  border: 1px solid var(--border); border-bottom: 0; border-radius: 6px 6px 0 0;
  background: var(--surface-hover); color: var(--text-3); padding: 6px 10px;
}
.report-library-tabs button.on { background: #fff; color: var(--accent); font-weight: 650; }
.report-library-detail-body { padding: 18px 20px 20px; }
.report-library-empty { color: var(--text-4); line-height: 1.7; }
.report-library-curve { width: 100%; min-height: 240px; }
@media (max-width: 760px) {
  .report-library-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .report-library-detail-head { flex-direction: column; }
}
```

- [ ] **Step 5: Run DOM behavior test and verify it passes**

Run: `.venv/Scripts/python -m pytest tests/api/test_static.py::test_report_library_renders_list_detail_and_back_button -q`

Expected: pass.

- [ ] **Step 6: Commit frontend task**

```bash
git add src/api/static/js/report-library.js src/api/static/css/app.css tests/api/test_static.py
git commit -m "feat(报告库): 实现列表与独立详情页"
```

---

### Task 5: Focused Verification And Browser Acceptance

**Files:**
- No source file changes expected.
- Uses: service/API/static tests and real local server.

**Interfaces:**
- Consumes completed Tasks 1-4.
- Produces validated report-library feature ready for user review.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
.venv/Scripts/python -m pytest tests/api/test_report_library.py tests/api/test_report_library_api.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run focused static tests**

Run:

```bash
.venv/Scripts/python -m pytest tests/api/test_static.py::TestStaticUI::test_index_contains_report_library_view tests/api/test_static.py::TestStaticUI::test_report_library_modules_served tests/api/test_static.py::test_report_library_renders_list_detail_and_back_button -q
```

Expected: all tests pass.

- [ ] **Step 3: Run syntax and targeted type checks**

Run:

```bash
pyright src/api/report_library.py src/api/app.py
```

Expected: no errors in changed Python files.

- [ ] **Step 4: Start real API server**

Run in PowerShell:

```powershell
./.venv/Scripts/stock-robot.exe api
```

Expected: server prints a localhost URL and serves `/`.

- [ ] **Step 5: Browser acceptance**

In the browser:

1. Open the local URL.
2. Confirm the topbar is taller and has no right-side “分析” global search.
3. Click left navigation `报告库`.
4. Confirm report cards load from `reports/`.
5. Open a stock report and confirm Markdown renders.
6. Return with the left Chevron icon.
7. Open an index report and confirm Markdown renders.
8. Return with the left Chevron icon.
9. Open a backtest report and confirm summary metrics render.
10. Switch `净值曲线` and confirm a non-empty SVG curve is visible.
11. Switch `交易明细` and confirm the table shows persisted CSV rows.
12. Click `下载` and confirm the Markdown attachment downloads or opens through `/api/v1/reports/{id}/download`.

Expected: each step succeeds without console errors or silent blank states.

- [ ] **Step 6: Commit verification note only if fixes were needed**

If verification requires source changes, commit those changes with:

```bash
git add <changed-files>
git commit -m "fix(报告库): 修复验收发现的问题"
```

If no fixes are needed, do not create an empty commit.

---

## Self-Review

- Spec coverage: The plan covers the accepted two-state UI, taller topbar, removal of the right-side analysis module, left Chevron return button, list search/filter/refresh, detail download, stock/index Markdown, backtest summary/curve/trades, empty states, controlled API, legacy paths, CSV/JSON damage, missing artifacts and path traversal protection.
- Placeholder scan: The plan contains concrete files, signatures, snippets, commands and expected results; it does not leave unspecified implementation work.
- Type consistency: The route layer consumes `ReportSummary.to_dict()` and `ReportDetail.to_dict()` from Task 1; the frontend consumes the same JSON keys asserted by Task 2; CSS class names match the DOM test in Task 4.
