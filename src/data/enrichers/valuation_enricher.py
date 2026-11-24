"""估值充实器 — 行情+财报推导日频 PE/PB/PS 序列"""
import itertools
import logging
from datetime import timedelta
from statistics import median

from data.akshare import get_total_shares_result
from data.enricher import DataEnricher
from data.schemas import (
    AnalysisContext,
    DailyValuationPoint,
    DimensionSufficiency,
    EnrichedValuation,
    SufficiencyLevel,
    ValuationHistorySummary,
)
from utils.numbers import is_positive_finite

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
            # 非正 TTM 仍是有效财务事实：后续由充实器禁用 PE、保留可计算的 PB。
            return ttm_profit, ttm_revenue

    # 单季数据：最近 4 期求和
    return (sum(f.net_profit for f in recent_4 if f.net_profit is not None),
            sum(f.revenue for f in recent_4 if f.revenue is not None))


class ValuationEnricher(DataEnricher):
    @staticmethod
    def extended_history(ctx: AnalysisContext) -> dict[str, dict]:
        snapshot = ctx.valuation_data
        if snapshot is None or snapshot.history_window_days <= 365:
            return {}
        if not snapshot.history_source.startswith(("百度", "东财")):
            return {}
        cutoff = ctx.collected_at.date()
        start = cutoff - timedelta(days=snapshot.history_window_days)
        source = ("东财日频估值历史" if snapshot.history_source.startswith("东财")
                  else "百度同源实测估值")
        result: dict[str, dict] = {}
        for metric, curve in (("pe", snapshot.extended_pe_history),
                              ("pb", snapshot.extended_pb_history)):
            samples = sorted((day, value) for day, value in curve.items()
                             if start <= day <= cutoff and is_positive_finite(value))
            if not samples:
                continue
            values = [value for _, value in samples]
            result[metric] = {
                "window": snapshot.history_window_label,
                "source": source,
                "start": str(samples[0][0]),
                "end": str(samples[-1][0]),
                "sample_count": len(values),
                "reference": values[-1],
                "percentile": (round(sum(value < values[-1] for value in values)
                                     / len(values) * 100, 1) if len(values) >= 252 else None),
            }
        return result

    def enrich(self, ctx: AnalysisContext) -> AnalysisContext:
        self._fill_derived_ps(ctx)
        if ctx.market in {"hk", "us"} or (ctx.valuation_data is not None and ctx.valuation_data.history_source.startswith(("百度", "东财"))):
            return self._enrich_measured(ctx, ctx.market)
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

        # 只检查实际参与当前 TTM 的期间，无关旧期缺失不影响当前值。
        from data.financial_ttm import financial_ttm

        ttm_profit = financial_ttm(financials, "net_profit", ctx.market, require_precision=True)
        ttm_revenue = financial_ttm(financials, "revenue", ctx.market, require_precision=True)
        if ttm_profit is None or ttm_revenue is None:
            ctx.sufficiency.valuation = DimensionSufficiency(
                level=SufficiencyLevel.INSUFFICIENT,
                reason="财务数据不足，无法计算 TTM 指标",
                sample_count=0, score_weight=0.0,
            )
            return ctx

        # 最近一期净资产：PB 分母优先普通股东权益（剔除永续债，对齐腾讯实测口径）
        latest_fin = max(financials, key=lambda x: x.fiscal_quarter)
        ttm_equity = latest_fin.common_equity if latest_fin.common_equity is not None else latest_fin.total_equity

        if ttm_equity is None or ttm_equity <= 0:
            ctx.sufficiency.valuation = DimensionSufficiency(
                level=SufficiencyLevel.INSUFFICIENT,
                reason="净资产数据缺失，无法计算 PB 估值",
                sample_count=0, score_weight=0.0,
            )
            return ctx

        # 获取总股本（实测锚定优先，TTM 口径复用去累积逻辑）
        if latest_fin.field_precision.get("common_equity" if latest_fin.common_equity is not None else "total_equity") != "source_native":
            ctx.enriched_valuation = None
            ctx.sufficiency.valuation = DimensionSufficiency(
                level=SufficiencyLevel.INSUFFICIENT, reason="财务金额精度不足，不派生估值", score_weight=0)
            return ctx
        total_shares = self._get_total_shares(ctx, ttm_profit, ttm_equity)

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
        valid_pb = [d for d in daily_points if d.pb is not None]
        valid_count = max(len(valid_pe), len(valid_pb))

        if valid_count < 30:
            level = SufficiencyLevel.INSUFFICIENT
            weight = 0.0
            reason = f"有效估值点数仅 {valid_count}（<30），估值分析不可用"
        elif ttm_profit <= 0 and valid_pb:
            level = SufficiencyLevel.PARTIAL
            weight = 0.5
            reason = f"TTM 净利润非正，PE 不适用；PB 有效点数 {len(valid_pb)}"
        elif valid_count >= 120:
            level = SufficiencyLevel.SUFFICIENT
            weight = 1.0
            reason = f"有效估值点数 {valid_count}（≥120），可计算完整历史分位"
        else:
            level = SufficiencyLevel.PARTIAL
            weight = 0.5
            reason = f"有效估值点数 {valid_count}（30–119），仅展示当前值不计算分位"

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

    @staticmethod
    def _enrich_measured(ctx: AnalysisContext, market: str = "hk") -> AnalysisContext:
        """只统计实测同源曲线，不用行情与未知财报币种推导倍数。"""
        market_label = {"hk": "港股", "us": "美股"}.get(market, "A 股")
        snapshot = ctx.valuation_data
        valid = snapshot is not None and any(
            is_positive_finite(value) for value in (snapshot.pe_ttm, snapshot.pb, snapshot.ps_ttm))
        if not valid or snapshot is None:
            ctx.enriched_valuation = None
            ctx.sufficiency.valuation = DimensionSufficiency(
                level=SufficiencyLevel.INSUFFICIENT,
                reason=f"{market_label}实测估值快照缺失，不混用财报币种推导估值",
                sample_count=0, score_weight=0.0)
            return ctx
        prices = {p.trade_date: p for p in ctx.price_data or []
                  if is_positive_finite(p.close) and p.trade_date <= ctx.collected_at.date()}
        points = []
        cutoff = ctx.collected_at.date()
        periods = set(snapshot.pe_history) | set(snapshot.pb_history)
        for period in sorted(periods):
            if not cutoff - timedelta(days=365) <= period <= cutoff:
                continue
            price = prices.get(period)
            pe = snapshot.pe_history.get(period) if snapshot.history_source else None
            pb = snapshot.pb_history.get(period) if snapshot.history_source else None
            pe = pe if is_positive_finite(pe) else None
            pb = pb if is_positive_finite(pb) else None
            if pe is not None or pb is not None:
                points.append(DailyValuationPoint(trade_date=period, close=price.close if price is not None else None, pe=pe, pb=pb))
        enriched = EnrichedValuation(daily_points=points, validated=True)
        for metric in ("pe", "pb"):
            samples = [(p.trade_date, getattr(p, metric)) for p in points if getattr(p, metric) is not None]
            values = [value for _, value in samples]
            summary = ValuationHistorySummary(source=snapshot.history_metric_sources.get(metric, snapshot.history_source), sample_count=len(samples))
            if samples:
                summary.start, summary.as_of = samples[0][0], samples[-1][0]
                summary.reference = values[-1]
                summary.median, summary.low, summary.high = median(values), min(values), max(values)
            if len(samples) >= 120:
                percentile = round(sum(value < values[-1] for value in values) / len(values) * 100, 1)
                setattr(enriched, f"{metric}_percentile", percentile)
            else:
                summary.reason = f"有效日期样本 {len(samples)}，不足 120 个，不计算分位"
            setattr(enriched, f"{metric}_history_summary", summary)
        enriched.pe_median = enriched.pe_history_summary.median
        enriched.pe_low = enriched.pe_history_summary.low
        enriched.pe_high = enriched.pe_history_summary.high
        cap = snapshot.market_cap
        if market == "us" and snapshot.ps_ttm is None and cap is not None and is_positive_finite(cap):
            from data.us_stock import us_ttm

            revenue = us_ttm(ctx.financial_data or []).get("revenue")
            revenue_source = "东财TTM营收"
            latest_fin = max(ctx.financial_data or [], key=lambda row: row.fiscal_quarter, default=None)
            currency_ok = (latest_fin is not None and latest_fin.amount_currency_verified
                           and latest_fin.report_currency == "USD")
            if revenue is None and latest_fin is not None and latest_fin.reported_revenue_ttm is not None:
                revenue = latest_fin.reported_revenue_ttm
                revenue_source = latest_fin.reported_revenue_source or "SEC XBRL营业收入"
                currency_ok = True
            if revenue is not None and is_positive_finite(revenue) and currency_ok:
                snapshot.ps_ttm = cap / revenue
                snapshot.ps_as_of = snapshot.market_cap_as_of
                snapshot.ps_source = f"新浪美股快照市值与{revenue_source}派生"
                snapshot.valuation_notes.append(
                    f"PS(TTM) 派生：总市值 {cap:.0f} ÷ TTM 营收 {revenue:.0f} = {snapshot.ps_ttm:.2f}；"
                    f"市值日期 {snapshot.market_cap_as_of or '未记录'}；"
                    f"TTM 最近四个财季截至 {latest_fin.fiscal_quarter if latest_fin else '未记录'}")
            elif not currency_ok:
                snapshot.valuation_notes.append("PS(TTM) 暂不可用：财报金额币种未核实，不能与美元市值直接相除")
            elif revenue is None:
                snapshot.valuation_notes.append("PS(TTM) 暂不可用：TTM 营收未取得")

        ctx.enriched_valuation = enriched
        complete = enriched.pe_percentile is not None and enriched.pb_percentile is not None
        ctx.sufficiency.valuation = DimensionSufficiency(
            level=SufficiencyLevel.SUFFICIENT if complete else SufficiencyLevel.PARTIAL,
            reason="近一年实测曲线，按各指标对应来源及有效日期计样本；PE/PB 分别以同源末值计算分位" if complete else
                   "仅有快照或部分历史；" + "；".join(s.reason for s in
                   (enriched.pe_history_summary, enriched.pb_history_summary) if s.reason),
            sample_count=max(1, len(points)), score_weight=1.0 if complete else 0.5)
        return ctx

    @staticmethod
    def _fill_derived_ps(ctx: AnalysisContext) -> None:
        """东财 PS 缺失时用已验证市值与 TTM 营收派生；仅展示。"""
        snapshot = ctx.valuation_data
        if snapshot is None or snapshot.ps_ttm is not None or ctx.market != "a-shares":
            return
        from data.financial_ttm import financial_ttm
        revenue = financial_ttm(ctx.financial_data or [], "revenue", "a-shares", require_precision=True)
        if revenue is None or revenue <= 0:
            return
        cap = snapshot.market_cap
        as_of = snapshot.market_cap_as_of or snapshot.date
        if cap is None:
            prices = sorted(ctx.price_data or [], key=lambda item: item.trade_date)
            shares = (snapshot.total_shares if snapshot.total_shares_as_of and snapshot.total_shares_source
                      and is_positive_finite(snapshot.total_shares) else get_total_shares_result(ctx.symbol, ctx.financial_data).value)
            if not prices or not shares or prices[-1].close <= 0:
                return
            cap = prices[-1].close * shares
            as_of = prices[-1].trade_date
        snapshot.ps_ttm = cap / revenue
        snapshot.ps_as_of = as_of
        snapshot.ps_source = "总市值 ÷ 同报告期TTM营收（派生，仅参考）"

    def _get_total_shares(self, ctx: AnalysisContext, ttm_profit: float,
                          ttm_equity: float) -> float | None:
        """总股本：实测 PE/PB 反推锚点优先，其次三级链。"""
        snapshot = ctx.valuation_data
        if snapshot and snapshot.total_shares_as_of and snapshot.total_shares_source and is_positive_finite(snapshot.total_shares):
            return snapshot.total_shares
        # 实测锚定：估值数据来自腾讯快照（独立口径），锚定计算只用内存数据
        measured_pe = ctx.valuation_data.pe_ttm if ctx.valuation_data else None
        if measured_pe is not None and is_positive_finite(measured_pe):
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
        measured_pb = ctx.valuation_data.pb if ctx.valuation_data else None
        if measured_pb is not None and is_positive_finite(measured_pb):
            sorted_prices = sorted(ctx.price_data or [], key=lambda x: x.trade_date)
            if sorted_prices and sorted_prices[-1].close > 0:
                return measured_pb * ttm_equity / sorted_prices[-1].close
        # 无实测 PE（快照失败）：三级链估算
        return get_total_shares_result(ctx.symbol, ctx.financial_data).value
