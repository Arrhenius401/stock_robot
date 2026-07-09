"""网络异常重试装饰器 — 对瞬时连接错误指数退避重试"""
import time
from functools import wraps
from http.client import RemoteDisconnected

import requests

# 仅对这些网络类异常重试；业务/逻辑异常立即抛出
NETWORK_ERRORS = (
    ConnectionError,
    RemoteDisconnected,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


def retry_on_network_error(max_attempts: int = 3, base_delay: float = 0.5):
    """重试装饰器：捕获网络异常，指数退避（base_delay, 2x, 4x...），用尽后抛出。"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except NETWORK_ERRORS:
                    attempt += 1
                    if attempt >= max_attempts:
                        raise
                    time.sleep(base_delay * (2 ** (attempt - 1)))
        return wrapper
    return decorator
