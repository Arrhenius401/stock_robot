"""只用于 AI 解读重试的短期报告上下文，不执行数据采集。"""
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from data.schemas import AnalysisContext, AnalysisResult
from report.signal import SignalConfig


@dataclass
class CommentaryContext:
    symbol: str
    name: str
    results: list[AnalysisResult]
    commentary: dict[str, str]
    context: AnalysisContext
    signal_config: SignalConfig | None
    created_at: float = field(default_factory=time.monotonic)
    output_path: Path | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class CommentaryStore:
    """有容量与时限的进程内上下文；重启后不拼接其他批次。"""
    def __init__(self, *, ttl_seconds: float = 1800, capacity: int = 64):
        self._ttl = ttl_seconds
        self._capacity = capacity
        self._reports: dict[str, CommentaryContext] = {}
        self._lock = threading.Lock()

    def remember(self, report: CommentaryContext) -> str:
        with self._lock:
            now = time.monotonic()
            expired = [key for key, value in self._reports.items()
                       if now - value.created_at >= self._ttl]
            for key in expired:
                del self._reports[key]
            if len(self._reports) >= self._capacity:
                del self._reports[next(iter(self._reports))]
            token = secrets.token_urlsafe(24)
            self._reports[token] = report
            return token

    def get(self, token: str) -> CommentaryContext | None:
        with self._lock:
            report = self._reports.get(token)
            if report is None:
                return None
            if time.monotonic() - report.created_at >= self._ttl:
                del self._reports[token]
                return None
            return report


commentary_store = CommentaryStore()
