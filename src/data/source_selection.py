"""适配器内部的窄范围历史序列选择，不管理缓存或网络重试。"""
import logging
from collections.abc import Callable, Iterable
from typing import TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")


def select_history(
    sources: Iterable[tuple[str, Callable[[], list[T]]]],
    on_error: Callable[[str, Exception], None],
    minimum: int = 60,
) -> list[T]:
    """足量立即采用；不足保留最长整份，同长后者优先，绝不拼接。"""
    best: list[T] = []
    for label, fetch in sources:
        try:
            rows = fetch()
        except Exception as error:  # noqa: BLE001 — 第三方来源隔离，保留诊断后切换
            logger.warning("%s获取失败: %s", label, error)
            on_error(label, error)
            continue
        if len(rows) >= minimum:
            return rows
        if len(rows) >= len(best):
            best = rows
        logger.warning("%s仅返回 %s 条合法行情（不足 %s）", label, len(rows), minimum)
        if not rows:
            on_error(label, ValueError("没有合法行情"))
    return best
