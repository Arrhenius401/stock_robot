"""订阅存储 — SQLite 持久化（subscriptions + push_runs 两张表）"""
from __future__ import annotations

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
            return int(cur.lastrowid or 0)

    def list(self) -> list[Subscription]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM subscriptions ORDER BY id").fetchall()
        return [PushStore._row_to_sub(r) for r in rows]

    def get(self, sub_id: int) -> Subscription | None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM subscriptions WHERE id=?", (sub_id,)
            ).fetchone()
        return PushStore._row_to_sub(row) if row else None

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
            return int(cur.lastrowid or 0)

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
