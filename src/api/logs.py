"""本机运行日志的只读 API。"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from utils.config import Config

_READ_TAIL_BYTES = 1_000_000
_MAX_SOURCE_LINES = 5_000


def _read_recent_lines(path: Path, *, start_offset: int | None = None) -> list[str]:
    """读取日志末尾，限制读取量以免页面查询大文件。"""
    if not path.is_file():
        return []
    try:
        with path.open("rb") as handle:
            size = path.stat().st_size
            # 服务启动时记录的偏移量把持久化日志切成单次运行的独立视图。
            # 文件轮转或截断后从开头重新读取，避免偏移量失效导致长期空白。
            offset = min(max(0, start_offset or 0), size)
            handle.seek(max(offset, size - _READ_TAIL_BYTES))
            content = handle.read().decode("utf-8", errors="replace")
    except OSError as error:
        raise RuntimeError("无法读取本机运行日志") from error
    return list(deque((line for line in content.splitlines() if line),
                      maxlen=_MAX_SOURCE_LINES))


def create_logs_router(
        *,
        log_path: Path | None = None,
        start_offset: int | None = None,
) -> APIRouter:
    """创建本机运行日志查询路由。"""
    router = APIRouter(prefix="/api/v1/logs", tags=["logs"])

    @router.get("")
    async def get_runtime_logs(
            level: str | None = Query(default=None, pattern="^(INFO|WARNING|ERROR)$"),
            limit: int = Query(default=200, ge=1, le=500),
    ) -> dict[str, object]:
        path = log_path or Config().config_dir / "runtime.log"
        try:
            lines = _read_recent_lines(path, start_offset=start_offset)
        except RuntimeError as error:
            raise HTTPException(status_code=500, detail=str(error)) from error
        if level:
            lines = [line for line in lines if f" {level} " in line]
        return {"lines": lines[-limit:], "available": path.is_file()}

    return router
