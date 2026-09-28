from subprocess import CompletedProcess

from radar.collector_autostart import WindowsCollectorAutostart


def _result(*, code: int = 0, stdout: str = "", stderr: str = "") -> CompletedProcess[str]:
    return CompletedProcess(["schtasks.exe"], code, stdout, stderr)


def test_status_reads_enabled_flag_from_task_xml():
    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        calls.append(command)
        return _result(stdout="<Task><Settings><Enabled>false</Enabled></Settings></Task>")

    status = WindowsCollectorAutostart(runner=runner, platform_name="nt").status()

    assert status.registered is True
    assert status.enabled is False
    assert calls == [["schtasks.exe", "/Query", "/TN", "StockRobotRadarCollector", "/XML"]]


def test_enable_creates_and_immediately_runs_task(tmp_path):
    executable = tmp_path / "stock-robot.exe"
    executable.touch()
    responses = iter((
        _result(code=1, stderr="ERROR: The system cannot find the file specified."),
        _result(),
        _result(),
        _result(stdout="<Task><Settings><Enabled>true</Enabled></Settings></Task>"),
    ))
    calls: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        calls.append(command)
        return next(responses)

    manager = WindowsCollectorAutostart(
        executable=executable,
        startup_directory=tmp_path / "Startup",
        runner=runner,
        platform_name="nt",
    )
    status = manager.set_enabled(True)

    assert status.enabled is True
    assert calls[1][1:5] == ["/Create", "/TN", "StockRobotRadarCollector", "/TR"]
    assert calls[1][5] == f"{executable} radar collect --hour 18 --minute 30"
    assert calls[2] == ["schtasks.exe", "/Run", "/TN", "StockRobotRadarCollector"]


def test_status_does_not_hide_unexpected_scheduler_error(tmp_path):
    def runner(_command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        return _result(code=1, stderr="ERROR: Access is denied.")

    status = WindowsCollectorAutostart(
        startup_directory=tmp_path / "Startup", runner=runner, platform_name="nt",
    ).status()

    assert status.enabled is False
    assert status.provider == "startup_folder"
    assert status.scheduler_error == "无法读取采集启动项：ERROR: Access is denied."


def test_enable_falls_back_to_current_user_startup_launcher(tmp_path):
    executable = tmp_path / "project" / ".venv" / "Scripts" / "stock-robot.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()

    def runner(_command: list[str], **_kwargs: object) -> CompletedProcess[str]:
        return _result(code=1, stderr="ERROR: The system cannot find the path specified.")

    manager = WindowsCollectorAutostart(
        executable=executable,
        startup_directory=tmp_path / "Startup",
        runner=runner,
        platform_name="nt",
    )
    status = manager.set_enabled(True)

    assert status.enabled is True
    assert status.provider == "startup_folder"
    assert "radar collect --hour 18 --minute 30" in (tmp_path / "Startup" / "stock-robot-radar-collector.cmd").read_text()

    disabled = manager.set_enabled(False)

    assert disabled.enabled is False
    assert not (tmp_path / "Startup" / "stock-robot-radar-collector.cmd").exists()
