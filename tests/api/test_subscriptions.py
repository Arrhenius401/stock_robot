import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.runtime import RuntimeManager
from push.models import Subscription, SubscriptionSymbol


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

    def run_subscription(self, sub, run_id=None):
        self.ran.append(sub.id)
        return {"total": 1, "ok": 1, "failures": []}


class _BlockingExecutor(_Executor):
    """让测试可在第一个后台推送执行期间切换运行时快照。"""

    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def run_subscription(self, sub, run_id=None):
        self.ran.append(sub.id)
        self.started.set()
        self.release.wait(timeout=2)
        return {"total": 1, "ok": 1, "failures": []}


@pytest.fixture(autouse=True)
def _fake_stock_names(mocker):
    """订阅保存需解析名称；测试不用外部行情接口。"""
    import pandas as pd
    mocker.patch("utils.symbols._ak_code_name", return_value=pd.DataFrame([
        {"code": "600519", "name": "贵州茅台"},
        {"code": "000001", "name": "平安银行"},
    ]))


def _make_app(tmp_path):
    from push.store import PushStore
    store = PushStore(tmp_path / "push.db")
    push = _PushStub(store, _Executor())
    core = MagicMock()
    core.pipeline = MagicMock()
    core.index_pipeline = MagicMock()
    return create_app(core=core, push=push), push


class TestSubscriptionsAPI:
    def test_symbol_search_contract_and_limit(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        state_dir = tmp_path / ".stock_robot"
        state_dir.mkdir()
        (state_dir / "push_stock_names.json").write_text(json.dumps({
            "fetched_at": time.time(),
            "stocks": {"000001": "平安银行", "600519": "贵州茅台"},
        }), encoding="utf-8")
        client = TestClient(create_app(push=False))
        response = client.get("/api/v1/push/symbols", params={"q": "000001", "limit": 8})
        assert response.status_code == 200
        assert response.json()["stocks_available"] is True
        candidates = response.json()["candidates"]
        assert {(item["symbol"], item["kind"]) for item in candidates} == {
            ("000001", "stock"), ("000001", "index"),
        }
        assert all(set(item) == {"symbol", "display_name", "kind", "index_style", "market"}
                   for item in candidates)
        assert client.get("/api/v1/push/symbols", params={"q": ""}).json() == {
            "candidates": [], "stocks_available": False,
        }
        assert client.get("/api/v1/push/symbols", params={"q": "x", "limit": 0}).status_code == 422
        assert client.get("/api/v1/push/symbols", params={"q": "x", "limit": 21}).status_code == 422

    @pytest.mark.parametrize("override", [
        {"name": "  "}, {"name": 123}, {"enabled": "false"},
        {"symbols": "600519"}, {"symbols": {"symbol": "600519"}},
        {"symbols": [123]}, {"symbols": [{"symbol": 600519}]},
    ])
    def test_subscription_input_rejects_invalid_types(self, tmp_path, override):
        app, _ = _make_app(tmp_path)
        body = {"name": "a", "symbols": ["600519"], "time": "08:00", **override}
        response = TestClient(app).post("/api/v1/subscriptions", json=body)
        assert response.status_code == 422

    def test_email_trial_missing_and_failed_credentials_are_safe(self, tmp_path, mocker):
        from push.store import PushStore
        from utils.config import Config

        config = Config(config_dir=tmp_path / "config")
        store = PushStore(tmp_path / "push.db")

        class Runtime(RuntimeManager):
            def __init__(self):
                pass

            def snapshot(self):
                return SimpleNamespace(config=config, push_store=store,
                                       push_executor=None, push_scheduler=None)

        client = TestClient(create_app(core=MagicMock(), push=False, runtime=Runtime()))
        assert client.post("/api/v1/push/email/test").status_code == 422
        for field, value in {"smtp_host": "smtp.example.com", "smtp_user": "a@example.com",
                             "smtp_password": "secret-123", "to_addr": "b@example.com"}.items():
            config.set(f"push.email.{field}", value)
        backend = mocker.patch("api.app.get_backend")
        backend.return_value.send.side_effect = RuntimeError("secret-123 auth failed")
        response = client.post("/api/v1/push/email/test")
        assert response.status_code == 502
        assert "secret-123" not in response.text

    def test_active_run_conflict_and_detail(self, tmp_path):
        from push.store import PushStore

        store = PushStore(tmp_path / "push.db")
        sub_id = store.create(Subscription(name="a", symbols=["600519"], channel="email", time="08:00"))  # pyright: ignore[reportArgumentType]
        executor = _BlockingExecutor()
        app = create_app(core=MagicMock(), push=_PushStub(store, executor))
        client = TestClient(app)
        first = client.post(f"/api/v1/subscriptions/{sub_id}/run")
        assert first.status_code == 202
        assert executor.started.wait(timeout=1)
        assert client.post(f"/api/v1/subscriptions/{sub_id}/run").status_code == 409
        assert client.delete(f"/api/v1/subscriptions/{sub_id}").status_code == 409
        run_id = first.json()["run_id"]
        detail = client.get(f"/api/v1/subscriptions/{sub_id}/runs/{run_id}").json()
        assert detail["status"] == "queued"
        assert detail["subscription_snapshot"]["name"] == "a"
        assert client.get(f"/api/v1/subscriptions/{sub_id}/runs").json()["runs"][0]["id"] == run_id
        executor.release.set()

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

    def test_create_honors_runtime_symbol_limit(self, tmp_path):
        """运行时快照中的上限应覆盖默认值。"""
        from push.store import PushStore
        from utils.config import Config

        config = Config(config_dir=tmp_path / "config")
        config.set("push.max_symbols_per_subscription", 1)
        store = PushStore(tmp_path / "push_limit.db")
        push = _PushStub(store, _Executor())

        class SnapshotRuntime(RuntimeManager):
            def __init__(self):
                pass

            def snapshot(self):
                return SimpleNamespace(
                    config=config,
                    push_store=store,
                    push_executor=push.executor,
                    push_scheduler=push,
                )

        app = create_app(core=MagicMock(), push=False, runtime=SnapshotRuntime())
        response = TestClient(app).post("/api/v1/subscriptions", json={
            "name": "限额", "symbols": ["600519", "000300"],
            "channel": "email", "time": "08:00",
        })

        assert response.status_code == 422

    def test_update_and_reload(self, tmp_path):
        app, push = _make_app(tmp_path)
        client = TestClient(app)
        sub_id = client.post("/api/v1/subscriptions", json={
            "name": "a", "symbols": ["600519"], "channel": "email", "time": "08:00",
        }).json()["id"]
        resp = client.put(f"/api/v1/subscriptions/{sub_id}", json={
            "name": "a2", "symbols": ["000001"], "channel": "email", "time": "09:30",
        })
        assert resp.status_code == 200
        assert resp.json()["channel"] == "email"
        assert push.reload_calls == 2
        got = client.get(f"/api/v1/subscriptions/{sub_id}").json()
        assert got["symbols"] == [{"symbol": "000001", "kind": "index",
                                   "index_style": "broad", "display_name": "上证指数"}]

    def test_overseas_index_style_is_saved_from_mapping(self, tmp_path):
        app, _ = _make_app(tmp_path)
        response = TestClient(app).post("/api/v1/subscriptions", json={
            "name": "海外指数", "symbols": [{"symbol": "HSI", "kind": "index"}],
            "channel": "email", "time": "08:00",
        })
        assert response.status_code == 200
        assert response.json()["symbols"] == [{
            "symbol": "HSI", "kind": "index", "index_style": "overseas",
            "display_name": "恒生指数",
        }]

    def test_update_toggles_enabled(self, tmp_path):
        app, _ = _make_app(tmp_path)
        client = TestClient(app)
        sub_id = client.post("/api/v1/subscriptions", json={
            "name": "a", "symbols": ["600519"], "channel": "email", "time": "08:00",
        }).json()["id"]
        resp = client.put(f"/api/v1/subscriptions/{sub_id}", json={
            "name": "a", "symbols": ["600519"], "channel": "email",
            "time": "08:00", "enabled": False,
        })
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        assert client.get(f"/api/v1/subscriptions/{sub_id}").json()["enabled"] is False
        # 全量替换语义：PUT 缺省 enabled 视为启用
        resp = client.put(f"/api/v1/subscriptions/{sub_id}", json={
            "name": "a", "symbols": ["600519"], "channel": "email", "time": "08:00",
        })
        assert resp.json()["enabled"] is True

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

    def test_manual_run_returns_run_id(self, tmp_path):
        app, push = _make_app(tmp_path)
        client = TestClient(app)
        sub_id = client.post("/api/v1/subscriptions", json={
            "name": "a", "symbols": ["600519"], "channel": "email", "time": "08:00",
        }).json()["id"]
        resp = client.post(f"/api/v1/subscriptions/{sub_id}/run")
        assert resp.status_code == 202
        assert resp.json()["run_id"] > 0
        assert len(push.executor.ran) == 1

    def test_manual_run_keeps_executor_snapshot_after_runtime_reload(self, tmp_path):
        """已启动的手动推送继续使用旧 executor，下一次才使用新 executor。"""
        from push.store import PushStore

        store = PushStore(tmp_path / "push_snapshot.db")
        sub_id = store.create(Subscription(
            name="a", symbols=[SubscriptionSymbol(symbol="600519")],
            channel="email", time="08:00"))
        old_executor = _BlockingExecutor()
        new_executor = _Executor()

        class SnapshotRuntime(RuntimeManager):
            def __init__(self):
                self.current = SimpleNamespace(
                    push_store=store, push_executor=old_executor,
                    push_scheduler=SimpleNamespace(reload=lambda: None),
                )
                self.calls = 0

            def snapshot(self):
                self.calls += 1
                return self.current

        runtime = SnapshotRuntime()
        app = create_app(core=MagicMock(), push=False, runtime=runtime)
        client = TestClient(app)

        first = client.post(f"/api/v1/subscriptions/{sub_id}/run")
        assert first.status_code == 202
        assert old_executor.started.wait(timeout=1)
        runtime.current = SimpleNamespace(
            push_store=store, push_executor=new_executor,
            push_scheduler=SimpleNamespace(reload=lambda: None),
        )
        old_executor.release.set()

        for _ in range(50):
            run = store.get_run(first.json()["run_id"])
            assert run is not None
            if run["status"] not in {"queued", "running"}:
                break
            threading.Event().wait(0.01)

        second = client.post(f"/api/v1/subscriptions/{sub_id}/run")
        assert second.status_code == 202
        for _ in range(20):
            if new_executor.ran:
                break
            threading.Event().wait(0.01)

        assert old_executor.ran == [sub_id]
        assert new_executor.ran == [sub_id]
        assert runtime.calls == 2
