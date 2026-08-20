# 每日定时报告推送实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用户订阅股票/指数，每天固定时间将分析报告推送到邮箱（全文）或企业微信（摘要）。

**Architecture:** 新包 `src/push/`（models/store/backends/summary/executor/scheduler），API 内嵌 APScheduler 定时触发，复用现有 Pipeline/IndexPipeline/build_report；三端管理（Web UI/API/CLI）。串行即推，单标的失败隔离。

**Tech Stack:** Python 3.11、Pydantic v2、FastAPI、APScheduler、smtplib、企业微信 API、SQLite

**Spec:** `docs/superpowers/specs/2026-08-20-push-notification-design.md`

**任务分级**（按 CLAUDE.md 子代理执行约定）：B 级（1/2/3/5）主会话直做；A 级（4/6/7/8/9）实现 + 合并单审查；S 级（10）实现 + 双审查 + 浏览器验收。

**环境命令**：`python` 指向 Anaconda，必须用 `.venv/Scripts/python`；pytest 用 `.venv/Scripts/python -m pytest <文件> -q`；pyright 单文件 `pyright <文件>`；ruff 用 `~/.vscode/extensions/charliermarsh.ruff-*/bundled/libs/bin/ruff.exe check <文件>`（通配符）。

---

### Task 1: 依赖与默认配置（B 级）

**Files:**
- Modify: `pyproject.toml`（dependencies）
- Modify: `src/utils/config.py:7-42`（DEFAULT_CONFIG）
- Test: `tests/utils/test_config.py`

- [ ] **Step 1: 安装依赖**

```bash
.venv/Scripts/python -m pip install apscheduler markdown
```

- [ ] **Step 2: 在 pyproject.toml 的 `[project] dependencies` 列表末尾添加**

```toml
    "apscheduler>=3.10",
    "markdown>=3.5",
```

- [ ] **Step 3: 在 src/utils/config.py 的 DEFAULT_CONFIG 中，`api` 节之后添加 push 节**

```python
    "push": {
        "enabled": True,
        "max_symbols_per_subscription": 20,
        "email": {
            "smtp_host": "smtp.qq.com",
            "smtp_port": 465,
            "smtp_user": "",
            "smtp_password": "",
            "to_addr": "",
        },
        "wecom": {
            "corp_id": "",
            "agent_id": "",
            "secret": "",
            "to_user": "@all",
        },
    },
```

- [ ] **Step 4: 添加配置默认值测试** — 在 `tests/utils/test_config.py` 末尾追加

```python
class TestPushConfig:
    def test_default_push_section(self, tmp_path):
        from utils.config import Config

        config = Config(tmp_path)
        assert config.get("push.enabled") is True
        assert config.get("push.max_symbols_per_subscription") == 20
        assert config.get("push.email.smtp_host") == "smtp.qq.com"
        assert config.get("push.wecom.to_user") == "@all"
```

（先读该文件确认既有类名/import 风格，追加为新类。）

- [ ] **Step 5: 运行测试**

Run: `.venv/Scripts/python -m pytest tests/utils/test_config.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add pyproject.toml src/utils/config.py tests/utils/test_config.py
git commit -m "feat(推送): 新增 push 配置节默认值与 apscheduler/markdown 依赖"
```

---

### Task 2: Subscription 模型（B 级）

**Files:**
- Create: `src/push/__init__.py`
- Create: `src/push/models.py`
- Test: `tests/push/__init__.py`、`tests/push/test_models.py`

- [ ] **Step 1: 创建空包初始化文件**

`src/push/__init__.py` 和 `tests/push/__init__.py` 均为空文件。

- [ ] **Step 2: 写失败测试** — `tests/push/test_models.py`

```python
import pytest
from pydantic import ValidationError

from push.models import Subscription


class TestSubscription:
    def test_valid(self):
        sub = Subscription(name="自选池", symbols=["600519", "000300"],
                           channel="email", time="08:00")
        assert sub.enabled is True
        assert sub.id is None

    def test_symbols_whitespace_stripped(self):
        sub = Subscription(name="t", symbols=[" 600519 ", "  "],
                           channel="wecom", time="09:30")
        assert sub.symbols == ["600519"]

    def test_empty_symbols_rejected(self):
        with pytest.raises(ValidationError):
            Subscription(name="t", symbols=[], channel="email", time="08:00")

    def test_bad_time_rejected(self):
        with pytest.raises(ValidationError):
            Subscription(name="t", symbols=["600519"], channel="email", time="8:00")
        with pytest.raises(ValidationError):
            Subscription(name="t", symbols=["600519"], channel="email", time="25:00")

    def test_bad_channel_rejected(self):
        with pytest.raises(ValidationError):
            Subscription(name="t", symbols=["600519"], channel="sms", time="08:00")
```

- [ ] **Step 3: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/push/test_models.py -q`
Expected: FAIL（ModuleNotFoundError: push.models）

- [ ] **Step 4: 实现 `src/push/models.py`**

```python
"""订阅模型 — 推送订阅的数据结构"""
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Channel = Literal["email", "wecom"]

TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class Subscription(BaseModel):
    """一个每日推送订阅：标的集合 + 渠道 + 时间"""
    id: int | None = None
    name: str
    symbols: list[str] = Field(min_length=1)
    channel: Channel
    time: str
    enabled: bool = True
    created_at: str = ""

    @field_validator("symbols")
    @classmethod
    def _validate_symbols(cls, v: list[str]) -> list[str]:
        cleaned = [s.strip() for s in v if s and s.strip()]
        if not cleaned:
            raise ValueError("symbols 不能为空")
        return cleaned

    @field_validator("time")
    @classmethod
    def _validate_time(cls, v: str) -> str:
        if not TIME_PATTERN.match(v):
            raise ValueError("time 格式必须为 HH:MM")
        return v
```

- [ ] **Step 5: 运行确认通过**

Run: `.venv/Scripts/python -m pytest tests/push/test_models.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/push/ tests/push/
git commit -m "feat(推送): Subscription 模型与校验"
```

---

### Task 3: PushStore 订阅存储（B 级）

**Files:**
- Create: `src/push/store.py`
- Test: `tests/push/test_store.py`

- [ ] **Step 1: 写失败测试** — `tests/push/test_store.py`

```python
from push.models import Subscription
from push.store import PushStore


def _sub(**kw):
    base = dict(name="自选池", symbols=["600519", "000300"],
                channel="email", time="08:00", created_at="2026-08-20T08:00:00+08:00")
    base.update(kw)
    return Subscription(**base)


class TestPushStore:
    def test_create_and_get(self, tmp_path):
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(_sub())
        got = store.get(sub_id)
        assert got is not None
        assert got.name == "自选池"
        assert got.symbols == ["600519", "000300"]
        assert got.channel == "email"

    def test_list_returns_all(self, tmp_path):
        store = PushStore(tmp_path / "push.db")
        store.create(_sub(name="a"))
        store.create(_sub(name="b", channel="wecom"))
        assert [s.name for s in store.list()] == ["a", "b"]

    def test_update(self, tmp_path):
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(_sub())
        sub = store.get(sub_id)
        sub.enabled = False
        sub.symbols = ["000001"]
        assert store.update(sub) is True
        got = store.get(sub_id)
        assert got.enabled is False
        assert got.symbols == ["000001"]

    def test_update_missing_returns_false(self, tmp_path):
        store = PushStore(tmp_path / "push.db")
        assert store.update(_sub(id=999)) is False

    def test_delete(self, tmp_path):
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(_sub())
        assert store.delete(sub_id) is True
        assert store.get(sub_id) is None
        assert store.delete(sub_id) is False

    def test_record_and_list_runs(self, tmp_path):
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(_sub())
        run_id = store.record_run(sub_id, total=2, ok=1, failures=["600519: 超时"])
        assert run_id > 0
        runs = store.list_runs(sub_id)
        assert len(runs) == 1
        assert runs[0]["ok"] == 1
        assert runs[0]["failures"] == ["600519: 超时"]

    def test_last_run_none_when_empty(self, tmp_path):
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(_sub())
        assert store.last_run(sub_id) is None
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/push/test_store.py -q`
Expected: FAIL（ModuleNotFoundError: push.store）

- [ ] **Step 3: 实现 `src/push/store.py`**

```python
"""订阅存储 — SQLite 持久化（subscriptions + push_runs 两张表）"""
import json
import sqlite3
import time
from pathlib import Path

from push.models import Subscription


class PushStore:
    """订阅 CRUD 与执行记录，模式与 api/sessions.py 的 SessionStore 一致"""

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
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    name       TEXT NOT NULL,
                    symbols    TEXT NOT NULL,
                    channel    TEXT NOT NULL,
                    time       TEXT NOT NULL,
                    enabled    INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS push_runs (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    subscription_id INTEGER NOT NULL,
                    ran_at          REAL NOT NULL,
                    total           INTEGER NOT NULL,
                    ok              INTEGER NOT NULL,
                    failures        TEXT NOT NULL
                );
            """)

    def create(self, sub: Subscription) -> int:
        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO subscriptions (name, symbols, channel, time, enabled, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (sub.name, json.dumps(sub.symbols, ensure_ascii=False), sub.channel,
                 sub.time, int(sub.enabled), sub.created_at),
            )
            return int(cur.lastrowid)

    def list(self) -> list[Subscription]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM subscriptions ORDER BY id").fetchall()
        return [self._row_to_sub(r) for r in rows]

    def get(self, sub_id: int) -> Subscription | None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM subscriptions WHERE id=?", (sub_id,)
            ).fetchone()
        return self._row_to_sub(row) if row else None

    def update(self, sub: Subscription) -> bool:
        if sub.id is None:
            return False
        with self._get_conn() as conn:
            cur = conn.execute(
                "UPDATE subscriptions SET name=?, symbols=?, channel=?, time=?, enabled=?"
                " WHERE id=?",
                (sub.name, json.dumps(sub.symbols, ensure_ascii=False), sub.channel,
                 sub.time, int(sub.enabled), sub.id),
            )
            return cur.rowcount > 0

    def delete(self, sub_id: int) -> bool:
        with self._get_conn() as conn:
            cur = conn.execute("DELETE FROM subscriptions WHERE id=?", (sub_id,))
            return cur.rowcount > 0

    def record_run(self, subscription_id: int, total: int, ok: int,
                   failures: list[str]) -> int:
        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO push_runs (subscription_id, ran_at, total, ok, failures)"
                " VALUES (?,?,?,?,?)",
                (subscription_id, time.time(), total, ok,
                 json.dumps(failures, ensure_ascii=False)),
            )
            return int(cur.lastrowid)

    def list_runs(self, subscription_id: int | None = None, limit: int = 20) -> list[dict]:
        sql = "SELECT * FROM push_runs"
        params: tuple = ()
        if subscription_id is not None:
            sql += " WHERE subscription_id=?"
            params = (subscription_id,)
        sql += " ORDER BY id DESC LIMIT ?"
        with self._get_conn() as conn:
            rows = conn.execute(sql, params + (limit,)).fetchall()
        return [
            {"id": r[0], "subscription_id": r[1], "ran_at": r[2], "total": r[3],
             "ok": r[4], "failures": json.loads(r[5])}
            for r in rows
        ]

    def last_run(self, subscription_id: int) -> dict | None:
        runs = self.list_runs(subscription_id, limit=1)
        return runs[0] if runs else None

    @staticmethod
    def _row_to_sub(row) -> Subscription:
        return Subscription(
            id=row[0], name=row[1], symbols=json.loads(row[2]),
            channel=row[3], time=row[4], enabled=bool(row[5]), created_at=row[6],
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/Scripts/python -m pytest tests/push/test_store.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/push/store.py tests/push/test_store.py
git commit -m "feat(推送): PushStore 订阅与执行记录存储"
```

---

### Task 4: 推送后端（A 级）

**Files:**
- Create: `src/push/backends/__init__.py`、`src/push/backends/base.py`、`src/push/backends/email.py`、`src/push/backends/wecom.py`
- Test: `tests/push/test_backends.py`

- [ ] **Step 1: 写失败测试** — `tests/push/test_backends.py`

```python
import pytest

from push.backends import get_backend
from push.backends.email import EmailBackend
from push.backends.wecom import WeComBackend


class TestGetBackend:
    def test_email(self):
        backend = get_backend("email", _fake_config())
        assert isinstance(backend, EmailBackend)

    def test_wecom(self):
        backend = get_backend("wecom", _fake_config())
        assert isinstance(backend, WeComBackend)

    def test_unknown_channel(self):
        with pytest.raises(ValueError):
            get_backend("sms", _fake_config())


class TestEmailBackend:
    def test_missing_config_raises(self):
        backend = EmailBackend({})
        with pytest.raises(ValueError):
            backend.send(title="t", content="c", content_type="markdown")

    def test_send_ssl_465(self, mocker):
        smtp_cls = mocker.patch("smtplib.SMTP_SSL")
        backend = EmailBackend(_email_cfg())
        backend.send(title="标题", content="**加粗**", content_type="markdown")
        ctx = smtp_cls.return_value.__enter__.return_value
        ctx.login.assert_called_once_with("user@qq.com", "auth-code")
        ctx.sendmail.assert_called_once()
        payload = ctx.sendmail.call_args.args[2]
        assert "标题" in payload
        assert "<strong>加粗</strong>" in payload  # markdown 已转 HTML

    def test_send_starttls_587(self, mocker):
        smtp_cls = mocker.patch("smtplib.SMTP")
        cfg = _email_cfg()
        cfg["smtp_port"] = 587
        backend = EmailBackend(cfg)
        backend.send(title="t", content="c", content_type="html")
        smtp_cls.return_value.__enter__.return_value.starttls.assert_called_once()


class TestWeComBackend:
    def test_missing_config_raises(self):
        backend = WeComBackend({})
        with pytest.raises(ValueError):
            backend.send(title="t", content="c", content_type="markdown")

    def test_send_with_token_cache(self, mocker):
        mocker.patch("requests.get", return_value=_resp({"errcode": 0, "access_token": "tok1"}))
        post = mocker.patch("requests.post", return_value=_resp({"errcode": 0}))
        backend = WeComBackend(_wecom_cfg())
        backend.send(title="标题", content="摘要", content_type="markdown")
        backend.send(title="标题2", content="摘要2", content_type="markdown")
        assert post.call_count == 2
        # 第二次发送复用缓存的 token，不再请求 gettoken
        assert mocker.patch("requests.get").call_count == 1

    def test_send_retries_on_token_expired(self, mocker):
        mocker.patch("requests.get", return_value=_resp({"errcode": 0, "access_token": "tok1"}))
        post = mocker.patch("requests.post", side_effect=[
            _resp({"errcode": 40014, "errmsg": "invalid token"}),
            _resp({"errcode": 0}),
        ])
        backend = WeComBackend(_wecom_cfg())
        backend.send(title="t", content="c", content_type="markdown")
        assert post.call_count == 2

    def test_send_error_raises(self, mocker):
        mocker.patch("requests.get", return_value=_resp({"errcode": 0, "access_token": "tok1"}))
        mocker.patch("requests.post", return_value=_resp({"errcode": 60020, "errmsg": "not allowed"}))
        backend = WeComBackend(_wecom_cfg())
        with pytest.raises(RuntimeError):
            backend.send(title="t", content="c", content_type="markdown")


def _fake_config():
    class _C:
        def get(self, key, default=None):
            data = {
                "push": {
                    "email": {"smtp_host": "smtp.qq.com", "smtp_port": 465,
                              "smtp_user": "user@qq.com", "smtp_password": "x",
                              "to_addr": "user@qq.com"},
                    "wecom": {"corp_id": "cid", "agent_id": "1", "secret": "sec",
                              "to_user": "@all"},
                },
            }
            node = data
            for k in key.split("."):
                node = node.get(k) if isinstance(node, dict) else None
            return node if node is not None else default
    return _C()


def _email_cfg():
    return {"smtp_host": "smtp.qq.com", "smtp_port": 465, "smtp_user": "user@qq.com",
            "smtp_password": "auth-code", "to_addr": "user@qq.com"}


def _wecom_cfg():
    return {"corp_id": "cid", "agent_id": "1", "secret": "sec", "to_user": "@all"}


def _resp(data):
    class _R:
        def json(self):
            return data
    return _R()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/push/test_backends.py -q`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现 `src/push/backends/base.py`**

```python
"""推送后端协议与工厂"""
from typing import Literal, Protocol


class PushBackend(Protocol):
    """推送渠道接口 — content 统一为 markdown，email 后端内部转 HTML"""
    name: str

    def send(self, *, title: str, content: str,
             content_type: Literal["html", "markdown"]) -> None: ...


def get_backend(channel: str, config) -> PushBackend:
    """按渠道构建后端；未知渠道或配置缺失抛 ValueError"""
    push_cfg = config.get("push") or {}
    if channel == "email":
        from push.backends.email import EmailBackend
        return EmailBackend(push_cfg.get("email") or {})
    if channel == "wecom":
        from push.backends.wecom import WeComBackend
        return WeComBackend(push_cfg.get("wecom") or {})
    raise ValueError(f"未知推送渠道: {channel}")
```

- [ ] **Step 4: 实现 `src/push/backends/__init__.py`**

```python
"""推送后端包"""
from push.backends.base import PushBackend, get_backend

__all__ = ["PushBackend", "get_backend"]
```

- [ ] **Step 5: 实现 `src/push/backends/email.py`**

```python
"""SMTP 邮件推送后端 — markdown 全文转 HTML 发送"""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


class EmailBackend:
    """邮件后端：端口 465 走 SSL，其余走 STARTTLS"""

    name = "email"

    def __init__(self, cfg: dict):
        self._host = str(cfg.get("smtp_host") or "")
        self._port = int(cfg.get("smtp_port") or 465)
        self._user = str(cfg.get("smtp_user") or "")
        self._password = str(cfg.get("smtp_password") or "")
        self._to_addr = str(cfg.get("to_addr") or "")

    def send(self, *, title: str, content: str,
             content_type: str = "markdown") -> None:
        if not (self._host and self._user and self._password and self._to_addr):
            raise ValueError("邮件配置缺失：smtp_host/smtp_user/smtp_password/to_addr")
        body = content
        if content_type == "markdown":
            import markdown as md
            body = md.markdown(content, extensions=["tables", "fenced_code"])
        msg = MIMEMultipart("alternative")
        msg["Subject"] = title
        msg["From"] = self._user
        msg["To"] = self._to_addr
        msg.attach(MIMEText(body, "html", "utf-8"))
        if self._port == 465:
            with smtplib.SMTP_SSL(self._host, self._port, timeout=30) as server:
                server.login(self._user, self._password)
                server.sendmail(self._user, [self._to_addr], msg.as_string())
        else:
            with smtplib.SMTP(self._host, self._port, timeout=30) as server:
                server.starttls()
                server.login(self._user, self._password)
                server.sendmail(self._user, [self._to_addr], msg.as_string())
        logger.info("邮件已发送至 %s: %s", self._to_addr, title)
```

- [ ] **Step 6: 实现 `src/push/backends/wecom.py`**

```python
"""企业微信应用消息推送后端 — markdown 消息，access_token 缓存 7200s"""
import logging
import time

import requests

logger = logging.getLogger(__name__)

TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
SEND_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"
TOKEN_TTL = 7200


class WeComBackend:
    """企业微信应用消息后端（msgtype=markdown）"""

    name = "wecom"

    def __init__(self, cfg: dict):
        self._corp_id = str(cfg.get("corp_id") or "")
        self._agent_id = str(cfg.get("agent_id") or "")
        self._secret = str(cfg.get("secret") or "")
        self._to_user = str(cfg.get("to_user") or "@all")
        self._token: str | None = None
        self._token_expire_at: float = 0.0

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expire_at:
            return self._token
        resp = requests.get(TOKEN_URL, params={
            "corpid": self._corp_id, "corpsecret": self._secret,
        }, timeout=30)
        data = resp.json()
        if data.get("errcode", 0) != 0:
            raise RuntimeError(f"企业微信获取 token 失败: {data.get('errmsg')}")
        self._token = data["access_token"]
        self._token_expire_at = time.time() + TOKEN_TTL
        return self._token

    def send(self, *, title: str, content: str,
             content_type: str = "markdown") -> None:
        if not (self._corp_id and self._agent_id and self._secret):
            raise ValueError("企业微信配置缺失：corp_id/agent_id/secret")
        body = {
            "touser": self._to_user,
            "msgtype": "markdown",
            "agentid": int(self._agent_id),
            "markdown": {"content": f"### {title}\n{content}"},
        }
        data = self._post(body)
        # token 失效（40014/42001）重取一次
        if data.get("errcode") in (40014, 42001):
            self._token = None
            self._token_expire_at = 0.0
            data = self._post(body)
        if data.get("errcode", 0) != 0:
            raise RuntimeError(f"企业微信发送失败: {data.get('errmsg')}")
        logger.info("企业微信消息已发送至 %s: %s", self._to_user, title)

    def _post(self, body: dict) -> dict:
        try:
            resp = requests.post(SEND_URL, params={"access_token": self._get_token()},
                                 json=body, timeout=30)
        except requests.RequestException as e:
            raise RuntimeError(f"企业微信发送失败: {e}") from e
        return resp.json()
```

- [ ] **Step 7: 运行确认通过**

Run: `.venv/Scripts/python -m pytest tests/push/test_backends.py -q`
Expected: PASS

- [ ] **Step 8: pyright 单文件检查**

Run: `pyright src/push/backends/`
Expected: 0 errors

- [ ] **Step 9: 提交**

```bash
git add src/push/backends/ tests/push/test_backends.py
git commit -m "feat(推送): SMTP 邮件与企业微信推送后端"
```

---

### Task 5: 推送内容构建（B 级）

**Files:**
- Create: `src/push/summary.py`
- Test: `tests/push/test_summary.py`

- [ ] **Step 1: 写失败测试** — `tests/push/test_summary.py`

```python
from data.schemas import AnalysisContext, AnalysisResult
from push.summary import build_index_full, build_index_summary, build_stock_summary
from report.signal import SignalConfig


def _results():
    return [
        AnalysisResult(dimension="financial", status="ok", score=8.0,
                       summary="财务稳健", metrics={"roe": 0.12}),
        AnalysisResult(dimension="technical", status="ok", score=5.0,
                       summary="均线缠绕", metrics={}),
    ]


def _ctx():
    from data.schemas import PriceData
    return AnalysisContext(
        symbol="600519", name="贵州茅台",
        price_data=[PriceData(date="2026-08-19", open=1, high=2, low=1,
                              close=1500.0, change_pct=2.35)],
    )


def _signal_cfg():
    return SignalConfig()


class TestStockSummary:
    def test_contains_key_info(self):
        text = build_stock_summary("600519", "贵州茅台", _results(), _ctx(), _signal_cfg())
        assert "贵州茅台" in text
        assert "600519" in text
        assert "1500.0" in text
        assert "+2.35%" in text
        assert "操作信号" in text
        assert "综合得分" in text
        assert "财务健康" in text

    def test_signal_level_by_score(self):
        high = build_stock_summary("600519", "贵州茅台",
                                   [AnalysisResult(dimension="financial", status="ok",
                                                   score=9.0, summary="s", metrics={})],
                                   _ctx(), _signal_cfg())
        assert "进攻" in high


class TestIndexSummary:
    def _report(self):
        from data.schemas import IndexReport
        from datetime import date
        return IndexReport(
            code="000300", name="沪深300", date=date(2026, 8, 19),
            overview={"latest_close": 3800.0},
            section_technical={"趋势": "多头排列"}, section_valuation={},
            section_capital={}, section_macro=None, section_sentiment={},
            tag_technical="bull", tag_valuation="neutral", tag_capital="positive",
            tag_macro="na", tag_sentiment="neutral",
            composite_comment="市场情绪回暖", position_coeff=None,
            risk_list=["外围波动"], visible_sections={"technical"},
        )

    def test_summary_contains_tags_and_comment(self):
        text = build_index_summary(self._report())
        assert "沪深300" in text
        assert "技术：bull" in text
        assert "综合点评：市场情绪回暖" in text
        assert "外围波动" in text

    def test_full_contains_sections(self):
        text = build_index_full(self._report())
        assert "技术面" in text
        assert "多头排列" in text
        assert "沪深300" in text


class TestStockFull:
    def test_build_report_reused(self):
        from push.summary import build_stock_full
        text = build_stock_full("600519", "贵州茅台", _results(), {"bulk": "解读"},
                                _ctx(), _signal_cfg())
        assert "贵州茅台" in text
        assert "解读" in text
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/push/test_summary.py -q`
Expected: FAIL（ModuleNotFoundError: push.summary）

- [ ] **Step 3: 实现 `src/push/summary.py`**

```python
"""推送内容构建 — 股票/指数的微信摘要与邮箱全文"""
from report.signal import SIGNAL_LABELS, derive_signal
from report.scoring import compute_price_info, compute_score_summary


def build_stock_summary(symbol: str, name: str, results, ctx,
                        signal_cfg) -> str:
    """微信摘要：核心指标 + 操作信号 + 维度评分 + 风险"""
    summary = compute_score_summary(results)
    price = compute_price_info(ctx)
    lines = [f"**{name}**（{symbol}）"]
    if price["latest_price"] is not None:
        pct = price["change_pct"]
        pct_txt = f"（{pct:+.2f}%）" if pct is not None else ""
        lines.append(f"> 最新收盘：{price['latest_price']} {pct_txt}")
    level = derive_signal(summary.final_score, signal_cfg.thresholds)
    action = signal_cfg.actions[level]
    lines.append(f"> 操作信号：**{SIGNAL_LABELS[level]}**｜{action.action}（{action.position}）")
    lines.append(f"> 综合得分：{summary.final_score}/10（风险扣分 {summary.risk_deduction}）")
    for row in summary.score_rows:
        lines.append(f"- {row['label']}：{row['score']}（{row['sufficiency']}）")
    for flag in summary.risk_flags:
        lines.append(f"- ⚠ {flag}")
    return "\n".join(lines)


def build_stock_full(symbol: str, name: str, results, commentary: dict,
                     ctx, signal_cfg) -> str:
    """邮箱全文：复用 build_report 的完整 markdown 报告"""
    from report.scoring import build_report
    return build_report(symbol, name, results, commentary, ctx,
                        signal_cfg=signal_cfg)


_TAG_LABELS = [
    ("tag_technical", "技术"), ("tag_valuation", "估值"),
    ("tag_capital", "资金"), ("tag_macro", "宏观"), ("tag_sentiment", "舆情"),
]


def build_index_summary(report) -> str:
    """指数微信摘要：多空标签 + 综合点评 + 风险"""
    lines = [f"**{report.name}**（{report.code}）", f"> 报告日期：{report.date}"]
    for attr, label in _TAG_LABELS:
        val = getattr(report, attr)
        if val not in ("na", "invalid", None):
            lines.append(f"- {label}：{val}")
    if report.composite_comment:
        lines.append(f"- 综合点评：{report.composite_comment}")
    for risk in report.risk_list:
        lines.append(f"- ⚠ {risk}")
    return "\n".join(lines)


def build_index_full(report) -> str:
    """指数邮箱全文：维度指标 markdown"""
    lines = [f"# {report.name}（{report.code}）", f"报告日期：{report.date}", ""]
    sections = [
        ("技术面", report.section_technical),
        ("估值", report.section_valuation),
        ("资金面", report.section_capital),
        ("宏观", report.section_macro),
        ("舆情", report.section_sentiment),
    ]
    for label, sec in sections:
        if sec is None:
            continue
        lines.append(f"## {label}")
        for k, v in sec.items():
            if isinstance(v, list):
                v = "、".join(str(x) for x in v)
            lines.append(f"- {k}：{v}")
        lines.append("")
    if report.risk_list:
        lines.append("## 风险")
        for risk in report.risk_list:
            lines.append(f"- ⚠ {risk}")
    return "\n".join(lines)
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/Scripts/python -m pytest tests/push/test_summary.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/push/summary.py tests/push/test_summary.py
git commit -m "feat(推送): 股票/指数摘要与全文构建"
```

---

### Task 6: 串行推送执行器（A 级）

**Files:**
- Create: `src/push/executor.py`
- Test: `tests/push/test_executor.py`

前置事实（勿重复探测）：`Pipeline.run(symbol, name)` → `(results, commentary, ctx)`；`IndexPipeline.run([AnalysisTarget(...)])` → `IndexPipelineResult(reports, compare, errors)`；`IndexMapping().lookup(code)` → entry（有 name/market/index_style）或 None；`validate_symbol`/`normalize_symbol`/`normalize_index_symbol`/`resolve_name` 在 `utils.symbols`；海外指数（纯大写 2-10 位）走 index 且 entry 为 None。

- [ ] **Step 1: 写失败测试** — `tests/push/test_executor.py`

```python
from data.schemas import AnalysisContext, AnalysisResult
from push.executor import PushExecutor
from push.models import Subscription


class _Core:
    def __init__(self):
        self.pipeline = _Pipeline()
        self.index_pipeline = _IndexPipeline()


class _Pipeline:
    def run(self, symbol, name):
        ctx = AnalysisContext(symbol=symbol, name=name)
        return ([AnalysisResult(dimension="financial", status="ok", score=7.0,
                                summary="OK", metrics={})],
                {"bulk": "综合解读"}, ctx)


class _IndexPipeline:
    def run(self, targets):
        from data.schemas import IndexReport
        from datetime import date
        target = targets[0]
        report = IndexReport(
            code=target.symbol, name=target.name, date=date(2026, 8, 19),
            overview={}, section_technical={"趋势": "多头"}, section_valuation={},
            section_capital={}, section_macro=None, section_sentiment={},
            tag_technical="bull", tag_valuation="neutral", tag_capital="positive",
            tag_macro="na", tag_sentiment="neutral",
            composite_comment="", position_coeff=None, risk_list=[],
            visible_sections={"technical"},
        )
        return type("R", (), {"reports": [report], "compare": None, "errors": []})()


class _Backend:
    def __init__(self):
        self.calls = []

    def send(self, *, title, content, content_type):
        self.calls.append({"title": title, "content": content, "content_type": content_type})


class _Config:
    def get(self, key, default=None):
        if key == "push":
            return {"email": {"smtp_host": "h", "smtp_user": "u", "smtp_password": "p",
                              "to_addr": "a@b.c", "smtp_port": 465}}
        if key == "signal":
            return {"thresholds": {"attack": 7, "watch": 4},
                    "actions": {"attack": {"action": "建仓", "position": "60%"},
                                "watch": {"action": "观察", "position": "30%"},
                                "defend": {"action": "回避", "position": "0%"}}}
        return default


class TestPushExecutor:
    def _executor(self, mocker, store):
        mocker.patch("push.executor.get_backend", return_value=_Backend())
        mocker.patch("push.executor.resolve_name", return_value="平安银行")
        return PushExecutor(_Core(), store, _Config())

    def test_stock_email_sends_full(self, mocker, tmp_path):
        from push.store import PushStore
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(Subscription(name="t", symbols=["000001"],
                                           channel="email", time="08:00"))
        backend = _Backend()
        mocker.patch("push.executor.get_backend", return_value=backend)
        mocker.patch("push.executor.resolve_name", return_value="平安银行")
        executor = PushExecutor(_Core(), store, _Config())
        result = executor.run_subscription(store.get(sub_id))
        assert result["ok"] == 1
        assert backend.calls[0]["title"].startswith("[Stock Robot]")
        assert "综合解读" in backend.calls[0]["content"]  # 全文含 AI 解读
        assert store.last_run(sub_id)["ok"] == 1

    def test_stock_wecom_sends_summary(self, mocker, tmp_path):
        from push.store import PushStore
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(Subscription(name="t", symbols=["000001"],
                                           channel="wecom", time="08:00"))
        backend = _Backend()
        mocker.patch("push.executor.get_backend", return_value=backend)
        mocker.patch("push.executor.resolve_name", return_value="平安银行")
        executor = PushExecutor(_Core(), store, _Config())
        result = executor.run_subscription(store.get(sub_id))
        assert result["ok"] == 1
        assert "操作信号" in backend.calls[0]["content"]  # 摘要含信号
        assert "综合解读" not in backend.calls[0]["content"]

    def test_index_wecom_sends_summary(self, mocker, tmp_path):
        from push.store import PushStore
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(Subscription(name="t", symbols=["000300"],
                                           channel="wecom", time="08:00"))
        backend = _Backend()
        mocker.patch("push.executor.get_backend", return_value=backend)
        mocker.patch("push.executor.resolve_name", return_value="平安银行")
        executor = PushExecutor(_Core(), store, _Config())
        result = executor.run_subscription(store.get(sub_id))
        assert result["ok"] == 1
        assert "沪深300" in backend.calls[0]["content"]
        assert "技术：bull" in backend.calls[0]["content"]

    def test_failed_symbol_isolated(self, mocker, tmp_path):
        from push.store import PushStore
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(Subscription(name="t", symbols=["000001", "BAD!!"],
                                           channel="email", time="08:00"))
        backend = _Backend()
        mocker.patch("push.executor.get_backend", return_value=backend)
        mocker.patch("push.executor.resolve_name", return_value="平安银行")
        executor = PushExecutor(_Core(), store, _Config())
        result = executor.run_subscription(store.get(sub_id))
        assert result["ok"] == 1
        assert len(result["failures"]) == 1
        assert "BAD" in result["failures"][0]
        run = store.last_run(sub_id)
        assert run["total"] == 2
        assert run["ok"] == 1
```

注意 `test_index_wecom_sends_summary` 断言 "沪深300"：IndexMapping().lookup("000300") 在测试环境会读真实 CSV（conftest 未 mock）。若 CSV 未收录 000300，entry 为 None，name=normalized="000300"，断言应改为 "000300"。**实现前先运行验证**：若 lookup 返回 None，把该断言改为 `"000300" in content`。若 CSV 存在且收录，保留 "沪深300"。

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/push/test_executor.py -q`
Expected: FAIL（ModuleNotFoundError: push.executor）

- [ ] **Step 3: 实现 `src/push/executor.py`**

```python
"""串行推送执行器 — 逐标的生成报告并推送，单标的失败隔离"""
import logging
import re

from data.index_mapping import IndexMapping
from data.schemas import AnalysisTarget
from push.backends import get_backend
from push.models import Subscription
from push.summary import (
    build_index_full,
    build_index_summary,
    build_stock_full,
    build_stock_summary,
)
from report.signal import load_signal_config
from utils.symbols import (
    normalize_index_symbol,
    normalize_symbol,
    resolve_name,
    validate_symbol,
)

logger = logging.getLogger(__name__)

OVERSEAS_PATTERN = re.compile(r"^[A-Z]{2,10}$")


class PushExecutor:
    """执行订阅推送：股票走 Pipeline，指数走 IndexPipeline，串行即推"""

    def __init__(self, core, store, config):
        self._core = core
        self._store = store
        self._config = config
        self._mapping = IndexMapping()

    def _classify(self, raw: str) -> tuple[str, str, object | None]:
        """返回 (normalized, kind, entry)；entry 为指数映射条目或 None"""
        if OVERSEAS_PATTERN.match(raw.strip().upper()):
            return raw.strip().upper(), "index", None
        idx_norm = normalize_index_symbol(raw)
        entry = self._mapping.lookup(idx_norm)
        if entry is not None:
            return idx_norm, "index", entry
        if validate_symbol(raw):
            return normalize_symbol(raw), "stock", None
        return "", "invalid", None

    def run_subscription(self, sub: Subscription) -> dict:
        """逐标的串行推送；单标的失败隔离并记录执行结果"""
        failures: list[str] = []
        ok = 0
        backend = get_backend(sub.channel, self._config)
        for raw in sub.symbols:
            try:
                self._push_one(raw, sub.channel, backend)
                ok += 1
            except Exception as e:  # noqa: BLE001 — 单标的失败不影响整体
                logger.error("推送 %s 失败: %s", raw, e)
                failures.append(f"{raw}: {e}")
        self._store.record_run(sub.id or 0, len(sub.symbols), ok, failures)
        return {"total": len(sub.symbols), "ok": ok, "failures": failures}

    def _push_one(self, raw: str, channel: str, backend) -> None:
        normalized, kind, entry = self._classify(raw)
        if kind == "invalid":
            raise ValueError(f"无效的代码: {raw}")
        if kind == "stock":
            self._push_stock(normalized, channel, backend)
        else:
            self._push_index(normalized, entry, channel, backend)

    def _push_stock(self, symbol: str, channel: str, backend) -> None:
        name = resolve_name(symbol) or symbol
        results, commentary, ctx = self._core.pipeline.run(symbol, name)
        signal_cfg = load_signal_config(self._config)
        if channel == "email":
            content = build_stock_full(symbol, name, results, commentary, ctx, signal_cfg)
            title = f"[Stock Robot] {name} 分析报告"
        else:
            content = build_stock_summary(symbol, name, results, ctx, signal_cfg)
            title = f"{name} 分析报告"
        backend.send(title=title, content=content, content_type="markdown")

    def _push_index(self, symbol: str, entry, channel: str, backend) -> None:
        if entry is not None:
            name, market, style = entry.name, entry.market, entry.index_style
        elif OVERSEAS_PATTERN.match(symbol):
            name, market, style = symbol, "overseas", "overseas"
        else:
            name, market, style = symbol, "a-shares", "broad"
        target = AnalysisTarget(
            target_type="index", symbol=symbol, name=name,
            market=market, index_style=style,
        )
        result = self._core.index_pipeline.run([target])
        if not result.reports:
            raise RuntimeError(f"指数分析无结果: {result.errors}")
        report = result.reports[0]
        if channel == "email":
            content = build_index_full(report)
        else:
            content = build_index_summary(report)
        backend.send(title=f"{report.name} 指数报告", content=content,
                     content_type="markdown")
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/Scripts/python -m pytest tests/push/test_executor.py -q`
Expected: PASS（若 test_index 断言失败，按 Step 1 的说明修正断言）

- [ ] **Step 5: pyright 单文件检查**

Run: `pyright src/push/executor.py`
Expected: 0 errors

- [ ] **Step 6: 提交**

```bash
git add src/push/executor.py tests/push/test_executor.py
git commit -m "feat(推送): 串行推送执行器与失败隔离"
```

---

### Task 7: APScheduler 调度集成（A 级）

**Files:**
- Create: `src/push/scheduler.py`
- Test: `tests/push/test_scheduler.py`

- [ ] **Step 1: 写失败测试** — `tests/push/test_scheduler.py`

```python
import pytest

from push.models import Subscription
from push.scheduler import PushScheduler


class _Executor:
    def __init__(self):
        self.runs = []

    def run_subscription(self, sub):
        self.runs.append(sub.id)
        return {"total": 1, "ok": 1, "failures": []}


class _Store:
    def __init__(self, subs):
        self._subs = subs

    def list(self):
        return self._subs

    def get(self, sub_id):
        for s in self._subs:
            if s.id == sub_id:
                return s
        return None


class _Config:
    def __init__(self, enabled=True):
        self._enabled = enabled

    def get(self, key, default=None):
        if key == "push.enabled":
            return self._enabled
        return default


class _SchedulerStub:
    def __init__(self):
        self.jobs = []
        self.started = False

    def start(self):
        self.started = True

    def remove_all_jobs(self):
        self.jobs = []

    def add_job(self, fn, trigger, id=None, replace_existing=False, kwargs=None):
        self.jobs.append({"id": id, "trigger": trigger, "kwargs": kwargs or {}})

    def shutdown(self, wait=False):
        self.started = False


class TestPushScheduler:
    def test_start_registers_jobs(self, mocker):
        subs = [Subscription(id=1, name="a", symbols=["600519"], channel="email",
                             time="08:30", enabled=True)]
        stub = _SchedulerStub()
        mocker.patch("push.scheduler.BackgroundScheduler", return_value=stub)
        scheduler = PushScheduler(_Executor(), _Store(subs), _Config())
        scheduler.start()
        assert stub.started is True
        assert len(stub.jobs) == 1
        assert stub.jobs[0]["id"] == "sub-1"
        assert stub.jobs[0]["kwargs"] == {"sub_id": 1}
        assert stub.jobs[0]["trigger"].hour == 8
        assert stub.jobs[0]["trigger"].minute == 30

    def test_disabled_skips_start(self, mocker):
        scheduler = PushScheduler(_Executor(), _Store([]), _Config(enabled=False))
        scheduler.start()
        mocker.patch("push.scheduler.BackgroundScheduler").assert_not_called()

    def test_reload_after_enabled_toggle(self, mocker):
        subs = [Subscription(id=1, name="a", symbols=["600519"], channel="email",
                             time="08:00", enabled=True),
                Subscription(id=2, name="b", symbols=["000300"], channel="wecom",
                             time="09:00", enabled=False)]
        stub = _SchedulerStub()
        mocker.patch("push.scheduler.BackgroundScheduler", return_value=stub)
        scheduler = PushScheduler(_Executor(), _Store(subs), _Config())
        scheduler.start()
        assert [j["id"] for j in stub.jobs] == ["sub-1"]  # 禁用订阅不注册
        scheduler._store._subs[1].enabled = True
        scheduler.reload()
        assert [j["id"] for j in stub.jobs] == ["sub-1", "sub-2"]

    def test_run_invokes_executor(self, mocker):
        executor = _Executor()
        subs = [Subscription(id=1, name="a", symbols=["600519"], channel="email",
                             time="08:00", enabled=True)]
        stub = _SchedulerStub()
        mocker.patch("push.scheduler.BackgroundScheduler", return_value=stub)
        scheduler = PushScheduler(executor, _Store(subs), _Config())
        scheduler.start()
        scheduler._run(1)
        assert executor.runs == [1]

    def test_shutdown_stops_scheduler(self, mocker):
        stub = _SchedulerStub()
        mocker.patch("push.scheduler.BackgroundScheduler", return_value=stub)
        scheduler = PushScheduler(_Executor(), _Store([]), _Config())
        scheduler.start()
        scheduler.shutdown()
        assert stub.started is False
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/push/test_scheduler.py -q`
Expected: FAIL（ModuleNotFoundError: push.scheduler）

- [ ] **Step 3: 实现 `src/push/scheduler.py`**

```python
"""APScheduler 集成 — 每日定时触发订阅推送"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


class PushScheduler:
    """管理每日推送任务；订阅变更后调用 reload() 重注册"""

    def __init__(self, executor, store, config):
        self._executor = executor
        self._store = store
        self._config = config
        self._scheduler: BackgroundScheduler | None = None

    def start(self):
        if not self._config.get("push.enabled", True):
            logger.info("push.enabled=false，跳过推送调度")
            return
        if self._scheduler is None:
            self._scheduler = BackgroundScheduler()
            self._scheduler.start()
            self.reload()

    def reload(self):
        """重读订阅并重新注册每日 cron 任务"""
        if self._scheduler is None:
            return
        self._scheduler.remove_all_jobs()
        enabled = 0
        for sub in self._store.list():
            if not sub.enabled or sub.id is None:
                continue
            hour, minute = sub.time.split(":")
            self._scheduler.add_job(
                self._run, CronTrigger(hour=int(hour), minute=int(minute)),
                id=f"sub-{sub.id}", replace_existing=True,
                kwargs={"sub_id": sub.id},
            )
            enabled += 1
        logger.info("推送调度已重载: %d 个启用订阅", enabled)

    def _run(self, sub_id: int):
        sub = self._store.get(sub_id)
        if sub is None:
            logger.warning("订阅 %s 不存在，跳过推送", sub_id)
            return
        logger.info("开始推送订阅 %s (%s)", sub.name, sub_id)
        try:
            result = self._executor.run_subscription(sub)
            logger.info("订阅 %s 推送完成: %d/%d 成功", sub.name,
                        result["ok"], result["total"])
        except Exception as e:  # noqa: BLE001 — 订阅级失败不影响调度器
            logger.error("订阅 %s 推送失败: %s", sub.name, e)

    def shutdown(self):
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/Scripts/python -m pytest tests/push/test_scheduler.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/push/scheduler.py tests/push/test_scheduler.py
git commit -m "feat(推送): APScheduler 每日调度与订阅重载"
```

---

### Task 8: API 集成（A 级）

**Files:**
- Modify: `src/api/app.py:67-385`（create_app 签名 + 端点）
- Test: `tests/api/test_subscriptions.py`（新建）

**前置事实：** `create_app(core=None, sessions=None)` 闭包模式；测试用 `from fastapi.testclient import TestClient` + `TestClient(app)`。

- [ ] **Step 1: 写失败测试** — `tests/api/test_subscriptions.py`

```python
import threading
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from api.app import create_app
from push.models import Subscription


class _PushStub:
    """替代 PushScheduler：记录 reload 调用，暴露 store/executor"""

    def __init__(self, store, executor):
        self.store = store
        self.executor = executor
        self.reload_calls = 0

    def reload(self):
        self.reload_calls += 1

    def start(self):
        pass

    def shutdown(self):
        pass


class _Executor:
    def __init__(self):
        self.ran = []

    def run_subscription(self, sub):
        self.ran.append(sub.id)
        return {"total": 1, "ok": 1, "failures": []}


def _make_app(tmp_path):
    from push.store import PushStore
    store = PushStore(tmp_path / "push.db")
    push = _PushStub(store, _Executor())
    core = MagicMock()
    core.pipeline = MagicMock()
    core.index_pipeline = MagicMock()
    return create_app(core=core, push=push), push


class TestSubscriptionsAPI:
    def test_list_empty(self, tmp_path):
        app, _ = _make_app(tmp_path)
        resp = TestClient(app).get("/api/v1/subscriptions")
        assert resp.status_code == 200
        assert resp.json()["subscriptions"] == []

    def test_create_and_list(self, tmp_path):
        app, push = _make_app(tmp_path)
        client = TestClient(app)
        resp = client.post("/api/v1/subscriptions", json={
            "name": "自选池", "symbols": ["600519", "000300"],
            "channel": "email", "time": "08:00",
        })
        assert resp.status_code == 200
        assert resp.json()["id"] == 1
        assert push.reload_calls == 1
        listed = client.get("/api/v1/subscriptions").json()["subscriptions"]
        assert len(listed) == 1
        assert listed[0]["name"] == "自选池"
        assert listed[0]["last_run"] is None

    def test_create_validation_errors(self, tmp_path):
        app, _ = _make_app(tmp_path)
        client = TestClient(app)
        resp = client.post("/api/v1/subscriptions", json={
            "name": "t", "symbols": [], "channel": "email", "time": "08:00",
        })
        assert resp.status_code == 422
        resp = client.post("/api/v1/subscriptions", json={
            "name": "t", "symbols": ["600519"], "channel": "sms", "time": "08:00",
        })
        assert resp.status_code == 422
        resp = client.post("/api/v1/subscriptions", json={
            "name": "t", "symbols": ["600519"], "channel": "email", "time": "8:00",
        })
        assert resp.status_code == 422

    def test_create_too_many_symbols(self, tmp_path):
        app, _ = _make_app(tmp_path)
        client = TestClient(app)
        resp = client.post("/api/v1/subscriptions", json={
            "name": "t", "symbols": [str(600000 + i) for i in range(21)],
            "channel": "email", "time": "08:00",
        })
        assert resp.status_code == 422

    def test_update_and_reload(self, tmp_path):
        app, push = _make_app(tmp_path)
        client = TestClient(app)
        sub_id = client.post("/api/v1/subscriptions", json={
            "name": "a", "symbols": ["600519"], "channel": "email", "time": "08:00",
        }).json()["id"]
        resp = client.put(f"/api/v1/subscriptions/{sub_id}", json={
            "name": "a2", "symbols": ["000001"], "channel": "wecom", "time": "09:30",
        })
        assert resp.status_code == 200
        assert resp.json()["channel"] == "wecom"
        assert push.reload_calls == 2
        got = client.get(f"/api/v1/subscriptions/{sub_id}").json()
        assert got["symbols"] == ["000001"]

    def test_delete_and_reload(self, tmp_path):
        app, push = _make_app(tmp_path)
        client = TestClient(app)
        sub_id = client.post("/api/v1/subscriptions", json={
            "name": "a", "symbols": ["600519"], "channel": "email", "time": "08:00",
        }).json()["id"]
        resp = client.delete(f"/api/v1/subscriptions/{sub_id}")
        assert resp.status_code == 200
        assert push.reload_calls == 2
        assert client.get("/api/v1/subscriptions").json()["subscriptions"] == []

    def test_get_missing_404(self, tmp_path):
        app, _ = _make_app(tmp_path)
        resp = TestClient(app).get("/api/v1/subscriptions/999")
        assert resp.status_code == 404

    def test_manual_run_returns_triggered(self, tmp_path):
        app, push = _make_app(tmp_path)
        client = TestClient(app)
        sub_id = client.post("/api/v1/subscriptions", json={
            "name": "a", "symbols": ["600519"], "channel": "email", "time": "08:00",
        }).json()["id"]
        resp = client.post(f"/api/v1/subscriptions/{sub_id}/run")
        assert resp.status_code == 200
        assert resp.json()["status"] == "triggered"
        assert len(push.executor.ran) == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/api/test_subscriptions.py -q`
Expected: FAIL（AttributeError: 'FastAPI' object has no attribute... 或 404）

- [ ] **Step 3: 修改 `src/api/app.py`**

在 `create_app` 签名处（app.py:67）改为：

```python
def create_app(core=None, sessions=None, push=None):
```

在函数体开头（`app = FastAPI(...)` 之前）加入推送模块初始化（仅当 core 注入且未显式传 push）：

```python
    push_store = None
    push_executor = None
    if push is None and core is not None:
        from push.executor import PushExecutor
        from push.scheduler import PushScheduler
        from push.store import PushStore
        from utils.config import Config
        config = Config()
        push_store = PushStore(config.config_dir / "push.db")
        push_executor = PushExecutor(core, push_store, config)
        push = PushScheduler(push_executor, push_store, config)
        push.start()
```

在 `clear_session` 端点之后、静态文件挂载注释之前，插入订阅端点：

```python
    # ---- 订阅推送管理 ----

    def _require_push():
        if push is None or push_store is None:
            raise HTTPException(status_code=503, detail="推送模块未初始化")
        return push_store

    def _parse_subscription(body: dict):
        # 注意：返回类型不标注 Subscription——该类为函数内 import，def 执行时
        # 求值返回标注会 NameError；调用方用局部变量即可
        from push.models import Subscription
        try:
            return Subscription(
                name=str(body.get("name", "")).strip(),
                symbols=list(body.get("symbols") or []),
                channel=str(body.get("channel", "")),
                time=str(body.get("time", "")),
            )
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=str(e.errors())) from e

    def _check_symbol_limit(sub: Subscription):
        from utils.config import Config
        limit = int(Config().get("push.max_symbols_per_subscription", 20))
        if len(sub.symbols) > limit:
            raise HTTPException(
                status_code=422,
                detail=f"标的数量 {len(sub.symbols)} 超过上限 {limit}")

    @app.get("/api/v1/subscriptions")
    async def list_subscriptions():
        store = _require_push()
        items = []
        for sub in store.list():
            item = sub.model_dump(mode="json")
            item["last_run"] = store.last_run(sub.id) if sub.id else None
            items.append(item)
        return JSONResponse({"subscriptions": items})

    @app.post("/api/v1/subscriptions")
    async def create_subscription(request: Request):
        store = _require_push()
        body = await request.json()
        sub = _parse_subscription(body)
        _check_symbol_limit(sub)
        from datetime import datetime, timezone
        sub.created_at = datetime.now().astimezone().isoformat()
        sub_id = store.create(sub)
        if push is not None:
            push.reload()
        # 先展开 model_dump（id 为 None），再覆盖真实 id
        return JSONResponse({**sub.model_dump(mode="json"), "id": sub_id})

    @app.get("/api/v1/subscriptions/{subscription_id}")
    async def get_subscription(subscription_id: int):
        store = _require_push()
        sub = store.get(subscription_id)
        if sub is None:
            raise HTTPException(status_code=404,
                                detail=f"订阅不存在: {subscription_id}")
        item = sub.model_dump(mode="json")
        item["last_run"] = store.last_run(subscription_id)
        return JSONResponse(item)

    @app.put("/api/v1/subscriptions/{subscription_id}")
    async def update_subscription(subscription_id: int, request: Request):
        store = _require_push()
        if store.get(subscription_id) is None:
            raise HTTPException(status_code=404,
                                detail=f"订阅不存在: {subscription_id}")
        body = await request.json()
        sub = _parse_subscription(body)
        _check_symbol_limit(sub)
        sub.id = subscription_id
        store.update(sub)
        if push is not None:
            push.reload()
        return JSONResponse(sub.model_dump(mode="json"))

    @app.delete("/api/v1/subscriptions/{subscription_id}")
    async def delete_subscription(subscription_id: int):
        store = _require_push()
        if not store.delete(subscription_id):
            raise HTTPException(status_code=404,
                                detail=f"订阅不存在: {subscription_id}")
        if push is not None:
            push.reload()
        return JSONResponse({"status": "ok"})

    @app.post("/api/v1/subscriptions/{subscription_id}/run")
    async def run_subscription(subscription_id: int):
        store = _require_push()
        sub = store.get(subscription_id)
        if sub is None:
            raise HTTPException(status_code=404,
                                detail=f"订阅不存在: {subscription_id}")
        if push_executor is None:
            raise HTTPException(status_code=503, detail="推送执行器未初始化")
        threading.Thread(target=push_executor.run_subscription, args=(sub,),
                         daemon=True).start()
        return JSONResponse({"status": "triggered"})
```

文件顶部 import 处补充（app.py 已有 import 区，追加）：

```python
import threading
from pydantic import ValidationError
from push.models import Subscription
```

（若 `threading` 已在文件顶部 import 则跳过；`Subscription` 已在 `_parse_subscription` 内 import，可只在顶部加 `import threading` 和 `from pydantic import ValidationError`，`_parse_subscription` 内的 `from push.models import Subscription` 保留。）

- [ ] **Step 4: 运行确认通过**

Run: `.venv/Scripts/python -m pytest tests/api/test_subscriptions.py -q`
Expected: PASS

- [ ] **Step 5: 回归既有 API 测试**

Run: `.venv/Scripts/python -m pytest tests/api/test_app.py tests/api/test_static.py -q`
Expected: PASS

- [ ] **Step 6: pyright 单文件检查**

Run: `pyright src/api/app.py`
Expected: 0 errors

- [ ] **Step 7: 提交**

```bash
git add src/api/app.py tests/api/test_subscriptions.py
git commit -m "feat(推送): 订阅管理 API 端点与调度器注入"
```

---

### Task 9: CLI 订阅命令（A 级）

**Files:**
- Modify: `src/stock_robot/cli.py`（新增 subscribe 命令组）
- Test: `tests/test_cli.py`（追加 TestSubscribe 类）

- [ ] **Step 1: 写失败测试** — `tests/test_cli.py` 末尾追加

```python
class TestSubscribe:
    def test_add_creates_subscription(self, mocker, tmp_path):
        mock_store = mocker.patch("push.store.PushStore")
        instance = mock_store.return_value
        instance.create.return_value = 7
        runner = CliRunner()
        result = runner.invoke(main, [
            "subscribe", "add",
            "--name", "自选池",
            "--symbols", "600519,000300",
            "--channel", "email",
            "--time", "08:30",
        ])
        assert result.exit_code == 0
        created = instance.create.call_args.args[0]
        assert created.name == "自选池"
        assert created.symbols == ["600519", "000300"]
        assert created.channel == "email"

    def test_add_invalid_channel(self):
        runner = CliRunner()
        result = runner.invoke(main, [
            "subscribe", "add",
            "--name", "t", "--symbols", "600519",
            "--channel", "sms", "--time", "08:30",
        ])
        assert result.exit_code != 0

    def test_list_prints_table(self, mocker, tmp_path):
        mock_store = mocker.patch("push.store.PushStore")
        from push.models import Subscription
        mock_store.return_value.list.return_value = [
            Subscription(id=1, name="自选池", symbols=["600519"],
                         channel="email", time="08:30")]
        runner = CliRunner()
        result = runner.invoke(main, ["subscribe", "list"])
        assert result.exit_code == 0
        assert "自选池" in result.output

    def test_remove(self, mocker, tmp_path):
        mock_store = mocker.patch("push.store.PushStore")
        mock_store.return_value.delete.return_value = True
        runner = CliRunner()
        result = runner.invoke(main, ["subscribe", "remove", "--id", "3"])
        assert result.exit_code == 0
        mock_store.return_value.delete.assert_called_once_with(3)

    def test_run_triggers_executor(self, mocker, tmp_path):
        mock_store = mocker.patch("push.store.PushStore")
        mocker.patch("api.bootstrap.build_agent_core")  # 避免真实构建 AgentCore
        from push.models import Subscription
        sub = Subscription(id=1, name="自选池", symbols=["600519"],
                           channel="email", time="08:30")
        mock_store.return_value.get.return_value = sub
        mock_executor = mocker.patch("push.executor.PushExecutor")
        mock_executor.return_value.run_subscription.return_value = {
            "total": 1, "ok": 1, "failures": []}
        runner = CliRunner()
        result = runner.invoke(main, ["subscribe", "run", "--id", "1"])
        assert result.exit_code == 0
        mock_executor.return_value.run_subscription.assert_called_once_with(sub)
        assert "1/1" in result.output
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -q`
Expected: FAIL（UsageError: No such command 'subscribe'）

- [ ] **Step 3: 在 `src/stock_robot/cli.py` 的 `rag` 命令组之后追加 subscribe 命令组**

```python
@main.group()
def subscribe():
    """管理每日定时推送订阅（邮件/企业微信）"""


@subscribe.command("add")
@click.option("--name", required=True, help="订阅名称")
@click.option("--symbols", required=True, help="标的代码，逗号/空格分隔")
@click.option("--channel", type=click.Choice(["email", "wecom"]), required=True,
              help="推送渠道")
@click.option("--time", "push_time", required=True, help="每日推送时间 HH:MM")
def subscribe_add(name, symbols, channel, push_time):
    """创建订阅"""
    import re
    from datetime import datetime, timezone

    from push.models import Subscription
    from push.store import PushStore
    from utils.config import Config

    config = Config()
    store = PushStore(config.config_dir / "push.db")
    sub = Subscription(
        name=name,
        symbols=[s for s in re.split(r"[,，\s]+", symbols) if s],
        channel=channel,
        time=push_time,
        created_at=datetime.now().astimezone().isoformat(),
    )
    sub_id = store.create(sub)
    console.print(f"[green]已创建订阅 #{sub_id}: {name}（{channel} {push_time}）[/green]")


@subscribe.command("list")
def subscribe_list():
    """列出全部订阅"""
    from push.store import PushStore
    from utils.config import Config

    store = PushStore(Config().config_dir / "push.db")
    table = Table(title="推送订阅")
    table.add_column("ID", style="cyan")
    table.add_column("名称", style="white")
    table.add_column("标的", style="yellow")
    table.add_column("渠道", style="green")
    table.add_column("时间", style="magenta")
    table.add_column("状态", style="green")
    for sub in store.list():
        last = store.last_run(sub.id) if sub.id else None
        status = "启用" if sub.enabled else "停用"
        if last:
            status += f"（上次 {last['ok']}/{last['total']} 成功）"
        table.add_row(str(sub.id), sub.name, "、".join(sub.symbols),
                      sub.channel, sub.time, status)
    console.print(table)


@subscribe.command("remove")
@click.option("--id", "sub_id", type=int, required=True, help="订阅 ID")
def subscribe_remove(sub_id):
    """删除订阅"""
    from push.store import PushStore
    from utils.config import Config

    store = PushStore(Config().config_dir / "push.db")
    if store.delete(sub_id):
        console.print(f"[green]已删除订阅 #{sub_id}[/green]")
    else:
        console.print(f"[red]订阅 #{sub_id} 不存在[/red]")


@subscribe.command("enable")
@click.option("--id", "sub_id", type=int, required=True, help="订阅 ID")
def subscribe_enable(sub_id):
    """启用订阅"""
    _set_enabled(sub_id, True)


@subscribe.command("disable")
@click.option("--id", "sub_id", type=int, required=True, help="订阅 ID")
def subscribe_disable(sub_id):
    """停用订阅"""
    _set_enabled(sub_id, False)


def _set_enabled(sub_id: int, enabled: bool):
    from push.store import PushStore
    from utils.config import Config

    store = PushStore(Config().config_dir / "push.db")
    sub = store.get(sub_id)
    if sub is None:
        console.print(f"[red]订阅 #{sub_id} 不存在[/red]")
        return
    sub.enabled = enabled
    store.update(sub)
    console.print(f"[green]订阅 #{sub_id} 已{'启用' if enabled else '停用'}[/green]")


@subscribe.command("run")
@click.option("--id", "sub_id", type=int, required=True, help="订阅 ID")
def subscribe_run(sub_id):
    """手动触发一次推送（同步执行，耗时取决于标的数）"""
    from push.executor import PushExecutor
    from push.store import PushStore
    from utils.config import Config

    config = Config()
    store = PushStore(config.config_dir / "push.db")
    sub = store.get(sub_id)
    if sub is None:
        console.print(f"[red]订阅 #{sub_id} 不存在[/red]")
        return
    from api.bootstrap import build_agent_core
    core = build_agent_core(config)
    executor = PushExecutor(core, store, config)
    with console.status("正在生成报告并推送..."):
        result = executor.run_subscription(sub)
    console.print(f"[green]推送完成: {result['ok']}/{result['total']} 成功[/green]")
    for failure in result["failures"]:
        console.print(f"[yellow]失败: {failure}[/yellow]")
```

（注意：subscribe 组定义需放在 `@main.group()` 装饰器可见的模块层；`console`、`Table` 已在 cli.py 顶部导入。`_set_enabled` 为模块级辅助函数。）

- [ ] **Step 4: 运行确认通过**

Run: `.venv/Scripts/python -m pytest tests/test_cli.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/stock_robot/cli.py tests/test_cli.py
git commit -m "feat(推送): CLI subscribe 命令组"
```

---

### Task 10: Web UI 订阅页（S 级）

**Files:**
- Modify: `src/api/static/index.html`（nav + view 容器）
- Modify: `src/api/static/js/api.js`（api 对象方法）
- Create: `src/api/static/js/subscriptions.js`
- Modify: `src/api/static/js/app.js`（注册初始化）

**前置事实：** nav 项结构 `<div class="nav-item" data-view="X">`；视图容器 `<div class="view" id="view-X">`；视图 JS 模块导出 `initXxx()` 并在 app.js 的 `init()` 调用；`api.request` 已封装（抛出带 status 的 Error）；`components.js` 有 `el(tag, cls, text)` 工具。

- [ ] **Step 1: 修改 `src/api/static/index.html`**

在 nav 的"指数分析"项之后追加：

```html
      <div class="nav-item" data-view="subscriptions">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22c5.5 0 10-4.5 10-10S17.5 2 12 2 2 6.5 2 12s4.5 10 10 10z"/><path d="M8 12h8"/><path d="M12 8v8"/></svg>
        <span>订阅推送</span>
      </div>
```

在 `</main>` 前（最后一个 view 之后）追加：

```html
    <div class="view" id="view-subscriptions">
      <div class="subs-form panel">
        <div class="panel-title">新建订阅</div>
        <div class="subs-form-row">
          <input id="subName" placeholder="订阅名称（如：自选池）">
          <input id="subSymbols" placeholder="标的代码，空格分隔（600519 000300）">
          <select id="subChannel">
            <option value="email">邮箱（全文）</option>
            <option value="wecom">企业微信（摘要）</option>
          </select>
          <input id="subTime" type="time" value="08:00">
          <button id="subCreateBtn">创建</button>
        </div>
        <div class="subs-error" id="subsError"></div>
      </div>
      <div id="subsList"></div>
    </div>
```

- [ ] **Step 2: 修改 `src/api/static/js/api.js`** — 在 `api` 对象中添加

```js
  listSubscriptions() {
    return request("/api/v1/subscriptions");
  },
  createSubscription(body) {
    return request("/api/v1/subscriptions", { method: "POST", body: JSON.stringify(body) });
  },
  updateSubscription(id, body) {
    return request(`/api/v1/subscriptions/${id}`, { method: "PUT", body: JSON.stringify(body) });
  },
  deleteSubscription(id) {
    return request(`/api/v1/subscriptions/${id}`, { method: "DELETE" });
  },
  triggerSubscription(id) {
    return request(`/api/v1/subscriptions/${id}/run`, { method: "POST" });
  },
```

- [ ] **Step 3: 创建 `src/api/static/js/subscriptions.js`**

```js
// 订阅推送管理视图：列表 + 创建 + 启停/删除/手动触发
import { el } from "./components.js";
import { api } from "./api.js";

const CHANNEL_LABELS = { email: "邮箱（全文）", wecom: "企业微信（摘要）" };

const content = () => document.getElementById("subsList");
const errorBox = () => document.getElementById("subsError");

export function initSubscriptions() {
  const btn = document.getElementById("subCreateBtn");
  btn.addEventListener("click", createSubscription);
  loadList();
}

function showError(msg) {
  errorBox().textContent = msg;
}

function clearError() {
  errorBox().textContent = "";
}

async function loadList() {
  const box = content();
  box.innerHTML = "";
  box.appendChild(el("div", "panel-title", "订阅列表"));
  let data;
  try {
    data = await api.listSubscriptions();
  } catch (err) {
    box.appendChild(el("div", "subs-error", `加载失败: ${err.message}`));
    return;
  }
  const subs = data.subscriptions || [];
  if (!subs.length) {
    box.appendChild(el("div", "dim", "暂无订阅，先在上方创建"));
    return;
  }
  for (const sub of subs) box.appendChild(subCard(sub));
}

function subCard(sub) {
  const card = el("div", "panel sub-card");
  const head = el("div", "sub-head");
  head.appendChild(el("span", "sub-name", sub.name));
  head.appendChild(el("span", "chip", CHANNEL_LABELS[sub.channel] || sub.channel));
  head.appendChild(el("span", "chip", sub.time));
  head.appendChild(el("span", "chip", sub.enabled ? "启用" : "停用"));
  const last = sub.last_run;
  if (last) {
    head.appendChild(el("span", "chip",
        `上次执行 ${new Date(last.ran_at * 1000).toLocaleString()} · ${last.ok}/${last.total} 成功`));
  }
  card.appendChild(head);
  card.appendChild(el("div", "sub-symbols", sub.symbols.join("、")));
  const actions = el("div", "sub-actions");
  const toggleBtn = el("button", "btn-sm", sub.enabled ? "停用" : "启用");
  toggleBtn.addEventListener("click", async () => {
    clearError();
    await api.updateSubscription(sub.id, {
      name: sub.name, symbols: sub.symbols,
      channel: sub.channel, time: sub.time, enabled: !sub.enabled,
    });
    loadList();
  });
  const runBtn = el("button", "btn-sm", "立即推送");
  runBtn.addEventListener("click", async () => {
    clearError();
    try {
      await api.triggerSubscription(sub.id);
      showError(`已触发推送 #${sub.id}，报告生成约需数分钟，完成后可在此查看结果`);
    } catch (err) {
      showError(err.message);
    }
  });
  const delBtn = el("button", "btn-sm danger", "删除");
  delBtn.addEventListener("click", async () => {
    clearError();
    if (!confirm(`确认删除订阅「${sub.name}」？`)) return;
    await api.deleteSubscription(sub.id);
    loadList();
  });
  actions.appendChild(toggleBtn);
  actions.appendChild(runBtn);
  actions.appendChild(delBtn);
  card.appendChild(actions);
  return card;
}

async function createSubscription() {
  clearError();
  const name = document.getElementById("subName").value.trim();
  const symbols = document.getElementById("subSymbols").value.trim().split(/\s+/).filter(Boolean);
  const channel = document.getElementById("subChannel").value;
  const time = document.getElementById("subTime").value;
  if (!name) return showError("请输入订阅名称");
  if (!symbols.length) return showError("请输入至少一个标的代码");
  try {
    await api.createSubscription({ name, symbols, channel, time });
    document.getElementById("subName").value = "";
    document.getElementById("subSymbols").value = "";
    loadList();
  } catch (err) {
    showError(err.message);
  }
}
```

- [ ] **Step 4: 修改 `src/api/static/js/app.js`** — 导入并注册

```js
import { initSubscriptions } from "./subscriptions.js";
```

在 `init()` 中、`initSessionStartup()` 之前加：

```js
  initSubscriptions();
```

- [ ] **Step 5: 浏览器手工验收（此项无法自动化，执行人手动完成）**

1. 启动：`! .venv/Scripts/python -m stock_robot.cli api`（或项目入口命令，绑 127.0.0.1:25618）
2. 打开 http://127.0.0.1:25618，左侧导航出现"订阅推送"
3. 创建订阅：名称"自选池"、标的"600519 000300"、渠道"企业微信（摘要）"、时间"08:00" → 点击创建 → 列表出现该订阅
4. 点"立即推送"→ 显示已触发提示；等待数分钟后列表刷新可见"上次执行 N/N 成功"
5. 点"停用"→ 状态变"停用"；点"启用"恢复
6. 点"删除"→ 确认后消失
7. 用 CLI 验证数据互通：`! .venv/Scripts/python -m stock_robot.cli subscribe list` 应看到同一条订阅

（若浏览器不可用，至少用 CLI 端到端：`subscribe add` → `subscribe run` 验证推送链路。）

- [ ] **Step 6: 提交**

```bash
git add src/api/static/index.html src/api/static/js/api.js src/api/static/js/subscriptions.js src/api/static/js/app.js
git commit -m "feat(推送): Web UI 订阅管理页面"
```

---

## 自审记录

- **Spec 覆盖**：models/store（spec 数据模型节）、backends（推送后端节）、summary（推送内容节）、executor（调度与执行节）、scheduler（调度节）、API 6 端点（三端管理节）、CLI subscribe（三端管理节）、Web UI（三端管理节）、push_runs 记录（executor/store）、全局开关（scheduler.start 判 push.enabled）、手动触发后台线程（API run 端点）。
- **歧义修正**：指数 `IndexMapping().lookup` 未命中时的 index_style 归属（海外默认 overseas、A 股默认 broad）在 Task 6 明确。
- **测试环境事实**：conftest.py 已 mock akshare 网络调用，executor 测试无需额外网络 mock；`resolve_name` 在 executor 测试中 mock。
- **依赖顺序**：Task 2→3→4→5→6→7→8→9→10（Task 1 独立），各任务可独立提交。
