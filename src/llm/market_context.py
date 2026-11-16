"""公共研报提示词的市场规则；报告分类区别说明不传给模型。"""
from typing import Any

from data.schemas import AnalysisContext


def market_prompt_context(ctx: AnalysisContext | None) -> dict[str, Any]:
    market = ctx.market if ctx is not None else "a-shares"
    industry = ctx.industry_data.industry if ctx is not None and ctx.industry_data else "行业数据不足"
    if market == "us":
        return {"market": market, "market_label": "美股", "industry_name": industry,
            "industry_scheme": "东财美股 F10 行业（BELONG_INDUSTRY）", "hk_amount_guard": False,
            "market_rules": "美股规则：上市报价为 USD；财报按财季和已记录的美国会计准则证据解读，缺少币种或会计准则证据时明确未核实，不能以上市地推断。行业经营比较仅 TTM 最新期，不虚构年度同行经营分；只解读程序给出的两项经营指标及样本数，财务 partial 必须说明可用子项和缺项。行业经营比较为 TTM 派生，未经核实的营收和净利率不得补造，不能混作年度或最新财报期。舆情本期未计分。统一使用中文‘自由现金流’。"}
    if market == "hk":
        return {"market": market, "market_label": "港股", "industry_name": industry,
            "industry_scheme": "恒生HSICS", "hk_amount_guard": True,
            "market_rules": "港股规则：行业使用恒生HSICS，不套用申万分类；只解读程序已计算的行业分和综合分。财务金额按数据源返回值展示，目录列报币种不能证明金额币种，不得声称上游未换汇。金额币种未核实时明确说明，不作金额同比、现金流趋势或与港币行情混算。按报告期分析，不能把半年度/年度数据当季度或推导未经验证的 TTM、历史分位。"}
    return {"market": market, "market_label": "A 股", "industry_scheme": "申万一级行业",
            "industry_name": ctx.sw_industry if ctx is not None else "", "hk_amount_guard": False,
            "market_rules": "A 股规则：申万行业口径；按已记录的财报原币、会计准则及报告期间解读，缺失不推断。"}


def prompt_metrics(metrics: dict[str, Any], market: str) -> dict[str, Any]:
    """分类区别仅供报告展示，避免把其他市场分类词注入美股提示词。"""
    if market != "us":
        return metrics
    return {key: value for key, value in metrics.items() if key not in {
        "industry_basis", "industry_scheme_note", "classification_scheme"}}
