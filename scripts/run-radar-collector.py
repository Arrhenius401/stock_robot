"""用当前项目源码启动采集命令，避免其他 editable 安装截获入口。"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _child_job(child: subprocess.Popen) -> tuple[Any, Any]:
    """将子进程放入关闭即终止的作业，任务被强制停止时一并回收。"""
    class BasicLimit(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in
                    ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                     "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class ExtendedLimit(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BasicLimit), ("IoInfo", IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = ExtendedLimit()
    limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        error = ctypes.WinError(ctypes.get_last_error())
        kernel.CloseHandle(handle)
        raise error
    process = kernel.OpenProcess(0x0101, False, child.pid)
    if not process:
        error = ctypes.WinError(ctypes.get_last_error())
        kernel.CloseHandle(handle)
        raise error
    try:
        if not kernel.AssignProcessToJobObject(handle, process):
            error = ctypes.WinError(ctypes.get_last_error())
            kernel.CloseHandle(handle)
            raise error
    finally:
        kernel.CloseHandle(process)
    return kernel, handle


def supervise(command: list[str], *, restart_delay: float = 15) -> int:
    """Windows 任务保持一个子进程；异常退出后延迟重启，正常退出即结束。"""
    if sys.platform != "win32":
        raise RuntimeError("监督模式仅用于 Windows，Linux 由 systemd 管理")
    while True:
        child = subprocess.Popen(command)
        kernel = handle = None
        try:
            kernel, handle = _child_job(child)
            result = child.wait()
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=10)
            if kernel is not None and handle is not None:
                kernel.CloseHandle(handle)
        if result == 0:
            return 0
        logger.warning("采集进程异常退出（%s），%s 秒后重启", result, restart_delay)
        time.sleep(restart_delay)


def main() -> None:
    """系统任务和人工命令均固定项目来源与工作目录。"""
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project / "src"))
    os.chdir(project)
    if sys.argv[1:2] == ["--supervise"]:
        raise SystemExit(supervise([sys.executable, str(Path(__file__).resolve()), *sys.argv[2:]]))
    from stock_robot.cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
