from apscheduler.triggers.cron import CronTrigger

from push.models import Subscription, SubscriptionSymbol
from push.scheduler import PushScheduler


class _Executor:
    def __init__(self):
        self.runs = []

    def run_subscription(self, sub):
        self.runs.append(sub.id)
        return {"total": 1, "ok": 1, "failures": []}


class _Store:
    def __init__(self, subs):
        self._subs = subs

    def list(self):
        return self._subs

    def get(self, sub_id):
        for s in self._subs:
            if s.id == sub_id:
                return s
        return None


class _Config:
    def __init__(self, enabled=True):
        self._enabled = enabled

    def get(self, key, default=None):
        if key == "push.enabled":
            return self._enabled
        return default


class _SchedulerStub:
    def __init__(self):
        self.jobs = []
        self.started = False
        self.paused = False

    @property
    def running(self):
        return self.started

    def start(self, paused=False):
        self.started = True
        self.paused = paused

    def resume(self):
        self.paused = False

    def remove_all_jobs(self):
        self.jobs = []

    def add_job(self, fn, trigger, id=None, replace_existing=False, kwargs=None):
        self.jobs.append({"id": id, "trigger": trigger, "kwargs": kwargs or {}})

    def shutdown(self, wait=False):
        self.started = False


class TestPushScheduler:
    def test_start_registers_jobs(self, mocker):
        subs = [Subscription(id=1, name="a", symbols=[SubscriptionSymbol(symbol="600519")], channel="email",
                             time="08:30", enabled=True)]
        stub = _SchedulerStub()
        mocker.patch("push.scheduler.BackgroundScheduler", return_value=stub)
        scheduler = PushScheduler(_Executor(), _Store(subs), _Config())
        scheduler.start()
        assert stub.started is True
        assert len(stub.jobs) == 1
        assert stub.jobs[0]["id"] == "sub-1"
        assert stub.jobs[0]["kwargs"] == {"sub_id": 1}
        # CronTrigger 不暴露 hour/minute 属性，按 fields 名称取值断言
        trigger = stub.jobs[0]["trigger"]
        assert isinstance(trigger, CronTrigger)
        fields = {f.name: str(f) for f in trigger.fields
                  if f.name in ("hour", "minute")}
        assert fields == {"hour": "8", "minute": "30"}

    def test_disabled_skips_start(self, mocker):
        scheduler = PushScheduler(_Executor(), _Store([]), _Config(enabled=False))
        scheduler.start()
        mocker.patch("push.scheduler.BackgroundScheduler").assert_not_called()

    def test_reload_after_enabled_toggle(self, mocker):
        subs = [Subscription(id=1, name="a", symbols=[SubscriptionSymbol(symbol="600519")], channel="email",
                             time="08:00", enabled=True),
                Subscription(id=2, name="b", symbols=[SubscriptionSymbol(symbol="000300")], channel="wecom",
                             time="09:00", enabled=False)]
        stub = _SchedulerStub()
        mocker.patch("push.scheduler.BackgroundScheduler", return_value=stub)
        scheduler = PushScheduler(_Executor(), _Store(subs), _Config())
        scheduler.start()
        assert [j["id"] for j in stub.jobs] == ["sub-1"]  # 禁用订阅不注册
        scheduler._store._subs[1].enabled = True
        scheduler.reload()
        assert [j["id"] for j in stub.jobs] == ["sub-1", "sub-2"]

    def test_run_invokes_executor(self, mocker):
        executor = _Executor()
        subs = [Subscription(id=1, name="a", symbols=[SubscriptionSymbol(symbol="600519")], channel="email",
                             time="08:00", enabled=True)]
        stub = _SchedulerStub()
        mocker.patch("push.scheduler.BackgroundScheduler", return_value=stub)
        scheduler = PushScheduler(executor, _Store(subs), _Config())
        scheduler.start()
        scheduler._run(1)
        assert executor.runs == [1]

    def test_old_and_new_scheduler_callbacks_keep_their_own_executor(self):
        """热切换后，滞后的旧 cron 回调不得转用新 executor。"""
        sub = Subscription(
            id=1, name="a", symbols=[SubscriptionSymbol(symbol="600519")],
            channel="email", time="08:00", enabled=True,
        )
        old_executor = _Executor()
        new_executor = _Executor()
        old_scheduler = PushScheduler(old_executor, _Store([sub]), _Config())
        new_scheduler = PushScheduler(new_executor, _Store([sub]), _Config())

        old_scheduler._run(1)
        new_scheduler._run(1)

        assert old_executor.runs == [1]
        assert new_executor.runs == [1]

    def test_shutdown_stops_scheduler(self, mocker):
        stub = _SchedulerStub()
        mocker.patch("push.scheduler.BackgroundScheduler", return_value=stub)
        scheduler = PushScheduler(_Executor(), _Store([]), _Config())
        scheduler.start()
        scheduler.shutdown()
        assert stub.started is False
