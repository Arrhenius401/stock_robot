"""订阅与推送运行的 SQLite 持久化。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from push.models import Subscription, SubscriptionSymbol


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _process_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    if sys.platform == "win32":
        # Windows 的 os.kill(pid, 0) 会发出控制信号，必须改用只读句柄查询。
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE，仅等待状态。
        if not handle:
            # 不存在的 PID 返回 ERROR_INVALID_PARAMETER；权限不足时保守保留活动运行。
            return ctypes.get_last_error() != 87
        try:
            return kernel.WaitForSingleObject(handle, 0) != 0
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class ActiveRunError(ValueError):
    """订阅已有活动运行。"""


class MissingSubscriptionError(ValueError):
    """运行开始前订阅已被删除或不支持。"""


class PushStore:
    """订阅管理和运行进度。"""

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                    symbols TEXT NOT NULL, channel TEXT NOT NULL, time TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS push_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, subscription_id INTEGER NOT NULL,
                    trigger TEXT NOT NULL DEFAULT 'manual', status TEXT NOT NULL DEFAULT 'succeeded',
                    queued_at TEXT NOT NULL DEFAULT '', started_at TEXT, finished_at TEXT,
                    total INTEGER NOT NULL DEFAULT 0, processed INTEGER NOT NULL DEFAULT 0,
                    ok INTEGER NOT NULL DEFAULT 0, current_symbol TEXT,
                    failures TEXT NOT NULL DEFAULT '[]', subscription_snapshot TEXT NOT NULL DEFAULT '{}',
                    ran_at REAL, owner_pid INTEGER);
            """)
            additions = {
                "subscriptions": {"updated_at": "TEXT NOT NULL DEFAULT ''"},
                "push_runs": {"trigger": "TEXT NOT NULL DEFAULT 'manual'", "status": "TEXT NOT NULL DEFAULT 'succeeded'",
                              "queued_at": "TEXT NOT NULL DEFAULT ''", "started_at": "TEXT", "finished_at": "TEXT",
                              "processed": "INTEGER NOT NULL DEFAULT 0", "current_symbol": "TEXT",
                              "subscription_snapshot": "TEXT NOT NULL DEFAULT '{}'", "ran_at": "REAL",
                              "owner_pid": "INTEGER"},
            }
            for table, fields in additions.items():
                columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                for name, declaration in fields.items():
                    if name not in columns:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
            conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS push_runs_one_active
                ON push_runs(subscription_id) WHERE status IN ('queued', 'running')""")

    def create(self, sub: Subscription) -> int:
        now = _now()
        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO subscriptions (name,symbols,channel,time,enabled,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (sub.name, _symbols_to_json(sub.symbols), sub.channel, sub.time,
                 int(sub.enabled), sub.created_at or now, now),
            )
            return int(cur.lastrowid or 0)

    def list(self) -> list[Subscription]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT * FROM subscriptions WHERE channel='email' ORDER BY id").fetchall()
        return [self._row_to_sub(row) for row in rows]

    def get(self, sub_id: int) -> Subscription | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM subscriptions WHERE id=? AND channel='email'", (sub_id,)).fetchone()
        return self._row_to_sub(row) if row else None

    def update(self, sub: Subscription) -> bool:
        if sub.id is None:
            return False
        with self._get_conn() as conn:
            cur = conn.execute(
                "UPDATE subscriptions SET name=?,symbols=?,channel=?,time=?,enabled=?,updated_at=? WHERE id=? AND channel='email'",
                (sub.name, _symbols_to_json(sub.symbols), sub.channel, sub.time,
                 int(sub.enabled), _now(), sub.id),
            )
            return cur.rowcount > 0

    def delete(self, sub_id: int) -> bool:
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM push_runs WHERE subscription_id=? AND status IN ('queued','running')", (sub_id,)).fetchone():
                raise ActiveRunError("订阅正在推送，暂不能删除")
            cur = conn.execute("DELETE FROM subscriptions WHERE id=?", (sub_id,))
            if cur.rowcount:
                conn.execute("DELETE FROM push_runs WHERE subscription_id=?", (sub_id,))
            return cur.rowcount > 0

    def queue_run(self, sub: Subscription, trigger: Literal["manual", "scheduled"]) -> int:
        if sub.id is None:
            raise ValueError("订阅尚未保存")
        try:
            with self._get_conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT * FROM subscriptions WHERE id=? AND channel='email'", (sub.id,),
                ).fetchone()
                if row is None:
                    raise MissingSubscriptionError("订阅不存在或渠道不可用")
                current = self._row_to_sub(row)
                cur = conn.execute(
                    "INSERT INTO push_runs (subscription_id,trigger,status,queued_at,total,ok,failures,subscription_snapshot,ran_at,owner_pid)"
                    " VALUES (?,?,'queued',?,?,0,'[]',?,?,?)",
                    (sub.id, trigger, _now(), len(current.symbols), current.model_dump_json(), time.time(), os.getpid()),
                )
                return int(cur.lastrowid or 0)
        except sqlite3.IntegrityError as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise ActiveRunError("订阅已有正在执行的推送") from exc
            raise

    def start_run(self, run_id: int) -> None:
        with self._get_conn() as conn:
            conn.execute("UPDATE push_runs SET status='running',started_at=? WHERE id=? AND status='queued'", (_now(), run_id))

    def set_current(self, run_id: int, symbol: str) -> None:
        with self._get_conn() as conn:
            conn.execute("UPDATE push_runs SET current_symbol=? WHERE id=?", (symbol, run_id))

    def advance_run(self, run_id: int, *, success: bool, failure: str | None = None) -> None:
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT failures FROM push_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError("运行记录不存在")
            failures = json.loads(row[0])
            if failure:
                failures.append(failure)
            conn.execute("UPDATE push_runs SET processed=processed+1,ok=ok+?,failures=?,current_symbol=NULL WHERE id=?",
                         (int(success), json.dumps(failures, ensure_ascii=False), run_id))

    def finish_run(self, run_id: int, *, error: str | None = None) -> dict[str, Any]:
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT total,processed,ok,failures FROM push_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError("运行记录不存在")
            failures = json.loads(row[3])
            if error:
                failures.append(error)
            status = "failed" if error else "succeeded" if row[1] == row[0] == row[2] else "partial" if row[2] else "failed"
            conn.execute("UPDATE push_runs SET status=?,finished_at=?,current_symbol=NULL,failures=? WHERE id=?",
                         (status, _now(), json.dumps(failures, ensure_ascii=False), run_id))
        return self.get_run(run_id) or {}

    def recover_interrupted(self) -> int:
        with self._get_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT id,owner_pid FROM push_runs WHERE status IN ('queued','running')",
            ).fetchall()
            stale_ids = [int(row["id"]) for row in rows if not _process_alive(row["owner_pid"])]
            for run_id in stale_ids:
                conn.execute(
                    "UPDATE push_runs SET status='interrupted',finished_at=?,current_symbol=NULL WHERE id=?",
                    (_now(), run_id),
                )
            return len(stale_ids)

    def get_run(self, run_id: int) -> dict[str, Any] | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM push_runs WHERE id=?", (run_id,)).fetchone()
        return self._row_to_run(row) if row else None

    def list_runs(self, subscription_id: int | None = None, limit: int = 20) -> list[dict[str, Any]]:
        sql = "SELECT * FROM push_runs"
        params: tuple[int, ...] = ()
        if subscription_id is not None:
            sql += " WHERE subscription_id=?"
            params = (subscription_id,)
        with self._get_conn() as conn:
            rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()
        return [self._row_to_run(row) for row in rows]

    def last_run(self, subscription_id: int) -> dict[str, Any] | None:
        runs = self.list_runs(subscription_id, 1)
        return runs[0] if runs else None

    def record_run(self, subscription_id: int, total: int, ok: int, failures: list[str]) -> int:
        """兼容旧调用；新执行路径使用 queue_run。"""
        with self._get_conn() as conn:
            cur = conn.execute(
                "INSERT INTO push_runs (subscription_id,trigger,status,queued_at,finished_at,total,processed,ok,failures,ran_at)"
                " VALUES (?,'manual',?,?,?,?,?,?,?,?)",
                (subscription_id, "succeeded" if ok == total else "partial" if ok else "failed",
                 _now(), _now(), total, total, ok, json.dumps(failures, ensure_ascii=False), time.time()),
            )
            return int(cur.lastrowid or 0)

    @staticmethod
    def _row_to_sub(row: sqlite3.Row) -> Subscription:
        return Subscription(id=row["id"], name=row["name"], symbols=_symbols_from_json(row["symbols"]),
                            channel=row["channel"], time=row["time"], enabled=bool(row["enabled"]),
                            created_at=row["created_at"], updated_at=row["updated_at"])

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["id"], "subscription_id": row["subscription_id"],
                "trigger": row["trigger"], "status": row["status"],
                "queued_at": row["queued_at"], "started_at": row["started_at"],
                "finished_at": row["finished_at"], "total": row["total"],
                "processed": row["processed"], "ok": row["ok"],
                "current_symbol": row["current_symbol"], "failures": json.loads(row["failures"]),
                "subscription_snapshot": json.loads(row["subscription_snapshot"])}


def _symbols_to_json(symbols: list[SubscriptionSymbol]) -> str:
    return json.dumps([symbol.model_dump() for symbol in symbols], ensure_ascii=False)


def _symbols_from_json(raw: str) -> list[SubscriptionSymbol]:
    return [SubscriptionSymbol(**item) for item in json.loads(raw)]
