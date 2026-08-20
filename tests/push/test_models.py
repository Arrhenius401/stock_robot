import pytest
from pydantic import ValidationError

from push.models import Subscription, SubscriptionSymbol


class TestSubscription:
    def test_valid(self):
        sub = Subscription(name="自选池", symbols=["600519", "000300"],
                           channel="email", time="08:00")
        assert sub.enabled is True
        assert sub.id is None
        assert sub.symbols[0].kind == "auto"

    def test_symbols_whitespace_stripped(self):
        sub = Subscription(name="t", symbols=[" 600519 ", "  "],
                           channel="wecom", time="09:30")
        assert sub.symbols[0].symbol == "600519"
        assert len(sub.symbols) == 1

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


class TestSubscriptionSymbol:
    def test_explicit_kind_and_style(self):
        sub = Subscription(
            name="t",
            symbols=[
                SubscriptionSymbol(symbol="000001", kind="stock"),
                SubscriptionSymbol(symbol="000300", kind="index",
                                   index_style="broad"),
            ],
            channel="email", time="08:00")
        assert sub.symbols[0].kind == "stock"
        assert sub.symbols[1].kind == "index"
        assert sub.symbols[1].index_style == "broad"

    def test_dict_items_parsed(self):
        sub = Subscription(
            name="t",
            symbols=[
                {"symbol": "000001", "kind": "index", "index_style": "broad"},
                {"symbol": "600519"},
            ],
            channel="email", time="08:00")
        assert sub.symbols[0].kind == "index"
        assert sub.symbols[0].index_style == "broad"
        assert sub.symbols[1].kind == "auto"

    def test_invalid_kind_rejected(self):
        with pytest.raises(ValidationError):
            Subscription(name="t", symbols=[{"symbol": "600519", "kind": "sms"}],
                         channel="email", time="08:00")

    def test_mixed_string_and_dict(self):
        sub = Subscription(
            name="t",
            symbols=["600519", {"symbol": "000300", "kind": "index"}],
            channel="email", time="08:00")
        assert len(sub.symbols) == 2
        assert sub.symbols[1].kind == "index"
