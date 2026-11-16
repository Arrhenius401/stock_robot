"""原子替换：Windows 下目标文件被安全软件/索引器短暂占用时有限重试。

只对共享冲突/拒绝访问重试，磁盘满、路径错误等异常立即抛出。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

_RETRY_WINERRORS = {5, 32}
_RETRY_ERRNOS = {11, 13, 16}


def replace_with_retry(source: str | Path, target: str | Path, *, attempts: int = 5) -> None:
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            if getattr(exc, 'winerror', None) not in _RETRY_WINERRORS and exc.errno not in _RETRY_ERRNOS:
                raise
            if attempt + 1 >= attempts:
                raise
            time.sleep(0.05 * (2 ** attempt))
