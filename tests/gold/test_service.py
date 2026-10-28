"""黄金服务边界：保持事实不受 AI 影响，归档与正文绑定。"""
from types import SimpleNamespace

import pytest

from gold.models import GoldRules
from gold.service import is_gold_request, load_rules


@pytest.mark.parametrize("text", ["黄金", "GOLD", "Au99.99", "XAUUSD", "请分析一下黄金", "国际黄金值得买吗？",
                                  "黄金价格", "今天金价", "分析国际金价"])
def test_gold_aliases(text):
    assert is_gold_request(text)


@pytest.mark.parametrize("text", ["山东黄金", "分析山东黄金600547", "黄金ETF", "黄金股票有哪些", "黄金首饰价格", ""])
def test_stock_and_product_names_do_not_route_to_gold(text):
    assert not is_gold_request(text)


def test_unknown_rules_rejected():
    with pytest.raises(ValueError):
        load_rules(SimpleNamespace(get=lambda *_: {"unknown_rule": 1}))


@pytest.mark.parametrize("settings", [{"rate_threshold_bp": float("inf")},
    {"source_timeout_seconds": 31}, {"total_timeout_seconds": 61},
    {"significant_low_percentile": 90}, {"reserve_marginal_months": 6}, {"annual_days": 1}])
def test_rules_reject_invalid_budgets_and_thresholds(settings):
    with pytest.raises(ValueError):
        GoldRules(**settings)
