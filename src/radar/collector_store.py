"""配置雷达采集守护的本地运行状态。"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any


class CollectorStore:
    """保存各标的池最近一次自动采集的结果，供 Web 总览审计。"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS collector_status (
                    universe_id TEXT PRIMARY KEY,
                    completed_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('completed', 'failed')),
                    run_id TEXT,
                    error_summary TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS collector_runtime (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    started_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS collector_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    universe_id TEXT,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL
                )
                """
            )

    def record(self, universe_id: str, status: str, result: str | None = None) -> None:
        """原子更新单个池的最近一次采集结果。"""
        if status not in {"completed", "failed"}:
            raise ValueError(f"未知采集状态: {status}")
        run_id = result if status == "completed" else None
        error_summary = result if status == "failed" else None
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO collector_status(universe_id, completed_at, status, run_id, error_summary)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(universe_id) DO UPDATE SET
                    completed_at = excluded.completed_at,
                    status = excluded.status,
                    run_id = excluded.run_id,
                    error_summary = excluded.error_summary
                """,
                (universe_id, _now_iso(), status, run_id, error_summary),
            )
            message = f"{universe_id} 采集完成：{run_id}" if run_id else f"{universe_id} 采集失败：{error_summary}"
            conn.execute(
                "INSERT INTO collector_events(occurred_at, universe_id, level, message) VALUES (?, ?, ?, ?)",
                (_now_iso(), universe_id, "info" if status == "completed" else "error", message),
            )

    def record_started(self) -> None:
        """记录一个新的守护进程生命周期。"""
        now = _now_iso()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO collector_runtime(singleton, started_at, heartbeat_at) VALUES (1, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET started_at = excluded.started_at, heartbeat_at = excluded.heartbeat_at
                """,
                (now, now),
            )
            conn.execute(
                "INSERT INTO collector_events(occurred_at, level, message) VALUES (?, ?, ?)",
                (now, "info", "采集守护已启动"),
            )

    def record_heartbeat(self) -> None:
        """刷新存活时间；不写事件，避免长期运行产生噪声。"""
        with closing(self._connect()) as conn, conn:
            conn.execute("UPDATE collector_runtime SET heartbeat_at = ? WHERE singleton = 1", (_now_iso(),))

    def runtime(self) -> dict[str, Any]:
        """读取守护进程最近心跳；未启动时返回明确状态。"""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT started_at, heartbeat_at FROM collector_runtime WHERE singleton = 1").fetchone()
        return dict(row) if row is not None else {"status": "never"}

    def recent_events(self, limit: int = 6) -> list[dict[str, Any]]:
        """读取有限条最新审计事件，供配置页排查。"""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT occurred_at, universe_id, level, message FROM collector_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest(self, universe_ids: tuple[str, ...]) -> list[dict[str, Any]]:
        """按池配置顺序返回状态；未执行过的池明确标识为 never。"""
        if not universe_ids:
            return []
        placeholders = ", ".join("?" for _ in universe_ids)
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT * FROM collector_status WHERE universe_id IN ({placeholders})",
                universe_ids,
            ).fetchall()
        found = {str(row["universe_id"]): dict(row) for row in rows}
        return [
            found.get(universe_id, {"universe_id": universe_id, "status": "never"})
            for universe_id in universe_ids
        ]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


def _now_iso() -> str:
    """生成带本地时区的审计时间。"""
    return datetime.now().astimezone().isoformat()
