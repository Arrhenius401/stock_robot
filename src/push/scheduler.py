"""APScheduler 集成 — 每日定时触发订阅推送"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


class PushScheduler:
    """管理每日推送任务；订阅变更后调用 reload() 重注册"""

    def __init__(self, executor, store, config):
        self._executor = executor
        self._store = store
        self._config = config
        self._scheduler: BackgroundScheduler | None = None
        self._paused = False

    def start(self, *, paused: bool = False):
        if not self._config.get("push.enabled", True):
            logger.info("push.enabled=false，跳过推送调度")
            return
        if self.is_running:
            return
        scheduler = BackgroundScheduler()
        self._scheduler = scheduler
        self._paused = paused
        try:
            scheduler.start(paused=paused)
            self.reload()
        except Exception:
            self._scheduler = None
            self._paused = False
            try:
                scheduler.shutdown(wait=False)
            except Exception:
                logger.exception("推送调度器启动失败后的清理异常")
            raise

    @property
    def is_running(self) -> bool:
        """供运行时切换确认调度器是否仍持有后台任务。"""
        return bool(self._scheduler is not None and self._scheduler.running)

    @property
    def is_active(self) -> bool:
        """仅在未暂停时允许 cron 实际执行。"""
        return self.is_running and not self._paused

    def activate(self) -> None:
        """恢复候选调度器，使已注册 cron 可执行。"""
        if self._scheduler is not None and self._paused:
            self._scheduler.resume()
            self._paused = False

    def reload(self):
        """重读订阅并重新注册每日 cron 任务"""
        if self._scheduler is None:
            return
        self._scheduler.remove_all_jobs()
        enabled = 0
        for sub in self._store.list():
            if not sub.enabled or sub.id is None:
                continue
            hour, minute = sub.time.split(":")
            self._scheduler.add_job(
                self._run, CronTrigger(hour=int(hour), minute=int(minute)),
                id=f"sub-{sub.id}", replace_existing=True,
                kwargs={"sub_id": sub.id},
            )
            enabled += 1
        logger.info("推送调度已重载: %d 个启用订阅", enabled)

    def _run(self, sub_id: int):
        sub = self._store.get(sub_id)
        if sub is None:
            logger.warning("订阅 %s 不存在，跳过推送", sub_id)
            return
        logger.info("开始推送订阅 %s (%s)", sub.name, sub_id)
        try:
            result = self._executor.run_subscription(sub)
            logger.info("订阅 %s 推送完成: %d/%d 成功", sub.name,
                        result["ok"], result["total"])
        except Exception as e:  # noqa: BLE001 — 订阅级失败不影响调度器
            logger.error("订阅 %s 推送失败: %s", sub.name, e)

    def shutdown(self):
        if self._scheduler is not None:
            try:
                self._scheduler.shutdown(wait=False)
            finally:
                self._scheduler = None
                self._paused = False
