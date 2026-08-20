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

    def start(self):
        if not self._config.get("push.enabled", True):
            logger.info("push.enabled=false，跳过推送调度")
            return
        if self._scheduler is None:
            self._scheduler = BackgroundScheduler()
            self._scheduler.start()
            self.reload()

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
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
