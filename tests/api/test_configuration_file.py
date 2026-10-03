"""完整配置编辑的草稿、安全和并发回归。"""
from fastapi.testclient import TestClient

from api.app import create_app
from utils.config import Config


def test_file_preview_preserves_unknown_fields_and_never_persists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = Config()
    client = TestClient(create_app(push=False), client=("127.0.0.1", 12345))
    original = client.get("/api/v1/config/file").json()
    source = original["source"] + "\ncustom:\n  value: keep\n"
    result = client.post("/api/v1/config/file/preview", json={"source": source})
    assert result.status_code == 200
    assert result.json()["source"] == source
    assert (config.config_dir / "config.yaml").read_text(encoding="utf-8") == original["source"]
    saved = client.put("/api/v1/config/file", json={"source": source, "revision": original["revision"]})
    assert saved.status_code == 200
    assert Config().get("custom.value") == "keep"
    assert client.put("/api/v1/config/file", json={"source": source, "revision": original["revision"]}).status_code == 409


def test_file_rejects_cross_origin_and_secret_in_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = TestClient(create_app(push=False), client=("127.0.0.1", 12345))
    assert client.get("/api/v1/config/file", headers={"origin": "https://evil.example"}).status_code == 403
    for source in ["llm: secret-private\n", "llm:\n  api_key: secret-private\n  api_key: duplicate\n", "custom: .nan\n", "- secret-private\n"]:
        response = client.post("/api/v1/config/file/preview", json={"source": source})
        assert response.status_code == 422
        assert "secret-private" not in response.text


def test_preview_does_not_create_config_and_reports_field_line(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = TestClient(create_app(push=False), client=("127.0.0.1", 12345))
    state = tmp_path / ".stock_robot"
    # 应用本身可能创建状态目录，预览不应额外创建配置文件。
    config_file = state / "config.yaml"
    before = config_file.read_bytes() if config_file.exists() else None
    response = client.post("/api/v1/config/file/preview", json={"source": "radar:\n  collector:\n    hour: 99\n"})
    assert response.status_code == 422
    assert response.json()["detail"]["line"] == 3
    assert (config_file.read_bytes() if config_file.exists() else None) == before


def test_known_hidden_fields_and_recursive_aliases_are_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = TestClient(create_app(push=False), client=("127.0.0.1", 12345))
    for source in ["backtest: null", "radar:\n  cost_profiles: null", "backtest:\n  initial_cash: bad", "custom: &loop\n  self: *loop", "custom: 2026-10-02"]:
        assert client.post("/api/v1/config/file/preview", json={"source": source}).status_code == 422


def test_save_response_revision_describes_its_own_snapshot(tmp_path, monkeypatch):
    from api.runtime import ReloadResult, RuntimeManager

    monkeypatch.chdir(tmp_path)

    class ConcurrentRuntime(RuntimeManager):
        def __init__(self):
            self.last_config = None

        def reload(self, config):
            Config(config_dir=config.config_dir).update({"llm": {"model": "other-window"}})
            return ReloadResult(applied=True)

    client = TestClient(create_app(push=False, runtime=ConcurrentRuntime()), client=("127.0.0.1", 12345))
    original = client.get("/api/v1/config/file").json()
    response = client.put("/api/v1/config", json={"config": {"llm": {"model": "this-window"}}, "revision": original["revision"]})
    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["config"]["llm"]["model"] == "this-window"
    assert snapshot["revision"] != client.get("/api/v1/config").json()["revision"]
    assert client.put("/api/v1/config", json={"config": {"llm": {"model": "stale"}}, "revision": snapshot["revision"]}).status_code == 409
