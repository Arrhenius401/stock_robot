"""单指数报告市场能力与展示口径。"""
from dataclasses import dataclass

_BASE = frozenset({"overview", "performance", "technical", "valuation"})
_FACTS = ("technical", "valuation")
_PLACEHOLDERS = (
    ("capital", "当前指数不适用已接入的资金流向指标。", "资金数据不可用。"),
    ("macro", "当前指数没有已适配的宏观指标，不以国内环境代替海外环境。", "宏观数据不可用。"),
    ("sentiment", "未接入适用新闻源。", "暂无可核实的新闻。"),
)
_LIMITATION = "估值分位受采样窗口限制，价格位置仅描述区间内位置。市场背景仅供参考。"


@dataclass(frozen=True)
class IndexProfile:
    key: str
    label: str
    market: str
    style: str | None
    sections: frozenset[str]
    dimensions: tuple[str, ...]
    capital_kind: str = "none"
    macro_kind: str = "none"
    news_kind: str = "none"
    valuation_kind: str = "index"
    limitation: str = _LIMITATION

    @property
    def quality_sections(self) -> frozenset[str]:
        supported = {"technical", "valuation", "strategy"}
        supported.update(key for key, kind in (
            ("capital", self.capital_kind), ("macro", self.macro_kind),
            ("sentiment", self.news_kind),
        ) if kind != "none")
        return self.sections & supported

    @property
    def placeholders(self) -> dict[str, dict[str, str]]:
        return {
            key: {"markdown": markdown, "unavailable": unavailable, "web": "当前指数不适用或未接入该项数据。"}
            for key, markdown, unavailable in _PLACEHOLDERS
        }

    def report_payload(self) -> dict:
        return {"key": self.key, "label": self.label, "sections": sorted(self.sections),
                "placeholders": self.placeholders, "limitation": self.limitation}


def profile_for(market: str, style: str | None) -> IndexProfile:
    if market == "hk":
        return IndexProfile("hk", "港股指数", market, style, _BASE | {"capital", "macro", "sentiment"},
                            (*_FACTS, "capital", "macro", "sentiment"),
                            "southbound_background", "hk", "market_search", "hk_official")
    if style == "strategy":
        return IndexProfile("a-strategy", "A 股策略指数", market, style, _BASE | {"strategy"},
                            (*_FACTS, "strategy"), valuation_kind="strategy_official")
    if style == "sector":
        return IndexProfile("a-sector", "A 股行业指数", market, style, _BASE | {"capital", "sentiment"},
                            (*_FACTS, "capital", "sentiment"), "northbound", news_kind="a_market")
    if style == "broad":
        return IndexProfile("a-broad", "A 股宽基指数", market, style, _BASE | {"capital", "macro", "sentiment"},
                            (*_FACTS, "capital", "macro", "sentiment"), "northbound", "cn", "a_market")
    if market == "us":
        return IndexProfile("us", "美股指数", market, style, _BASE | {"macro", "sentiment"},
                            (*_FACTS, "macro", "sentiment"), macro_kind="us",
                            news_kind="market_search", valuation_kind="unsupported")
    if style == "overseas":
        return IndexProfile("overseas", "海外指数", market, style, _BASE | {"macro", "sentiment"},
                            _FACTS, valuation_kind="unsupported")
    return IndexProfile("a-default", "A 股指数", market, style, _BASE | {"sentiment"},
                        (*_FACTS, "sentiment"), news_kind="a_market")
