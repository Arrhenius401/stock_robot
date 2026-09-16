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
