from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from api.app import create_app


class _PushStub:
    """替代 PushScheduler：记录 reload 调用，暴露 store/executor"""

    def __init__(self, store, executor):
        self.store = store
        self.executor = executor
        self.reload_calls = 0

    def reload(self):
        self.reload_calls += 1

    def start(self):
        pass

    def shutdown(self):
        pass


class _Executor:
    def __init__(self):
        self.ran = []

    def run_subscription(self, sub):
        self.ran.append(sub.id)
        return {"total": 1, "ok": 1, "failures": []}


def _make_app(tmp_path):
    from push.store import PushStore
    store = PushStore(tmp_path / "push.db")
    push = _PushStub(store, _Executor())
    core = MagicMock()
    core.pipeline = MagicMock()
    core.index_pipeline = MagicMock()
    return create_app(core=core, push=push), push


class TestSubscriptionsAPI:
    def test_list_empty(self, tmp_path):
        app, _ = _make_app(tmp_path)
        resp = TestClient(app).get("/api/v1/subscriptions")
        assert resp.status_code == 200
        assert resp.json()["subscriptions"] == []

    def test_create_and_list(self, tmp_path):
        app, push = _make_app(tmp_path)
        client = TestClient(app)
        resp = client.post("/api/v1/subscriptions", json={
            "name": "自选池", "symbols": ["600519", "000300"],
            "channel": "email", "time": "08:00",
        })
        assert resp.status_code == 200
        assert resp.json()["id"] == 1
        assert push.reload_calls == 1
        listed = client.get("/api/v1/subscriptions").json()["subscriptions"]
        assert len(listed) == 1
        assert listed[0]["name"] == "自选池"
        assert listed[0]["last_run"] is None

    def test_create_validation_errors(self, tmp_path):
        app, _ = _make_app(tmp_path)
        client = TestClient(app)
        resp = client.post("/api/v1/subscriptions", json={
            "name": "t", "symbols": [], "channel": "email", "time": "08:00",
        })
        assert resp.status_code == 422
        resp = client.post("/api/v1/subscriptions", json={
            "name": "t", "symbols": ["600519"], "channel": "sms", "time": "08:00",
        })
        assert resp.status_code == 422
        resp = client.post("/api/v1/subscriptions", json={
            "name": "t", "symbols": ["600519"], "channel": "email", "time": "8:00",
        })
        assert resp.status_code == 422

    def test_create_too_many_symbols(self, tmp_path):
        app, _ = _make_app(tmp_path)
        client = TestClient(app)
        resp = client.post("/api/v1/subscriptions", json={
            "name": "t", "symbols": [str(600000 + i) for i in range(21)],
            "channel": "email", "time": "08:00",
        })
        assert resp.status_code == 422

    def test_update_and_reload(self, tmp_path):
        app, push = _make_app(tmp_path)
        client = TestClient(app)
        sub_id = client.post("/api/v1/subscriptions", json={
            "name": "a", "symbols": ["600519"], "channel": "email", "time": "08:00",
        }).json()["id"]
        resp = client.put(f"/api/v1/subscriptions/{sub_id}", json={
            "name": "a2", "symbols": ["000001"], "channel": "wecom", "time": "09:30",
        })
        assert resp.status_code == 200
        assert resp.json()["channel"] == "wecom"
        assert push.reload_calls == 2
        got = client.get(f"/api/v1/subscriptions/{sub_id}").json()
        # symbols 为 SubscriptionSymbol dict 列表（字符串简写 → kind=auto）
        assert got["symbols"] == [{"symbol": "000001", "kind": "auto",
                                   "index_style": None}]

    def test_delete_and_reload(self, tmp_path):
        app, push = _make_app(tmp_path)
        client = TestClient(app)
        sub_id = client.post("/api/v1/subscriptions", json={
            "name": "a", "symbols": ["600519"], "channel": "email", "time": "08:00",
        }).json()["id"]
        resp = client.delete(f"/api/v1/subscriptions/{sub_id}")
        assert resp.status_code == 200
        assert push.reload_calls == 2
        assert client.get("/api/v1/subscriptions").json()["subscriptions"] == []

    def test_get_missing_404(self, tmp_path):
        app, _ = _make_app(tmp_path)
        resp = TestClient(app).get("/api/v1/subscriptions/999")
        assert resp.status_code == 404

    def test_manual_run_returns_triggered(self, tmp_path):
        app, push = _make_app(tmp_path)
        client = TestClient(app)
        sub_id = client.post("/api/v1/subscriptions", json={
            "name": "a", "symbols": ["600519"], "channel": "email", "time": "08:00",
        }).json()["id"]
        resp = client.post(f"/api/v1/subscriptions/{sub_id}/run")
        assert resp.status_code == 200
        assert resp.json()["status"] == "triggered"
        assert len(push.executor.ran) == 1
