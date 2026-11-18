"""公共研报提示词的市场规则；报告分类区别说明不传给模型。"""
from typing import Any

from data.schemas import AnalysisContext
from report.market_profile import profile_for


def market_prompt_context(ctx: AnalysisContext | None) -> dict[str, Any]:
    market = ctx.market if ctx is not None else "a-shares"
    profile = profile_for(market)
    industry = ctx.industry_data.industry if ctx is not None and ctx.industry_data else "行业数据不足"
    if market == "us":
        return {"market": market, "market_label": "美股", "industry_name": industry,
            "industry_scheme": "东财美股 F10 行业（BELONG_INDUSTRY）", "hk_amount_guard": False,
            "market_rules": profile.prompt_rules}
    if market == "hk":
        return {"market": market, "market_label": "港股", "industry_name": industry,
            "industry_scheme": "恒生HSICS", "hk_amount_guard": True,
            "market_rules": profile.prompt_rules}
    return {"market": market, "market_label": "A 股", "industry_scheme": "申万一级行业",
            "industry_name": ctx.sw_industry if ctx is not None else "", "hk_amount_guard": False,
            "market_rules": profile.prompt_rules}


def prompt_metrics(metrics: dict[str, Any], market: str) -> dict[str, Any]:
    """分类区别仅供报告展示，避免把其他市场分类词注入美股提示词。"""
    if market != "us":
        return metrics
    return {key: value for key, value in metrics.items() if key not in {
        "industry_basis", "industry_scheme_note", "classification_scheme"}}
