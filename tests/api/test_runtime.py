from dataclasses import dataclass
from typing import cast

from api.bootstrap import AgentCore
from api.runtime import RuntimeManager
from push.executor import PushExecutor
from push.scheduler import PushScheduler
from utils.config import Config


@dataclass
class _FakeExecutor(PushExecutor):
    core: AgentCore | None
    store: object
    config: Config


class _FakeScheduler(PushScheduler):
    def __init__(self, executor: PushExecutor, store: object, config: Config):
        self.executor = executor
        self.store = store
        self.config = config
        self.start_calls = 0
        self.shutdown_calls = 0
        self.fail_start = False
        self.start_then_fail = False
        self.fail_shutdown_before_stop = False
        self.stop_then_fail_shutdown = False
        self.running = False
        self.active = False
        self.activate_calls = 0

    def start(self, *, paused: bool = False) -> None:
        self.start_calls += 1
        if self.start_then_fail:
            self.running = True
            self.active = not paused
            raise RuntimeError("调度器启动失败")
        if self.fail_start:
            raise RuntimeError("调度器启动失败")
        self.running = True
        self.active = not paused

    def activate(self) -> None:
        self.activate_calls += 1
        self.active = True

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        if self.fail_shutdown_before_stop:
            raise RuntimeError("调度器关闭失败")
        self.running = False
        self.active = False
        if self.stop_then_fail_shutdown:
            raise RuntimeError("调度器关闭失败")

    @property
    def is_running(self) -> bool:
        return self.running

    @property
    def is_active(self) -> bool:
        return self.active


class _FakeCore(AgentCore):
    """仅记录构建时配置，避免测试触发真实数据源与 LLM 初始化。"""

    def __init__(self, config: Config):
        self.config = config


class TestRuntimeManager:
    def test_reload_replaces_snapshot_then_starts_new_scheduler_and_stops_old(self, tmp_path):
        """替换顺序或生命周期遗漏会让新旧调度器状态断言失败。"""
        config = Config(config_dir=tmp_path)
        cores = [cast(AgentCore, object()), cast(AgentCore, object())]
        schedulers: list[_FakeScheduler] = []

        def core_factory(_config: Config) -> AgentCore:
            return cores.pop(0)

        def scheduler_factory(executor: PushExecutor, store: object, scheduler_config: Config) -> _FakeScheduler:
            scheduler = _FakeScheduler(executor, store, scheduler_config)
            schedulers.append(scheduler)
            return scheduler

        runtime = RuntimeManager(
            config,
            core_factory=core_factory,
            executor_factory=_FakeExecutor,
            scheduler_factory=scheduler_factory,
        )
        old_snapshot = runtime.snapshot()

        result = runtime.reload(config)
        current_snapshot = runtime.snapshot()

        assert result.applied is True
        assert current_snapshot is not old_snapshot
        assert current_snapshot.core is not old_snapshot.core
        assert schedulers[1].start_calls == 1
        assert schedulers[0].shutdown_calls == 1

    def test_reload_build_failure_keeps_original_snapshot(self, tmp_path):
        """候选构建失败时若提前替换，会丢失可用运行时。"""
        config = Config(config_dir=tmp_path)
        initial_core = cast(AgentCore, object())
        calls = 0

        def core_factory(_config: Config) -> AgentCore:
            nonlocal calls
            calls += 1
            if calls == 1:
                return initial_core
            raise RuntimeError("构建失败")

        runtime = RuntimeManager(
            config,
            core_factory=core_factory,
            executor_factory=_FakeExecutor,
            scheduler_factory=_FakeScheduler,
        )
        original_snapshot = runtime.snapshot()

        result = runtime.reload(config)

        assert result.applied is False
        assert result.error is not None
        assert runtime.snapshot() is original_snapshot
        assert runtime.snapshot().core is initial_core

    def test_held_snapshot_remains_unchanged_after_reload(self, tmp_path):
        """请求已持有的快照不得被 reload 原地修改。"""
        config = Config(config_dir=tmp_path)
        old_core = cast(AgentCore, object())
        new_core = cast(AgentCore, object())
        cores = [old_core, new_core]
        runtime = RuntimeManager(
            config,
            core_factory=lambda _config: cores.pop(0),
            executor_factory=_FakeExecutor,
            scheduler_factory=_FakeScheduler,
        )
        held_snapshot = runtime.snapshot()

        assert runtime.reload(config).applied is True

        assert held_snapshot.core is old_core
        assert held_snapshot.config is not config
        assert runtime.snapshot().core is new_core

    def test_reload_after_source_config_set_and_update_keeps_held_snapshot_isolated(self, tmp_path):
        """来源配置原地写入后，旧任务及其 core 仍读取构建时配置。"""
        source_config = Config(config_dir=tmp_path)
        runtime = RuntimeManager(
            source_config,
            core_factory=_FakeCore,
            executor_factory=_FakeExecutor,
            scheduler_factory=_FakeScheduler,
        )
        held_snapshot = runtime.snapshot()

        source_config.set("signal.thresholds.attack", 9)
        source_config.update({"llm": {"model": "snapshot-safe-model"}})
        assert runtime.reload(source_config).applied is True
        current_snapshot = runtime.snapshot()
        held_core = cast(_FakeCore, held_snapshot.core)
        current_core = cast(_FakeCore, current_snapshot.core)

        assert held_snapshot.config.get("signal.thresholds.attack") == 7
        assert held_core.config.get("signal.thresholds.attack") == 7
        assert held_snapshot.config.get("llm.model") == "gpt-4o"
        assert held_core.config.get("llm.model") == "gpt-4o"
        assert current_snapshot.config.get("signal.thresholds.attack") == 9
        assert current_core.config.get("signal.thresholds.attack") == 9
        assert current_snapshot.config.get("llm.model") == "snapshot-safe-model"
        assert current_core.config.get("llm.model") == "snapshot-safe-model"

    def test_reload_does_not_start_scheduler_when_push_disabled(self, tmp_path):
        """关闭推送时错误启动调度器会创建不应存在的后台任务。"""
        config = Config(config_dir=tmp_path)
        config.set("push.enabled", False)
        schedulers: list[_FakeScheduler] = []

        def scheduler_factory(executor: PushExecutor, store: object, scheduler_config: Config) -> _FakeScheduler:
            scheduler = _FakeScheduler(executor, store, scheduler_config)
            schedulers.append(scheduler)
            return scheduler

        runtime = RuntimeManager(
            config,
            core_factory=lambda _config: cast(AgentCore, object()),
            executor_factory=_FakeExecutor,
            scheduler_factory=scheduler_factory,
        )

        assert runtime.reload(config).applied is True
        assert [scheduler.start_calls for scheduler in schedulers] == [0, 0]

    def test_reload_start_failure_keeps_previous_snapshot_and_cleans_candidate(self, tmp_path):
        """候选调度器启动失败时必须恢复旧调度器且不提交候选。"""
        config = Config(config_dir=tmp_path)
        schedulers: list[_FakeScheduler] = []

        def scheduler_factory(executor: PushExecutor, store: object, scheduler_config: Config) -> _FakeScheduler:
            scheduler = _FakeScheduler(executor, store, scheduler_config)
            if schedulers:
                scheduler.fail_start = True
            schedulers.append(scheduler)
            return scheduler

        runtime = RuntimeManager(
            config,
            core_factory=lambda _config: cast(AgentCore, object()),
            executor_factory=_FakeExecutor,
            scheduler_factory=scheduler_factory,
        )
        original_snapshot = runtime.snapshot()

        result = runtime.reload(config)

        assert result.applied is False
        assert runtime.snapshot() is original_snapshot
        assert schedulers[0].shutdown_calls == 1
        assert schedulers[0].start_calls == 2
        assert schedulers[1].shutdown_calls == 1

    def test_reload_keeps_candidate_when_previous_stops_then_raises(self, tmp_path):
        """旧调度器停止后抛错时，快照必须保持指向仍在运行的候选调度器。"""
        config = Config(config_dir=tmp_path)
        schedulers: list[_FakeScheduler] = []

        def scheduler_factory(executor: PushExecutor, store: object, scheduler_config: Config) -> _FakeScheduler:
            scheduler = _FakeScheduler(executor, store, scheduler_config)
            if not schedulers:
                scheduler.stop_then_fail_shutdown = True
            schedulers.append(scheduler)
            return scheduler

        runtime = RuntimeManager(
            config,
            core_factory=lambda _config: cast(AgentCore, object()),
            executor_factory=_FakeExecutor,
            scheduler_factory=scheduler_factory,
        )
        original_snapshot = runtime.snapshot()

        result = runtime.reload(config)

        assert result.applied is True
        assert runtime.snapshot() is not original_snapshot
        assert schedulers[0].shutdown_calls == 1
        assert schedulers[0].running is False
        assert schedulers[1].start_calls == 1
        assert schedulers[1].running is True
        assert schedulers[1].active is True
        assert schedulers[1].shutdown_calls == 0

    def test_reload_rolls_back_when_previous_reports_still_running_on_shutdown_error(self, tmp_path):
        """旧调度器明确仍运行时，必须停掉候选并回滚快照以避免重复推送。"""
        config = Config(config_dir=tmp_path)
        schedulers: list[_FakeScheduler] = []

        def scheduler_factory(executor: PushExecutor, store: object, scheduler_config: Config) -> _FakeScheduler:
            scheduler = _FakeScheduler(executor, store, scheduler_config)
            if not schedulers:
                scheduler.fail_shutdown_before_stop = True
            schedulers.append(scheduler)
            return scheduler

        runtime = RuntimeManager(
            config,
            core_factory=lambda _config: cast(AgentCore, object()),
            executor_factory=_FakeExecutor,
            scheduler_factory=scheduler_factory,
        )
        original_snapshot = runtime.snapshot()

        result = runtime.reload(config)

        assert result.applied is False
        assert runtime.snapshot() is original_snapshot
        assert schedulers[0].running is True
        assert schedulers[1].shutdown_calls == 1
        assert schedulers[1].running is False

    def test_reload_start_cleanup_failure_keeps_previous_snapshot_and_reports_failure(self, tmp_path):
        """候选启动后清理失败时，不能把不确定运行态标为已应用。"""
        config = Config(config_dir=tmp_path)
        schedulers: list[_FakeScheduler] = []

        def scheduler_factory(executor: PushExecutor, store: object, scheduler_config: Config) -> _FakeScheduler:
            scheduler = _FakeScheduler(executor, store, scheduler_config)
            if schedulers:
                scheduler.start_then_fail = True
                scheduler.fail_shutdown_before_stop = True
            schedulers.append(scheduler)
            return scheduler

        runtime = RuntimeManager(
            config,
            core_factory=lambda _config: cast(AgentCore, object()),
            executor_factory=_FakeExecutor,
            scheduler_factory=scheduler_factory,
        )
        original_snapshot = runtime.snapshot()

        result = runtime.reload(config)

        assert result.applied is False
        assert result.error == "运行时调度器启动失败"
        assert runtime.snapshot() is original_snapshot
        assert schedulers[0].running is True
        assert schedulers[0].active is True
        assert schedulers[1].running is True
        assert schedulers[1].active is False
        assert schedulers[1].shutdown_calls == 1

    def test_reload_shutdown_cleanup_failure_keeps_previous_snapshot_and_reports_failure(self, tmp_path):
        """双调度器都无法确认清理时，必须保留旧快照并明确失败。"""
        config = Config(config_dir=tmp_path)
        schedulers: list[_FakeScheduler] = []

        def scheduler_factory(executor: PushExecutor, store: object, scheduler_config: Config) -> _FakeScheduler:
            scheduler = _FakeScheduler(executor, store, scheduler_config)
            scheduler.fail_shutdown_before_stop = True
            schedulers.append(scheduler)
            return scheduler

        runtime = RuntimeManager(
            config,
            core_factory=lambda _config: cast(AgentCore, object()),
            executor_factory=_FakeExecutor,
            scheduler_factory=scheduler_factory,
        )
        original_snapshot = runtime.snapshot()

        result = runtime.reload(config)

        assert result.applied is False
        assert result.error == "运行时调度器切换失败"
        assert runtime.snapshot() is original_snapshot
        assert schedulers[0].running is True
        assert schedulers[1].running is False
        assert schedulers[1].active is False
        assert schedulers[1].shutdown_calls == 1

    def test_reload_candidate_start_failure_reports_restore_failure_without_activating_candidate(self, tmp_path):
        """旧调度器恢复失败时也不能让未提交候选执行 cron。"""
        config = Config(config_dir=tmp_path)
        schedulers: list[_FakeScheduler] = []

        def scheduler_factory(executor: PushExecutor, store: object, scheduler_config: Config) -> _FakeScheduler:
            scheduler = _FakeScheduler(executor, store, scheduler_config)
            if schedulers:
                scheduler.fail_start = True
            schedulers.append(scheduler)
            return scheduler

        runtime = RuntimeManager(
            config,
            core_factory=lambda _config: cast(AgentCore, object()),
            executor_factory=_FakeExecutor,
            scheduler_factory=scheduler_factory,
        )
        original_snapshot = runtime.snapshot()
        schedulers[0].fail_start = True

        result = runtime.reload(config)

        assert result.applied is False
        assert result.error == "运行时调度器恢复失败"
        assert runtime.snapshot() is original_snapshot
        assert schedulers[0].active is False
        assert schedulers[1].active is False
