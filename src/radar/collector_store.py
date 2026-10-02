"""配置雷达采集守护的本地运行状态。"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4


class CollectorRevisionConflict(ValueError):
    """设置已被另一个页面修改。"""


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

            conn.executescript("""
                CREATE TABLE IF NOT EXISTS collector_settings (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    enabled INTEGER NOT NULL DEFAULT 0, hour INTEGER NOT NULL DEFAULT 18,
                    minute INTEGER NOT NULL DEFAULT 30, revision INTEGER NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO collector_settings(singleton) VALUES (1);
                CREATE TABLE IF NOT EXISTS collector_runs (
                    id TEXT PRIMARY KEY, universe_id TEXT NOT NULL, target_date TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, universe_version INTEGER NOT NULL,
                    source TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, available_at TEXT NOT NULL,
                    worker_id TEXT, lease_until TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
                    error_summary TEXT, snapshot_run_id TEXT,
                    UNIQUE(universe_id,target_date,fingerprint)
                );
                CREATE TABLE IF NOT EXISTS collector_items (
                    run_id TEXT NOT NULL REFERENCES collector_runs(id), symbol TEXT NOT NULL,
                    status TEXT NOT NULL, error_summary TEXT,
                    PRIMARY KEY(run_id,symbol)
                );
            """)
            conn.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(collector_runtime)")}
            for name in ("worker_id", "phase", "last_error"):
                if name not in columns:
                    conn.execute(f"ALTER TABLE collector_runtime ADD COLUMN {name} TEXT")

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

    def record_started(self, worker_id: str | None = None, phase: str = "waiting") -> None:
        """记录一个新的守护进程生命周期。"""
        now = _now_iso()
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO collector_runtime(singleton, started_at, heartbeat_at, worker_id, phase) VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET started_at = excluded.started_at, heartbeat_at = excluded.heartbeat_at, worker_id = excluded.worker_id, phase = excluded.phase, last_error = NULL
                """,
                (now, now, worker_id, phase),
            )
            conn.execute(
                "INSERT INTO collector_events(occurred_at, level, message) VALUES (?, ?, ?)",
                (now, "info", "采集守护已启动"),
            )

    def record_heartbeat(self, worker_id: str | None = None, phase: str = "waiting", error_summary: str | None = None) -> None:
        """刷新存活时间；不写事件，避免长期运行产生噪声。"""
        with closing(self._connect()) as conn, conn:
            conn.execute("UPDATE collector_runtime SET heartbeat_at=?,phase=?,last_error=? WHERE singleton=1 AND (? IS NULL OR worker_id=?)", (_now_iso(), phase, error_summary, worker_id, worker_id))

    def runtime(self) -> dict[str, Any]:
        """读取守护进程最近心跳；未启动时返回明确状态。"""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT started_at, heartbeat_at, worker_id, phase, last_error FROM collector_runtime WHERE singleton = 1").fetchone()
        return dict(row) if row is not None else {"status": "never"}

    def record_event(self, level: str, message: str, universe_id: str | None = None) -> None:
        """公开错误事件入口，让服务诊断可由配置页面读取。"""
        if level not in {"debug", "info", "warning", "error"}:
            raise ValueError("无效的事件级别")
        with closing(self._connect()) as conn, conn:
            self._event(conn, universe_id, level, message)

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

    def settings(self) -> dict[str, Any]:
        """读取跨进程持久设置，未知旧状态采用关闭默认值。"""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT enabled,hour,minute,revision FROM collector_settings WHERE singleton=1").fetchone()
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
        return result

    def update_settings(self, *, enabled: bool, hour: int, minute: int, revision: int) -> dict[str, Any]:
        """带修订号更新；关闭只取消自动任务。"""
        if not isinstance(enabled, bool) or type(hour) is not int or type(minute) is not int or not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("无效的自动采集设置")
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(
                "UPDATE collector_settings SET enabled=?,hour=?,minute=?,revision=revision+1 WHERE singleton=1 AND revision=?",
                (enabled, hour, minute, revision),
            )
            if not changed.rowcount:
                raise CollectorRevisionConflict("设置修订冲突，请重新加载")
            if not enabled:
                conn.execute("UPDATE collector_runs SET status='cancelled',phase='cancelled',updated_at=? WHERE source!='manual' AND status IN ('queued','retry_wait')", (_utc_iso(),))
                conn.execute("UPDATE collector_runs SET cancel_requested=1 WHERE source!='manual' AND status='running'")
            self._event(conn, None, "info", f"自动采集设置已保存，启用={enabled}")
        return self.settings()

    def promote_manual(self, run_id: str) -> dict[str, Any]:
        """人工请求接管已有任务；执行中的所有权保持不变。"""
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status FROM collector_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError(f"采集任务不存在：{run_id}")
            if row["status"] in {"queued", "retry_wait", "running"}:
                conn.execute("UPDATE collector_runs SET source='manual',cancel_requested=0,updated_at=? WHERE id=?", (_utc_iso(), run_id))
                if row["status"] == "retry_wait":
                    conn.execute("UPDATE collector_runs SET status='queued',phase='queued',available_at=? WHERE id=?", (_utc_iso(), run_id))
                self._event(conn, None, "info", f"采集任务已由人工请求接管：{run_id}")
        return self.get_run(run_id)

    def cancel_superseded(self, target_date: date | str) -> int:
        """恢复只补最新目标，取消过时自动排队，人工任务保持可执行。"""
        target = date.fromisoformat(target_date) if isinstance(target_date, str) else target_date
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute("UPDATE collector_runs SET status='cancelled',phase='cancelled',error_summary='最新目标交易日已取代旧计划',updated_at=? WHERE source!='manual' AND status IN ('queued','retry_wait') AND target_date<?", (_utc_iso(), target.isoformat()))
            if changed.rowcount:
                self._event(conn, None, "info", f"已取消 {changed.rowcount} 个过时自动采集任务，最新目标 {target}")
        return changed.rowcount

    def enqueue(self, universe_id: str, target_date: date | str, fingerprint: str, source: str,
                items: list[str], universe_version: int = 1) -> dict[str, Any]:
        """按池、日期与配置指纹幂等入队，终态自动任务不会重新创建。"""
        if source not in {"manual", "automatic", "recovery"} or not items or not fingerprint:
            raise ValueError("无效的采集任务")
        target = date.fromisoformat(target_date) if isinstance(target_date, str) else target_date
        now = _utc_iso()
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT id FROM collector_runs WHERE universe_id=? AND target_date=? AND fingerprint=?",
                                    (universe_id, target.isoformat(), fingerprint)).fetchone()
            if existing:
                run_id = existing["id"]
            else:
                run_id = uuid4().hex
                conn.execute("INSERT INTO collector_runs(id,universe_id,target_date,fingerprint,universe_version,source,status,phase,created_at,updated_at,available_at) VALUES (?,?,?,?,?,?,'queued','queued',?,?,?)",
                             (run_id, universe_id, target.isoformat(), fingerprint, universe_version, source, now, now, now))
                conn.executemany("INSERT INTO collector_items(run_id,symbol,status) VALUES (?,?,'pending')", [(run_id, symbol) for symbol in dict.fromkeys(items)])
                self._event(conn, universe_id, "info", f"采集任务已排队：{run_id}")
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        """读取任务和各标的结果。"""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM collector_runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError(f"采集任务不存在：{run_id}")
            result = dict(row)
            result["cancel_requested"] = bool(result["cancel_requested"])
            result["items"] = [dict(item) for item in conn.execute("SELECT symbol,status,error_summary FROM collector_items WHERE run_id=? ORDER BY rowid", (run_id,))]
        return result

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        """返回最新持久采集记录。"""
        with closing(self._connect()) as conn:
            ids = [row["id"] for row in conn.execute("SELECT id FROM collector_runs ORDER BY created_at DESC,rowid DESC LIMIT ?", (max(1, min(limit, 200)),))]
        return [self.get_run(run_id) for run_id in ids]

    def claim(self, worker_id: str, now: datetime | None = None, lease_seconds: int = 60) -> dict[str, Any] | None:
        """事务内认领一个任务；单队列同时只允许一个执行者。"""
        instant = _instant(now)
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM collector_runs WHERE status='running' LIMIT 1").fetchone():
                return None
            row = conn.execute("SELECT id FROM collector_runs WHERE status IN ('queued','retry_wait') AND available_at<=? ORDER BY created_at,rowid LIMIT 1", (instant.isoformat(),)).fetchone()
            if row is None:
                return None
            run_id = row["id"]
            conn.execute("UPDATE collector_runs SET status='running',phase='preparing',attempt=attempt+1,worker_id=?,lease_until=?,updated_at=? WHERE id=?",
                         (worker_id, (instant + timedelta(seconds=lease_seconds)).isoformat(), instant.isoformat(), run_id))
        return self.get_run(run_id)

    @staticmethod
    def _owned(conn: sqlite3.Connection, run_id: str, worker_id: str | None) -> None:
        row = conn.execute("SELECT status,worker_id FROM collector_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError(f"采集任务不存在：{run_id}")
        if worker_id is not None and (row["status"] != "running" or row["worker_id"] != worker_id):
            raise ValueError("采集任务租约已失效")

    def set_phase(self, run_id: str, phase: str, worker_id: str | None = None) -> None:
        """更新执行阶段；旧执行者不能覆盖已恢复任务。"""
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            self._owned(conn, run_id, worker_id)
            conn.execute("UPDATE collector_runs SET phase=?,updated_at=? WHERE id=?", (phase, _utc_iso(), run_id))

    def record_item(self, run_id: str, symbol: str, status: str, error_summary: str | None = None,
                    worker_id: str | None = None) -> None:
        """记录标的结果，重试不能清除已成功的数据状态。"""
        if status not in {"pending", "running", "completed", "failed"}:
            raise ValueError("无效的采集项状态")
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            self._owned(conn, run_id, worker_id)
            changed = conn.execute("UPDATE collector_items SET status=?,error_summary=? WHERE run_id=? AND symbol=? AND status!='completed'", (status, error_summary, run_id, symbol))
            if not changed.rowcount and not conn.execute("SELECT 1 FROM collector_items WHERE run_id=? AND symbol=?", (run_id, symbol)).fetchone():
                raise ValueError(f"采集项不存在：{symbol}")

    def invalidate_item(self, run_id: str, symbol: str, error: str, worker_id: str) -> None:
        """执行器验证缓存失败时显式降级，不能把已失效缓存继续标为成功。"""
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            self._owned(conn, run_id, worker_id)
            changed = conn.execute("UPDATE collector_items SET status='failed',error_summary=? WHERE run_id=? AND symbol=?", (error, run_id, symbol))
            if not changed.rowcount:
                raise ValueError(f"采集项不存在：{symbol}")
            self._event(conn, None, "warning", f"采集任务 {run_id} 的缓存项 {symbol} 已失效：{error}")

    def finish(self, run_id: str, status: str, error_summary: str | None = None,
               snapshot_run_id: str | None = None, worker_id: str | None = None) -> None:
        """收敛任务终态并保留审计。"""
        if status not in {"completed", "partial", "failed", "cancelled"}:
            raise ValueError("无效的采集结束状态")
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            self._owned(conn, run_id, worker_id)
            conn.execute("UPDATE collector_runs SET status=?,phase=?,error_summary=?,snapshot_run_id=?,worker_id=NULL,lease_until=NULL,updated_at=? WHERE id=?",
                         (status, status, error_summary, snapshot_run_id, _utc_iso(), run_id))
            universe_id = conn.execute("SELECT universe_id FROM collector_runs WHERE id=?", (run_id,)).fetchone()["universe_id"]
            if status != "cancelled":
                legacy_status = "completed" if status in {"completed", "partial"} and snapshot_run_id else "failed"
                conn.execute("INSERT INTO collector_status(universe_id,completed_at,status,run_id,error_summary) VALUES (?,?,?,?,?) ON CONFLICT(universe_id) DO UPDATE SET completed_at=excluded.completed_at,status=excluded.status,run_id=excluded.run_id,error_summary=excluded.error_summary",
                             (universe_id, _utc_iso(), legacy_status, snapshot_run_id if legacy_status == "completed" else None, error_summary if legacy_status == "failed" else None))
            self._event(conn, universe_id, "info" if status == "completed" else "warning", f"采集任务 {run_id}：{status}")

    def retry(self, run_id: str, manual: bool = True, now: datetime | None = None) -> dict[str, Any]:
        """仅重置失败项，自动重试最多三次，人工重试重新获得预算。"""
        instant = _instant(now)
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status,attempt,source FROM collector_runs WHERE id=?", (run_id,)).fetchone()
            if row is None or row["status"] not in {"failed", "partial", "cancelled"}:
                raise ValueError("任务当前不能重试")
            if not manual and row["source"] != "manual" and not conn.execute("SELECT enabled FROM collector_settings WHERE singleton=1").fetchone()["enabled"]:
                raise ValueError("自动采集已关闭，不能自动重试")
            if not manual and row["attempt"] >= 3:
                raise ValueError("自动重试次数已耗尽")
            delay = 0 if manual else (60 if row["attempt"] <= 1 else 300)
            conn.execute("UPDATE collector_runs SET status=?,phase='queued',attempt=?,source=?,available_at=?,updated_at=?,cancel_requested=0,error_summary=NULL WHERE id=?",
                         ("queued" if manual else "retry_wait", 0 if manual else row["attempt"], "manual" if manual else row["source"], (instant+timedelta(seconds=delay)).isoformat(), instant.isoformat(), run_id))
            conn.execute("UPDATE collector_items SET status='pending',error_summary=NULL WHERE run_id=? AND status!='completed'", (run_id,))
        return self.get_run(run_id)

    def recover_expired(self, now: datetime | None = None) -> int:
        """回收失去心跳的租约，成功项保持不变，耗尽预算后终止。"""
        instant = _instant(now)
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute("SELECT id,attempt,cancel_requested FROM collector_runs WHERE status='running' AND lease_until<=?", (instant.isoformat(),)).fetchall()
            for row in rows:
                status = "cancelled" if row["cancel_requested"] else ("failed" if row["attempt"] >= 3 else "retry_wait")
                delay = 60 if row["attempt"] <= 1 else 300
                conn.execute("UPDATE collector_runs SET status=?,phase=?,worker_id=NULL,lease_until=NULL,available_at=?,updated_at=?,error_summary='采集进程中断，租约已过期' WHERE id=?",
                             (status, status, (instant+timedelta(seconds=delay)).isoformat(), instant.isoformat(), row["id"]))
                conn.execute("UPDATE collector_items SET status='pending' WHERE run_id=? AND status='running'", (row["id"],))
                self._event(conn, None, "warning", f"采集任务租约回收：{row['id']}")
        return len(rows)

    def renew_lease(self, run_id: str, worker_id: str, now: datetime | None = None, lease_seconds: int = 60) -> None:
        """执行过程中延续任务租约。"""
        instant = _instant(now)
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            self._owned(conn, run_id, worker_id)
            conn.execute("UPDATE collector_runs SET lease_until=?,updated_at=? WHERE id=?", ((instant+timedelta(seconds=lease_seconds)).isoformat(), instant.isoformat(), run_id))

    @staticmethod
    def _event(conn: sqlite3.Connection, universe_id: str | None, level: str, message: str) -> None:
        conn.execute("INSERT INTO collector_events(occurred_at,universe_id,level,message) VALUES (?,?,?,?)", (_utc_iso(), universe_id, level, message))

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn


def _now_iso() -> str:
    """生成带本地时区的审计时间。"""
    return datetime.now().astimezone().isoformat()


def _instant(now: datetime | None = None) -> datetime:
    """统一为 UTC，拒绝不带时区的测试或调用时间。"""
    result = now if now is not None else datetime.now(UTC)
    if result.tzinfo is None:
        raise ValueError("采集时间必须带时区")
    return result.astimezone(UTC)


def _utc_iso() -> str:
    """生成可以按文本比较的统一时间。"""
    return _instant().isoformat()
