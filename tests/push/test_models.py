import pytest
from pydantic import ValidationError

from push.models import Subscription


class TestSubscription:
    def test_valid(self):
        sub = Subscription(name="自选池", symbols=["600519", "000300"],
                           channel="email", time="08:00")
        assert sub.enabled is True
        assert sub.id is None

    def test_symbols_whitespace_stripped(self):
        sub = Subscription(name="t", symbols=[" 600519 ", "  "],
                           channel="wecom", time="09:30")
        assert sub.symbols == ["600519"]

    def test_empty_symbols_rejected(self):
        with pytest.raises(ValidationError):
            Subscription(name="t", symbols=[], channel="email", time="08:00")

    def test_bad_time_rejected(self):
        with pytest.raises(ValidationError):
            Subscription(name="t", symbols=["600519"], channel="email", time="8:00")
        with pytest.raises(ValidationError):
            Subscription(name="t", symbols=["600519"], channel="email", time="25:00")

    def test_bad_channel_rejected(self):
        with pytest.raises(ValidationError):
            Subscription(name="t", symbols=["600519"], channel="sms", time="08:00")
