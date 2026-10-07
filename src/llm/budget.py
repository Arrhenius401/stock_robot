"""自动输出预算与有界模型能力缓存，不持久化凭据。"""
import hashlib
import logging
import threading
import time
from collections import OrderedDict
from typing import Any

from llm.transport import request_deadline

logger = logging.getLogger(__name__)
AUTO_BUDGET = 8192
_cache: OrderedDict[tuple[str, str, str, str], tuple[float, int | None]] = OrderedDict()
_cache_lock = threading.Lock()
_pending: dict[tuple[str, str, str, str], threading.Event] = {}


def normalize_budget(value: Any) -> int | None:
    """None表示自动，显式预算必须是正整数。"""
    if value is None:
        return None
    if type(value) is not int or value <= 0:
        raise ValueError("max_tokens 必须为 null 或正整数")
    return value


def remaining_time(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("模型生成总时间预算已耗尽")
    return remaining


def clear_model_limit_cache() -> None:
    """清理内存能力缓存，供测试和显式刷新使用。"""
    with _cache_lock:
        _cache.clear()


def model_output_limit(client: Any, model: str, *, protocol: str,
                       deadline: float | None) -> int | None:
    """SDK能力查询最多2秒，字段缺失或服务不支持时返回None。"""
    query_deadline = min(time.monotonic() + 2.0, deadline) if deadline is not None else time.monotonic() + 2.0
    timeout = remaining_time(query_deadline)
    credential = hashlib.sha256(str(client.api_key).encode()).hexdigest()
    key = (protocol, str(client.base_url), model, credential)
    # 全局锁仅保护缓存；不同模型的SDK调用互不阻塞，同一键由事件单飞。
    while True:
        with _cache_lock:
            cached = _cache.get(key)
            if cached is not None and cached[0] > time.monotonic():
                _cache.move_to_end(key)
                return cached[1]
            pending = _pending.get(key)
            if pending is None:
                pending = threading.Event()
                _pending[key] = pending
                break
        if not pending.wait(timeout=timeout):
            return None
        timeout = remaining_time(query_deadline)

    limit: int | None = None
    token = request_deadline.set(query_deadline)
    try:
        timeout = remaining_time(query_deadline)
        bounded_client = client.with_options(max_retries=0)
        if protocol == "claude":
            info = bounded_client.models.retrieve(model, timeout=timeout)
            value = getattr(info, "max_tokens", None)
            if type(value) is not int:
                value = getattr(info, "max_output_tokens", None)
        else:
            items = bounded_client.models.list(timeout=timeout).data
            info = next((item for item in items if item.id == model), None)
            value = getattr(info, "max_output_tokens", None)
        if type(value) is int and value > 0:
            limit = value
    except Exception as exc:  # noqa: BLE001 — 第三方模型元数据接口不统一，失败有界回退
        logger.debug("模型能力查询不可用（%s），采用回退预算", type(exc).__name__)
    finally:
        request_deadline.reset(token)
        with _cache_lock:
            _cache[key] = (time.monotonic() + (3600 if limit is not None else 300), limit)
            _cache.move_to_end(key)
            while len(_cache) > 128:
                _cache.popitem(last=False)
            del _pending[key]
            pending.set()
    return limit



def automatic_budget(limit: int | None) -> int:
    return min(AUTO_BUDGET, limit) if limit is not None else AUTO_BUDGET


def recovery_budget(used: int, limit: int | None) -> int | None:
    """已知能力可提高预算；未知能力只在8192回退边界内恢复。"""
    ceiling = limit if limit is not None else AUTO_BUDGET
    candidate = min(max(AUTO_BUDGET, used * 2), ceiling)
    return candidate if candidate > used else None
