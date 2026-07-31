"""估值充实器 — 行情+财报推导日频 PE/PB/PS 序列"""
import logging
from statistics import median
from data.enricher import DataEnricher
from data.schemas import (
    AnalysisContext, DimensionSufficiency, SufficiencyLevel,
    EnrichedValuation, DailyValuationPoint,
)

logger = logging.getLogger(__name__)


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

        # 从最近 4 期财报计算 TTM 值
        sorted_fin = sorted(financials, key=lambda x: x.fiscal_quarter)
        recent_4 = sorted_fin[-4:]

        # 检查数据是否表现为累计 YTD（每季度营收递增）
        # 若为累计数据则去累积：Q1, Q2-Q1, Q3-Q2, Q4-Q3
        revs = [f.revenue for f in recent_4 if f.revenue is not None]
        profits = [f.net_profit for f in recent_4 if f.net_profit is not None]
        looks_accumulated = (
            len(revs) == 4 and len(profits) == 4
            and revs[0] > 0 and revs[1] > revs[0] and revs[2] > revs[1] and revs[3] > revs[2]
        )
        if looks_accumulated:
            deacc_profits = [profits[0], profits[1] - profits[0],
                           profits[2] - profits[1], profits[3] - profits[2]]
            deacc_revs = [revs[0], revs[1] - revs[0],
                         revs[2] - revs[1], revs[3] - revs[2]]
            ttm_profit = sum(deacc_profits)
            ttm_revenue = sum(deacc_revs)
        else:
            ttm_profit = sum(f.net_profit for f in recent_4 if f.net_profit is not None)
            ttm_revenue = sum(f.revenue for f in recent_4 if f.revenue is not None)

        ttm_equity = recent_4[-1].total_equity  # 最近一期净资产

        if ttm_profit <= 0 or ttm_equity is None or ttm_equity <= 0:
            ctx.sufficiency.valuation = DimensionSufficiency(
                level=SufficiencyLevel.INSUFFICIENT,
                reason="TTM 净利润为负或净资产数据缺失，无法计算有效估值",
                sample_count=0, score_weight=0.0,
            )
            return ctx

        # 获取总股本
        total_shares = self._get_total_shares(ctx)

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
        )

        ctx.sufficiency.valuation = DimensionSufficiency(
            level=level, reason=reason, sample_count=valid_count, score_weight=weight,
        )
        return ctx

    def _get_total_shares(self, ctx: AnalysisContext) -> float | None:
        """获取总股本"""
        try:
            from data.akshare import get_individual_info
            from utils.numbers import parse_cn_number
            info = get_individual_info(ctx.symbol)
            for key, val in info.items():
                if "总股本" in str(key):
                    return parse_cn_number(str(val))
        except Exception:
            pass
        # 回退：从最近一期财报反推
        financials = sorted(ctx.financial_data or [], key=lambda x: x.fiscal_quarter)
        if financials:
            latest = financials[-1]
            if latest.total_equity and latest.total_equity > 0:
                if ctx.valuation_data and ctx.valuation_data.pb and ctx.valuation_data.pb > 0:
                    sorted_prices = sorted(ctx.price_data or [], key=lambda x: x.trade_date)
                    if sorted_prices:
                        avg_price = sum(p.close for p in sorted_prices[-20:]) / min(20, len(sorted_prices))
                        if avg_price > 0:
                            return latest.total_equity * ctx.valuation_data.pb / avg_price
        return None
