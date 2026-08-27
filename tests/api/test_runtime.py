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

    def start(self) -> None:
        self.start_calls += 1

    def shutdown(self) -> None:
        self.shutdown_calls += 1


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
        assert held_snapshot.config is config
        assert runtime.snapshot().core is new_core

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
