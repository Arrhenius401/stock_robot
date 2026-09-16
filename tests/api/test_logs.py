"""运行日志 API 回归测试。"""

from fastapi.testclient import TestClient

from api.app import create_app
from utils.config import Config


def test_runtime_logs_filters_level_and_returns_latest_lines(tmp_path, monkeypatch):
    """等级过滤只返回相应日志，并保持尾部顺序。"""
    config = Config(config_dir=tmp_path)
    (tmp_path / "runtime.log").write_text(
        "2026-09-16 10:00:00 INFO app service started\n"
        "2026-09-16 10:00:01 WARNING app data delayed\n"
        "2026-09-16 10:00:02 ERROR app refresh failed\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("api.logs.Config", lambda: config)

    response = TestClient(create_app(core=None, push=False)).get(
        "/api/v1/logs?level=ERROR&limit=10")

    assert response.status_code == 200
    assert response.json() == {
        "available": True,
        "lines": ["2026-09-16 10:00:02 ERROR app refresh failed"],
    }


def test_runtime_logs_reports_missing_file(tmp_path, monkeypatch):
    """首次启动前没有日志文件也应返回可渲染的空结果。"""
    config = Config(config_dir=tmp_path)
    monkeypatch.setattr("api.logs.Config", lambda: config)

    response = TestClient(create_app(core=None, push=False)).get("/api/v1/logs")

    assert response.status_code == 200
    assert response.json() == {"available": False, "lines": []}
