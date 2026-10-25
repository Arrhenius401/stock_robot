"""配置写入的 Windows 原子替换重试。"""
import os

from utils.config import Config


def test_config_save_retries_transient_permission_error(tmp_path, monkeypatch):
    config = Config(tmp_path / ".stock_robot")
    real_replace = os.replace
    attempts = 0

    def flaky_replace(source, target):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("temporarily locked")
        return real_replace(source, target)

    monkeypatch.setattr("utils.config.os.replace", flaky_replace)
    config.set("llm.temperature", 0.1)

    assert attempts >= 2
    assert config.get("llm.temperature") == 0.1
