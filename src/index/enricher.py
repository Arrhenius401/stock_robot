"""指数充实器 — 分位计算、标签映射，计算结果不入缓存"""
from data.schemas import IndexAnalysisContext


def compute_percentile(current: float, historical: list[float]) -> float | None:
    """计算当前值在历史序列中的分位（0-100），值越小分位越低

    分位 = 历史序列中 <= 当前值的样本占比，使中位数对应约 50 分位
    """
    if not historical or current is None:
        return None
    if len(historical) == 1:
        return 50.0
    below = sum(1 for v in historical if v <= current)
    return round((below / len(historical)) * 100, 1)


def tag_valuation(pe_percentile: float | None) -> str:
    """PE 分位 → 估值标签"""
    if pe_percentile is None:
        return "invalid"
    if pe_percentile < 30:
        return "undervalued"
    elif pe_percentile > 70:
        return "overvalued"
    return "neutral"


def tag_technical(ma_5: float | None, ma_20: float | None, close: float | None) -> str:
    """均线系统 → 趋势标签"""
    if close is None:
        return "shake"
    if ma_5 is not None and ma_20 is not None:
        if close > ma_5 > ma_20:
            return "bull"
        elif close < ma_5 < ma_20:
            return "bear"
    return "shake"


def tag_capital(north_bound: float | None, main_inflow: float | None) -> str:
    """资金流向 → 资金面标签"""
    positive = 0
    if north_bound is not None and north_bound > 0:
        positive += 1
    if main_inflow is not None and main_inflow > 0:
        positive += 1
    if north_bound is None and main_inflow is None:
        return "neutral"
    if positive >= 1:
        return "positive"
    return "negative"


class IndexValuationEnricher:
    """估值分位充实器 — 从日频 PE/PB 序列计算分位并回填元数据"""

    def enrich(self, ctx: IndexAnalysisContext,
               daily_pe_values: list[float] | None = None,
               daily_pb_values: list[float] | None = None) -> IndexAnalysisContext:
        if ctx.valuation_data is None:
            return ctx

        val = ctx.valuation_data

        if daily_pe_values and val.pe_ttm is not None:
            val.pe_percentile = compute_percentile(val.pe_ttm, daily_pe_values)
        if daily_pb_values and val.pb is not None:
            val.pb_percentile = compute_percentile(val.pb, daily_pb_values)

        # 分位元数据
        if daily_pe_values or daily_pb_values:
            all_vals = (daily_pe_values or []) + (daily_pb_values or [])
            if len(all_vals) < 252:  # 不足 1 年交易日
                val.valuation_valid = False

        return ctx
