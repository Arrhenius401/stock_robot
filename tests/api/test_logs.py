"""运行日志 API 回归测试。"""

from fastapi.testclient import TestClient

from api.app import create_app
from api.logs import _read_recent_lines, create_logs_router
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


def test_runtime_logs_reads_only_lines_written_after_service_start(tmp_path):
    """启动偏移量隔离持久化文件中的上一次运行记录。"""
    path = tmp_path / "runtime.log"
    path.write_text("previous run\n", encoding="utf-8")
    start_offset = path.stat().st_size
    with path.open("a", encoding="utf-8") as handle:
        handle.write("current run\n")

    assert _read_recent_lines(path, start_offset=start_offset) == ["current run"]


def test_runtime_logs_api_uses_service_start_offset(tmp_path, monkeypatch):
    """日志接口应保留启动后的初始化与服务就绪日志。"""
    config = Config(config_dir=tmp_path)
    path = tmp_path / "runtime.log"
    path.write_text("previous run\n", encoding="utf-8")
    start_offset = path.stat().st_size
    path.write_text("previous run\ninitializing\nWeb 服务正在运行: http://127.0.0.1:25618\n",
                    encoding="utf-8")
    monkeypatch.setattr("api.logs.Config", lambda: config)

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_logs_router(start_offset=start_offset))
    response = TestClient(app).get("/api/v1/logs")

    assert response.json()["lines"] == [
        "initializing",
        "Web 服务正在运行: http://127.0.0.1:25618",
    ]


def test_runtime_logs_api_uses_the_current_service_log_file(tmp_path):
    """并行服务的日志文件不得混入当前服务的日志页。"""
    current_path = tmp_path / "runtime-current.log"
    current_path.write_text("current service\n", encoding="utf-8")
    (tmp_path / "runtime.log").write_text("other service\n", encoding="utf-8")

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(create_logs_router(log_path=current_path))
    response = TestClient(app).get("/api/v1/logs")

    assert response.json()["lines"] == ["current service"]
