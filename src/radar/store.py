"""配置雷达的不可变 SQLite 快照存储。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from radar.models import SnapshotItem


class RadarStore:
    """只将已完成运行暴露给读取方的本地快照库。"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create_run(
        self,
        *,
        universe_id: str,
        universe_version: int,
        score_profile: str,
        provider: str,
        as_of_date: str,
    ) -> str:
        """创建不可见的运行，调用方完成全部项目后再提交。"""
        run_id = uuid.uuid4().hex
        now = _now_iso()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO snapshot_runs(
                    run_id, universe_id, universe_version, score_profile, provider,
                    as_of_date, status, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?)
                """,
                (run_id, universe_id, universe_version, score_profile, provider, as_of_date, now),
            )
        return run_id

    def add_item(self, run_id: str, item: SnapshotItem) -> None:
        """写入运行中的项目；已完成运行不可修改。"""
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                """
                INSERT INTO snapshot_items(
                    run_id, symbol, name, category, status, observed_at, source_run_id,
                    close, amount, score, rank, grade, factors_json, error_summary
                )
                SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                WHERE EXISTS (
                    SELECT 1 FROM snapshot_runs WHERE run_id = ? AND status = 'running'
                )
                """,
                (
                    run_id,
                    item.symbol,
                    item.name,
                    item.category,
                    item.status,
                    item.observed_at.isoformat() if item.observed_at else None,
                    item.source_run_id,
                    item.close,
                    item.amount,
                    item.score,
                    item.rank,
                    item.grade,
                    json.dumps(item.factors, ensure_ascii=False) if item.factors is not None else None,
                    item.error_summary,
                    run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"运行不存在或已完成，无法写入: {run_id}")

    def complete_run(self, run_id: str) -> None:
        """原子地将运行从 running 变为 completed。"""
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                """
                UPDATE snapshot_runs
                SET status = 'completed', completed_at = ?
                WHERE run_id = ? AND status = 'running'
                """,
                (_now_iso(), run_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"运行不存在或已完成，无法提交: {run_id}")

    def fail_run(self, run_id: str, error_summary: str) -> None:
        """标记失败运行；失败运行不会替代上一次可读快照。"""
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                UPDATE snapshot_runs
                SET status = 'failed', completed_at = ?, error_summary = ?
                WHERE run_id = ? AND status = 'running'
                """,
                (_now_iso(), error_summary, run_id),
            )

    def latest_completed(self, universe_id: str) -> dict[str, Any] | None:
        """返回最近完成快照及其项目；运行中和失败运行永不泄漏。"""
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                """
                SELECT * FROM snapshot_runs
                WHERE universe_id = ? AND status = 'completed'
                ORDER BY completed_at DESC LIMIT 1
                """,
                (universe_id,),
            ).fetchone()
            return self._snapshot(conn, row) if row is not None else None

    def get_snapshot(self, run_id: str) -> dict[str, Any] | None:
        """按 ID 获取已完成快照。"""
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT * FROM snapshot_runs WHERE run_id = ? AND status = 'completed'", (run_id,)
            ).fetchone()
            return self._snapshot(conn, row) if row is not None else None

    def copy_latest_healthy_item(self, universe_id: str, symbol: str) -> SnapshotItem | None:
        """获取上一个完成快照中的新鲜项目，供刷新失败时降级为 stale。"""
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                """
                SELECT item.*
                FROM snapshot_items AS item
                JOIN snapshot_runs AS run ON run.run_id = item.run_id
                WHERE run.universe_id = ? AND run.status = 'completed'
                    AND item.symbol = ? AND item.status = 'fresh'
                ORDER BY run.completed_at DESC LIMIT 1
                """,
                (universe_id, symbol),
            ).fetchone()
        if row is None:
            return None
        item = SnapshotItem.model_validate(dict(row))
        return item.model_copy(update={"source_run_id": item.source_run_id or str(row["run_id"])})

    def status_summary(self, universe_id: str) -> dict[str, int] | None:
        """汇总最近完成快照的项目状态。"""
        snapshot = self.latest_completed(universe_id)
        if snapshot is None:
            return None
        counts = {"fresh": 0, "stale": 0, "failed": 0}
        for item in snapshot["items"]:
            counts[item["status"]] += 1
        return counts

    def _initialize(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS snapshot_runs (
                    run_id TEXT PRIMARY KEY,
                    universe_id TEXT NOT NULL,
                    universe_version INTEGER NOT NULL,
                    score_profile TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    as_of_date TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'failed')),
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    error_summary TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_snapshot_runs_latest
                    ON snapshot_runs(universe_id, status, completed_at DESC);
                CREATE TABLE IF NOT EXISTS snapshot_items (
                    run_id TEXT NOT NULL REFERENCES snapshot_runs(run_id),
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('fresh', 'stale', 'failed')),
                    observed_at TEXT,
                    source_run_id TEXT,
                    close REAL,
                    amount REAL,
                    score REAL,
                    rank INTEGER,
                    grade TEXT NOT NULL,
                    factors_json TEXT,
                    error_summary TEXT,
                    PRIMARY KEY(run_id, symbol)
                );
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(snapshot_items)")}
            if "factors_json" not in columns:
                conn.execute("ALTER TABLE snapshot_items ADD COLUMN factors_json TEXT")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _snapshot(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        items = conn.execute(
            "SELECT * FROM snapshot_items WHERE run_id = ? ORDER BY category, rank, symbol", (row["run_id"],)
        ).fetchall()
        payload["items"] = [
            {**dict(item), "factors": json.loads(item["factors_json"]) if item["factors_json"] else None}
            for item in items
        ]
        return json.loads(json.dumps(payload, ensure_ascii=False))


def _now_iso() -> str:
    """生成带本地时区的审计时间。"""
    return datetime.now().astimezone().isoformat()
