"""受控配置 API 的边界契约测试。"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.configuration import mask_secret
from utils.config import Config


@pytest.fixture
def config_client(tmp_path: Path, monkeypatch):
    """将配置固定到临时项目目录，避免测试读取真实用户配置。"""
    monkeypatch.chdir(tmp_path)
    config = Config()
    config.set("llm.api_key", "abcde12345vwxyz")
    config.set("push.email.smtp_password", "smtp-secret-xyz")
    config.set("push.wecom.secret", "wecom-secret-xyz")
    return config, TestClient(create_app(push=False))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("short", "*****"),
        ("12345678", "********"),
        ("123456789", "1234*6789"),
        ("12345678901", "1234***8901"),
        ("123456789012", "12345**89012"),
    ],
)
def test_mask_secret_preserves_only_the_contractually_allowed_characters(value, expected):
    """密钥掩码严格遵循短密钥全遮蔽、长密钥首尾保留规则。"""
    assert mask_secret(value) == expected


def test_get_config_only_returns_safe_whitelist_and_read_only_paths(config_client):
    """公开配置不泄露密钥，且只给出两个只读路径。"""
    config, client = config_client

    response = client.get("/api/v1/config")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"config", "paths"}
    assert payload["paths"] == {
        "state_dir": str(config.config_dir),
        "config_file": str(config.config_dir / "config.yaml"),
    }
    assert payload["config"]["llm"]["api_key"] == {
        "configured": True,
        "masked": "abcde*****vwxyz",
    }
    assert payload["config"]["push"]["email"]["smtp_password"] == {
        "configured": True,
        "masked": "smtp-*****t-xyz",
    }
    assert payload["config"]["push"]["wecom"]["secret"] == {
        "configured": True,
        "masked": "wecom******t-xyz",
    }
    assert "abcde12345vwxyz" not in str(payload)
    assert "smtp-secret-xyz" not in str(payload)
    assert "wecom-secret-xyz" not in str(payload)


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("llm.api_key", "abcde12345vwxyz"),
        ("push.email.smtp_password", "smtp-secret-xyz"),
        ("push.wecom.secret", "wecom-secret-xyz"),
    ],
)
def test_get_credential_only_allows_explicit_secret_keys(config_client, key, expected):
    """完整密钥读取只允许三个明确白名单键。"""
    _, client = config_client

    response = client.get(f"/api/v1/config/credentials/{key}")

    assert response.status_code == 200
    assert response.json() == {"value": expected}


def test_get_credential_rejects_unknown_key(config_client):
    """非白名单密钥不能通过凭据接口读取。"""
    _, client = config_client

    response = client.get("/api/v1/config/credentials/llm.model")

    assert response.status_code == 404


def test_update_deep_merges_only_submitted_values_and_persists_once(config_client, monkeypatch):
    """局部更新不重置同层字段，并在一次写盘后可被新实例读取。"""
    config, client = config_client
    persist_calls = 0
    original_persist = config._persist

    def count_persist():
        nonlocal persist_calls
        persist_calls += 1
        original_persist()

    monkeypatch.setattr(config, "_persist", count_persist)
    monkeypatch.setattr("api.configuration.Config", lambda: config)

    response = client.put("/api/v1/config", json={
        "config": {"llm": {"temperature": 0.8}},
    })

    assert response.status_code == 200
    assert response.json()["restart_required"] is False
    assert response.json()["config"]["llm"]["temperature"] == 0.8
    assert response.json()["config"]["llm"]["model"] == "gpt-4o"
    assert persist_calls == 1
    assert Config(config_dir=config.config_dir).get("llm.temperature") == 0.8


def test_config_update_deep_merges_and_persists_once(tmp_path: Path, monkeypatch):
    """Config.update 递归合并后只触发一次持久化。"""
    config = Config(config_dir=tmp_path)
    persist_calls = 0
    original_persist = config._persist

    def count_persist():
        nonlocal persist_calls
        persist_calls += 1
        original_persist()

    monkeypatch.setattr(config, "_persist", count_persist)

    config.update({"data": {"cache_ttl": {"daily": 12}}})

    assert config.get("data.cache_ttl.daily") == 12
    assert config.get("data.cache_ttl.news") == 21600
    assert persist_calls == 1


def test_empty_secret_does_not_overwrite_existing_value(config_client, monkeypatch):
    """编辑页提交空密钥表示保留旧值。"""
    config, client = config_client
    monkeypatch.setattr("api.configuration.Config", lambda: config)

    response = client.put("/api/v1/config", json={
        "config": {"llm": {"api_key": ""}},
    })

    assert response.status_code == 200
    assert config.get("llm.api_key") == "abcde12345vwxyz"
    assert response.json()["config"]["llm"]["api_key"]["masked"] == "abcde*****vwxyz"


@pytest.mark.parametrize("field", ["host", "port"])
def test_api_bind_change_requires_restart(config_client, monkeypatch, field):
    """监听地址或端口变更明确提示用户重启服务。"""
    config, client = config_client
    monkeypatch.setattr("api.configuration.Config", lambda: config)
    value = "0.0.0.0" if field == "host" else 3000

    response = client.put("/api/v1/config", json={"config": {"api": {field: value}}})

    assert response.status_code == 200
    assert response.json()["restart_required"] is True
    assert config.get(f"api.{field}") == value


@pytest.mark.parametrize(
    "body",
    [
        {"config": {"unknown": True}},
        {"config": {"llm": {"unknown": True}}},
        {"unexpected": True},
        {"config": {"push": {"email": {"unknown": True}}}},
    ],
)
def test_update_rejects_unknown_fields(config_client, body):
    """白名单以外字段不能被写入配置文件。"""
    _, client = config_client

    response = client.put("/api/v1/config", json=body)

    assert response.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"config": {"llm": {"provider": "gemini"}}},
        {"config": {"llm": {"model": "   "}}},
        {"config": {"llm": {"temperature": -0.01}}},
        {"config": {"llm": {"temperature": 2.01}}},
        {"config": {"llm": {"max_tokens": 0}}},
        {"config": {"llm": {"max_tokens": 128001}}},
        {"config": {"llm": {"retry_times": -1}}},
        {"config": {"llm": {"retry_times": 11}}},
        {"config": {"llm": {"timeout_seconds": 0}}},
        {"config": {"llm": {"timeout_seconds": 601}}},
        {"config": {"data": {"cache_ttl": {"daily": 0}}}},
        {"config": {"api": {"host": " "}}},
        {"config": {"api": {"port": 0}}},
        {"config": {"api": {"port": 65536}}},
        {"config": {"push": {"max_symbols_per_subscription": 0}}},
        {"config": {"push": {"email": {"smtp_host": " "}}}},
        {"config": {"push": {"email": {"smtp_port": 0}}}},
        {"config": {"push": {"email": {"smtp_user": " "}}}},
        {"config": {"push": {"email": {"to_addr": " "}}}},
        {"config": {"push": {"wecom": {"corp_id": " "}}}},
        {"config": {"push": {"wecom": {"agent_id": " "}}}},
        {"config": {"push": {"wecom": {"to_user": " "}}}},
        {"config": {"signal": {"thresholds": {"watch": 0}}}},
        {"config": {"signal": {"thresholds": {"watch": 7}}}},
        {"config": {"signal": {"thresholds": {"attack": 11}}}},
        {"config": {"signal": {"actions": {"attack": {"action": " "}}}}},
        {"config": {"signal": {"actions": {"watch": {"position": " "}}}}},
    ],
)
def test_update_rejects_invalid_config_boundaries(config_client, body):
    """所有受控字段在越界、空白或违反阈值关系时均被拒绝。"""
    _, client = config_client

    response = client.put("/api/v1/config", json=body)

    assert response.status_code == 422
