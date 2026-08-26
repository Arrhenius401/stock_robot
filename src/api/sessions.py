"""会话持久化 — SQLite 存储 sessions 与 messages"""
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from agent.memory import Memory, MemoryMessage
from api.session_titles import derive_session_title, derive_session_title_from_messages

_AUTOMATIC_TITLE_SOURCES = ("legacy", "local", "llm", "default")


def is_draft_session_id(session_id: str | None) -> bool:
    """判断是否为仅存在于浏览器中的临时会话 ID。"""
    return bool(session_id and session_id.startswith("draft-"))


class SessionStore:
    """SQLite 会话存储 — sessions + messages 两张表"""

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._lock = threading.RLock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    title       TEXT NOT NULL,
                    title_source TEXT NOT NULL DEFAULT 'legacy',
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
                CREATE INDEX IF NOT EXISTS idx_artifacts_session
                    ON artifacts(session_id, created_at);
            """)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
            if "title_source" not in columns:
                conn.execute(
                    "ALTER TABLE sessions ADD COLUMN title_source TEXT NOT NULL DEFAULT 'legacy'"
                )
            self._purge_empty_sessions(conn)

    @staticmethod
    def _purge_empty_sessions(conn: sqlite3.Connection) -> int:
        """删除没有消息的会话及其关联成果。"""
        empty_sessions = """
            SELECT s.session_id FROM sessions s
            WHERE NOT EXISTS (
                SELECT 1 FROM messages m WHERE m.session_id = s.session_id
            )
        """
        conn.execute(
            f"DELETE FROM artifacts WHERE session_id IN ({empty_sessions})"
        )
        result = conn.execute(
            f"DELETE FROM sessions WHERE session_id IN ({empty_sessions})"
        )
        return result.rowcount

    def purge_empty_sessions(self) -> int:
        """清理零消息会话，返回已删除的会话数。"""
        with self._lock, self._get_conn() as conn:
            return self._purge_empty_sessions(conn)

    def create_session(self, session_id: str, title: str, source: str = "legacy") -> None:
        now = time.time()
        with self._lock, self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sessions "
                "(session_id, title, title_source, created_at, updated_at) VALUES (?,?,?,?,?)",
                (session_id, title, source, now, now),
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
                """SELECT s.session_id, s.title, s.title_source, s.created_at, s.updated_at,
                          COUNT(m.id) AS message_count
                   FROM sessions s LEFT JOIN messages m ON m.session_id = s.session_id
                   GROUP BY s.session_id ORDER BY s.updated_at DESC"""
            ).fetchall()
        return [
            {"session_id": r[0], "title": r[1], "title_source": r[2],
             "created_at": r[3], "updated_at": r[4], "message_count": r[5]}
            for r in rows
        ]

    def get_messages(self, session_id: str) -> list[MemoryMessage]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT id, role, content FROM messages WHERE session_id=? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [
            {"message_id": r[0], "role": r[1], "content": r[2]}
            for r in rows
        ]

    def append_message(self, session_id: str, role: str, content: str) -> int:
        now = time.time()
        with self._lock, self._get_conn() as conn:
            cursor = conn.execute(
                "INSERT INTO messages (session_id, role, content, created_at) VALUES (?,?,?,?)",
                (session_id, role, content, now),
            )
            conn.execute(
                "UPDATE sessions SET updated_at=? WHERE session_id=?",
                (now, session_id),
            )
        message_id = cursor.lastrowid
        if message_id is None:
            raise RuntimeError("SQLite 未返回新消息 ID")
        return int(message_id)

    def update_title(
        self, session_id: str, title: str, source: str, only_if_automatic: bool = False
    ) -> bool:
        """更新标题；自动更新不能覆盖用户手动标题。"""
        now = time.time()
        query = "UPDATE sessions SET title=?, title_source=?, updated_at=? WHERE session_id=?"
        params: tuple[object, ...] = (title, source, now, session_id)
        if only_if_automatic:
            placeholders = ", ".join("?" for _ in _AUTOMATIC_TITLE_SOURCES)
            query += f" AND title_source IN ({placeholders})"
            params += _AUTOMATIC_TITLE_SOURCES
        with self._lock, self._get_conn() as conn:
            result = conn.execute(query, params)
        return result.rowcount == 1

    def delete_session(self, session_id: str) -> None:
        with self._lock, self._get_conn() as conn:
            conn.execute("DELETE FROM artifacts WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))

    @staticmethod
    def _artifact_from_row(
        row: tuple[str, str, int | None, str, str | None, str, float, float]
    ) -> dict:
        return {
            "artifact_id": row[0],
            "session_id": row[1],
            "message_id": row[2],
            "kind": row[3],
            "symbol": row[4],
            "payload": json.loads(row[5]),
            "created_at": row[6],
            "updated_at": row[7],
        }

    def save_artifact(
        self,
        session_id: str,
        *,
        kind: str,
        symbol: str | None,
        payload: dict,
        message_id: int | None = None,
    ) -> dict:
        """保存结构化报告成果，并返回其完整记录。"""
        artifact_id = uuid.uuid4().hex
        now = time.time()
        payload_json = json.dumps(payload, ensure_ascii=False, default=str)
        artifact = {
            "artifact_id": artifact_id,
            "session_id": session_id,
            "message_id": message_id,
            "kind": kind,
            "symbol": symbol,
            "payload": json.loads(payload_json),
            "created_at": now,
            "updated_at": now,
        }
        with self._lock, self._get_conn() as conn:
            conn.execute(
                """INSERT INTO artifacts
                   (artifact_id, session_id, message_id, kind, symbol, payload_json,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    artifact_id,
                    session_id,
                    message_id,
                    kind,
                    symbol,
                    payload_json,
                    now,
                    now,
                ),
            )
        return artifact

    def list_artifacts(self, session_id: str) -> list[dict]:
        """按创建顺序获取指定会话的所有成果。"""
        with self._lock, self._get_conn() as conn:
            rows = conn.execute(
                """SELECT artifact_id, session_id, message_id, kind, symbol, payload_json,
                          created_at, updated_at
                   FROM artifacts WHERE session_id=? ORDER BY created_at, rowid""",
                (session_id,),
            ).fetchall()
        return [self._artifact_from_row(row) for row in rows]

    def get_artifact(self, artifact_id: str) -> dict | None:
        """按 ID 读取单个成果；不存在时返回 None。"""
        with self._lock, self._get_conn() as conn:
            row = conn.execute(
                """SELECT artifact_id, session_id, message_id, kind, symbol, payload_json,
                          created_at, updated_at
                   FROM artifacts WHERE artifact_id=?""",
                (artifact_id,),
            ).fetchone()
        return self._artifact_from_row(row) if row is not None else None


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
                      max_messages=self._max_messages,
                      facts_path=self._facts_path)

    def get_or_create(self, session_id: str | None,
                      first_message: str = "") -> tuple[str, Memory]:
        with self._lock:
            if is_draft_session_id(session_id):
                session_id = None
            if session_id and session_id in self._memories:
                return session_id, self._memories[session_id]
            if session_id and self._store.session_exists(session_id):
                memory = self._new_memory(session_id)
                memory.messages = self._store.get_messages(session_id)[-self._max_messages:]
                self._memories[session_id] = memory
                return session_id, memory
            sid = session_id or uuid.uuid4().hex
            title = derive_session_title(first_message)
            source = "default" if not first_message.strip() else "local"
            self._store.create_session(sid, title, source)
            memory = self._new_memory(sid)
            self._memories[sid] = memory
            return sid, memory

    def get_memory(self, session_id: str) -> Memory | None:
        with self._lock:
            return self._memories.get(session_id)

    def list_sessions(self) -> list[dict]:
        self._store.purge_empty_sessions()
        sessions = self._store.list_sessions()
        for item in sessions:
            if item["title_source"] == "manual" or item["message_count"] == 0:
                continue
            self.refresh_automatic_title(item["session_id"])
        return self._store.list_sessions()

    def refresh_automatic_title(self, session_id: str) -> str | None:
        """用前两条用户消息回填自动标题，不覆盖手动标题。"""
        meta = next((item for item in self._store.list_sessions()
                     if item["session_id"] == session_id), None)
        if meta is None or meta["title_source"] == "manual":
            return None
        messages = [message["content"] for message in self._store.get_messages(session_id)
                    if message["role"] == "user"][:2]
        title = derive_session_title_from_messages(messages)
        if title == meta["title"]:
            return None
        return title if self._store.update_title(
            session_id, title, "local", only_if_automatic=True) else None

    def get_messages(self, session_id: str) -> list[MemoryMessage] | None:
        """按会话读回持久化消息；会话不存在返回 None"""
        if not self._store.session_exists(session_id):
            return None
        return self._store.get_messages(session_id)

    def save_artifact(
        self,
        session_id: str,
        *,
        kind: str,
        symbol: str | None,
        payload: dict,
        message_id: int | None = None,
        memory: Memory | None = None,
    ) -> dict:
        """线程安全地保存成果；带 Memory 时拒绝已失效的旧执行。"""
        with self._lock:
            if memory is not None and (
                    self._memories.get(session_id) is not memory
                    or not memory.active):
                raise RuntimeError("旧会话执行已失效，拒绝保存成果")
            return self._store.save_artifact(
                session_id,
                kind=kind,
                symbol=symbol,
                payload=payload,
                message_id=message_id,
            )

    def get_artifact(self, artifact_id: str) -> dict | None:
        """线程安全地读取单个结构化报告成果。"""
        with self._lock:
            return self._store.get_artifact(artifact_id)

    def get_session_detail(self, session_id: str) -> dict | None:
        """返回会话消息与成果；会话不存在时返回 None。"""
        with self._lock:
            if not self._store.session_exists(session_id):
                return None
            return {
                "messages": self._store.get_messages(session_id),
                "artifacts": self._store.list_artifacts(session_id),
            }

    def delete(self, session_id: str) -> bool:
        with self._lock:
            if not self._store.session_exists(session_id):
                return False
            memory = self._memories.pop(session_id, None)
            if memory:
                memory.invalidate()
            self._store.delete_session(session_id)
            return True

    def rename(self, session_id: str, title: str, manual: bool = True) -> bool:
        """重命名会话；默认视为用户手动命名。"""
        source = "manual" if manual else "local"
        with self._lock:
            return self._store.update_title(session_id, title, source)

    def maybe_update_title(self, session_id: str, title: str, source: str) -> bool:
        """仅在当前标题为自动来源时写入新标题。"""
        with self._lock:
            return self._store.update_title(
                session_id, title, source, only_if_automatic=True
            )
