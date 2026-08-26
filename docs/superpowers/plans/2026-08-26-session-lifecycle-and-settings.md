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

## 固定接口契约

### 配置 API

GET /api/v1/config 的 200 响应固定为：

~~~json
{
  "config": {
    "llm": {"provider": "openai", "model": "gpt-4o", "enabled": true,
      "api_key": {"configured": true, "masked": "sk-a****9z"},
      "base_url": "", "temperature": 0.3, "max_tokens": 2000,
      "retry_times": 2, "timeout_seconds": 60},
    "data": {"cache_ttl": {"daily": 86400, "quarterly": 604800, "news": 21600},
      "disclaimer_accepted": false},
    "api": {"host": "127.0.0.1", "port": 25618},
    "push": {"enabled": true, "max_symbols_per_subscription": 20,
      "email": {"smtp_host": "smtp.qq.com", "smtp_port": 465, "smtp_user": "",
        "smtp_password": {"configured": false, "masked": ""}, "to_addr": ""},
      "wecom": {"corp_id": "", "agent_id": "", "secret": {"configured": false, "masked": ""},
        "to_user": "@all"}},
    "signal": {"thresholds": {"attack": 7, "watch": 4},
      "actions": {"attack": {"action": "可考虑建仓/加仓", "position": "60%-80%"},
        "watch": {"action": "持有观察，等待明确方向", "position": "30%-50%"},
        "defend": {"action": "减仓或回避", "position": "0%-20%"}}}
  },
  "paths": {"state_dir": "D:/project/.stock_robot",
    "config_file": "D:/project/.stock_robot/config.yaml"}
}
~~~

PUT /api/v1/config 的请求体只能是上述 config 的任意嵌套子集；未提交字段保持原值，密钥空字符串表示保持原值。成功时返回完整的安全配置、同一 paths 和 restart_required。GET /api/v1/config/credentials/{key} 仅支持 llm.api_key、push.email.smtp_password、push.wecom.secret，响应为 value 字段；其余 key 必须 404。

后端必须拒绝未知键，且按以下边界返回 422：provider 仅 openai 或 claude；model、host、邮箱主机、用户/收件人、企业微信 ID、action、position 为去空白后的非空字符串；temperature 为 0 至 2；max_tokens 为 1 至 128000；retry_times 为 0 至 10；timeout_seconds 为 1 至 600；全部 cache_ttl、smtp_port、max_symbols_per_subscription 和 api.port 分别为正整数，其中 port 不大于 65535；阈值满足 0 < watch < attack <= 10。

### 草稿会话与 SSE 顺序

1. createDraftSession 创建 draft- 前缀 ID，只初始化本地缓存；它不写 sessionDetails，因此列表刷新永远不会显示它。
2. sendMessage 发送草稿 ID 后，先把用户气泡显示在 DOM，并把 run 暂挂在草稿 ID 下；服务端 get_or_create 把草稿 ID 视作 None，创建真实 UUID 和首条用户消息。
3. session_title、plan、artifact 等任何带真实 session_id 的第一条 SSE 事件到达时，前端必须先调用 adoptPersistedSession，再处理该事件。迁移必须同时更新 run.sessionId 和 artifact.session_id。
4. 迁移后只允许真实 ID 写入 sessionDetails，并调用 markSessionListMutation；此前在途的 refreshSessionList 响应因 revision 已变化而丢弃，不能删除刚迁移的真实缓存。
5. 用户在 SSE 尚未返回真实 ID 前删除/切换会话时，取消草稿 run；迟到事件若对应已取消 run 或 tombstone，必须不迁移、不写缓存。
6. 只有持久化会话出现在侧边栏，因此重命名、删除、历史详情请求从不接受 draft- ID；服务端未知或 draft- 详情、重命名、删除均返回 404。

### 设置表单与提交规则

设置页使用以下固定分区和字段顺序，避免通过对象遍历产生不稳定 UI：

| 分区 | 字段路径 | 控件 |
| --- | --- | --- |
| LLM 设置 | llm.enabled、provider、model、api_key、base_url、temperature、max_tokens、retry_times、timeout_seconds | 复选框、下拉框、文本、密钥框、数值框 |
| 数据与缓存 | data.disclaimer_accepted、data.cache_ttl.daily、quarterly、news | 复选框、正整数数值框 |
| 服务设置 | api.host、api.port | 文本、1-65535 数值框 |
| 推送设置 | push.enabled、max_symbols_per_subscription、email 全部字段、wecom 全部字段 | 复选框、文本、数值、密钥框 |
| 信号策略 | signal.thresholds.attack、watch、三种 action/position | 数值、文本 |

所有字段初始值从 GET 返回填充，且在用户修改前不进入待提交集合。保存按钮构造仅含已修改字段的最小嵌套 config；密钥“输入新值”为空或仍等于掩码时都不得包含在请求中。每个密钥显示一个 button，默认 aria-label 为“显示完整密钥”；点击成功后只将完整值放入该字段的局部闭包，button 变为“隐藏完整密钥”；再次点击或离开配置视图时删除该局部值并恢复 masked 文本。任何 API 失败必须保留用户已编辑的表单值，不回填为服务端值。

设置视图进入时才 GET /api/v1/config，首次加载失败显示“无法加载配置，请检查服务连接后重试”和重试按钮。成功保存后重新 GET 安全配置；若 restart_required 为 true，在成功提示中显示“服务地址或端口已保存，重启 stock-robot run 后生效”，否则显示“配置已保存”。

### 测试矩阵

| 行为 | 测试位置 | 关键断言 |
| --- | --- | --- |
| 三命令进度样式 | tests/test_cli.py、tests/test_cli_index.py | 三列、transient、没有 task.completed |
| 旧空记录迁移 | tests/api/test_sessions.py | 重启后空 sessions 与 artifact 同时消失，有消息记录保留 |
| 首发草稿 ID | tests/api/test_app.py | REST 与 SSE 返回的 ID 不等于 draft ID，数据库无 draft 行，首条 user message 属于真实 ID |
| 路由收敛 | tests/api/test_app.py | POST sessions 与 POST clear 返回 404，删除仍能删除真实会话 |
| 缓存迁移/竞态 | tests/api/test_static.py | 所有按 ID 分桶状态移至真实 ID，陈旧列表和已取消 SSE 不复活会话 |
| 配置安全与校验 | tests/api/test_configuration.py | 完整响应无明文；三种凭据单独读取；未知键及每类非法边界 422 |
| 设置页面 | tests/api/test_static.py | 五分区、眼睛按需请求/关闭即擦除、最小 PUT、重启/错误提示 |

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
