"""第三方 SDK 的可终止进程边界，避免无超时网络调用卡住采集服务。"""
from __future__ import annotations

import importlib
import logging
import multiprocessing
from multiprocessing.connection import Connection
from typing import Any

logger = logging.getLogger(__name__)


class ProviderRequestError(RuntimeError):
    """子进程超时、异常或非正常退出。"""


def _request_child(connection: Connection, module_name: str, function_name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
    """仅在请求进程内加载 SDK，结果通过私有管道传回。"""
    try:
        function: Any = importlib.import_module(module_name)
        for name in function_name.split("."):
            function = getattr(function, name)
        result = function(*args, **kwargs)
        connection.send((True, result))
    except Exception as exc:  # noqa: BLE001 — 第三方 SDK 隔离边界，向父进程传递具体错误
        logger.warning("数据源进程请求失败: %s: %s", type(exc).__name__, exc)
        connection.send((False, f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()


def bounded_provider_call(module_name: str, function_name: str, kwargs: dict[str, Any] | None = None, *, args: tuple[Any, ...] = (), timeout: float = 30) -> Any:
    """超时后终止整个请求进程；不遗留后台 SDK 网络线程。"""
    if timeout <= 0:
        raise ValueError("请求超时必须大于 0")
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_request_child, args=(child, module_name, function_name, args, kwargs or {}), daemon=True)
    try:
        process.start()
        child.close()
        if not parent.poll(timeout):
            raise ProviderRequestError(f"数据源请求超过 {timeout:g} 秒，已终止超时进程")
        try:
            success, result = parent.recv()
        except (EOFError, OSError) as exc:
            raise ProviderRequestError("数据源请求进程异常退出") from exc
        if not success:
            raise ProviderRequestError(str(result))
        return result
    finally:
        parent.close()
        child.close()
        if process.pid is not None:
            process.join(timeout=0.2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
            process.close()
