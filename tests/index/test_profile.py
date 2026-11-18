"""七类指数报告的章节、研判维度及旧数据质量口径。"""
import pytest

from index.profile import profile_for

BASE = {"overview", "performance", "technical", "valuation"}
FACTS = ("technical", "valuation")


@pytest.mark.parametrize(("market", "style", "key", "sections", "dimensions"), [
    ("a-shares", "broad", "a-broad", {"capital", "macro", "sentiment"}, (*FACTS, "capital", "macro", "sentiment")),
    ("a-shares", "sector", "a-sector", {"capital", "sentiment"}, (*FACTS, "capital", "sentiment")),
    ("a-shares", "strategy", "a-strategy", {"strategy"}, (*FACTS, "strategy")),
    ("a-shares", None, "a-default", {"sentiment"}, (*FACTS, "sentiment")),
    ("a-shares", "unknown", "a-default", {"sentiment"}, (*FACTS, "sentiment")),
    ("hk", "overseas", "hk", {"capital", "macro", "sentiment"}, (*FACTS, "capital", "macro", "sentiment")),
    ("us", "overseas", "us", {"macro", "sentiment"}, (*FACTS, "macro", "sentiment")),
    ("other", "overseas", "overseas", {"macro", "sentiment"}, FACTS),
])
def test_index_profile_matrix(market, style, key, sections, dimensions):
    profile = profile_for(market, style)
    assert profile.key == key
    assert profile.sections == BASE | sections
    assert profile.dimensions == dimensions
    assert profile.market == market
    assert profile.style == style
