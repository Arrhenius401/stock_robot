# Web UI 跑通子项目 A：后端接通 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 API 与 Agent 核心的接线，实现 analyze/index/会话端点与 LLM 重试，让 `stock-robot api` 一键启动可用的 Web 后端。

**Architecture:** 应用工厂 + 显式注入：`build_agent_core(config)` 组装 Pipeline（单例）、工具（注入 pipeline）、LLM；`create_app(core, sessions)` 按会话现建轻量 Planner/Executor；会话消息经 `SessionManager` 落盘 SQLite；同步阻塞调用统一 `asyncio.to_thread`。

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, SQLite（标准库 sqlite3）, pytest + pytest-mock + pytest-asyncio + httpx

**Spec:** `docs/superpowers/specs/2026-08-13-api-wiring-design.md`

**测试命令约定**（Windows，bash shell）：

```bash
cd /d/code/stock_robot
python -m pytest tests/api/test_sessions.py -q          # 单文件
python -m pytest tests/api/test_sessions.py::TestSessionManager::test_messages_persist_and_restore -v   # 单测试
python -m pytest -q                                     # 全量
```

提交前检查（项目 CLAUDE.md 要求）：

```bash
ruff check .
pyright
.venv/Scripts/python -m pytest -q
```

提交信息格式遵循项目规范（中文，`feat(功能点): 简述`）。

---

### Task 0: 修正 spec 术语（状态枚举与实现不一致）

spec 第 5.3 节与第 6 节写了 `status: "insufficient"`，但 `AnalysisResult.status` 的 Literal 实际为 `"ok" | "partial" | "unavailable"`（src/data/schemas.py:190）。API 维度状态将原样透传，spec 必须与实现一致。

**Files:**
- Modify: `docs/superpowers/specs/2026-08-13-api-wiring-design.md`

- [ ] **Step 1: 修正 spec 两处术语**

第 5.3 节（analyze 响应描述）中：

```
响应：`{symbol, name, overview{最新收盘/涨跌幅}, score{base/final/risk_deduction}, score_rows, dimensions{...: {status, summary, score, metrics, risk_flags}}, commentary, generated_at}`。数据不足维度 `status: "insufficient"`，HTTP 仍 200。
```

改为：

```
响应：`{symbol, name, overview{最新收盘/涨跌幅}, score{base/final/risk_deduction}, score_rows, dimensions{...: {status, summary, score, metrics, risk_flags}}, commentary, generated_at}`。数据不足维度 `status: "unavailable"`（AnalysisResult 原始枚举值），HTTP 仍 200。
```

第 6 节错误处理表中：

```
| 数据源全失败 | 维度 `status: "insufficient"`，报告正常返回，HTTP 200 |
```

改为：

```
| 数据源全失败 | 维度 `status: "unavailable"`（AnalysisResult 原始枚举值），报告正常返回，HTTP 200 |
```

- [ ] **Step 2: 提交**

```bash
cd /d/code/stock_robot
git add docs/superpowers/specs/2026-08-13-api-wiring-design.md
git commit -m "docs(设计): 修正 spec 中维度状态枚举术语，与 AnalysisResult 实际值一致"
```

---

### Task 1: 会话存储与 SessionManager

**Files:**
- Create: `src/api/sessions.py`
- Modify: `src/agent/memory.py`（构造参数扩展）
- Test: `tests/api/test_sessions.py`

- [ ] **Step 1: 编写失败测试**

创建 `tests/api/test_sessions.py`：

```python
"""会话管理单元测试"""
import pytest

from api.sessions import SessionManager, SessionStore


@pytest.fixture
def store(tmp_path):
    return SessionStore(tmp_path / "sessions.db")


@pytest.fixture
def facts_path(tmp_path):
    return tmp_path / "facts.json"


class TestSessionStore:
    def test_create_and_list(self, store):
        store.create_session("s1", "标题一")
        store.append_message("s1", "user", "你好")
        sessions = store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "s1"
        assert sessions[0]["title"] == "标题一"
        assert sessions[0]["message_count"] == 1

    def test_get_messages_ordered(self, store):
        store.create_session("s1", "t")
        store.append_message("s1", "user", "第一条")
        store.append_message("s1", "tool", "第二条")
        msgs = store.get_messages("s1")
        assert [m["content"] for m in msgs] == ["第一条", "第二条"]

    def test_clear_and_delete(self, store):
        store.create_session("s1", "t")
        store.append_message("s1", "user", "x")
        store.clear_messages("s1")
        assert store.get_messages("s1") == []
        store.delete_session("s1")
        assert not store.session_exists("s1")
        assert store.list_sessions() == []


class TestSessionManager:
    def test_get_or_create_new_session(self, store, facts_path):
        mgr = SessionManager(store, facts_path=facts_path)
        sid, memory = mgr.get_or_create(None, "帮我分析平安银行")
        assert sid
        assert memory.session_id == sid
        assert store.session_exists(sid)
        assert store.list_sessions()[0]["title"] == "帮我分析平安银行"

    def test_get_or_create_returns_existing_memory(self, store, facts_path):
        mgr = SessionManager(store, facts_path=facts_path)
        sid, m1 = mgr.get_or_create(None, "你好")
        m1.add_message("user", "补充消息")
        sid2, m2 = mgr.get_or_create(sid)
        assert sid2 == sid
        assert m2 is m1

    def test_messages_persist_and_restore(self, store, facts_path):
        mgr = SessionManager(store, facts_path=facts_path)
        sid, m1 = mgr.get_or_create(None, "你好")
        m1.add_message("user", "第一条")
        m1.add_message("tool", "结果一")

        mgr2 = SessionManager(store, facts_path=facts_path)  # 模拟重启
        sid2, m2 = mgr2.get_or_create(sid)
        assert [m["content"] for m in m2.messages] == ["第一条", "结果一"]

    def test_clear_session_keeps_facts(self, store, facts_path):
        mgr = SessionManager(store, facts_path=facts_path)
        sid, m1 = mgr.get_or_create(None, "你好")
        m1.set_fact("pref", "成长股")
        m1.add_message("user", "x")
        assert mgr.clear(sid)
        assert m1.messages == []
        assert m1.facts == {"pref": "成长股"}
        assert store.get_messages(sid) == []

    def test_delete_session(self, store, facts_path):
        mgr = SessionManager(store, facts_path=facts_path)
        sid, _ = mgr.get_or_create(None, "你好")
        assert mgr.delete(sid)
        assert mgr.delete(sid) is False
        assert mgr.get_memory(sid) is None
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /d/code/stock_robot && python -m pytest tests/api/test_sessions.py -q
```

Expected: 全部 FAIL（`ModuleNotFoundError: No module named 'api.sessions'`）

- [ ] **Step 3: 修改 `src/agent/memory.py` 构造参数**

将 `Memory.__init__`（当前第 68 行）替换为：

```python
    def __init__(self, max_messages: int = 30, facts_path: Path | None = None,
                 session_id: str | None = None, message_store=None):
        self._max_messages = max_messages
        self.messages: list[dict[str, str]] = []
        self.plan_history: list[Plan] = []
        self.facts: dict[str, Any] = {}
        self.session_id = session_id
        self._message_store = message_store
        if facts_path is None:
            facts_path = Path.home() / ".stock_robot" / "agent_facts.json"
        self._facts_path = Path(facts_path)
        self._load_facts()
```

将 `add_message`（当前第 78-81 行）替换为：

```python
    def add_message(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})
        if len(self.messages) > self._max_messages:
            self.messages = self.messages[-self._max_messages:]
        if self._message_store is not None and self.session_id:
            self._message_store.append_message(self.session_id, role, content)
```

（其余方法不变。`message_store` 类型用 duck-typing，不引入类型依赖。）

- [ ] **Step 4: 创建 `src/api/sessions.py` 实现**

```python
"""会话持久化 — SQLite 存储 sessions 与 messages"""
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from agent.memory import Memory


class SessionStore:
    """SQLite 会话存储 — sessions + messages 两张表"""

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    title       TEXT NOT NULL,
                    created_at  REAL NOT NULL,
                    updated_at  REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role       TEXT NOT NULL,
                    content    TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
            """)

    def create_session(self, session_id: str, title: str) -> None:
        now = time.time()
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sessions (session_id, title, created_at, updated_at) VALUES (?,?,?,?)",
                (session_id, title, now, now),
            )

    def session_exists(self, session_id: str) -> bool:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            return row is not None

    def list_sessions(self) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT s.session_id, s.title, s.created_at, s.updated_at, COUNT(m.id) AS message_count
                   FROM sessions s LEFT JOIN messages m ON m.session_id = s.session_id
                   GROUP BY s.session_id ORDER BY s.updated_at DESC"""
            ).fetchall()
        return [
            {"session_id": r[0], "title": r[1], "created_at": r[2],
             "updated_at": r[3], "message_count": r[4]}
            for r in rows
        ]

    def get_messages(self, session_id: str) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE session_id=? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [{"role": r[0], "content": r[1]} for r in rows]

    def append_message(self, session_id: str, role: str, content: str) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO messages (session_id, role, content, created_at) VALUES (?,?,?,?)",
                (session_id, role, content, time.time()),
            )

    def clear_messages(self, session_id: str) -> None:
        with self._get_conn() as conn:
            conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))

    def delete_session(self, session_id: str) -> None:
        with self._get_conn() as conn:
            conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))


class SessionManager:
    """会话管理器 — 内存缓存 Memory + SQLite 持久化，线程安全"""

    def __init__(self, store: SessionStore, max_messages: int = 30,
                 facts_path: Path | None = None):
        self._store = store
        self._max_messages = max_messages
        self._facts_path = facts_path
        self._memories: dict[str, Memory] = {}
        self._lock = threading.Lock()

    def _new_memory(self, session_id: str) -> Memory:
        return Memory(session_id=session_id, message_store=self._store,
                      facts_path=self._facts_path)

    def get_or_create(self, session_id: str | None,
                      first_message: str = "") -> tuple[str, Memory]:
        with self._lock:
            if session_id and session_id in self._memories:
                return session_id, self._memories[session_id]
            if session_id and self._store.session_exists(session_id):
                memory = self._new_memory(session_id)
                memory.messages = self._store.get_messages(session_id)[-self._max_messages:]
                self._memories[session_id] = memory
                return session_id, memory
            sid = session_id or uuid.uuid4().hex
            title = (first_message or "新会话")[:20]
            self._store.create_session(sid, title)
            memory = self._new_memory(sid)
            self._memories[sid] = memory
            return sid, memory

    def get_memory(self, session_id: str) -> Memory | None:
        with self._lock:
            return self._memories.get(session_id)

    def list_sessions(self) -> list[dict]:
        return self._store.list_sessions()

    def clear(self, session_id: str) -> bool:
        with self._lock:
            if not self._store.session_exists(session_id):
                return False
            self._store.clear_messages(session_id)
            memory = self._memories.get(session_id)
            if memory:
                memory.clear_session()
            return True

    def delete(self, session_id: str) -> bool:
        with self._lock:
            if not self._store.session_exists(session_id):
                return False
            self._store.delete_session(session_id)
            self._memories.pop(session_id, None)
            return True
```

- [ ] **Step 5: 运行测试确认通过**

```bash
cd /d/code/stock_robot && python -m pytest tests/api/test_sessions.py -q
```

Expected: 9 passed

- [ ] **Step 6: 回归 Memory 现有测试**

```bash
cd /d/code/stock_robot && python -m pytest tests/agent/test_memory.py -q
```

Expected: 全绿（构造参数默认 None，现有调用不受影响）

- [ ] **Step 7: 提交**

```bash
cd /d/code/stock_robot
git add tests/api/test_sessions.py src/api/sessions.py src/agent/memory.py
git commit -m "feat(会话): 添加 SessionStore/SessionManager 会话隔离与消息持久化"
```

---

### Task 2: LLM 重试与超时

**Files:**
- Modify: `src/llm/base.py`（加 `_call_with_retry`）
- Modify: `src/llm/openai.py`（超时 + 重试）
- Modify: `src/llm/claude.py`（超时 + 重试）
- Modify: `src/utils/config.py`（DEFAULT_CONFIG 新键）
- Modify: `tests/llm/test_openai.py`、`tests/llm/test_claude.py`（适配新构造签名）
- Test: `tests/llm/test_retry.py`（新增）

- [ ] **Step 1: 编写失败测试 `tests/llm/test_retry.py`**

```python
"""LLM 重试机制测试"""
from unittest.mock import MagicMock

from llm.openai import OpenAIAdapter


class TestRetry:
    def test_retries_then_succeeds(self, mocker):
        mocker.patch("llm.base.time.sleep")
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "分析完成"
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_client.chat.completions.create.side_effect = [
            Exception("临时故障"), Exception("再次失败"), mock_response,
        ]
        mocker.patch("llm.openai.OpenAI", return_value=mock_client)

        adapter = OpenAIAdapter(api_key="sk-test", model="gpt-4o")
        result = adapter.generate("分析")

        assert "分析完成" in result
        assert mock_client.chat.completions.create.call_count == 3

    def test_retry_exhausted_returns_error_text(self, mocker):
        mocker.patch("llm.base.time.sleep")
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("持续失败")
        mocker.patch("llm.openai.OpenAI", return_value=mock_client)

        adapter = OpenAIAdapter(api_key="sk-test")
        result = adapter.generate("分析")

        assert "LLM 分析暂时不可用" in result
        assert mock_client.chat.completions.create.call_count == 3

    def test_retry_times_zero_single_attempt(self, mocker):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("失败")
        mocker.patch("llm.openai.OpenAI", return_value=mock_client)

        adapter = OpenAIAdapter(api_key="sk-test", retry_times=0)
        result = adapter.generate("分析")

        assert "LLM 分析暂时不可用" in result
        assert mock_client.chat.completions.create.call_count == 1

    def test_claude_retries_then_succeeds(self, mocker):
        mocker.patch("llm.base.time.sleep")
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock()]
        mock_response.content[0].text = "解读完成"
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 5
        mock_client.messages.create.side_effect = [Exception("临时故障"), mock_response]
        mocker.patch("llm.claude.Anthropic", return_value=mock_client)

        from llm.claude import ClaudeAdapter
        adapter = ClaudeAdapter(api_key="sk-ant-test")
        result = adapter.generate("分析")

        assert "解读完成" in result
        assert mock_client.messages.create.call_count == 2
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /d/code/stock_robot && python -m pytest tests/llm/test_retry.py -q
```

Expected: 全 FAIL（`TypeError: unexpected keyword argument 'retry_times'`，且调用次数为 1 而非 3）

- [ ] **Step 3: 修改 `src/llm/base.py`**

整文件替换为：

```python
import logging
import time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class LLMBackend(ABC):
    """LLM 后端抽象接口 — 所有模型适配器需实现此接口"""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """模型名称"""
        ...

    @abstractmethod
    def generate(self, prompt: str, **kwargs) -> str:
        """生成回复"""
        ...

    def _call_with_retry(self, fn, retry_times: int = 2, base_delay: float = 1.0):
        """调用 fn，失败时指数退避重试；重试耗尽后抛出最后一次异常"""
        last_exc: Exception | None = None
        for attempt in range(retry_times + 1):
            try:
                return fn()
            except Exception as e:  # noqa: BLE001 — 重试逻辑需捕获全部异常类型
                last_exc = e
                if attempt < retry_times:
                    delay = base_delay * (2 ** attempt)
                    logger.warning("LLM 调用失败（第 %d/%d 次），%.0fs 后重试: %s",
                                   attempt + 1, retry_times, delay, e)
                    time.sleep(delay)
        assert last_exc is not None
        raise last_exc
```

- [ ] **Step 4: 修改 `src/llm/openai.py`**

整文件替换为：

```python
"""OpenAI GPT 适配器"""
import logging
from openai import OpenAI
from llm.base import LLMBackend

logger = logging.getLogger(__name__)


class OpenAIAdapter(LLMBackend):
    def __init__(self, api_key: str, model: str = "gpt-4o", temperature: float = 0.3,
                 max_tokens: int = 2000, base_url: str | None = None,
                 timeout: float = 60.0, retry_times: int = 2):
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._retry_times = retry_times
        client_kwargs = {"api_key": api_key, "timeout": timeout}
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = OpenAI(**client_kwargs)

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, prompt: str, system: str | None = None, **kwargs) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        def _create():
            return self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=kwargs.get("temperature", self._temperature),
                max_tokens=kwargs.get("max_tokens", self._max_tokens),
            )

        try:
            response = self._call_with_retry(
                _create, retry_times=kwargs.get("retry_times", self._retry_times)
            )
            content = response.choices[0].message.content or ""
            usage = response.usage
            if usage:
                self._log_usage(usage.prompt_tokens, usage.completion_tokens)
            return content
        except Exception as e:
            logger.error(f"OpenAI API 调用失败: {e}")
            return f"（LLM 分析暂时不可用：{e}，请检查 API 配置）"

    def _log_usage(self, prompt_tokens: int, completion_tokens: int):
        try:
            cost = self._estimate_cost(prompt_tokens, completion_tokens)
            from llm.usage import UsageLogger
            from pathlib import Path
            log_path = Path.home() / ".stock_robot" / "usage.log"
            UsageLogger(log_path).log(self._model, prompt_tokens, completion_tokens, cost)
        except Exception:
            pass

    def _estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        pricing = {
            "gpt-4o": (2.50 / 1_000_000, 10.00 / 1_000_000),
            "gpt-4o-mini": (0.15 / 1_000_000, 0.60 / 1_000_000),
        }
        input_price, output_price = pricing.get(self._model, (0, 0))
        return prompt_tokens * input_price + completion_tokens * output_price
```

- [ ] **Step 5: 修改 `src/llm/claude.py`**

整文件替换为：

```python
"""Anthropic Claude 适配器"""
import logging
from anthropic import Anthropic
from llm.base import LLMBackend

logger = logging.getLogger(__name__)


class ClaudeAdapter(LLMBackend):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6",
                 temperature: float = 0.3, max_tokens: int = 2000,
                 base_url: str | None = None, timeout: float = 60.0,
                 retry_times: int = 2):
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._retry_times = retry_times
        client_kwargs = {"api_key": api_key, "timeout": timeout}
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = Anthropic(**client_kwargs)

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, prompt: str, system: str | None = None, **kwargs) -> str:
        def _create():
            kwargs_dict = {
                "model": self._model,
                "max_tokens": kwargs.get("max_tokens", self._max_tokens),
                "temperature": kwargs.get("temperature", self._temperature),
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                kwargs_dict["system"] = system
            return self._client.messages.create(**kwargs_dict)

        try:
            response = self._call_with_retry(
                _create, retry_times=kwargs.get("retry_times", self._retry_times)
            )
            content = ""
            for block in response.content:
                if hasattr(block, "text"):
                    content += block.text

            usage = response.usage
            if usage:
                self._log_usage(usage.input_tokens, usage.output_tokens)
            return content
        except Exception as e:
            logger.error(f"Claude API 调用失败: {e}")
            return f"（LLM 分析暂时不可用：{e}，请检查 API 配置）"

    def _log_usage(self, prompt_tokens: int, completion_tokens: int):
        try:
            cost = self._estimate_cost(prompt_tokens, completion_tokens)
            from llm.usage import UsageLogger
            from pathlib import Path
            log_path = Path.home() / ".stock_robot" / "usage.log"
            UsageLogger(log_path).log(self._model, prompt_tokens, completion_tokens, cost)
        except Exception:
            pass

    def _estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        pricing = {
            "claude-opus-4-7": (15.00 / 1_000_000, 75.00 / 1_000_000),
            "claude-sonnet-4-6": (3.00 / 1_000_000, 15.00 / 1_000_000),
            "claude-haiku-4-5-20251001": (0.80 / 1_000_000, 4.00 / 1_000_000),
        }
        input_price, output_price = pricing.get(self._model, (0, 0))
        return prompt_tokens * input_price + completion_tokens * output_price
```

- [ ] **Step 6: 修改 `src/utils/config.py` DEFAULT_CONFIG**

在 `DEFAULT_CONFIG` 的 `"llm"` 字典中 `"max_tokens": 2000,` 之后加两行：

```python
        "retry_times": 2,
        "timeout_seconds": 60,
```

- [ ] **Step 7: 更新既有 LLM 测试以匹配新构造签名**

`tests/llm/test_openai.py`：
- `test_generate_with_error_returns_data_only_message`（第 30-39 行）：在 `mocker.patch(...)` 行后加 `mocker.patch("llm.base.time.sleep")`（避免重试睡眠拖慢测试）
- `test_base_url_passed_to_client`（第 41-46 行）：断言改为

```python
        mock_openai.assert_called_once_with(
            api_key="sk-test", base_url="https://api.deepseek.com/v1", timeout=60.0
        )
```

- `test_no_base_url_omits_arg`（第 48-51 行）：断言改为

```python
        mock_openai.assert_called_once_with(api_key="sk-test", timeout=60.0)
```

`tests/llm/test_claude.py` 做同样三处修改：
- `test_generate_with_error_returns_fallback` 加 `mocker.patch("llm.base.time.sleep")`
- `test_base_url_passed_to_client` 断言改为

```python
        mock_anthropic.assert_called_once_with(
            api_key="sk-ant-test", base_url="https://api.deepseek.com/anthropic", timeout=60.0
        )
```

- `test_no_base_url_omits_arg` 断言改为

```python
        mock_anthropic.assert_called_once_with(api_key="sk-ant-test", timeout=60.0)
```

- [ ] **Step 8: 运行 LLM 全部测试**

```bash
cd /d/code/stock_robot && python -m pytest tests/llm/ -q
```

Expected: 全绿（含新增 test_retry.py 4 个测试）

- [ ] **Step 9: 提交**

```bash
cd /d/code/stock_robot
git add src/llm/base.py src/llm/openai.py src/llm/claude.py src/utils/config.py tests/llm/
git commit -m "feat(LLM): 添加重试（2次指数退避）与超时（60s）支持"
```

---

### Task 3: pipeline_tools 消除事件循环阻塞与重建 Pipeline

**Files:**
- Modify: `src/agent/pipeline_tools.py`
- Test: `tests/agent/test_pipeline_tools.py`（新增 1 个测试，其余应保持绿）

- [ ] **Step 1: 新增失败测试**

`tests/agent/test_pipeline_tools.py` 的 `TestAnalyzeStockTool` 类中新增：

```python
    @pytest.mark.asyncio
    async def test_execute_returns_error_when_pipeline_not_injected(self):
        tool = AnalyzeStockTool()
        result = await tool.execute(symbol="000001")
        assert result.status == "error"
        assert "未注入" in (result.error or "")
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /d/code/stock_robot && python -m pytest tests/agent/test_pipeline_tools.py::TestAnalyzeStockTool::test_execute_returns_error_when_pipeline_not_injected -v
```

Expected: FAIL（当前会走 `_get_pipeline()` 静态构建，构造真实 Pipeline 而不是返回"未注入"错误）

- [ ] **Step 3: 修改 `src/agent/pipeline_tools.py`**

（1）文件头部导入区（`logger = logging.getLogger(__name__)` 之后）加：

```python
import asyncio
```

（2）`AnalyzeStockTool.execute`（当前第 115-156 行）整体替换为：

```python
    async def execute(self, **kwargs) -> ToolResult:
        symbol = str(kwargs.get("symbol", "")).strip()
        if not symbol:
            return ToolResult(status="error", error="股票代码不能为空",
                             metadata={"source": "pipeline"})

        if self._pipeline is None:
            return ToolResult(status="error", error="Pipeline 未注入，无法执行分析",
                             metadata={"source": "pipeline"})

        from data.schemas import AnalysisTarget

        try:
            target = AnalysisTarget(
                target_type="stock", symbol=symbol,
                name=symbol, market="a-shares",
            )
            pipe_result = await asyncio.to_thread(
                self._pipeline.run, targets=[target]
            )
            errors = pipe_result.errors
            reports = pipe_result.reports

            if errors:
                return ToolResult(status="error", error="; ".join(errors),
                                 metadata={"source": "pipeline", "symbol": symbol})
            if not reports:
                return ToolResult(status="error",
                                 error=f"未能生成 {symbol} 的分析报告",
                                 metadata={"source": "pipeline", "symbol": symbol})

            report = reports[0]
            return ToolResult(
                status="success",
                data={
                    "code": report.code,
                    "name": report.name,
                    "overview": report.overview,
                    "comments": getattr(report, "comments", []),
                    "dimensions": getattr(report, "dimensions", {}),
                },
                metadata={"source": "pipeline", "symbol": symbol},
            )
        except Exception as e:  # noqa: BLE001 — 工具执行隔离，失败以 ToolResult 返回
            logger.error(f"analyze_stock 执行失败: {e}")
            return ToolResult(status="error", error=str(e),
                             metadata={"source": "pipeline", "symbol": symbol})
```

（3）删除 `AnalyzeStockTool._get_pipeline` 静态方法（当前第 158-202 行整块）。

（4）`AnalyzeIndexTool.execute`（当前第 226-270 行）整体替换为：

```python
    async def execute(self, **kwargs) -> ToolResult:
        symbol = str(kwargs.get("symbol", "")).strip()
        if not symbol:
            return ToolResult(status="error", error="指数代码不能为空",
                             metadata={"source": "pipeline"})

        if self._pipeline is None:
            return ToolResult(status="error", error="IndexPipeline 未注入，无法执行分析",
                             metadata={"source": "pipeline"})

        from data.index_mapping import IndexMapping
        from data.schemas import AnalysisTarget

        try:
            mapping = IndexMapping()
            entry = mapping.lookup(symbol)
            target = AnalysisTarget(
                target_type="index", symbol=symbol,
                name=entry.name if entry else symbol,
                market="a-shares",
                index_style=entry.index_style if entry else "broad",
            )
            result = await asyncio.to_thread(self._pipeline.run, targets=[target])

            if result.errors:
                return ToolResult(status="error", error="; ".join(result.errors),
                                 metadata={"source": "pipeline", "symbol": symbol})
            if not result.reports:
                return ToolResult(status="error",
                                 error=f"未能生成指数 {symbol} 的分析报告",
                                 metadata={"source": "pipeline", "symbol": symbol})

            report = result.reports[0]
            return ToolResult(
                status="success",
                data={
                    "code": report.code,
                    "name": report.name,
                    "overview": report.overview,
                    "composite_comment": getattr(report, "composite_comment", ""),
                    "position_coeff": getattr(report, "position_coeff", None),
                },
                metadata={"source": "pipeline", "symbol": symbol},
            )
        except Exception as e:  # noqa: BLE001 — 工具执行隔离，失败以 ToolResult 返回
            logger.error(f"analyze_index 执行失败: {e}")
            return ToolResult(status="error", error=str(e),
                             metadata={"source": "pipeline", "symbol": symbol})
```

（5）删除 `AnalyzeIndexTool._get_pipeline` 静态方法（当前第 272-275 行）。

（6）`GetSnapshotTool.execute`（当前第 299-319 行）整体替换为：

```python
    async def execute(self, **kwargs) -> ToolResult:
        symbol = str(kwargs.get("symbol", "")).strip()
        if not symbol:
            return ToolResult(status="error", error="指数代码不能为空",
                             metadata={"source": "pipeline"})

        if self._pipeline is None:
            return ToolResult(status="error", error="IndexPipeline 未注入，无法查询快照",
                             metadata={"source": "pipeline"})

        try:
            data = await asyncio.to_thread(self._pipeline.get_snapshot, symbol)
            if data is None:
                return ToolResult(
                    status="error",
                    error=f"无法获取指数 {symbol} 的估值快照，可能该指数不支持估值查询",
                    metadata={"source": "pipeline", "symbol": symbol},
                )
            return ToolResult(status="success", data=data,
                             metadata={"source": "pipeline", "symbol": symbol})
        except Exception as e:  # noqa: BLE001 — 工具执行隔离，失败以 ToolResult 返回
            logger.error(f"get_snapshot 执行失败: {e}")
            return ToolResult(status="error", error=str(e),
                             metadata={"source": "pipeline", "symbol": symbol})
```

（7）删除 `GetSnapshotTool._get_pipeline` 静态方法（当前第 321-324 行）。

- [ ] **Step 4: 运行 pipeline_tools 全量测试**

```bash
cd /d/code/stock_robot && python -m pytest tests/agent/test_pipeline_tools.py -q
```

Expected: 全绿。既有 mock pipeline 测试在 `to_thread` 下行为不变（异常仍会传播到 execute 内的 try/except）。

- [ ] **Step 5: 提交**

```bash
cd /d/code/stock_robot
git add src/agent/pipeline_tools.py tests/agent/test_pipeline_tools.py
git commit -m "fix(工具): pipeline 工具用 asyncio.to_thread 包装同步调用并移除重建逻辑，pipeline 一律注入"
```

---

### Task 4: 评分逻辑抽取为共享模块

**Files:**
- Create: `src/report/scoring.py`
- Modify: `src/stock_robot/cli.py`（analyze 命令改用共享函数，删除内联评分代码）
- Test: `tests/report/test_scoring.py`（新增）

- [ ] **Step 1: 编写失败测试 `tests/report/test_scoring.py`**

```python
"""评分计算与报告组装测试"""
from types import SimpleNamespace

import pytest

from report.scoring import build_report, compute_price_info, compute_score_summary


def _result(dimension, score, status="ok", risk_flags=None, score_detail=""):
    from data.schemas import AnalysisResult
    return AnalysisResult(
        dimension=dimension, status=status, summary=f"{dimension} 摘要",
        score=score, score_detail=score_detail,
        risk_flags=risk_flags or [],
    )


class TestComputeScoreSummary:
    def test_weighted_base_score(self):
        results = [
            _result("financial", 8.0),
            _result("technical", 6.0),
            _result("valuation", 4.0),
            _result("industry", 9.0),
        ]
        summary = compute_score_summary(results)
        # 8.0*0.30 + 6.0*0.20 + 4.0*0.25 + 9.0*0.25 = 6.85 → round(…, 1) = 6.8
        assert summary.base_score == 6.8
        assert summary.risk_deduction == 0
        assert summary.final_score == 6.8

    def test_risk_flags_deduct_capped_at_10(self):
        flags = [f"风险{i}" for i in range(12)]
        results = [
            _result("financial", 8.0, risk_flags=flags),
        ]
        summary = compute_score_summary(results)
        assert summary.risk_deduction == 10
        assert summary.final_score == max(0, summary.base_score - 10)
        assert len(summary.risk_flags) == 12

    def test_missing_dimension_not_scored(self):
        results = [_result("financial", 7.0)]
        summary = compute_score_summary(results)
        assert summary.base_score == 7.0  # 仅财务维度：7.0*0.30/0.30
        assert summary.score_rows[0]["label"] == "财务健康"
        assert summary.score_rows[0]["score"] == "7.0"
        assert summary.score_rows[0]["weight"] == "30%"

    def test_none_score_shown_as_na(self):
        results = [_result("financial", None)]
        summary = compute_score_summary(results)
        assert summary.score_rows[0]["score"] == "N/A"


class TestComputePriceInfo:
    def test_price_position(self):
        ctx = SimpleNamespace(price_data=[
            SimpleNamespace(high=10.0, low=2.0, close=4.0),
            SimpleNamespace(high=12.0, low=3.0, close=6.0),
        ])
        info = compute_price_info(ctx)
        assert info["year_high"] == 12.0
        assert info["year_low"] == 2.0
        assert info["latest_price"] == 6.0
        assert info["price_position"] == "40%"

    def test_no_price_data(self):
        ctx = SimpleNamespace(price_data=[])
        info = compute_price_info(ctx)
        assert info["year_high"] is None
        assert info["price_position"] == "暂无"


class TestBuildReport:
    def test_builds_report_with_scores(self, mocker):
        mock_builder_cls = mocker.patch("report.builder.ReportBuilder")
        mock_builder = mock_builder_cls.return_value
        mock_builder.build.return_value = "RENDERED_REPORT"

        results = [_result("financial", 8.0)]
        ctx = SimpleNamespace(price_data=[], industry_data=None)
        report = build_report("000001", "平安银行", results, {"bulk": "解读"},
                              ctx, no_llm=False)

        assert report == "RENDERED_REPORT"
        call_kwargs = mock_builder.build.call_args.kwargs
        assert call_kwargs["symbol"] == "000001"
        assert call_kwargs["industry"] == "未知"
        assert call_kwargs["base_score"] == 8.0
        assert call_kwargs["final_score"] == 8.0
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /d/code/stock_robot && python -m pytest tests/report/test_scoring.py -q
```

Expected: FAIL（`ModuleNotFoundError: No module named 'report.scoring'`）

- [ ] **Step 3: 创建 `src/report/scoring.py`**

```python
"""评分计算与报告组装 — CLI 与 API 共用"""
from dataclasses import dataclass, field

DIM_WEIGHTS = {"financial": 0.30, "technical": 0.20,
               "valuation": 0.25, "industry": 0.25}
DIM_LABELS = {"financial": "财务健康", "technical": "技术趋势",
              "valuation": "估值合理", "industry": "行业对比",
              "sentiment": "舆情风险"}
DIM_WEIGHT_LABELS = {"financial": "30%", "technical": "20%",
                     "valuation": "25%", "industry": "25%",
                     "sentiment": "不计分"}
SUFFICIENCY_LABEL = {"ok": "充足", "partial": "部分可用", "unavailable": "数据不足"}


@dataclass
class ScoreSummary:
    score_rows: list[dict] = field(default_factory=list)
    base_score: float = 0.0
    risk_deduction: int = 0
    final_score: float = 0.0
    risk_flags: list[str] = field(default_factory=list)


def compute_score_summary(results) -> ScoreSummary:
    """维度加权得分 + 风险扣分"""
    results_map = {r.dimension: r for r in results}
    score_rows = []
    all_risk_flags = []

    base_score = 0.0
    total_weight = 0.0
    for dim, weight in DIM_WEIGHTS.items():
        r = results_map.get(dim)
        if r and r.score is not None:
            base_score += r.score * weight
            total_weight += weight
    if total_weight > 0:
        base_score = round(base_score / total_weight, 1)

    risk_deduction = 0
    for r in results:
        risk_deduction += len(r.risk_flags)
    risk_deduction = min(risk_deduction, 10)
    final_score = max(0, base_score - risk_deduction)

    for dim, label in DIM_LABELS.items():
        r = results_map.get(dim)
        if r:
            score_rows.append({
                "label": label,
                "score": f"{r.score:.1f}" if r.score is not None else "N/A",
                "weight": DIM_WEIGHT_LABELS[dim],
                "sufficiency": SUFFICIENCY_LABEL.get(r.status, r.status),
                "detail": r.score_detail or "",
            })
        all_risk_flags.extend(r.risk_flags if r else [])

    return ScoreSummary(score_rows=score_rows, base_score=base_score,
                        risk_deduction=risk_deduction, final_score=final_score,
                        risk_flags=all_risk_flags)


def compute_price_info(ctx) -> dict:
    """从价格序列计算年内高低点与当前位置"""
    price_data = ctx.price_data or []
    year_high = max(p.high for p in price_data) if price_data else None
    year_low = min(p.low for p in price_data) if price_data else None
    latest_price = price_data[-1].close if price_data else None
    if year_high and year_low and latest_price and (year_high - year_low) > 0:
        pct = (latest_price - year_low) / (year_high - year_low) * 100
        price_position = f"{pct:.0f}%"
    else:
        price_position = "暂无"
    return {"year_high": year_high, "year_low": year_low,
            "latest_price": latest_price, "price_position": price_position}


def build_report(symbol, name, results, commentary, ctx,
                 no_llm: bool = False, market_env: dict | None = None) -> str:
    """组装完整报告文本（ReportBuilder 渲染）"""
    from report.builder import ReportBuilder

    summary = compute_score_summary(results)
    price_info = compute_price_info(ctx)
    industry = ctx.industry_data.industry if ctx.industry_data else "未知"

    builder = ReportBuilder()
    return builder.build(
        symbol, name, results, commentary,
        no_llm=no_llm,
        industry=industry,
        year_high=f"{price_info['year_high']:.2f}" if price_info['year_high'] else "暂无",
        year_low=f"{price_info['year_low']:.2f}" if price_info['year_low'] else "暂无",
        price_position=price_info['price_position'],
        score_rows=summary.score_rows,
        base_score=summary.base_score,
        risk_deduction=summary.risk_deduction,
        final_score=summary.final_score,
        risk_flags=summary.risk_flags,
        market_env=market_env,
    )
```

- [ ] **Step 4: 运行确认通过**

```bash
cd /d/code/stock_robot && python -m pytest tests/report/test_scoring.py -q
```

Expected: 7 passed

- [ ] **Step 5: 改造 `src/stock_robot/cli.py` 的 analyze 命令**

（1）删除函数内 `from report.builder import ReportBuilder` 导入（当前第 131 行）。

（2）将当前第 187-269 行的整块内联评分代码（从 `# 计算综合打分` 到 `report = builder.build(...)` 调用结束）替换为：

```python
    # 大盘环境快照（可选，供报告中嵌入指数环境摘要）
    market_env = None
    if with_market:
        try:
            from index.pipeline import IndexPipeline
            index_pipeline = IndexPipeline()
            snapshot = index_pipeline.get_snapshot("000300")
            if snapshot:
                market_env = snapshot
        except Exception:
            pass

    from report.scoring import build_report
    report = build_report(symbol, name, results, commentary, ctx,
                          no_llm=no_llm, market_env=market_env)
```

注意：原代码中 `market_env` 块位于第 243-253 行（在 `builder = ReportBuilder()` 之前），替换后顺序变为 market_env 先于 build_report，行为等价。

（3）`saved_path` 与 `console.print` 部分保持不变（当前第 271-273 行）。

- [ ] **Step 6: 运行 CLI 相关测试确认无回归**

```bash
cd /d/code/stock_robot && python -m pytest tests/test_cli.py tests/report/ -q
```

Expected: 全绿

- [ ] **Step 7: 提交**

```bash
cd /d/code/stock_robot
git add src/report/scoring.py tests/report/test_scoring.py src/stock_robot/cli.py
git commit -m "refactor(报告): 抽取评分计算与报告组装为 report.scoring 共享模块"
```

---

### Task 5: bootstrap 组装函数

**Files:**
- Create: `src/api/bootstrap.py`
- Test: `tests/api/test_bootstrap.py`（新增）

- [ ] **Step 1: 编写失败测试 `tests/api/test_bootstrap.py`**

```python
"""bootstrap 组装测试"""
import pytest

from api.bootstrap import build_agent_core
from utils.config import Config


@pytest.fixture
def config(tmp_path):
    return Config(config_dir=tmp_path)


class TestBuildAgentCore:
    def test_pipeline_injected_into_tools(self, config, mocker):
        mocker.patch("rag.engine.RAGEngine", side_effect=RuntimeError("无 ChromaDB"))
        core = build_agent_core(config)
        tool = core.registry.get("analyze_stock")
        assert tool is not None
        assert tool._pipeline is not None

    def test_rag_unavailable_skipped(self, config, mocker):
        mocker.patch("rag.engine.RAGEngine", side_effect=RuntimeError("无 ChromaDB"))
        core = build_agent_core(config)
        names = {t.name for t in core.registry.list_all()}
        assert "analyze_stock" in names
        assert "rag_search" not in names
        assert "rag_list_sources" not in names

    def test_rag_registered_when_available(self, config, mocker):
        fake_engine = mocker.Mock()
        fake_engine.embedding_name = "test-embedding"
        mocker.patch("rag.engine.RAGEngine", return_value=fake_engine)
        core = build_agent_core(config)
        names = {t.name for t in core.registry.list_all()}
        assert "rag_search" in names
        assert "rag_list_sources" in names

    def test_llm_none_when_no_api_key(self, config, mocker):
        mocker.patch("rag.engine.RAGEngine", side_effect=RuntimeError("无 ChromaDB"))
        core = build_agent_core(config)
        assert core.llm is None
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /d/code/stock_robot && python -m pytest tests/api/test_bootstrap.py -q
```

Expected: FAIL（`ModuleNotFoundError: No module named 'api.bootstrap'`）

- [ ] **Step 3: 创建 `src/api/bootstrap.py`**

```python
"""Agent 核心组装 — CLI chat 与 API 共用的依赖装配"""
import logging
from dataclasses import dataclass

from agent.pipeline_tools import (
    AnalyzeIndexTool,
    AnalyzeStockTool,
    GetSnapshotTool,
    ScreenStocksTool,
    _wrap_pipeline,
)
from agent.tools import ToolRegistry
from llm.base import LLMBackend

logger = logging.getLogger(__name__)


@dataclass
class AgentCore:
    """Agent 运行所需的核心依赖集合"""
    registry: ToolRegistry
    pipeline: object      # 真实 Pipeline（run(symbol, name, market) 接口）
    index_pipeline: object
    llm: LLMBackend | None = None


def build_llm(config) -> LLMBackend | None:
    """按配置构建 LLM 后端；未配置 api_key 或初始化失败返回 None"""
    provider = config.get("llm.provider", "openai")
    api_key = config.get("llm.api_key", "")
    base_url = config.get("llm.base_url", "") or None
    retry_times = config.get("llm.retry_times", 2)
    timeout = config.get("llm.timeout_seconds", 60)

    if not api_key:
        return None
    try:
        if provider == "openai":
            from llm.openai import OpenAIAdapter
            return OpenAIAdapter(
                api_key=api_key,
                model=config.get("llm.model", "gpt-4o"),
                temperature=config.get("llm.temperature", 0.3),
                max_tokens=config.get("llm.max_tokens", 2000),
                base_url=base_url,
                timeout=timeout,
                retry_times=retry_times,
            )
        elif provider == "claude":
            from llm.claude import ClaudeAdapter
            return ClaudeAdapter(
                api_key=api_key,
                model=config.get("llm.model", "claude-sonnet-4-6"),
                temperature=config.get("llm.temperature", 0.3),
                max_tokens=config.get("llm.max_tokens", 2000),
                base_url=base_url,
                timeout=timeout,
                retry_times=retry_times,
            )
    except Exception as e:
        logger.warning(f"LLM 后端初始化失败: {e}")
    return None


def build_agent_core(config=None, llm_enabled: bool | None = None) -> AgentCore:
    """组装 Agent 完整依赖：Pipeline、工具注册表、LLM

    Pipeline/IndexPipeline 各构建一次并注入工具，消除每次工具调用重建的开销。
    """
    from analysis.financial import FinancialAnalyzer
    from analysis.industry import IndustryAnalyzer
    from analysis.sentiment import SentimentAnalyzer
    from analysis.technical import TechnicalAnalyzer
    from analysis.valuation import ValuationAnalyzer
    from core.pipeline import Pipeline
    from core.registry import Registry
    from data.akshare import AkShareAdapter
    from index.pipeline import IndexPipeline
    from utils.config import Config

    config = config or Config()
    llm = build_llm(config)

    reg = Registry()
    reg.register_data_source(AkShareAdapter())
    reg.register_analysis_module(FinancialAnalyzer())
    reg.register_analysis_module(TechnicalAnalyzer())
    reg.register_analysis_module(ValuationAnalyzer())
    reg.register_analysis_module(IndustryAnalyzer())
    reg.register_analysis_module(SentimentAnalyzer())
    if llm is not None:
        reg.register_llm_backend(llm, provider=config.get("llm.provider", "openai"))

    if llm_enabled is None:
        llm_enabled = config.get("llm.enabled", True)
    pipeline = Pipeline(registry=reg, config=config,
                        llm_enabled=llm_enabled and llm is not None)
    index_pipeline = IndexPipeline()

    registry = ToolRegistry()
    registry.register(AnalyzeStockTool(pipeline=_wrap_pipeline(pipeline)))
    registry.register(AnalyzeIndexTool(index_pipeline=index_pipeline))
    registry.register(GetSnapshotTool(index_pipeline=index_pipeline))
    registry.register(ScreenStocksTool())

    # RAG 工具（ChromaDB 不可用则静默跳过，与 CLI 原行为一致）
    try:
        from agent.rag_tools import RAGListSourcesTool, RAGSearchTool
        from rag.engine import RAGEngine

        rag_engine = RAGEngine()
        registry.register(RAGSearchTool(engine=rag_engine))
        registry.register(RAGListSourcesTool(engine=rag_engine))
        logger.info("RAG 工具已注册 (embedding=%s)", rag_engine.embedding_name)
    except Exception as e:
        logger.warning("RAG 工具不可用，跳过注册: %s", e)

    return AgentCore(registry=registry, pipeline=pipeline,
                     index_pipeline=index_pipeline, llm=llm)
```

- [ ] **Step 4: 运行确认通过**

```bash
cd /d/code/stock_robot && python -m pytest tests/api/test_bootstrap.py -q
```

Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
cd /d/code/stock_robot
git add src/api/bootstrap.py tests/api/test_bootstrap.py
git commit -m "feat(API): 添加 build_agent_core 组装函数，Pipeline 单例注入工具"
```

---

### Task 6: app.py 接线修复与端点实现

**Files:**
- Modify: `src/api/app.py`（整文件重写）
- Test: `tests/api/test_app.py`（整文件重写）

- [ ] **Step 1: 重写测试 `tests/api/test_app.py`**

整文件替换为：

```python
"""FastAPI HTTP API 端点测试（真实接线：Planner/Executor/SessionManager）"""
import json

import pytest
from httpx import ASGITransport, AsyncClient

from api.app import create_app
from api.bootstrap import AgentCore
from api.sessions import SessionManager, SessionStore
from agent.tools import ToolRegistry, ToolResult


class FakeLLM:
    """返回合法 JSON 计划的 LLM，验证 Planner 解析路径"""

    def generate(self, prompt, system=None, **kwargs):
        return json.dumps({
            "goal": "测试目标",
            "complexity": "simple",
            "steps": [{"id": "step-1", "description": "echo 测试"}],
        }, ensure_ascii=False)


class EchoTool:
    name = "echo"
    description = "回显工具，echo 输入内容"
    parameters = {"type": "object", "properties": {"text": {"type": "string"}}}
    tags = ["test"]
    source = "pipeline"

    async def execute(self, **kwargs):
        return ToolResult(status="success", data={"echo": kwargs.get("text", "")})


class FakePipeline:
    def run(self, symbol, name, market="a-shares"):
        from data.schemas import AnalysisContext, AnalysisResult
        results = [AnalysisResult(
            dimension="financial", status="ok", summary="财务健康", score=8.0,
        )]
        return results, {"bulk": "AI 解读"}, AnalysisContext(
            symbol=symbol, name=name, market=market,
        )


class FakeIndexReport:
    def __init__(self):
        self.code = "000300"
        self.name = "沪深300"

    def model_dump(self, mode="json"):
        return {"code": self.code, "name": self.name}


class FakeIndexPipeline:
    def run(self, targets, on_progress=None):
        from types import SimpleNamespace
        return SimpleNamespace(reports=[FakeIndexReport()], errors=[])


def make_core():
    registry = ToolRegistry()
    registry.register(EchoTool())
    return AgentCore(registry=registry, pipeline=FakePipeline(),
                     index_pipeline=FakeIndexPipeline(), llm=FakeLLM())


@pytest.fixture
def app(tmp_path):
    store = SessionStore(tmp_path / "sessions.db")
    sessions = SessionManager(store, facts_path=tmp_path / "facts.json")
    return create_app(core=make_core(), sessions=sessions)


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestToolsEndpoint:
    @pytest.mark.asyncio
    async def test_list_tools(self, client):
        resp = await client.get("/api/v1/tools")
        assert resp.status_code == 200
        tools = resp.json()["tools"]
        assert len(tools) == 1
        assert tools[0]["name"] == "echo"


class TestChatEndpoint:
    @pytest.mark.asyncio
    async def test_chat_runs_agent_end_to_end(self, client):
        resp = await client.post("/api/v1/chat", json={"message": "echo 测试"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"]
        assert "完成: 1/1 步骤" in data["response"]
        assert data["plan"]["steps"][0]["status"] == "done"
        assert any("echo" in t for t in data["tool_results"])

    @pytest.mark.asyncio
    async def test_chat_empty_message_returns_422(self, client):
        resp = await client.post("/api/v1/chat", json={"message": ""})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_chat_with_session_id_reuses_session(self, client):
        r1 = await client.post("/api/v1/chat", json={"message": "echo 一"})
        sid = r1.json()["session_id"]
        r2 = await client.post("/api/v1/chat", json={"message": "echo 二", "session_id": sid})
        assert r2.status_code == 200
        assert r2.json()["session_id"] == sid


class TestStreamEndpoint:
    @pytest.mark.asyncio
    async def test_stream_returns_sse(self, client):
        resp = await client.post("/api/v1/chat/stream", json={"message": "echo 测试"})
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

    @pytest.mark.asyncio
    async def test_stream_contains_plan_and_done_events(self, client):
        async with client.stream("POST", "/api/v1/chat/stream",
                                 json={"message": "echo 测试"}) as resp:
            assert resp.status_code == 200
            body = ""
            async for line in resp.aiter_lines():
                body += line
        assert '"type": "start"' in body
        assert '"type": "plan"' in body
        assert '"type": "done"' in body


class TestAnalyzeEndpoint:
    @pytest.mark.asyncio
    async def test_analyze_returns_report_json(self, client, mocker):
        mocker.patch("utils.symbols.resolve_name", return_value="平安银行")
        resp = await client.post("/api/v1/analyze", json={"symbol": "000001"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "000001"
        assert data["name"] == "平安银行"
        assert data["dimensions"]["financial"]["score"] == 8.0
        assert data["score"]["base"] == 8.0
        assert data["commentary"] == "AI 解读"

    @pytest.mark.asyncio
    async def test_analyze_invalid_symbol_returns_422(self, client):
        resp = await client.post("/api/v1/analyze", json={"symbol": "abc"})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_analyze_empty_symbol_returns_422(self, client):
        resp = await client.post("/api/v1/analyze", json={"symbol": ""})
        assert resp.status_code == 422


class TestIndexEndpoint:
    @pytest.mark.asyncio
    async def test_index_returns_report_json(self, client):
        resp = await client.post("/api/v1/index", json={"symbol": "000300"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["reports"][0]["code"] == "000300"
        assert data["errors"] == []

    @pytest.mark.asyncio
    async def test_index_invalid_symbol_returns_422(self, client):
        resp = await client.post("/api/v1/index", json={"symbol": "###"})
        assert resp.status_code == 422


class TestSessionsEndpoints:
    @pytest.mark.asyncio
    async def test_sessions_crud(self, client):
        create_resp = await client.post("/api/v1/sessions")
        assert create_resp.status_code == 200
        sid = create_resp.json()["session_id"]

        list_resp = await client.get("/api/v1/sessions")
        ids = [s["session_id"] for s in list_resp.json()["sessions"]]
        assert sid in ids

        clear_resp = await client.post(f"/api/v1/sessions/{sid}/clear")
        assert clear_resp.status_code == 200

        del_resp = await client.delete(f"/api/v1/sessions/{sid}")
        assert del_resp.status_code == 200

        del_again = await client.delete(f"/api/v1/sessions/{sid}")
        assert del_again.status_code == 404

    @pytest.mark.asyncio
    async def test_clear_unknown_session_returns_404(self, client):
        resp = await client.post("/api/v1/sessions/nope/clear")
        assert resp.status_code == 404


class TestNoCoreMode:
    @pytest.fixture
    def empty_app(self):
        return create_app()

    @pytest.fixture
    async def empty_client(self, empty_app):
        transport = ASGITransport(app=empty_app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    @pytest.mark.asyncio
    async def test_chat_returns_not_injected_message(self, empty_client):
        resp = await empty_client.post("/api/v1/chat", json={"message": "你好"})
        assert resp.status_code == 200
        assert "Agent 核心未注入" in resp.json()["response"]

    @pytest.mark.asyncio
    async def test_analyze_returns_503(self, empty_client):
        resp = await empty_client.post("/api/v1/analyze", json={"symbol": "000001"})
        assert resp.status_code == 503
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /d/code/stock_robot && python -m pytest tests/api/test_app.py -q
```

Expected: 大量 FAIL（现有 app.py 无法处理 core/sessions 参数；chat 会 500 因为 `await planner.plan` 与签名不匹配）

- [ ] **Step 3: 重写 `src/api/app.py`**

整文件替换为：

```python
"""FastAPI HTTP API — REST + SSE 流式接口"""
import asyncio
import json
import logging
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from agent.executor import Executor
from agent.memory import TaskStatus
from agent.planner import Planner

logger = logging.getLogger(__name__)


def _json_safe(payload) -> dict:
    """将 payload 中的非 JSON 类型（date 等）转为字符串后重新解析"""
    return json.loads(json.dumps(payload, ensure_ascii=False, default=str))


def create_app(core=None, sessions=None):
    app = FastAPI(title="Stock Robot API", version="0.1.0",
                  description="AI 驱动的股票分析研报助手 HTTP API")

    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                       allow_headers=["*"])

    if sessions is None and core is not None:
        from api.sessions import SessionManager, SessionStore
        from utils.config import Config
        sessions = SessionManager(SessionStore(Config().config_dir / "sessions.db"))

    def _build_agent(session_memory):
        """按会话现建轻量 Planner/Executor（两者无状态，构造廉价）"""
        planner = Planner(llm=core.llm, registry=core.registry, memory=session_memory)
        executor = Executor(registry=core.registry, memory=session_memory)
        return planner, executor

    def _extract_session_id(body: dict, request: Request):
        return body.get("session_id") or request.headers.get("X-Session-Id")

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/api/v1/tools")
    async def list_tools():
        if core is None:
            return JSONResponse({"tools": []})
        return JSONResponse({"tools": core.registry.list_all_summary(),
                             "total": len(core.registry.list_all())})

    @app.post("/api/v1/chat")
    async def chat(request: Request):
        body = await request.json()
        message = str(body.get("message", "")).strip()
        session_id = _extract_session_id(body, request)
        if not message:
            raise HTTPException(status_code=422, detail="message 不能为空")
        if core is None or sessions is None:
            return JSONResponse({"response": f"[API 模式] 收到消息: {message}（Agent 核心未注入）",
                                 "session_id": session_id or ""})

        try:
            sid, memory = sessions.get_or_create(session_id, message)
            memory.add_message("user", message)
            planner, executor = _build_agent(memory)
            plan = await asyncio.to_thread(planner.plan, message)
            plan = await executor.execute(plan)
            done = sum(1 for s in plan.steps if s.status == TaskStatus.DONE)
            total = len(plan.steps)
            tool_results = [m["content"] for m in memory.messages if m["role"] == "tool"]
            return JSONResponse({
                "response": f"目标: {plan.goal}\n完成: {done}/{total} 步骤",
                "plan": {"goal": plan.goal, "steps": [
                    {"id": s.id, "description": s.description, "status": s.status.value}
                    for s in plan.steps
                ]},
                "tool_results": tool_results,
                "session_id": sid,
            })
        except Exception as e:  # noqa: BLE001 — HTTP 边界兜底，返回 500 而非崩溃
            logger.error("Agent 对话失败: %s", e)
            return JSONResponse({"response": f"处理请求时出错: {e}",
                                 "session_id": session_id or ""}, status_code=500)

    @app.post("/api/v1/chat/stream")
    async def chat_stream(request: Request):
        body = await request.json()
        message = str(body.get("message", "")).strip()
        if not message:
            raise HTTPException(status_code=422, detail="message 不能为空")

        async def event_stream():
            yield f"data: {json.dumps({'type': 'start', 'message': message}, ensure_ascii=False)}\n\n"
            if core is None or sessions is None:
                yield f"data: {json.dumps({'type': 'text', 'content': '[API 模式] Agent 核心未注入'}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                return

            session_id = _extract_session_id(body, request)
            queue: asyncio.Queue = asyncio.Queue()

            def on_progress(stage, current, total, label):
                queue.put_nowait({"type": "progress", "stage": stage,
                                  "current": current, "total": total, "label": label})

            async def run_agent():
                try:
                    sid, memory = sessions.get_or_create(session_id, message)
                    memory.add_message("user", message)
                    planner, executor = _build_agent(memory)
                    plan = await asyncio.to_thread(planner.plan, message)
                    await queue.put({"type": "plan", "goal": plan.goal,
                                     "steps": [s.description for s in plan.steps],
                                     "session_id": sid})
                    plan = await executor.execute(plan, on_progress=on_progress)
                    done = sum(1 for s in plan.steps if s.status == TaskStatus.DONE)
                    total = len(plan.steps)
                    await queue.put({"type": "result",
                                     "summary": f"目标: {plan.goal}\n完成: {done}/{total} 步骤"})
                except Exception as e:  # noqa: BLE001 — SSE 流内兜底，错误以事件返回
                    logger.error("Agent 流式对话失败: %s", e)
                    await queue.put({"type": "error", "message": str(e)})
                await queue.put({"type": "done"})

            task = asyncio.create_task(run_agent())
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
                if event["type"] == "done":
                    break
            await task

        return StreamingResponse(event_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})

    @app.post("/api/v1/analyze")
    async def analyze(request: Request):
        body = await request.json()
        symbol = str(body.get("symbol", "")).strip()
        if not symbol:
            raise HTTPException(status_code=422, detail="symbol 不能为空")
        if core is None:
            raise HTTPException(status_code=503, detail="Agent 核心未注入")

        from utils.symbols import normalize_symbol, resolve_name, validate_symbol
        if not validate_symbol(symbol):
            raise HTTPException(status_code=422, detail=f"无效的股票代码: {symbol}")
        symbol = normalize_symbol(symbol)
        name = await asyncio.to_thread(resolve_name, symbol) or symbol

        try:
            results, commentary, ctx = await asyncio.to_thread(
                core.pipeline.run, symbol, name
            )
            from report.scoring import compute_price_info, compute_score_summary

            summary = compute_score_summary(results)
            price_info = compute_price_info(ctx)
            dimensions = {}
            for r in results:
                dimensions[r.dimension] = {
                    "status": r.status, "summary": r.summary, "score": r.score,
                    "score_detail": r.score_detail, "metrics": r.metrics,
                    "risk_flags": r.risk_flags,
                }
            payload = {
                "symbol": symbol,
                "name": name,
                "overview": {
                    "latest_close": price_info["latest_price"],
                    "year_high": price_info["year_high"],
                    "year_low": price_info["year_low"],
                    "price_position": price_info["price_position"],
                    "industry": ctx.industry_data.industry if ctx.industry_data else "未知",
                },
                "score": {"base": summary.base_score, "final": summary.final_score,
                          "risk_deduction": summary.risk_deduction},
                "score_rows": summary.score_rows,
                "dimensions": dimensions,
                "commentary": commentary.get("bulk", ""),
                "generated_at": datetime.now().astimezone().isoformat(),
            }
            return JSONResponse(_json_safe(payload))
        except Exception as e:  # noqa: BLE001 — HTTP 边界兜底
            logger.error("个股分析失败: %s", e)
            return JSONResponse({"symbol": symbol, "error": str(e)}, status_code=500)

    @app.post("/api/v1/index")
    async def index(request: Request):
        body = await request.json()
        symbol = str(body.get("symbol", "")).strip()
        index_style = body.get("index_style")
        if not symbol:
            raise HTTPException(status_code=422, detail="symbol 不能为空")
        if core is None:
            raise HTTPException(status_code=503, detail="Agent 核心未注入")

        from data.index_mapping import IndexMapping
        from data.schemas import AnalysisTarget
        from utils.symbols import normalize_index_symbol, validate_index_symbol

        if not validate_index_symbol(symbol):
            raise HTTPException(status_code=422, detail=f"无效的指数代码: {symbol}")
        normalized = normalize_index_symbol(symbol)
        entry = IndexMapping().lookup(normalized)
        if entry is None:
            if index_style not in ("broad", "sector", "overseas"):
                raise HTTPException(
                    status_code=422,
                    detail=f"无法识别指数 {normalized}，请指定 index_style (broad/sector/overseas)")
            target = AnalysisTarget(target_type="index", symbol=normalized,
                                    name=normalized, market="a-shares",
                                    index_style=index_style)
        else:
            target = AnalysisTarget(target_type="index", symbol=normalized,
                                    name=entry.name, market=entry.market,
                                    index_style=entry.index_style)

        try:
            result = await asyncio.to_thread(core.index_pipeline.run, [target])
            payload = {
                "reports": [r.model_dump(mode="json") for r in result.reports],
                "errors": result.errors,
            }
            return JSONResponse(_json_safe(payload))
        except Exception as e:  # noqa: BLE001 — HTTP 边界兜底
            logger.error("指数分析失败: %s", e)
            return JSONResponse({"symbol": symbol, "error": str(e)}, status_code=500)

    @app.get("/api/v1/sessions")
    async def list_sessions():
        if sessions is None:
            return JSONResponse({"sessions": []})
        return JSONResponse({"sessions": sessions.list_sessions()})

    @app.post("/api/v1/sessions")
    async def create_session():
        if sessions is None:
            raise HTTPException(status_code=503, detail="会话管理未初始化")
        sid, _ = sessions.get_or_create(None)
        return JSONResponse({"session_id": sid})

    @app.delete("/api/v1/sessions/{session_id}")
    async def delete_session(session_id: str):
        if sessions is None:
            raise HTTPException(status_code=503, detail="会话管理未初始化")
        if not sessions.delete(session_id):
            raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
        return JSONResponse({"status": "ok"})

    @app.post("/api/v1/sessions/{session_id}/clear")
    async def clear_session(session_id: str):
        if sessions is None:
            raise HTTPException(status_code=503, detail="会话管理未初始化")
        if not sessions.clear(session_id):
            raise HTTPException(status_code=404, detail=f"会话不存在: {session_id}")
        return JSONResponse({"status": "ok"})

    # 挂载 Web UI 静态文件（必须放在所有 API 路由之后，"/" 挂载会兜底捕获其余路径，
    # 按注册顺序匹配，API 路由优先）
    import os

    from fastapi.staticfiles import StaticFiles

    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.isdir(static_dir):
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app


app = create_app()
```

- [ ] **Step 4: 运行确认通过**

```bash
cd /d/code/stock_robot && python -m pytest tests/api/test_app.py -q
```

Expected: 全绿（约 17 个测试）

注意：若 `test_stream_contains_plan_and_done_events` 因 httpx 流式行为失败，先确认 `client.stream` 用法与 httpx 版本匹配（`httpx` 为 FastAPI TestClient 依赖，版本 ≥0.27 均支持上述写法），不要跳过该测试。

- [ ] **Step 5: 提交**

```bash
cd /d/code/stock_robot
git add src/api/app.py tests/api/test_app.py
git commit -m "feat(API): 修复 Agent 接线，实现 analyze/index/会话端点与 SSE 进度事件"
```

---

### Task 7: CLI api 命令、chat 改造与 README

**Files:**
- Modify: `src/stock_robot/cli.py`
- Modify: `README.md`
- Test: `tests/test_cli.py`（新增 api 命令冒烟测试）

- [ ] **Step 1: 新增冒烟测试**

`tests/test_cli.py` 末尾新增：

```python
def test_api_command_help():
    """api 命令存在且可显示帮助"""
    from click.testing import CliRunner
    from stock_robot.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["api", "--help"])
    assert result.exit_code == 0
    assert "启动 Web API 服务" in result.output
```

- [ ] **Step 2: 运行确认失败**

```bash
cd /d/code/stock_robot && python -m pytest tests/test_cli.py::test_api_command_help -v
```

Expected: FAIL（`No such command 'api'`）

- [ ] **Step 3: 修改 `src/stock_robot/cli.py`**

（1）新增 `api` 命令（放在 `chat` 命令定义之前，即当前第 508 行 `@main.command()` 的 chat 块之前）：

```python
@main.command()
@click.option("--host", default="127.0.0.1", help="监听地址")
@click.option("--port", default=8000, type=int, help="监听端口")
def api(host, port):
    """启动 Web API 服务（含 Web UI）"""
    from api.app import create_app
    from api.bootstrap import build_agent_core
    from utils.config import Config

    config = Config()
    core = build_agent_core(config)
    app = create_app(core=core)
    logger.info("Stock Robot API 启动于 http://%s:%d", host, port)
    import uvicorn
    uvicorn.run(app, host=host, port=port)
```

（2）`chat` 命令主体（当前第 512-557 行）改为：

```python
@main.command()
@click.option("--ask", "-a", default=None, help="单次对话（非交互式）")
@click.option("--verbose", "-v", is_flag=True, help="显示计划和工具调用细节")
def chat(ask, verbose):
    """进入 AI Agent 对话模式，支持复杂投研任务的自主拆解和分析"""
    from agent.executor import Executor
    from agent.memory import Memory
    from agent.planner import Planner
    from api.bootstrap import build_agent_core
    from output.renderer import RichRenderer
    from utils.config import Config

    config = Config()
    renderer = RichRenderer(console=console)
    core = build_agent_core(config)

    memory = Memory()
    planner = Planner(llm=core.llm, registry=core.registry, memory=memory)
    executor = Executor(registry=core.registry, memory=memory)

    if ask:
        _run_agent_query(ask, planner, executor, memory, renderer)
        return

    _run_interactive_chat(planner, executor, memory, renderer)
```

（3）删除 `_get_llm_for_agent` 函数（当前第 646-674 行，已无调用方；LLM 构建由 bootstrap.build_llm 承担）。

- [ ] **Step 4: 运行 CLI 与 agent 相关测试**

```bash
cd /d/code/stock_robot && python -m pytest tests/test_cli.py tests/agent/ -q
```

Expected: 全绿

- [ ] **Step 5: 更新 README 启动说明**

找到 README 中当前 Web 相关段落（含 `PYTHONPATH=src python -m uvicorn api.app:app` 的章节，约 235-245 行），替换为：

```markdown
### Web UI

一键启动（自动注入 Agent 核心）：

```bash
stock-robot api --host 127.0.0.1 --port 8000
```

浏览器打开 http://127.0.0.1:8000 使用 Web 聊天界面。

主要 API 端点：

- `POST /api/v1/chat` — Agent 对话（body: `{"message": "...", "session_id": "可选"}`）
- `POST /api/v1/chat/stream` — SSE 流式对话（start/plan/progress/result/done 事件）
- `POST /api/v1/analyze` — 个股分析（body: `{"symbol": "600519"}`），返回完整报告 JSON
- `POST /api/v1/index` — 指数分析（body: `{"symbol": "000300", "index_style": "可选"}`）
- `GET/POST /api/v1/sessions`、`DELETE /api/v1/sessions/{id}`、`POST /api/v1/sessions/{id}/clear` — 会话管理
- `GET /api/v1/tools` — 工具列表

> 无 Agent 模式（仅调试静态页）：`PYTHONPATH=src python -m uvicorn api.app:app`，
> 该模式下 chat 返回"Agent 核心未注入"提示，analyze/index 返回 503。
```

- [ ] **Step 6: 提交**

```bash
cd /d/code/stock_robot
git add src/stock_robot/cli.py tests/test_cli.py README.md
git commit -m "feat(CLI): 添加 stock-robot api 启动命令，chat 改用 build_agent_core 组装"
```

---

### Task 8: 全量回归与手动验收

**Files:** 无新文件

- [ ] **Step 1: 全量测试**

```bash
cd /d/code/stock_robot && python -m pytest -q
```

Expected: 全部通过（原 73 个测试文件 + 新增 4 个测试文件，无失败）

- [ ] **Step 2: 静态检查**

```bash
cd /d/code/stock_robot && ruff check .
cd /d/code/stock_robot && pyright
```

Expected: 均 0 错误。若 pyright 报 `message_store`/`core` 等参数未标注类型，按项目规范加 `Any` 或 `object` 标注后重跑，不得跳过。

- [ ] **Step 3: 手动验收（无 LLM key 的降级路径）**

```bash
cd /d/code/stock_robot && PYTHONPATH=src python -m stock_robot.cli api --port 8765
```

另开终端：

```bash
# 1. 健康检查
curl http://127.0.0.1:8765/health
# 期望: {"status":"ok","version":"0.1.0"}

# 2. Agent 对话（无 LLM key 时走单步计划 + 确定性工具）
curl -X POST http://127.0.0.1:8765/api/v1/chat -H "Content-Type: application/json" -d '{"message":"帮我分析平安银行"}'
# 期望: HTTP 200，response 含"完成: 1/1 步骤"，session_id 非空

# 3. 个股分析
curl -X POST http://127.0.0.1:8765/api/v1/analyze -H "Content-Type: application/json" -d '{"symbol":"600519"}'
# 期望: HTTP 200，JSON 含 dimensions/scores/commentary（约 10-30 秒，取决于数据源）

# 4. 会话持久化：重启服务后
curl http://127.0.0.1:8765/api/v1/sessions
# 期望: 列表含之前会话，message_count ≥ 1

# 5. 浏览器打开 http://127.0.0.1:8765 验证 Web UI 可加载、可对话
```

- [ ] **Step 4: 若验收发现 bug，用 systematic-debugging 技能修复并补测试，重新跑 Step 1-2**

- [ ] **Step 5: 收尾提交（仅当验收过程产生修复时）**

```bash
cd /d/code/stock_robot
git add -A docs/superpowers/specs/2026-08-13-api-wiring-design.md 2>/dev/null; git status
git commit -m "fix(API): 手动验收问题修复"   # 仅当有修复时
```

---

## 附录：实现顺序依赖图

```
Task 1 (sessions) ──┐
Task 2 (llm)     ──┼──> Task 5 (bootstrap) ──> Task 6 (app.py) ──> Task 7 (cli) ──> Task 8 (回归)
Task 3 (tools)   ──┤
Task 4 (scoring) ──┘
```

Task 0 可随时先行。Task 1-4 相互独立，可并行；Task 5 依赖 1-4；Task 6 依赖 5；Task 7 依赖 6。
