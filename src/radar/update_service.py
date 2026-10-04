"""手动与自动组合更新：持久入队、共享采集锁、分别收敛 ETF 和海外结果。"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from filelock import Timeout

from radar.collector_worker import CollectorWorker

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")


class UpdateCancelled(RuntimeError):
    """自动更新关闭后在分支边界停止，人工接管仍继续。"""


class UpdateStore:
    """组合任务与旧 ETF 队列共存，所有状态更新校验执行所有权。"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS radar_updates (
                    id TEXT PRIMARY KEY, universe_id TEXT NOT NULL, target_date TEXT,
                    source TEXT NOT NULL, schedule_key TEXT,
                    status TEXT NOT NULL, phase TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    worker_id TEXT, lease_until TEXT, attempt INTEGER NOT NULL DEFAULT 0,
                    etf TEXT, overseas TEXT, error TEXT, snapshot_run_id TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS radar_updates_active
                    ON radar_updates(universe_id,COALESCE(target_date,''))
                    WHERE status IN ('queued','running');
                CREATE UNIQUE INDEX IF NOT EXISTS radar_updates_schedule
                    ON radar_updates(universe_id,schedule_key) WHERE schedule_key IS NOT NULL;
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def enqueue(self, universe_id: str, target: date | None, *, source: str = "manual",
                schedule_key: str | None = None) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        target_text = target.isoformat() if target else None
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT id FROM radar_updates WHERE universe_id=? AND COALESCE(target_date,'')=COALESCE(?,'') AND status IN ('queued','running')",
                               (universe_id, target_text)).fetchone()
            if row is None and schedule_key:
                row = conn.execute("SELECT id FROM radar_updates WHERE universe_id=? AND schedule_key=?", (universe_id, schedule_key)).fetchone()
            if row is not None:
                task_id = row["id"]
                # 人工接管不会改变运行中任务的租约，也不改变自动开关。
                if source == "manual":
                    conn.execute("UPDATE radar_updates SET source='manual' WHERE id=? AND status IN ('queued','running')", (task_id,))
            else:
                task_id = uuid.uuid4().hex
                conn.execute("INSERT INTO radar_updates(id,universe_id,target_date,source,schedule_key,status,phase,created_at,updated_at) VALUES(?,?,?,?,?,'queued','calendar',?,?)",
                             (task_id, universe_id, target_text, source, schedule_key, now, now))
        return self.get(task_id)

    def get(self, task_id: str) -> dict[str, Any]:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM radar_updates WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise ValueError("组合更新任务不存在")
        result = dict(row)
        for key in ("etf", "overseas"):
            result[key] = json.loads(result[key]) if result[key] else None
        result.update(task_id=result["id"], run_id=result["snapshot_run_id"])
        return result

    def latest(self, universe_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT id FROM radar_updates WHERE universe_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (universe_id,)).fetchone()
        return self.get(row["id"]) if row else None

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT id FROM radar_updates ORDER BY created_at DESC,rowid DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
        return [self.get(row["id"]) for row in rows]

    def claim(self, worker_id: str, now: datetime | None = None, *, manual_only: bool = False) -> dict[str, Any] | None:
        instant = now or datetime.now(UTC)
        if instant.tzinfo is None:
            raise ValueError("执行时间必须带时区")
        instant = instant.astimezone(UTC)
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM radar_updates WHERE status='running'").fetchone():
                return None
            row = conn.execute("SELECT id FROM radar_updates WHERE status='queued' AND (?=0 OR source='manual') ORDER BY created_at,rowid LIMIT 1", (int(manual_only),)).fetchone()
            if row is None:
                return None
            conn.execute("UPDATE radar_updates SET status='running',phase='calendar',worker_id=?,lease_until=?,updated_at=?,attempt=attempt+1 WHERE id=?",
                         (worker_id, (instant + timedelta(seconds=90)).isoformat(), instant.isoformat(), row["id"]))
        return self.get(row["id"])

    def update(self, task_id: str, worker_id: str, **fields: Any) -> None:
        allowed = {"phase", "status", "etf", "overseas", "error", "snapshot_run_id"}
        if set(fields) - allowed:
            raise ValueError("不支持的任务字段")
        instant = datetime.now(UTC)
        values = {key: json.dumps(value, ensure_ascii=False) if key in {"etf", "overseas"} else value for key, value in fields.items()}
        values["updated_at"] = instant.isoformat()
        values["lease_until"] = (instant + timedelta(seconds=90)).isoformat()
        with closing(self._connect()) as conn, conn:
            columns = ",".join(key + "=?" for key in values)
            changed = conn.execute(f"UPDATE radar_updates SET {columns} WHERE id=? AND worker_id=? AND status='running'", (*values.values(), task_id, worker_id))
            if changed.rowcount != 1:
                raise ValueError("组合更新租约已失效")

    def recover_expired(self, now: datetime | None = None) -> int:
        instant = (now or datetime.now(UTC)).astimezone(UTC)
        with closing(self._connect()) as conn, conn:
            changed = conn.execute("UPDATE radar_updates SET status=CASE WHEN attempt>=3 THEN 'failed' ELSE 'queued' END,phase=CASE WHEN attempt>=3 THEN 'failed' ELSE 'calendar' END,worker_id=NULL,lease_until=NULL,error='执行进程中断，租约已过期',updated_at=? WHERE status='running' AND lease_until<=?", (instant.isoformat(), instant.isoformat()))
        return changed.rowcount

    def cancel_automatic(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("UPDATE radar_updates SET status='cancelled',phase='cancelled',error='自动采集已关闭',updated_at=? WHERE source='automatic' AND status='queued'", (datetime.now(UTC).isoformat(),))


class UpdateService:
    """Web 只执行人工任务，独立服务可执行组合自动任务；线程不冒充服务心跳。"""

    def __init__(self, worker: CollectorWorker, snapshots: Any, overseas: Any, *, manual_only: bool = True):
        self.worker = worker
        self.snapshots = snapshots
        self.overseas = overseas
        self.store = UpdateStore(worker.store.db_path)
        self.manual_only = manual_only
        self.worker.write_runtime = not manual_only
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def enqueue(self, universe_id: str, target: date | None = None) -> dict[str, Any]:
        check_date = target or datetime.now(SHANGHAI).date()
        universe = self.worker.repository.active_on(universe_id, check_date)
        if not universe.enabled:
            raise ValueError("标的池已关闭")
        if target and target > datetime.now(SHANGHAI).date():
            raise ValueError("目标日期不能是未来日期")
        result = self.store.enqueue(universe_id, target)
        etf = result.get("etf") or {}
        if etf.get("task_id"):
            child = self.worker.store.get_run(etf["task_id"])
            if child["status"] in {"cancelled", "failed", "partial"}:
                self.worker.store.retry(child["id"], manual=True)
            else:
                self.worker.store.promote_manual(child["id"])
        self._wake.set()
        return result

    def schedule(self, now: datetime) -> None:
        settings = self.worker.store.settings()
        local = now.astimezone(SHANGHAI)
        if not settings["enabled"] or (local.hour, local.minute) < (settings["hour"], settings["minute"]):
            return
        # 每天检查海外实际收盘记录，国内节假日不阻止海外采集。
        for universe in self.worker.repository.enabled_on(local.date()):
            self.store.enqueue(universe.id, None, source="automatic", schedule_key=local.date().isoformat())

    def execute_one(self) -> bool:
        try:
            # 与旧独立采集共用同一文件锁；嵌套ETF执行使用同一可重入锁实例。
            with self.worker.execution_lock.acquire(timeout=0):
                self.worker.store.recover_expired()
                if not self.worker.store.settings()["enabled"]:
                    self.store.cancel_automatic()
                self.store.recover_expired()
                task = self.store.claim(self.worker.worker_id, manual_only=self.manual_only)
                if task is None:
                    return False
                self._execute(task)
                return True
        except Timeout:
            return False

    def _execute(self, task: dict[str, Any]) -> None:
        task_id = task["id"]
        owner = self.worker.worker_id
        done = threading.Event()

        def heartbeat() -> None:
            while not done.wait(15):
                try:
                    self.store.update(task_id, owner)
                    if not self.manual_only:
                        current = self.store.get(task_id)
                        self.worker.store.record_heartbeat(worker_id=owner, phase=current["phase"])
                except (sqlite3.Error, ValueError) as exc:
                    logger.warning("组合任务续租失败：%s", exc)
                    return

        pulse = threading.Thread(target=heartbeat, daemon=True, name="radar-update-lease")
        pulse.start()
        etf: dict[str, Any] = {"status": "failed", "error": None, "snapshot_run_id": None}
        market: dict[str, Any] = {"items": []}

        def check_cancelled() -> None:
            current = self.store.get(task_id)
            if current["source"] == "automatic" and not self.worker.store.settings()["enabled"]:
                raise UpdateCancelled("自动采集已关闭，组合更新在安全边界停止")

        try:
            check_cancelled()
            try:
                self.store.update(task_id, owner, phase="calendar")
                now = datetime.now(SHANGHAI)
                target = date.fromisoformat(task["target_date"]) if task["target_date"] else None
                if self.store.get(task_id)["source"] == "manual":
                    run = self.worker.enqueue_manual(now, task["universe_id"], target)[0]
                else:
                    run = self.worker.enqueue_automatic(now, task["universe_id"])[0]
                self.store.update(task_id, owner, phase="etf", etf={"status": run["status"], "task_id": run["id"], "error": run["error_summary"], "snapshot_run_id": run["snapshot_run_id"]})
                if self.store.get(task_id)["source"] == "manual":
                    run = self.worker.store.promote_manual(run["id"])
                if run["status"] in {"queued", "retry_wait"}:
                    self.worker.execute_one(run_id=run["id"])
                run = self.worker.store.get_run(run["id"])
                while run["status"] in {"queued", "retry_wait"}:
                    check_cancelled()
                    self.store.update(task_id, owner, phase="etf", etf={"status": run["status"], "task_id": run["id"], "error": run["error_summary"], "snapshot_run_id": run["snapshot_run_id"]})
                    remaining = max(0, (datetime.fromisoformat(run["available_at"]) - datetime.now(UTC)).total_seconds())
                    if self._stop.wait(min(remaining, 2)):
                        raise RuntimeError("Web服务正在关闭，尚未执行的失败项可重新更新")
                    if remaining > 2:
                        continue
                    self.worker.execute_one(run_id=run["id"])
                    run = self.worker.store.get_run(run["id"])
                etf = {"status": run["status"], "error": run["error_summary"], "snapshot_run_id": run["snapshot_run_id"], "task_id": run["id"], "target_date": run["target_date"]}
            except UpdateCancelled:
                raise
            except Exception as exc:  # noqa: BLE001, RUF100 — ETF分支隔离，保留诊断
                logger.exception("组合任务 ETF 分支失败：%s", task_id)
                etf["error"] = str(exc)
            snapshot = self.snapshots.get_snapshot(etf["snapshot_run_id"]) if etf.get("snapshot_run_id") else None
            if snapshot is None:
                snapshot = self.snapshots.latest_completed(task["universe_id"])
            snapshot_id = snapshot["run_id"] if snapshot else None
            self.store.update(task_id, owner, phase="overseas", etf=etf, snapshot_run_id=snapshot_id)
            check_cancelled()
            try:
                universe = self.worker.repository.active_on(task["universe_id"], datetime.now(SHANGHAI).date())
                market = self.overseas.refresh(universe, snapshot, datetime.now(SHANGHAI))
            except Exception as exc:
                logger.exception("组合任务海外分支失败：%s", task_id)
                market = {"items": [], "error": str(exc)}
            self.store.update(task_id, owner, phase="finalizing", overseas=market)
            etf_ok = etf["status"] == "completed"
            overseas_ok = not market.get("error") and all(item.get("status") == "fresh" and not item.get("error") for item in market.get("items", []))
            usable = snapshot is not None or any(item.get("as_of_date") for item in market.get("items", []))
            status = "completed" if etf_ok and overseas_ok else "partial" if usable else "failed"
            errors = [value for value in (etf.get("error"), market.get("error")) if value]
            if not overseas_ok and not market.get("error"):
                errors.append("部分海外指数数据不可用或沿用缓存，请查看逐项原因")
            if not etf_ok and not etf.get("error"):
                errors.append("ETF 分支尚未完整完成，请查看更新结果")
            self.store.update(task_id, owner, status=status, phase=status, error="；".join(errors) or None)
        except UpdateCancelled as exc:
            self.store.update(task_id, owner, status="cancelled", phase="cancelled", error=str(exc), etf=etf, overseas=market)
        except Exception as exc:  # noqa: BLE001, RUF100 — 任务隔离边界必须保留错误
            logger.exception("组合任务失败：%s", task_id)
            try:
                self.store.update(task_id, owner, status="failed", phase="failed", error=str(exc), etf=etf, overseas=market)
            except (sqlite3.Error, ValueError) as store_error:
                logger.error("组合任务无法收敛，等待租约恢复：%s", store_error)
        finally:
            done.set()
            pulse.join(timeout=1)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._serve, daemon=True, name="radar-manual-updates")
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                if not self.execute_one():
                    # 兼容旧人工失败项的有限自动重试，不认领自动ETF任务。
                    self.worker.execute_one(manual_only=self.manual_only)
            except Exception:  # noqa: BLE001, RUF100 — 后台队列隔离，异常必须进入日志
                logger.exception("配置雷达后台队列异常")
            self._wake.wait(2)
            self._wake.clear()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        # 不强制取消网络中的任务；仍在运行的线程保留锁与续租直至安全结束。
