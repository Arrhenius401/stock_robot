import sqlite3
import time
from pathlib import Path


class CacheManager:
    """SQLite 缓存管理器 — 基于 TTL 的自动驱逐"""

    def __init__(self, db_path: Path, default_ttls: dict[str, int] | None = None):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._default_ttls = default_ttls or {
            "price": 86400,
            "valuation": 86400,
            "financial": 604800,
            "industry": 604800,
            "news": 21600,
        }
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    data_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    date_key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    ttl_seconds INTEGER NOT NULL,
                    PRIMARY KEY (data_type, symbol, date_key)
                )
            """)

    def get(self, data_type: str, symbol: str, date_key: str) -> str | None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT value, created_at, ttl_seconds FROM cache WHERE data_type=? AND symbol=? AND date_key=?",
                (data_type, symbol, date_key),
            ).fetchone()
            if row is None:
                return None
            value, created_at, ttl_seconds = row
            if ttl_seconds >= 0 and (time.time() - created_at) > ttl_seconds:
                self.invalidate(data_type, symbol, date_key)
                return None
            return value

    def get_stale(self, data_type: str, symbol: str, date_key: str) -> str | None:
        """读取缓存原始值，忽略 TTL；用于短时效数据源失败时的降级兜底。"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM cache WHERE data_type=? AND symbol=? AND date_key=?",
                (data_type, symbol, date_key),
            ).fetchone()
            if row is None:
                return None
            return row[0]

    def put(self, data_type: str, symbol: str, date_key: str, value: str, ttl_seconds: int | None = None):
        if ttl_seconds is None:
            ttl_seconds = self._default_ttls.get(data_type, 86400)
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (data_type, symbol, date_key, value, created_at, ttl_seconds) VALUES (?,?,?,?,?,?)",
                (data_type, symbol, date_key, value, time.time(), ttl_seconds),
            )

    def invalidate(self, data_type: str, symbol: str, date_key: str):
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM cache WHERE data_type=? AND symbol=? AND date_key=?",
                (data_type, symbol, date_key),
            )

    def invalidate_symbol(self, symbol: str):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM cache WHERE symbol=?", (symbol,))

    def clear(self):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM cache")

    def cleanup_old_entries(self, data_type: str, symbol: str, keep_date_key: str):
        """删除同一 (data_type, symbol) 下非当前 date_key 的旧条目"""
        with self._get_conn() as conn:
            conn.execute(
                "DELETE FROM cache WHERE data_type=? AND symbol=? AND date_key!=?",
                (data_type, symbol, keep_date_key),
            )

    def stats(self) -> dict:
        with self._get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
            db_size = self._db_path.stat().st_size if self._db_path.exists() else 0
        return {"total_entries": count, "db_size_bytes": db_size}
