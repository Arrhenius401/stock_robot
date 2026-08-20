from push.models import Subscription
from push.store import PushStore


def _sub(**kw):
    base = dict(name="自选池", symbols=["600519", "000300"],
                channel="email", time="08:00", created_at="2026-08-20T08:00:00+08:00")
    base.update(kw)
    return Subscription(**base)


class TestPushStore:
    def test_create_and_get(self, tmp_path):
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(_sub())
        got = store.get(sub_id)
        assert got is not None
        assert got.name == "自选池"
        assert got.symbols == ["600519", "000300"]
        assert got.channel == "email"

    def test_list_returns_all(self, tmp_path):
        store = PushStore(tmp_path / "push.db")
        store.create(_sub(name="a"))
        store.create(_sub(name="b", channel="wecom"))
        assert [s.name for s in store.list()] == ["a", "b"]

    def test_update(self, tmp_path):
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(_sub())
        sub = store.get(sub_id)
        sub.enabled = False
        sub.symbols = ["000001"]
        assert store.update(sub) is True
        got = store.get(sub_id)
        assert got.enabled is False
        assert got.symbols == ["000001"]

    def test_update_missing_returns_false(self, tmp_path):
        store = PushStore(tmp_path / "push.db")
        assert store.update(_sub(id=999)) is False

    def test_delete(self, tmp_path):
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(_sub())
        assert store.delete(sub_id) is True
        assert store.get(sub_id) is None
        assert store.delete(sub_id) is False

    def test_record_and_list_runs(self, tmp_path):
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(_sub())
        run_id = store.record_run(sub_id, total=2, ok=1, failures=["600519: 超时"])
        assert run_id > 0
        runs = store.list_runs(sub_id)
        assert len(runs) == 1
        assert runs[0]["ok"] == 1
        assert runs[0]["failures"] == ["600519: 超时"]

    def test_last_run_none_when_empty(self, tmp_path):
        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(_sub())
        assert store.last_run(sub_id) is None
