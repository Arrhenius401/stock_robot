# 会话生命周期与配置界面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 统一 CLI 进度条，阻止空会话持久化并清理历史空记录，同时提供安全、可校验的可视化配置管理页面。

**Architecture:** CLI 共享一个 Rich Progress 工厂。前端以 draft- 前缀的内存 ID 关联首发消息，服务端为该 ID 生成真实 UUID 会话，前端在首个 SSE 会话事件到达时原子迁移所有会话缓存。配置 API 在 Config 之上定义白名单、校验和凭据序列化，设置页只访问这些受控接口。

**Tech Stack:** Python 3.11、Click、Rich、FastAPI、Pydantic、PyYAML、SQLite、原生 ES modules、pytest、Node 静态模块测试。

**Spec:** docs/superpowers/specs/2026-08-26-session-lifecycle-and-settings-design.md

## Global Constraints

- Python 测试使用 D:\code\stock_robot\.venv\Scripts\python.exe -m pytest；Ruff 使用 VS Code 扩展 bundled 的通配符路径。
- analyze、index 与 run 只显示 Spinner、阶段描述和横向条；transient=True 清理，绝不显示完成计数。
- 临时会话绝不写 SQLite；真实 ID 由服务端生成；历史空会话、清空会话 UI/API/测试一并删除。
- 仅允许配置白名单字段；密钥默认脱敏，空密钥更新保留原值；host/port 只提示重启。
- 不开放行业评分、报告模板、内部存储路径与策略文件的 Web 编辑。

## 文件结构

| 文件 | 职责 |
| --- | --- |
| src/stock_robot/cli.py | 共享进度条工厂。 |
| tests/test_cli.py、tests/test_cli_index.py | CLI 样式与回归测试。 |
| src/api/sessions.py、src/api/app.py | 草稿 ID、零消息清理、路由收敛。 |
| src/api/configuration.py | 新建：配置白名单、校验、掩码、局部更新。 |
| src/utils/config.py | 单次持久化的深度更新。 |
| src/api/static/js/state.js、sessions.js、chat.js | 草稿、缓存迁移、聊天接管。 |
| src/api/static/js/settings.js | 新建：配置页面与凭据眼睛开关。 |
| src/api/static/js/api.js、app.js、index.html、app.css | API 封装、页面入口和样式。 |
| tests/api/test_sessions.py、test_app.py、test_configuration.py、test_static.py | 后端、API、静态模块测试。 |

## Task 1: 统一 CLI 进度条

**Files:**
- Modify: src/stock_robot/cli.py:1-360,512-540
- Modify: tests/test_cli.py:1-110、tests/test_cli_index.py:1-180

**Interfaces:** 产生 _create_cli_progress()，固定为三列 SpinnerColumn、TextColumn 阶段描述、BarColumn，并使用全局 console 与 transient=True。

- [ ] **Step 1: 写入失败测试**

~~~python
def test_create_cli_progress_matches_run_style():
    from stock_robot.cli import _create_cli_progress
    progress = _create_cli_progress()
    assert progress.transient is True
    assert len(progress.columns) == 3
    assert all("task.completed" not in str(column) for column in progress.columns)
~~~

为 analyze、index 的 mock pipeline 触发 on_progress("data", 1, 3, "读取")，断言命令仍成功。

- [ ] **Step 2: 运行失败测试**

Run: D:\code\stock_robot\.venv\Scripts\python.exe -m pytest tests/test_cli.py tests/test_cli_index.py -q

Expected: FAIL，无法导入工厂。

- [ ] **Step 3: 写入最小实现**

~~~python
def _create_cli_progress():
    """创建与 run 命令一致、结束后自动清理的进度条。"""
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        console=console,
        transient=True,
    )
~~~

将三处内联 Progress 全部替换为 with _create_cli_progress() as progress。保留 completed、total 更新，但删除完成计数列。

- [ ] **Step 4: 验证**

Run: D:\code\stock_robot\.venv\Scripts\python.exe -m pytest tests/test_cli.py tests/test_cli_index.py -q

Expected: PASS。

Run: & "$env:USERPROFILE\.vscode\extensions\charliermarsh.ruff-*\bundled\libs\bin\ruff.exe" check src/stock_robot/cli.py tests/test_cli.py tests/test_cli_index.py

Expected: All checks passed!

- [ ] **Step 5: 提交**

~~~powershell
git add src/stock_robot/cli.py tests/test_cli.py tests/test_cli_index.py
git commit -m "refactor(命令行): 统一分析进度条样式"
~~~

## Task 2: 服务端延迟持久化与空会话清理

**Files:**
- Modify: src/api/sessions.py:16-310、src/api/app.py:280-470,723-790
- Modify: tests/api/test_sessions.py:1-330、tests/api/test_app.py:1200-1335

**Interfaces:** 产生 is_draft_session_id(session_id: str | None) 和 SessionStore.purge_empty_sessions()。SessionManager.get_or_create 遇到草稿 ID 必须返回新 UUID；持久化 ID 的恢复行为不变。

- [ ] **Step 1: 写入失败测试**

~~~python
def test_initialization_removes_legacy_empty_sessions(tmp_path):
    db_path = tmp_path / "sessions.db"
    store = SessionStore(db_path)
    store.create_session("empty", "新会话")
    store.create_session("kept", "有消息")
    store.append_message("kept", "user", "你好")
    restarted = SessionStore(db_path)
    assert restarted.session_exists("empty") is False
    assert restarted.session_exists("kept") is True

def test_draft_id_creates_server_uuid(store, facts_path):
    manager = SessionManager(store, facts_path=facts_path)
    sid, _ = manager.get_or_create("draft-browser-1", "分析 000001")
    assert sid != "draft-browser-1"
    assert store.session_exists("draft-browser-1") is False
    assert store.session_exists(sid) is True
~~~

将 POST /api/v1/sessions 和 clear 成功测试改为断言 404。重命名、删除 API 测试先通过注入 SessionManager 创建含用户消息的会话。

- [ ] **Step 2: 运行失败测试**

Run: D:\code\stock_robot\.venv\Scripts\python.exe -m pytest tests/api/test_sessions.py tests/api/test_app.py -q

Expected: FAIL，空会话仍存在、草稿 ID 被直接写入，或旧路由仍返回 200。

- [ ] **Step 3: 写入最小实现**

~~~python
def is_draft_session_id(session_id: str | None) -> bool:
    return isinstance(session_id, str) and session_id.startswith("draft-")

def purge_empty_sessions(self) -> int:
    with self._lock, self._get_conn() as conn:
        result = conn.execute(
            "DELETE FROM sessions WHERE NOT EXISTS "
            "(SELECT 1 FROM messages WHERE messages.session_id=sessions.session_id)"
        )
    return result.rowcount
~~~

在 _init_db 迁移完成后以及 SessionManager.list_sessions 查询前调用清理。草稿 ID 在 get_or_create 中视为无 ID。删除 create_session、clear_session 路由、SessionManager.clear、SessionStore.clear_messages 和专用失效逻辑。SSE 首个 session_title 或 plan 必须携带真实 session_id；无 core 模式保持现有降级响应。

- [ ] **Step 4: 验证**

Run: D:\code\stock_robot\.venv\Scripts\python.exe -m pytest tests/api/test_sessions.py tests/api/test_app.py -q

Expected: PASS。

Run: & "$env:USERPROFILE\.vscode\extensions\charliermarsh.ruff-*\bundled\libs\bin\ruff.exe" check src/api/sessions.py src/api/app.py tests/api/test_sessions.py tests/api/test_app.py

Expected: All checks passed!

- [ ] **Step 5: 提交**

~~~powershell
git add src/api/sessions.py src/api/app.py tests/api/test_sessions.py tests/api/test_app.py
git commit -m "feat(会话): 延迟持久化并清理空会话"
~~~

## Task 3: 前端草稿会话接管

**Files:**
- Modify: src/api/static/js/state.js、src/api/static/js/sessions.js、src/api/static/js/chat.js
- Modify: src/api/static/js/api.js、src/api/static/index.html、tests/api/test_static.py

**Interfaces:** 产生 createDraftSession、isDraftSessionId、adoptPersistedSession(draftId, sessionId)。后者必须迁移 sessionMessages、sessionArtifacts、sessionRuns、sessionRunEpochs、sessionDetailGenerations、sessionDetailStale 和 currentSessionId。

- [ ] **Step 1: 写入失败 Node DOM 测试**

~~~javascript
await initSessionStartup();
const draftId = store.currentSessionId;
if (!draftId.startsWith("draft-")) throw new Error("启动未创建内存草稿会话");
document.getElementById("chatInput").value = "未发送内容";
await document.getElementById("newSessionBtn").click();
if (store.currentSessionId !== draftId || document.getElementById("chatInput").value !== "") {
  throw new Error("重复新建未复用草稿并清空输入");
}
~~~

模拟 chatStream 返回真实 ID，断言草稿 ID 不再位于任一缓存桶、用户消息和 run 均迁移到真实 ID、刷新列表只显示真实会话；断言 quickClear 和 api.clearSession 均不存在。

- [ ] **Step 2: 运行失败测试**

Run: D:\code\stock_robot\.venv\Scripts\python.exe -m pytest tests/api/test_static.py -q

Expected: FAIL，当前实现会创建会话、不能迁移草稿且仍有清空按钮。

- [ ] **Step 3: 写入最小实现**

~~~javascript
export function adoptPersistedSession(draftId, sessionId) {
  if (!isDraftSessionId(draftId) || draftId === sessionId) return;
  for (const key of ["sessionMessages", "sessionArtifacts", "sessionRuns",
    "sessionRunEpochs", "sessionDetailGenerations", "sessionDetailStale"]) {
    if (Object.hasOwn(store[key], draftId)) {
      store[key][sessionId] = store[key][draftId];
      delete store[key][draftId];
    }
  }
  if (store.currentSessionId === draftId) store.currentSessionId = sessionId;
}
~~~

ensureSession 仅创建 draft- 加随机 UUID 的 ID 和空缓存。新建按钮遇草稿时清空输入和聊天展示并复用；遇真实会话时创建新草稿。sendMessage 首次收到真实 SSE ID 时先迁移草稿缓存，再附着 run 与追加用户消息。删除 quickClear HTML、监听器和 api.clearSession。

- [ ] **Step 4: 验证**

Run: D:\code\stock_robot\.venv\Scripts\python.exe -m pytest tests/api/test_static.py -q

Expected: PASS。

- [ ] **Step 5: 提交**

~~~powershell
git add src/api/static/js/state.js src/api/static/js/sessions.js src/api/static/js/chat.js src/api/static/js/api.js src/api/static/index.html tests/api/test_static.py
git commit -m "feat(界面): 使用临时会话并移除清空操作"
~~~

## Task 4: 受控配置 API

**Files:**
- Create: src/api/configuration.py
- Modify: src/utils/config.py、src/api/app.py
- Create: tests/api/test_configuration.py

**Interfaces:** build_public_config(config)、read_secret(config, key)、update_public_config(config, payload)。API 为 GET /api/v1/config、GET /api/v1/config/credentials/{key}、PUT /api/v1/config；更新响应为 config 与 restart_required。

- [ ] **Step 1: 写入失败测试**

~~~python
async def test_config_update_keeps_blank_secret_and_flags_restart(client):
    response = await client.put("/api/v1/config", json={"config": {
        "llm": {"api_key": ""}, "api": {"port": 28000}
    }})
    assert response.status_code == 200
    assert response.json()["restart_required"] is True
~~~

覆盖公开响应白名单和只读路径、掩码首尾 4/5 字符、三种完整凭据键、未知凭据 404、未知字段/非正缓存时长/非法端口/attack 小于等于 watch/空 action 或 position 的 422、局部更新不覆盖嵌套项、空密钥保留旧值。

- [ ] **Step 2: 运行失败测试**

Run: D:\code\stock_robot\.venv\Scripts\python.exe -m pytest tests/api/test_configuration.py -q

Expected: FAIL，模块和路由尚不存在。

- [ ] **Step 3: 写入最小实现**

~~~python
SECRET_KEYS = frozenset({
    "llm.api_key", "push.email.smtp_password", "push.wecom.secret",
})

def mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    edge = 4 if len(value) < 12 else 5
    return f"{value[:edge]}{'*' * (len(value) - edge * 2)}{value[-edge:]}"
~~~

configuration.py 定义嵌套白名单和纯函数校验。新增 Config.update(values)，深度合并并单次 _persist。更新时拒绝白名单外字段且跳过空字符串密钥；公开表示为 configured 和 masked。create_app 建立 Config 闭包、注册三条路由，将 ValueError 映射为 422；输入含 api.host 或 api.port 时置 restart_required=True。

- [ ] **Step 4: 验证**

Run: D:\code\stock_robot\.venv\Scripts\python.exe -m pytest tests/api/test_configuration.py -q

Expected: PASS。

Run: pyright src/api/configuration.py src/utils/config.py src/api/app.py

Expected: 0 errors。

Run: & "$env:USERPROFILE\.vscode\extensions\charliermarsh.ruff-*\bundled\libs\bin\ruff.exe" check src/api/configuration.py src/utils/config.py src/api/app.py tests/api/test_configuration.py

Expected: All checks passed!

- [ ] **Step 5: 提交**

~~~powershell
git add src/api/configuration.py src/utils/config.py src/api/app.py tests/api/test_configuration.py
git commit -m "feat(配置): 提供受控的配置管理接口"
~~~

## Task 5: 配置页面与眼睛开关

**Files:**
- Create: src/api/static/js/settings.js
- Modify: src/api/static/js/api.js、src/api/static/js/app.js、src/api/static/index.html、src/api/static/css/app.css
- Modify: tests/api/test_static.py

**Interfaces:** 产生 initSettings 与 renderSettings(payload)；只在点击 settings-secret-toggle 时调用 api.getCredential(key)。

- [ ] **Step 1: 写入失败 Node DOM 测试**

模拟 api.getConfig 返回五个分区、只读路径和掩码密钥；断言页面显示“LLM 设置”和掩码。点击眼睛后断言只调用一次 api.getCredential(key) 并显示完整值，二次点击恢复掩码。断言空密钥不在 PUT body，restart_required 显示“重启 stock-robot run 后生效”，422 显示在表单错误区。

- [ ] **Step 2: 运行失败测试**

Run: D:\code\stock_robot\.venv\Scripts\python.exe -m pytest tests/api/test_static.py -q

Expected: FAIL，设置模块和页面不存在。

- [ ] **Step 3: 写入最小实现**

在 HTML 侧边栏增加 data-view="settings" 的“配置”导航及 view-settings 和 settingsContent。app.js 导入并调用 initSettings，增加标题。api.js 增加 getConfig、updateConfig、getCredential。

settings.js 由字段元数据数组渲染 LLM、数据与缓存、服务、推送、信号策略五个 section；数值输入有 min/step，布尔为 checkbox。每个密钥有掩码、随状态更新 aria-label 的眼睛按钮和“输入新值”密码框；只点击睁眼才取完整值，闭眼后删除内存完整值。保存只收集变更，空密钥不入 payload，成功后重新请求安全配置。CSS 复用 panel 并为配置网格、错误、提示、眼睛按钮补充移动端规则。

- [ ] **Step 4: 验证**

Run: D:\code\stock_robot\.venv\Scripts\python.exe -m pytest tests/api/test_static.py tests/api/test_app.py -q

Expected: PASS。

- [ ] **Step 5: 提交**

~~~powershell
git add src/api/static/js/settings.js src/api/static/js/api.js src/api/static/js/app.js src/api/static/index.html src/api/static/css/app.css tests/api/test_static.py
git commit -m "feat(界面): 添加可视化配置管理页"
~~~

## Task 6: 文档与真实路径验收

**Files:**
- Modify: README.md
- Modify: tests/api/test_static.py

- [ ] **Step 1: 补充文档和静态入口测试**

在静态资源清单加入 /js/settings.js。README 说明设置页、配置文件位置、host/port 需重启生效、凭据遮罩仅用于本地可信环境，以及未发送新会话不会保存。

- [ ] **Step 2: 运行针对性回归**

Run: D:\code\stock_robot\.venv\Scripts\python.exe -m pytest tests/test_cli.py tests/test_cli_index.py tests/api/test_sessions.py tests/api/test_app.py tests/api/test_configuration.py tests/api/test_static.py -q

Expected: PASS。

- [ ] **Step 3: 使用真实启动路径验收**

Run: D:\code\stock_robot\.venv\Scripts\stock-robot.exe run

在浏览器检查：启动进度结束后消失；空会话刷新后不恢复、连续新建仅清空输入；发送后真实会话可恢复；配置可保存，host/port 有重启提示，眼睛开关显示/隐藏完整密钥。

- [ ] **Step 4: 执行提交前全量检查**

Run: & "$env:USERPROFILE\.vscode\extensions\charliermarsh.ruff-*\bundled\libs\bin\ruff.exe" check .

Expected: All checks passed!

Run: pyright

Expected: 0 errors。

Run: D:\code\stock_robot\.venv\Scripts\python.exe -m pytest -q

Expected: 全部通过。

- [ ] **Step 5: 提交**

~~~powershell
git add README.md tests/api/test_static.py
git commit -m "docs(使用): 说明会话与配置管理"
~~~
