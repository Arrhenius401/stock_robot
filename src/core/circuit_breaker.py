"""内存断路器 — per (symbol, data_type) 失败计数，防源头故障时重复请求"""
import time


class CircuitBreaker:
    def __init__(self, fail_threshold: int = 3, open_seconds: float = 600.0):
        self._fail_threshold = fail_threshold
        self._open_seconds = open_seconds
        self._fails: dict[tuple[str, str], int] = {}
        self._opened_until: dict[tuple[str, str], float] = {}

    def is_open(self, symbol: str, data_type: str) -> bool:
        key = (symbol, data_type)
        until = self._opened_until.get(key)
        if until is None:
            return False
        if time.time() >= until:
            # pop 而非 del：并发线程同时过期时，第二个 pop 拿到 None 也不抛 KeyError
            self._opened_until.pop(key, None)
            self._fails[key] = 0
            return False
        return True

    def record_success(self, symbol: str, data_type: str):
        key = (symbol, data_type)
        self._fails.pop(key, None)
        self._opened_until.pop(key, None)

    def record_failure(self, symbol: str, data_type: str):
        key = (symbol, data_type)
        if key in self._opened_until:
            return
        self._fails[key] = self._fails.get(key, 0) + 1
        if self._fails[key] >= self._fail_threshold:
            self._opened_until[key] = time.time() + self._open_seconds
