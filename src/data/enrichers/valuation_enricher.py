"""估值充实器 — 行情+财报推导日频 PE/PB/PS 序列"""
import itertools
import logging
from statistics import median

from data.akshare import get_total_shares
from data.enricher import DataEnricher
from data.schemas import (
    AnalysisContext,
    DailyValuationPoint,
    DimensionSufficiency,
    EnrichedValuation,
    SufficiencyLevel,
)

logger = logging.getLogger(__name__)


def _estimate_shares_offline(financials: list | None) -> float | None:
    """离线估算总股本：最新一期 net_profit / basic_eps（纯内存，不触发网络）"""
    sorted_fin = sorted(financials or [], key=lambda x: x.fiscal_quarter)
    if not sorted_fin:
        return None
    latest = sorted_fin[-1]
    if latest.net_profit is not None and latest.net_profit > 0 \
            and latest.basic_eps is not None and latest.basic_eps > 0:
        return latest.net_profit / latest.basic_eps
    return None


def _is_cumulative_yoy(sorted_fin: list) -> bool:
    """同年内相邻期营收严格递增（累计 YTD 特征），排除单季/平坦数据"""
    latest_year = sorted_fin[-1].fiscal_quarter.year
    same_year = [f for f in sorted_fin if f.fiscal_quarter.year == latest_year]
    vals = [f.revenue for f in same_year if f.revenue is not None]
    return len(vals) >= 2 and all(b > a for a, b in itertools.pairwise(vals))


def _compute_ttm(financials: list) -> tuple[float | None, float | None]:
    """累计 YTD / 单季自适应的 TTM 净利润与营收

    A 股财报为累计 YTD。跨年序列 [Q3'25(9月), Q4'25(12月), Q1'26(3月), Q2'26(6月)]
    中 Q1 累计 < 上年 Q4 累计，4 期严格递增判据会误判为单季导致 TTM 放大近 4 倍。
    有去年同期对齐（≥5 期、同期月份匹配、同年累计特征）时：
        TTM = 最新累计 + 去年全年 − 去年同期累计
    否则：4 期严格递增 → 去累积求和；单季数据 → 最近 4 期直接求和。
    """
    sorted_fin = sorted(financials, key=lambda x: x.fiscal_quarter)
    if len(sorted_fin) < 4:
        return None, None
    recent_4 = sorted_fin[-4:]
    revs = [f.revenue for f in recent_4 if f.revenue is not None]
    profits = [f.net_profit for f in recent_4 if f.net_profit is not None]

    # 4 期严格递增 → 累计去累积：Q1, Q2-Q1, Q3-Q2, Q4-Q3
    if (len(revs) == 4 and len(profits) == 4
            and revs[0] > 0 and revs[1] > revs[0] and revs[2] > revs[1] and revs[3] > revs[2]):
        return (profits[0] + (profits[1] - profits[0]) + (profits[2] - profits[1]) + (profits[3] - profits[2]),
                revs[0] + (revs[1] - revs[0]) + (revs[2] - revs[1]) + (revs[3] - revs[2]))

    # 跨年累计对齐：最新期与去年同期（往前 4 期）月份匹配、同年累计特征、有去年 Q4
    latest = sorted_fin[-1]
    same_q_prev = sorted_fin[-5] if len(sorted_fin) >= 5 else None
    if (same_q_prev is not None and latest.fiscal_quarter.month == same_q_prev.fiscal_quarter.month
            and _is_cumulative_yoy(sorted_fin)):
        prev_year_q4 = next(
            (f for f in sorted_fin
             if f.fiscal_quarter.year == latest.fiscal_quarter.year - 1
             and f.fiscal_quarter.month == 12),
            None,
        )
        if (prev_year_q4 is not None and latest.net_profit is not None
                and prev_year_q4.net_profit is not None and same_q_prev.net_profit is not None
                and latest.revenue is not None and prev_year_q4.revenue is not None
                and same_q_prev.revenue is not None):
            ttm_profit = latest.net_profit + prev_year_q4.net_profit - same_q_prev.net_profit
            ttm_revenue = latest.revenue + prev_year_q4.revenue - same_q_prev.revenue
            if ttm_profit > 0:
                return ttm_profit, ttm_revenue
            return None, None  # 对齐 TTM 非正：财务异常，交由调用方判 INSUFFICIENT

    # 单季数据：最近 4 期求和
    return (sum(f.net_profit for f in recent_4 if f.net_profit is not None),
            sum(f.revenue for f in recent_4 if f.revenue is not None))


class ValuationEnricher(DataEnricher):
    def enrich(self, ctx: AnalysisContext) -> AnalysisContext:
        prices = ctx.price_data or []
        financials = ctx.financial_data or []

        # 前置条件检查：行情数据必须充足
        if ctx.sufficiency and ctx.sufficiency.price.level == SufficiencyLevel.INSUFFICIENT:
            ctx.sufficiency.valuation = DimensionSufficiency(
                level=SufficiencyLevel.INSUFFICIENT, reason="行情数据不足，无法推导估值序列",
                sample_count=0, score_weight=0.0,
            )
            return ctx

        # 前置条件：至少 4 期财报
        if len(financials) < 4:
            ctx.sufficiency.valuation = DimensionSufficiency(
                level=SufficiencyLevel.INSUFFICIENT,
                reason=f"仅 {len(financials)} 期财报，无法计算 TTM 指标",
                sample_count=0, score_weight=0.0,
            )
            return ctx

        # 从最近财报计算 TTM（累计 YTD / 单季自适应）
        ttm_profit, ttm_revenue = _compute_ttm(financials)
        if ttm_profit is None or ttm_revenue is None:
            ctx.sufficiency.valuation = DimensionSufficiency(
                level=SufficiencyLevel.INSUFFICIENT,
                reason="财务数据不足，无法计算 TTM 指标",
                sample_count=0, score_weight=0.0,
            )
            return ctx

        # 最近一期净资产：PB 分母优先普通股东权益（剔除永续债，对齐腾讯实测口径）
        latest_fin = max(financials, key=lambda x: x.fiscal_quarter)
        ttm_equity = latest_fin.common_equity or latest_fin.total_equity

        if ttm_profit <= 0 or ttm_equity is None or ttm_equity <= 0:
            ctx.sufficiency.valuation = DimensionSufficiency(
                level=SufficiencyLevel.INSUFFICIENT,
                reason="TTM 净利润为负或净资产数据缺失，无法计算有效估值",
                sample_count=0, score_weight=0.0,
            )
            return ctx

        # 获取总股本（实测锚定优先，TTM 口径复用去累积逻辑）
        total_shares = self._get_total_shares(ctx, ttm_profit)

        # 逐日推导估值
        sorted_prices = sorted(prices, key=lambda x: x.trade_date)
        daily_points = []
        for p in sorted_prices:
            if total_shares is None or total_shares <= 0:
                continue
            market_cap = p.close * total_shares
            pe = market_cap / ttm_profit if ttm_profit > 0 else None
            pb = market_cap / ttm_equity if ttm_equity > 0 else None
            ps = market_cap / ttm_revenue if ttm_revenue and ttm_revenue > 0 else None

            # 剔除异常：PE 为负或 PE>200 视为异常点
            if pe is not None and (pe <= 0 or pe > 200):
                pe = None
            if pb is not None and pb <= 0:
                pb = None

            daily_points.append(DailyValuationPoint(
                trade_date=p.trade_date, close=p.close, pe=pe, pb=pb, ps=ps,
            ))

        # 统计有效点
        valid_pe = [d for d in daily_points if d.pe is not None]
        valid_count = len(valid_pe)

        if valid_count >= 120:
            level = SufficiencyLevel.SUFFICIENT
            weight = 1.0
            reason = f"有效估值点数 {valid_count}（≥120），可计算完整历史分位"
        elif valid_count >= 30:
            level = SufficiencyLevel.PARTIAL
            weight = 0.5
            reason = f"有效估值点数 {valid_count}（30–119），仅展示当前值不计算分位"
        else:
            level = SufficiencyLevel.INSUFFICIENT
            weight = 0.0
            reason = f"有效估值点数仅 {valid_count}（<30），估值分析不可用"

        pe_values = sorted([d.pe for d in valid_pe])

        pe_current = valid_pe[-1].pe if valid_pe else None
        pe_percentile = None
        pb_percentile = None
        pe_zone = ""
        if pe_current is not None and len(pe_values) >= 120:
            below = sum(1 for p in pe_values if p < pe_current)
            pe_percentile = round(below / len(pe_values) * 100, 1)
            if pe_percentile < 30:
                pe_zone = "低估"
            elif pe_percentile <= 70:
                pe_zone = "中性"
            else:
                pe_zone = "高估"

        # PB 分位
        valid_pb = [d for d in daily_points if d.pb is not None]
        pb_values = sorted([d.pb for d in valid_pb])
        pb_current = valid_pb[-1].pb if valid_pb else None
        if pb_current is not None and len(pb_values) >= 120:
            below_pb = sum(1 for p in pb_values if p < pb_current)
            pb_percentile = round(below_pb / len(pb_values) * 100, 1)

        ctx.enriched_valuation = EnrichedValuation(
            daily_points=daily_points,
            pe_percentile=pe_percentile,
            pb_percentile=pb_percentile,
            pe_zone=pe_zone,
            pe_median=median(pe_values) if pe_values else None,
            pe_high=max(pe_values) if pe_values else None,
            pe_low=min(pe_values) if pe_values else None,
            validated=(
                ctx.valuation_data is not None
                and ctx.valuation_data.pe_ttm is not None
                and ctx.valuation_data.pe_ttm > 0
            ),
        )

        ctx.sufficiency.valuation = DimensionSufficiency(
            level=level, reason=reason, sample_count=valid_count, score_weight=weight,
        )
        return ctx

    def _get_total_shares(self, ctx: AnalysisContext, ttm_profit: float) -> float | None:
        """总股本：实测 PE 反推锚点优先，其次三级链"""
        # 实测锚定：估值数据来自腾讯快照（独立口径），锚定计算只用内存数据
        measured_pe = ctx.valuation_data.pe_ttm if ctx.valuation_data else None
        if measured_pe is not None and measured_pe > 0:
            sorted_prices = sorted(ctx.price_data or [], key=lambda x: x.trade_date)
            if sorted_prices:
                latest_close = sorted_prices[-1].close
                if latest_close > 0:
                    anchor = measured_pe * ttm_profit / latest_close
                    if anchor > 0:
                        # 偏差告警仅用离线估算，锚定路径不触发网络请求
                        estimated = _estimate_shares_offline(ctx.financial_data)
                        if estimated and abs(estimated - anchor) / anchor > 0.15:
                            logger.warning(
                                f"{ctx.symbol} 总股本估算偏差 >15%: 估算 {estimated:.2e} vs 锚定 {anchor:.2e}"
                            )
                        return anchor
        # 无实测 PE（快照失败）：三级链估算
        return get_total_shares(ctx.symbol, ctx.financial_data)
