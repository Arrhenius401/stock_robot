"""会话持久化 — SQLite 存储 sessions 与 messages"""
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from agent.memory import Memory
from api.session_titles import derive_session_title

_AUTOMATIC_TITLE_SOURCES = ("legacy", "local", "llm", "default")


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
            """)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
            if "title_source" not in columns:
                conn.execute(
                    "ALTER TABLE sessions ADD COLUMN title_source TEXT NOT NULL DEFAULT 'legacy'"
                )

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

    def get_messages(self, session_id: str) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE session_id=? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [{"role": r[0], "content": r[1]} for r in rows]

    def append_message(self, session_id: str, role: str, content: str) -> None:
        now = time.time()
        with self._lock, self._get_conn() as conn:
            conn.execute(
                "INSERT INTO messages (session_id, role, content, created_at) VALUES (?,?,?,?)",
                (session_id, role, content, now),
            )
            conn.execute(
                "UPDATE sessions SET updated_at=? WHERE session_id=?",
                (now, session_id),
            )

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

    def clear_messages(self, session_id: str) -> None:
        with self._lock, self._get_conn() as conn:
            conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))

    def delete_session(self, session_id: str) -> None:
        with self._lock, self._get_conn() as conn:
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
                      max_messages=self._max_messages,
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
        return self._store.list_sessions()

    def get_messages(self, session_id: str) -> list[dict] | None:
        """按会话读回持久化消息；会话不存在返回 None"""
        if not self._store.session_exists(session_id):
            return None
        return self._store.get_messages(session_id)

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
