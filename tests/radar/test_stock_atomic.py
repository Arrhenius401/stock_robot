"""原子替换重试：只吞并重试共享冲突，其他错误立即抛出。"""
from __future__ import annotations

import pytest

from radar.stocks import atomic


def test_retries_access_denied_then_succeeds(monkeypatch):
    calls = []

    def fake_replace(source, target):
        calls.append((source, target))
        if len(calls) < 3:
            raise PermissionError(13, 'denied')

    monkeypatch.setattr(atomic.os, 'replace', fake_replace)
    monkeypatch.setattr(atomic.time, 'sleep', lambda _: None)
    atomic.replace_with_retry('a', 'b')
    assert len(calls) == 3


def test_non_retryable_error_raises_immediately(monkeypatch):
    calls = []

    def fake_replace(source, target):
        calls.append((source, target))
        raise FileNotFoundError(2, 'missing')

    monkeypatch.setattr(atomic.os, 'replace', fake_replace)
    monkeypatch.setattr(atomic.time, 'sleep', lambda _: None)
    with pytest.raises(FileNotFoundError):
        atomic.replace_with_retry('a', 'b')
    assert len(calls) == 1


def test_exhausts_attempts(monkeypatch):
    calls = []

    def fake_replace(source, target):
        calls.append((source, target))
        raise PermissionError(13, 'denied')

    monkeypatch.setattr(atomic.os, 'replace', fake_replace)
    monkeypatch.setattr(atomic.time, 'sleep', lambda _: None)
    with pytest.raises(PermissionError):
        atomic.replace_with_retry('a', 'b', attempts=2)
    assert len(calls) == 2
