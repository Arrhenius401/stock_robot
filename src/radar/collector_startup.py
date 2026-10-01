"""独立采集服务的平台启动适配；业务开关始终由任务库管理。"""

from __future__ import annotations

import getpass
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)


class StartupError(RuntimeError):
    """系统启动配置不可用。"""


class CollectorStartup:
    """按项目隔离系统启动配置，Linux 网页仅查询状态。"""

    def __init__(
        self, project: Path, *, python: Path | None = None,
        platform: str | None = None, process_cwd: Callable[[int], Path] | None = None, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ):
        self.project = project.resolve()
        # 保留虚拟环境入口；Linux Python 常是指向系统解释器的符号链接。
        self.python = (python or Path(sys.executable)).absolute()
        self.platform = platform or sys.platform
        self.runner = runner
        self.process_cwd = process_cwd or self._read_process_cwd
        digest = hashlib.sha256(str(self.project).encode()).hexdigest()[:12]
        self.name = f'StockRobotRadarCollector-{digest}'
        self.unit_name = f'stock-robot-radar-{digest}.service'

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return self.runner(command, capture_output=True, text=True, errors='replace', timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise StartupError(f'系统启动管理命令失败：{exc}') from exc

    @staticmethod
    def _task_not_found(response: subprocess.CompletedProcess[str]) -> bool:
        """仅明确不存在时提供可启用状态，权限及其他未知错误保持未知。"""
        message = (response.stdout + response.stderr).lower()
        return any(value in message for value in (
            "cannot find the file", "cannot find the path", "task does not exist",
            "找不到指定的文件", "找不到指定的路径", "任务不存在",
        ))

    def status(self) -> dict[str, Any]:
        """系统配置不能充当采集服务心跳，未知值用 None 表示。"""
        windows = self.platform == 'win32'
        result: dict[str, Any] = {
            'provider': 'task_scheduler' if windows else 'systemd' if self.platform == 'linux' else 'manual',
            'name': self.name if windows else self.unit_name,
            'supported': windows or self.platform == 'linux', 'editable': windows,
            'registered': False, 'enabled': None, 'active': None, 'error': None,
        }
        try:
            if windows:
                response = self._run(['schtasks.exe', '/Query', '/TN', self.name, '/XML'])
                if response.returncode:
                    if self._task_not_found(response):
                        result.update(registered=False, enabled=False)
                    else:
                        result['error'] = response.stderr.strip() or response.stdout.strip() or '无法查询启动任务'
                    return result
                root = ET.fromstring(response.stdout)
                node = root.find('.//{*}Settings/{*}Enabled')
                result.update(registered=True, enabled=node is None or (node.text or '').lower() == 'true')
            elif self.platform == 'linux':
                response = self._run(['systemctl', 'is-enabled', self.unit_name])
                value = response.stdout.strip()
                if value in {'enabled', 'disabled', 'static', 'indirect', 'masked', 'enabled-runtime'}:
                    result.update(registered=True, enabled=value in {'enabled', 'enabled-runtime'})
                elif value == 'not-found' or 'not found' in response.stderr.lower():
                    result.update(registered=False, enabled=False)
                else:
                    raise StartupError(response.stderr.strip() or 'systemd 状态不可读取，使用前台服务手动托管')
                active = self._run(['systemctl', 'is-active', self.unit_name])
                result['active'] = active.stdout.strip() == 'active'
        except (StartupError, ET.ParseError) as exc:
            result['error'] = str(exc)
        return result

    def windows_xml(self, user: str) -> str:
        """当前用户登录触发，失败恢复、不依赖电池或空闲条件。"""
        ns = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
        ET.register_namespace('', ns)
        root = ET.Element(f'{{{ns}}}Task', version='1.2')

        def node(parent: ET.Element, tag: str, value: str | None = None, **attributes: str) -> ET.Element:
            element = ET.SubElement(parent, f'{{{ns}}}{tag}', attributes)
            element.text = value
            return element

        trigger = node(node(root, 'Triggers'), 'LogonTrigger')
        node(trigger, 'Enabled', 'true')
        node(trigger, 'UserId', user)
        principal = node(node(root, 'Principals'), 'Principal', id='Author')
        node(principal, 'UserId', user)
        node(principal, 'LogonType', 'InteractiveToken')
        node(principal, 'RunLevel', 'LeastPrivilege')
        settings = node(root, 'Settings')
        for tag, value in {
            'MultipleInstancesPolicy': 'IgnoreNew', 'DisallowStartIfOnBatteries': 'false',
            'StopIfGoingOnBatteries': 'false', 'AllowHardTerminate': 'true',
            'StartWhenAvailable': 'true', 'RunOnlyIfNetworkAvailable': 'false',
        }.items():
            node(settings, tag, value)
        idle = node(settings, 'IdleSettings')
        node(idle, 'StopOnIdleEnd', 'false')
        node(idle, 'RestartOnIdle', 'false')
        for tag, value in {'AllowStartOnDemand': 'true', 'Enabled': 'true', 'Hidden': 'true',
                           'RunOnlyIfIdle': 'false', 'WakeToRun': 'false', 'ExecutionTimeLimit': 'PT0S'}.items():
            node(settings, tag, value)
        restart = node(settings, 'RestartOnFailure')
        node(restart, 'Interval', 'PT1M')
        node(restart, 'Count', '3')
        action = node(node(root, 'Actions', Context='Author'), 'Exec')
        node(action, 'Command', str(self.python))
        node(action, 'Arguments', f'"{self.project / "scripts/run-radar-collector.py"}" --supervise radar daemon')
        node(action, 'WorkingDirectory', str(self.project))
        return ET.tostring(root, encoding='unicode')

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        """更改登录启动不更改业务开关，也不关闭已运行服务。"""
        if self.platform != 'win32':
            raise StartupError('系统启动设置由服务器部署命令管理，请安装或禁用 systemd 服务')
        if enabled:
            if not (self.project / 'scripts/run-radar-collector.py').is_file():
                raise StartupError('项目采集启动脚本不存在')
            if not self.python.is_file():
                raise StartupError(f'Python 程序不存在：{self.python}')
            # 临时 XML 无凭据，命令使用参数列表，保留固定工作目录。
            with NamedTemporaryFile(suffix='.xml', delete=False) as file:
                xml_path = Path(file.name)
                domain = os.environ.get('USERDOMAIN', '')
                username = getpass.getuser()
                user = f'{domain}\\{username}' if domain else username
                file.write(self.windows_xml(user).encode('utf-16'))
            try:
                response = self._run(['schtasks.exe', '/Create', '/TN', self.name, '/XML', str(xml_path), '/F'])
            finally:
                xml_path.unlink(missing_ok=True)
            if response.returncode:
                raise StartupError(response.stderr.strip() or response.stdout.strip() or '无法注册登录启动任务')
            response = self._run(['schtasks.exe', '/Run', '/TN', self.name])
        else:
            response = self._run(['schtasks.exe', '/Change', '/TN', self.name, '/Disable'])
        if response.returncode:
            raise StartupError(response.stderr.strip() or response.stdout.strip() or '无法更新启动任务')
        return self.status()

    @staticmethod
    def _read_process_cwd(process_id: int) -> Path:
        """可用时核验旧进程目录；缺少可选查询库时不冒险终止。"""
        try:
            import psutil
        except ImportError as exc:
            raise StartupError("缺少进程目录查询能力，请人工停止旧采集进程") from exc
        try:
            return Path(psutil.Process(process_id).cwd()).resolve()
        except psutil.Error as exc:
            raise StartupError(f"旧进程 {process_id} 工作目录无法核验：{exc}") from exc

    @property
    def legacy_executable(self) -> Path:
        """旧启动器必须精确属于当前项目，不能按名字处理其他项目。"""
        return self.project / ".venv" / "Scripts" / "stock-robot.exe"

    def _legacy_pattern(self) -> str:
        executable = re.escape(str(self.legacy_executable)).replace(r"\\", r"[\\/]")
        python = re.escape(str(self.project / ".venv/Scripts/python.exe")).replace(r"\\", r"[\\/]")
        entry = rf'(?:"{executable}"|{executable})'
        interpreter = rf'(?:"{python}"|{python})'
        return rf'^\s*(?:{entry}|{interpreter}\s+{entry})\s+radar\s+collect(?:\s+--(?:hour|minute)\s+\d+)*\s*$'

    def _legacy_command_matches(self, command: str) -> bool:
        return bool(re.fullmatch(self._legacy_pattern(), command, flags=re.IGNORECASE))

    def _legacy_launcher_matches(self, content: str) -> bool:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if len(lines) != 2 or lines[0].lower() != "@echo off":
            return False
        project = re.escape(str(self.project)).replace(r"\\", r"[\\/]")
        prefix = rf'^start\s+""\s+/b\s+/d\s+"{project}"\s+'
        match = re.match(prefix, lines[1], flags=re.IGNORECASE)
        return match is not None and self._legacy_command_matches(lines[1][match.end():])

    @staticmethod
    def _ps_literal(value: str) -> str:
        """PowerShell 单引号字面量，不让项目路径成为命令代码。"""
        return "'" + value.replace("'", "''") + "'"

    def _powershell(self, script: str) -> subprocess.CompletedProcess[str]:
        return self._run(["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script])

    def _legacy_process_rows(self) -> list[dict[str, Any]]:
        """本机优先逐进程查询，避免 CIM 全表扫描阻塞；测试 runner 保留后备边界。"""
        if self.runner is subprocess.run:
            try:
                import psutil
            except ImportError:
                logger.debug("缺少 psutil，旧采集核验降级到 PowerShell")
            else:
                rows = []
                try:
                    for process in psutil.process_iter(["pid", "name"]):
                        name = str(process.info.get("name") or "").lower()
                        if not (name.startswith("python") or name == "stock-robot.exe"):
                            continue
                        try:
                            command = subprocess.list2cmdline(process.cmdline())
                        except psutil.NoSuchProcess:
                            logger.debug("旧入口核验时进程已退出：%s", process.pid)
                            continue
                        if self._legacy_command_matches(command):
                            rows.append({"ProcessId": process.pid, "CommandLine": command})
                except psutil.Error as exc:
                    raise StartupError(f"旧采集进程无法核验：{exc}") from exc
                return rows
        response = self._powershell("Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object { $_.CommandLine -match 'radar\\s+collect' } | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress")
        if response.returncode:
            raise StartupError(response.stderr.strip() or "旧采集进程无法核验")
        payload = json.loads(response.stdout) if response.stdout.strip() else []
        return payload if isinstance(payload, list) else [payload]

    def _stop_legacy_process(self, process_id: int) -> None:
        """停止前重新核验 PID 的当前命令与目录，禁止宽泛结束 Python 进程。"""
        if self.runner is subprocess.run:
            try:
                import psutil
            except ImportError:
                logger.debug("缺少 psutil，旧采集停止降级到 PowerShell")
            else:
                try:
                    process = psutil.Process(process_id)
                    if not self._legacy_command_matches(subprocess.list2cmdline(process.cmdline())):
                        raise StartupError(f"旧进程 {process_id} 命令已改变，请人工核验")
                    if Path(process.cwd()).resolve() != self.project:
                        raise StartupError(f"旧进程 {process_id} 工作目录已改变，请人工核验")
                    process.terminate()
                    process.wait(timeout=10)
                except psutil.NoSuchProcess:
                    logger.debug("旧采集进程已退出：%s", process_id)
                except psutil.Error as exc:
                    raise StartupError(f"旧进程 {process_id} 无法停止：{exc}") from exc
                return
        pattern = self._ps_literal(self._legacy_pattern())
        script = f"$collectorProcess = Get-CimInstance Win32_Process -Filter 'ProcessId = {process_id}' -ErrorAction Stop; if ($collectorProcess -and $collectorProcess.CommandLine -match {pattern}) {{ Stop-Process -Id {process_id} -ErrorAction Stop }}"
        response = self._powershell(script)
        if response.returncode:
            raise StartupError(response.stderr.strip() or f"旧进程 {process_id} 无法停止")

    def migrate_legacy(self, *, state_dir: Path, startup_directory: Path | None = None) -> dict[str, Any]:
        """清理确认属于当前项目的旧采集入口；权限不足时留下关闭标记。"""
        result: dict[str, Any] = {"cleanup_pending": False, "errors": [], "task_matched": False,
                                  "launcher_matched": False, "stopped_process_ids": [], "active_legacy_process_ids": [], "process_verification_failed": False, "unsafe_overlap": False, "marker_retained": False}
        if self.platform != "win32":
            return result
        marker = Path(state_dir) / "radar_collector.disabled"
        if startup_directory is None:
            app_data = os.environ.get("APPDATA")
            startup_directory = (Path(app_data) if app_data else Path.home() / "AppData/Roaming") / "Microsoft/Windows/Start Menu/Programs/Startup"
        launcher = startup_directory / "stock-robot-radar-collector.cmd"
        process_ids: list[int] = []
        try:
            task = self._run(["schtasks.exe", "/Query", "/TN", "StockRobotRadarCollector", "/XML"])
            if task.returncode == 0:
                root = ET.fromstring(task.stdout)
                actions = root.findall(".//{*}Exec")
                action = actions[0] if len(actions) == 1 else None
                if action is not None:
                    command = action.find("{*}Command")
                    arguments = action.find("{*}Arguments")
                    if command is not None:
                        combined = f'"{(command.text or "").strip().strip(chr(34))}" {(arguments.text or "") if arguments is not None else ""}'
                        if self._legacy_command_matches(combined):
                            working_directory = action.find("{*}WorkingDirectory")
                            if working_directory is None or not working_directory.text:
                                result["errors"].append("旧采集任务的工作目录无法核验，请人工检查旧任务")
                            else:
                                result["task_matched"] = Path(working_directory.text).resolve() == self.project
            elif not self._task_not_found(task):
                result["errors"].append(task.stderr.strip() or "旧启动任务无法核验")
        except (StartupError, ET.ParseError) as exc:
            result["errors"].append(str(exc))
        try:
            if launcher.is_file():
                result["launcher_matched"] = self._legacy_launcher_matches(launcher.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError) as exc:
            result["errors"].append(f"旧启动文件无法核验：{exc}")
        try:
            rows = self._legacy_process_rows()
            for row in rows:
                if isinstance(row, dict) and isinstance(row.get("ProcessId"), int) and self._legacy_command_matches(str(row.get("CommandLine", ""))):
                    process_id = row["ProcessId"]
                    try:
                        if self.process_cwd(process_id).resolve() == self.project:
                            process_ids.append(process_id)
                    except (StartupError, OSError) as exc:
                        result["errors"].append(str(exc))
                        result["active_legacy_process_ids"].append(process_id)
        except (StartupError, ValueError, TypeError) as exc:
            result["errors"].append(f"旧采集进程无法核验：{exc}")
            result["process_verification_failed"] = True
        if result["task_matched"] or result["launcher_matched"] or process_ids or result["errors"]:
            try:
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text("disabled\n", encoding="utf-8")
            except OSError as exc:
                result["errors"].append(f"旧采集关闭标记保存失败：{exc}")
                result["cleanup_pending"] = True
                result["active_legacy_process_ids"].extend(process_ids)
                result["unsafe_overlap"] = bool(result["task_matched"] or result["launcher_matched"] or result["active_legacy_process_ids"] or result["process_verification_failed"])
                result["marker_retained"] = marker.is_file()
                return result
        if result["task_matched"]:
            try:
                response = self._run(["schtasks.exe", "/Change", "/TN", "StockRobotRadarCollector", "/Disable"])
                if response.returncode:
                    raise StartupError(response.stderr.strip() or "旧采集任务关闭失败")
            except StartupError as exc:
                result["errors"].append(str(exc))
        if result["launcher_matched"]:
            try:
                launcher.unlink(missing_ok=True)
            except OSError as exc:
                result["errors"].append(f"旧启动文件清理失败：{exc}")
        for process_id in process_ids:
            try:
                if self.process_cwd(process_id).resolve() != self.project:
                    continue
                self._stop_legacy_process(process_id)
                result["stopped_process_ids"].append(process_id)
            except (StartupError, OSError) as exc:
                result["errors"].append(str(exc))
                result["active_legacy_process_ids"].append(process_id)
        result["unsafe_overlap"] = bool(result["active_legacy_process_ids"] or result["process_verification_failed"])
        result["cleanup_pending"] = bool(result["errors"])
        # 旧任务仍注册但已禁用，保留关闭标记防止人工重新启用旧入口。
        result["marker_retained"] = marker.is_file()
        return result

    def linux_unit(self, user: str) -> str:
        """生成普通服务账户的系统级 unit；安装由部署人员执行。"""
        if user == 'root' or not re.fullmatch(r'[a-z_][a-z0-9_-]*\$?', user):
            raise ValueError('请指定有效的普通服务账户')

        def quote(path: Path) -> str:
            value = str(path)
            if '\n' in value or '\r' in value:
                raise ValueError('服务路径不能包含换行')
            return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'

        # WorkingDirectory 不解析 shell 引号或 C 转义；内部空格直接保留。
        working_directory = str(self.project)
        if any(ord(char) < 32 for char in working_directory) or working_directory[-1:].isspace() or working_directory.endswith("\\"):
            raise ValueError('服务工作目录含无法安全表达的控制字符或结尾字符')
        working_directory = working_directory.replace('%', '%%')
        return (
            '[Unit]\nDescription=Stock Robot radar collector\nAfter=network-online.target\n'
            'Wants=network-online.target\n\n[Service]\nType=simple\n'
            f'User={user}\nWorkingDirectory={working_directory}\n'
            f'ExecStart=:{quote(self.python)} {quote(self.project / "scripts/run-radar-collector.py")} radar daemon\n'
            'Restart=on-failure\nRestartSec=15\nTimeoutStopSec=45\n'
            '\n[Install]\nWantedBy=multi-user.target\n'
        )
