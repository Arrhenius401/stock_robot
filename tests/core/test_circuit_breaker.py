"""内存断路器测试"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from core.circuit_breaker import CircuitBreaker


class TestCircuitBreaker:
    def test_open_after_threshold_failures(self):
        cb = CircuitBreaker(fail_threshold=3, open_seconds=600)
        for _ in range(3):
            cb.record_failure("000001", "valuation")
        assert cb.is_open("000001", "valuation")

    def test_success_resets(self):
        cb = CircuitBreaker(fail_threshold=3, open_seconds=600)
        cb.record_failure("000001", "valuation")
        cb.record_failure("000001", "valuation")
        cb.record_success("000001", "valuation")
        cb.record_failure("000001", "valuation")
        assert not cb.is_open("000001", "valuation")

    def test_open_does_not_block_other_types(self):
        cb = CircuitBreaker(fail_threshold=2, open_seconds=600)
        cb.record_failure("000001", "valuation")
        cb.record_failure("000001", "valuation")
        assert cb.is_open("000001", "valuation")
        assert not cb.is_open("000001", "industry")

    def test_auto_recovers_after_open_seconds(self):
        cb = CircuitBreaker(fail_threshold=1, open_seconds=0.05)
        cb.record_failure("000001", "valuation")
        assert cb.is_open("000001", "valuation")
        time.sleep(0.1)
        assert not cb.is_open("000001", "valuation")

    def test_partial_failures_below_threshold(self):
        cb = CircuitBreaker(fail_threshold=3, open_seconds=600)
        cb.record_failure("000001", "valuation")
        cb.record_failure("000001", "valuation")
        assert not cb.is_open("000001", "valuation")
