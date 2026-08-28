"""API 运行时快照的原子替换管理。"""
import copy
import logging
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock

from api.bootstrap import AgentCore, build_agent_core
from push.executor import PushExecutor
from push.scheduler import PushScheduler
from push.store import PushStore
from utils.config import Config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeSnapshot:
    """一次请求或推送任务可独占持有的运行时依赖。"""

    config: Config
    core: AgentCore | None
    push_store: PushStore | None
    push_executor: PushExecutor | None
    push_scheduler: PushScheduler | None


@dataclass(frozen=True)
class ReloadResult:
    """运行时替换结果。"""

    applied: bool
    error: str | None = None


class RuntimeManager:
    """在不中断已开始任务的前提下替换 API 运行时。"""

    def __init__(
        self,
        config: Config,
        *,
        core_factory: Callable[[Config], AgentCore | None] = build_agent_core,
        executor_factory: Callable[[AgentCore | None, PushStore, Config], PushExecutor] = PushExecutor,
        scheduler_factory: Callable[[PushExecutor, PushStore, Config], PushScheduler] = PushScheduler,
    ) -> None:
        self._lock = Lock()
        self._core_factory = core_factory
        self._executor_factory = executor_factory
        self._scheduler_factory = scheduler_factory
        self._snapshot = self._build_snapshot(config)
        self._start_scheduler(self._snapshot)

    def snapshot(self) -> RuntimeSnapshot:
        """读取当前快照；调用方在锁外继续使用返回对象。"""
        with self._lock:
            return self._snapshot

    def reload(self, config: Config) -> ReloadResult:
        """事务式替换运行时，未提交候选不会激活可执行 cron。"""
        try:
            candidate = self._build_snapshot(config)
        except Exception:  # noqa: BLE001 — 运行时依赖构建边界需保留旧快照
            logger.error("运行时快照重建失败，保留当前运行时快照")
            return ReloadResult(applied=False, error="运行时快照重建失败")

        previous = self.snapshot()
        try:
            self._shutdown_scheduler(previous)
        except Exception:
            if self._scheduler_is_running(previous):
                logger.exception("旧运行时调度器仍在运行，取消运行时切换")
                self._cleanup_scheduler(candidate)
                return ReloadResult(applied=False, error="运行时调度器切换失败")
            logger.exception("旧运行时调度器已停止但关闭异常，继续切换")

        try:
            self._start_scheduler(candidate, paused=True)
        except Exception:
            logger.exception("候选运行时调度器准备失败，恢复旧运行时")
            self._cleanup_scheduler(candidate)
            if not self._restore_scheduler(previous):
                return ReloadResult(applied=False, error="运行时调度器恢复失败")
            return ReloadResult(applied=False, error="运行时调度器启动失败")

        with self._lock:
            self._snapshot = candidate
        try:
            self._activate_scheduler(candidate)
        except Exception:
            logger.exception("候选运行时调度器激活失败，恢复旧运行时")
            self._cleanup_scheduler(candidate)
            with self._lock:
                if self._snapshot is candidate:
                    self._snapshot = previous
            if not self._restore_scheduler(previous):
                return ReloadResult(applied=False, error="运行时调度器恢复失败")
            return ReloadResult(applied=False, error="运行时调度器激活失败")

        return ReloadResult(applied=True)

    def _build_snapshot(self, config: Config) -> RuntimeSnapshot:
        """在锁外构建候选快照，避免阻塞正在读取快照的请求。"""
        snapshot_config = self._copy_config(config)
        core = self._core_factory(snapshot_config)
        push_store = PushStore(snapshot_config.config_dir / "push.db")
        push_executor = self._executor_factory(core, push_store, snapshot_config)
        push_scheduler = self._scheduler_factory(push_executor, push_store, snapshot_config)
        return RuntimeSnapshot(
            config=snapshot_config,
            core=core,
            push_store=push_store,
            push_executor=push_executor,
            push_scheduler=push_scheduler,
        )

    @staticmethod
    def _copy_config(config: Config) -> Config:
        """复制配置数据，防止后续来源配置原地更新影响已持有快照。"""
        snapshot_config = Config(config_dir=config.config_dir)
        snapshot_config.data = copy.deepcopy(config.data)
        return snapshot_config

    @staticmethod
    def _start_scheduler(snapshot: RuntimeSnapshot, *, paused: bool = False) -> None:
        """仅在推送启用时启动调度器；候选可先暂停以避免提前执行。"""
        if snapshot.push_scheduler is not None and snapshot.config.get("push.enabled", True):
            snapshot.push_scheduler.start(paused=paused)

    @staticmethod
    def _activate_scheduler(snapshot: RuntimeSnapshot) -> None:
        """提交候选快照后才激活其已暂停的 cron。"""
        if snapshot.push_scheduler is not None and snapshot.config.get("push.enabled", True):
            snapshot.push_scheduler.activate()

    @staticmethod
    def _shutdown_scheduler(snapshot: RuntimeSnapshot) -> None:
        """在替换后停止旧调度器；其实现以 wait=False 关闭后台调度。"""
        if snapshot.push_scheduler is not None:
            snapshot.push_scheduler.shutdown()

    @staticmethod
    def _scheduler_is_running(snapshot: RuntimeSnapshot) -> bool:
        """读取调度器公开运行状态；未知状态时不将其误判为仍在运行。"""
        scheduler = snapshot.push_scheduler
        return bool(getattr(scheduler, "is_running", False))

    @classmethod
    def _restore_scheduler(cls, snapshot: RuntimeSnapshot) -> bool:
        """候选失败后尽力恢复已停止的旧调度器。"""
        try:
            cls._start_scheduler(snapshot)
        except Exception:
            logger.exception("旧运行时调度器恢复失败")
            return False
        return True

    @classmethod
    def _cleanup_scheduler(cls, snapshot: RuntimeSnapshot) -> bool:
        """尽力回收未提交候选快照的调度器，清理失败不覆盖主错误。"""
        try:
            cls._shutdown_scheduler(snapshot)
        except Exception:
            logger.exception("候选运行时调度器清理失败")
            return False
        return True
