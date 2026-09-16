"""Windows 任务计划程序中的雷达采集守护启动项。"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

_TASK_NAME = "StockRobotRadarCollector"
_MISSING_TASK_MARKERS = ("cannot find the file", "找不到指定的文件", "任务不存在")


class CollectorAutostartError(RuntimeError):
    """任务计划程序不可用或拒绝执行时抛出。"""


@dataclass(frozen=True)
class CollectorAutostartStatus:
    """面向 API 的启动项状态。"""

    supported: bool
    registered: bool
    enabled: bool
    task_name: str
    schedule: dict[str, object]
    provider: str = "task_scheduler"
    scheduler_error: str | None = None

    def to_dict(self) -> dict[str, object]:
        """转为 JSON 友好的状态。"""
        return {
            "supported": self.supported,
            "registered": self.registered,
            "enabled": self.enabled,
            "task_name": self.task_name,
            "schedule": self.schedule,
            "provider": self.provider,
            "scheduler_error": self.scheduler_error,
        }


class WindowsCollectorAutostart:
    """注册、启停和核验当前用户的 Windows 采集启动任务。"""

    def __init__(
        self,
        *,
        executable: Path | None = None,
        startup_directory: Path | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        platform_name: str | None = None,
    ) -> None:
        self._executable = executable or Path(sys.executable).with_name("stock-robot.exe")
        self._startup_directory = startup_directory or self._default_startup_directory()
        self._runner = runner
        self._platform_name = platform_name or os.name

    def status(self) -> CollectorAutostartStatus:
        """读取任务 XML；XML 的 Enabled 字段不受系统语言影响。"""
        schedule = {"timezone": "Asia/Shanghai", "weekdays": True, "hour": 18, "minute": 30}
        if self._platform_name != "nt":
            return CollectorAutostartStatus(False, False, False, _TASK_NAME, schedule)
        completed = self._execute(["/Query", "/TN", _TASK_NAME, "/XML"])
        if completed.returncode:
            detail = f"{completed.stdout}\n{completed.stderr}".lower()
            if any(marker in detail for marker in _MISSING_TASK_MARKERS):
                fallback = self._startup_status(schedule)
                if fallback is not None:
                    return fallback
                return CollectorAutostartStatus(True, False, False, _TASK_NAME, schedule)
            fallback = self._startup_status(schedule, self._command_error("无法读取采集启动项", completed))
            if fallback is not None:
                return fallback
            return CollectorAutostartStatus(
                True, False, False, _TASK_NAME, schedule,
                provider="startup_folder",
                scheduler_error=self._command_error("无法读取采集启动项", completed),
            )
        try:
            root = ET.fromstring(completed.stdout)
        except ET.ParseError as exc:
            raise CollectorAutostartError("采集启动项返回了无法解析的任务 XML") from exc
        enabled_node = root.find(".//{*}Enabled")
        enabled = enabled_node is None or (enabled_node.text or "true").strip().lower() == "true"
        return CollectorAutostartStatus(True, True, enabled, _TASK_NAME, schedule)

    def set_enabled(self, enabled: bool) -> CollectorAutostartStatus:
        """切换启动项；启用后立即拉起守护进程，无需等待下一次登录。"""
        if self._platform_name != "nt":
            raise CollectorAutostartError("当前系统不支持 Windows 任务计划程序")
        current = self.status()
        if enabled:
            if current.provider == "startup_folder":
                self._create_startup_launcher()
            else:
                try:
                    if not current.registered:
                        self._create_task()
                    elif not current.enabled:
                        self._require_success(["/Change", "/TN", _TASK_NAME, "/Enable"], "无法启用采集启动项")
                    self._require_success(["/Run", "/TN", _TASK_NAME], "采集启动项已启用，但无法立即启动")
                except CollectorAutostartError:
                    self._create_startup_launcher()
        elif current.provider == "startup_folder":
            self._remove_startup_launcher()
        elif current.registered and current.enabled:
            self._require_success(["/Change", "/TN", _TASK_NAME, "/Disable"], "无法关闭采集启动项")
        return self.status()

    @staticmethod
    def _default_startup_directory() -> Path:
        app_data = os.environ.get("APPDATA")
        if not app_data:
            return Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        return Path(app_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"

    @property
    def _launcher_path(self) -> Path:
        return self._startup_directory / "stock-robot-radar-collector.cmd"

    def _startup_status(
        self, schedule: dict[str, object], scheduler_error: str | None = None,
    ) -> CollectorAutostartStatus | None:
        if not self._launcher_path.is_file() and scheduler_error is None:
            return None
        return CollectorAutostartStatus(
            True,
            self._launcher_path.is_file(),
            self._launcher_path.is_file(),
            _TASK_NAME,
            schedule,
            provider="startup_folder",
            scheduler_error=scheduler_error,
        )

    def _create_startup_launcher(self) -> None:
        if not self._executable.is_file():
            raise CollectorAutostartError(f"未找到项目命令行程序：{self._executable}")
        executable = str(self._executable)
        working_directory = str(self._executable.parents[2])
        target = f'"{executable}"' if " " in executable else executable
        content = f'@echo off\r\nstart "" /b /d "{working_directory}" {target} radar collect --hour 18 --minute 30\r\n'
        try:
            self._startup_directory.mkdir(parents=True, exist_ok=True)
            self._launcher_path.write_text(content, encoding="utf-8", newline="")
        except OSError as exc:
            raise CollectorAutostartError(f"无法创建采集启动器：{exc}") from exc

    def _remove_startup_launcher(self) -> None:
        try:
            self._launcher_path.unlink(missing_ok=True)
        except OSError as exc:
            raise CollectorAutostartError(f"无法关闭采集启动器：{exc}") from exc

    def _create_task(self) -> None:
        if not self._executable.is_file():
            raise CollectorAutostartError(f"未找到项目命令行程序：{self._executable}")
        executable = str(self._executable)
        target = f'"{executable}"' if " " in executable else executable
        command = f"{target} radar collect --hour 18 --minute 30"
        self._require_success(
            ["/Create", "/TN", _TASK_NAME, "/TR", command, "/SC", "ONLOGON", "/RL", "LIMITED", "/F"],
            "无法创建采集启动项",
        )

    def _require_success(self, arguments: list[str], message: str) -> None:
        completed = self._execute(arguments)
        if completed.returncode:
            raise CollectorAutostartError(self._command_error(message, completed))

    def _execute(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return self._runner(
                ["schtasks.exe", *arguments],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
            )
        except FileNotFoundError as exc:
            raise CollectorAutostartError("未找到 Windows 任务计划程序") from exc
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CollectorAutostartError(f"任务计划程序执行失败：{exc}") from exc

    @staticmethod
    def _command_error(message: str, completed: subprocess.CompletedProcess[str]) -> str:
        detail = completed.stderr.strip() or completed.stdout.strip()
        return f"{message}{f'：{detail}' if detail else ''}"
