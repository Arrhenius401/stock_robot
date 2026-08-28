"""通用计分器 — 纯 YAML 驱动，无硬编码阈值"""
from dataclasses import dataclass
from statistics import mean

from analysis.scorers.base import BaseScorer
from data.schemas import AnalysisContext


def _ema_line(data: list[float], period: int) -> list[float]:
    if not data:
        return []
    multiplier = 2 / (period + 1)
    ema = data[0]
    line = [ema]
    for value in data[1:]:
        ema = (value - ema) * multiplier + ema
        line.append(ema)
    return line


@dataclass(frozen=True, slots=True)
class MacdValues:
    """MACD 结果值对象。"""

    dif: float
    dea: float
    bar: float


def calculate_macd(closes: list[float]) -> MacdValues:
    """根据完整收盘价序列计算 MACD。"""

    if not closes:
        return MacdValues(dif=0.0, dea=0.0, bar=0.0)

    ema12_line = _ema_line(closes, 12)
    ema26_line = _ema_line(closes, 26)
    dif_line = [ema12 - ema26 for ema12, ema26 in zip(ema12_line, ema26_line)]
    dea_line = _ema_line(dif_line, 9)
    dif = dif_line[-1]
    dea = dea_line[-1]
    return MacdValues(dif=dif, dea=dea, bar=2 * (dif - dea))


class GeneralScorer(BaseScorer):
    """通用计分器 — 为每个维度提供独立的计分方法，全部参数从 YAML 读取。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._method_map = {
            "financial": self.score_financial,
            "valuation": self.score_valuation,
            "industry": self.score_industry,
            "technical": self.score_technical,
            "sentiment": self.score_sentiment,
        }

    @property
    def dimension(self) -> str:
        return "general"

    def score(self, context: AnalysisContext) -> tuple[float, str, list[str]]:
        raise NotImplementedError("使用 score_dimension(dim, context) 按维度计分")

    def score_dimension(
        self, dim: str, context: AnalysisContext
    ) -> tuple[float, str, list[str]]:
        method = self._method_map.get(dim)
        if method is None:
            return 0.0, f"未知维度: {dim}", []
        return method(context)

    # —— 财务维度 ——
    def score_financial(self, ctx: AnalysisContext) -> tuple[float, str, list[str]]:
        cfg = self.config.get("financial", {})
        if not cfg.get("enabled", True):
            return 0.0, "财务维度已禁用", []
        financials = ctx.financial_data or []
        if not financials:
            return 0.0, "财务数据不可用", []
        sorted_data = sorted(financials, key=lambda x: x.fiscal_quarter, reverse=True)
        latest = sorted_data[0]

        total = 0.0
        details = []
        risks = []

        # ROE
        roe_cfg = cfg.get("roe", {})
        if roe_cfg.get("enabled", True) and not roe_cfg.get("use_custom_calc"):
            s, _ = self._tier_score(latest.roe, roe_cfg.get("tiers", []))
            max_s = roe_cfg.get("max_score", 3)
            total += s
            label = f"{latest.roe*100:.1f}%" if latest.roe is not None else "缺失"
            details.append(f"ROE {label}，得 {s}/{max_s} 分")
            if (latest.roe is not None and latest.roe < 0
                    and roe_cfg.get("extra_config", {}).get("negative_to_zero")):
                risks.append("roe_low")

        # 资产负债率
        debt_cfg = cfg.get("debt_ratio", {})
        if debt_cfg.get("enabled", True) and not debt_cfg.get("use_custom_calc"):
            asset_liability = None
            if latest.total_assets and latest.total_equity and latest.total_equity > 0:
                asset_liability = (1 - latest.total_equity / latest.total_assets) * 100
            s, _ = self._range_score(asset_liability, debt_cfg.get("tiers", []))
            max_s = debt_cfg.get("max_score", 2)
            total += s
            label = f"{asset_liability:.0f}%" if asset_liability is not None else "缺失"
            details.append(f"资产负债率 {label}，得 {s}/{max_s} 分")
            extreme = debt_cfg.get("extra_config", {}).get("extreme_threshold", 90)
            if asset_liability is not None and asset_liability > extreme:
                risks.append("high_debt")

        # 经营现金流匹配
        cf_cfg = cfg.get("cashflow_match", {})
        if cf_cfg.get("enabled", True):
            np_val = latest.net_profit
            ocf = latest.operating_cash_flow
            neg_zero = cf_cfg.get("extra_config", {}).get("negative_profit_zero")
            if np_val is not None and np_val <= 0 and neg_zero:
                details.append("净利润为负，现金流匹配 0 分")
                risks.append("cash_flow_mismatch")
            elif ocf is not None and np_val is not None and np_val > 0:
                ratio = ocf / np_val
                s, _ = self._tier_score(ratio, cf_cfg.get("tiers", []))
                max_s = cf_cfg.get("max_score", 3)
                total += s
                details.append(f"经营现金流/净利润 {ratio:.2f}，得 {s}/{max_s} 分")
                if ratio < 0.5:
                    risks.append("cash_flow_mismatch")

        # 毛利率稳定性
        gm_cfg = cfg.get("gross_margin_stability", {})
        if gm_cfg.get("enabled", True):
            gms = [d.gross_margin for d in sorted_data[:4] if d.gross_margin is not None]
            if gms:
                if any(g < 0 for g in gms) and gm_cfg.get("extra_config", {}).get("negative_gm_zero"):
                    details.append("存在负毛利率，得 0 分")
                else:
                    gm_range = max(gms) - min(gms) if len(gms) >= 2 else 0
                    s = 0
                    for tier in gm_cfg.get("tiers", []):
                        max_vol = tier.get("max_vol_pp", float("inf"))
                        if gm_range * 100 <= max_vol:
                            s = tier["score"]
                            break
                    max_s = gm_cfg.get("max_score", 2)
                    total += s
                    details.append(f"近4期毛利率波动 {gm_range*100:.1f}pp，得 {s}/{max_s} 分")

        return round(total, 1), "；".join(details), risks

    # —— 估值维度 ——
    def score_valuation(self, ctx: AnalysisContext) -> tuple[float, str, list[str]]:
        cfg = self.config.get("valuation", {})
        if not cfg.get("enabled", True):
            return 0.0, "估值维度已禁用", []
        ev = ctx.enriched_valuation
        ei = ctx.enriched_industry

        total = 0.0
        details = []
        risks = []

        # PE 历史分位
        pe_cfg = cfg.get("pe_percentile", {})
        if pe_cfg.get("enabled", True):
            pe_pct = ev.pe_percentile if ev else None
            reverse = pe_cfg.get("percentile_reverse", False)
            s, _ = self._tier_score(pe_pct, pe_cfg.get("tiers", []), reverse=reverse)
            max_s = pe_cfg.get("max_score", 4)
            total += s
            pct_label = f"{pe_pct:.0f}%" if pe_pct is not None else "缺失"
            details.append(f"PE 分位 {pct_label}，得 {s}/{max_s} 分")
            if pe_pct is not None and pe_pct > 90:
                risks.append("high_pe_premium")

        # PB 历史分位
        pb_cfg = cfg.get("pb_percentile", {})
        if pb_cfg.get("enabled", True):
            pb_pct = ev.pb_percentile if ev else None
            pb_val = ctx.valuation_data.pb if ctx.valuation_data else None
            if pb_val is not None and pb_val <= 0:
                details.append("PB 为负，得 0 分")
            elif pb_pct is not None:
                s, _ = self._tier_score(pb_pct, pb_cfg.get("tiers", []))
                max_s = pb_cfg.get("max_score", 2)
                total += s
                details.append(f"PB 分位 {pb_pct:.0f}%，得 {s}/{max_s} 分")

        # 行业溢价
        prem_cfg = cfg.get("industry_premium", {})
        if prem_cfg.get("enabled", True):
            premium = ei.target_pe_premium if ei else None
            s, _ = self._tier_score(premium, prem_cfg.get("tiers", []))
            max_s = prem_cfg.get("max_score", 4)
            total += s
            label = f"{premium:.0f}%" if premium is not None else "缺失"
            details.append(f"行业溢价 {label}，得 {s}/{max_s} 分")

        return round(total, 1), "；".join(details), risks

    # —— 行业维度 ——
    def score_industry(self, ctx: AnalysisContext) -> tuple[float, str, list[str]]:
        cfg = self.config.get("industry", {})
        if not cfg.get("enabled", True):
            return 0.0, "行业维度已禁用", []
        ei = ctx.enriched_industry

        score = float(cfg.get("base_score", 5))
        details = [f"基础分 {score}"]
        risks: list[str] = []

        if ei:
            peer_bonus = cfg.get("peer_count_bonus", {})
            if ei.peer_count >= 8:
                score += peer_bonus.get("sufficient", 2)
                details.append(f"同行 {ei.peer_count} 家（充足），+{peer_bonus.get('sufficient', 2)} 分")
            elif ei.peer_count >= 3:
                score += peer_bonus.get("partial", 1)
                details.append(f"同行 {ei.peer_count} 家（偏少），+{peer_bonus.get('partial', 1)} 分")

            rank_bonus = cfg.get("market_cap_rank_bonus", {})
            if ei.target_market_cap_rank is not None and ei.target_market_cap_rank <= 5:
                score += rank_bonus.get("top", 2)
                details.append(f"市值第 {ei.target_market_cap_rank} 位（头部），+{rank_bonus.get('top', 2)} 分")
            elif ei.target_market_cap_rank is not None:
                score += rank_bonus.get("ranked", 1)
                details.append(f"市值第 {ei.target_market_cap_rank} 位，+{rank_bonus.get('ranked', 1)} 分")

            # 毛利率对比
            if ei.industry_median_gross_margin is not None and ctx.financial_data:
                latest_gm = ctx.financial_data[0].gross_margin if ctx.financial_data else None
                if latest_gm is not None:
                    diff = (latest_gm - ei.industry_median_gross_margin) * 100
                    if diff > 5:
                        score += 1
                        details.append(f"毛利率高于行业 {diff:.0f}pp，+1 分")
                    elif diff < -5:
                        details.append(f"毛利率低于行业 {abs(diff):.0f}pp")
                        risks.append("industry_weak_margin")

        score = round(min(score, 10.0), 1)
        return score, "；".join(details), risks

    # —— 技术维度 ——
    def score_technical(self, ctx: AnalysisContext) -> tuple[float, str, list[str]]:
        prices = ctx.price_data or []
        if not prices:
            return 0.0, "行情数据不可用", []

        sorted_prices = sorted(prices, key=lambda x: x.trade_date)
        closes = [p.close for p in sorted_prices]

        cfg = self.config.get("technical", {})
        total = 0.0
        details = []
        risks = []

        # 均线结构
        ma_cfg = cfg.get("ma_structure", {})
        if ma_cfg.get("enabled", True):
            ma5 = mean(closes[-5:]) if len(closes) >= 5 else None
            ma20 = mean(closes[-20:]) if len(closes) >= 20 else None
            ma60 = mean(closes[-60:]) if len(closes) >= 60 else None
            max_s = ma_cfg.get("max_score", 4)
            if ma5 and ma20 and ma60:
                if ma5 > ma20 > ma60:
                    s = 4; total += s; details.append(f"多头排列，得 {s}/{max_s} 分")
                elif ma5 > ma20 and ma20 < ma60:
                    s = 2; total += s; details.append(f"均线交叉震荡，得 {s}/{max_s} 分")
                else:
                    s = 1; total += s; details.append(f"空头排列，得 {s}/{max_s} 分")
                    risks.append("bearish_ma")
            elif ma5 and ma20:
                s = 2; total += s; details.append(f"缺 MA60，得 {s}/{max_s} 分")

        # 量价配合
        vp_cfg = cfg.get("volume_price", {})
        if vp_cfg.get("enabled", True) and len(closes) >= 20:
            latest_close = closes[-1]
            ma20_v = mean(closes[-20:])
            price_vs_ma20 = (latest_close - ma20_v) / ma20_v * 100 if ma20_v > 0 else 0
            volumes = [p.volume for p in sorted_prices[-5:]]
            prev_vols = [p.volume for p in sorted_prices[-25:-5]]
            vol_ratio = mean(volumes) / mean(prev_vols) if prev_vols and mean(prev_vols) > 0 else 1.0
            max_s = vp_cfg.get("max_score", 3)
            if price_vs_ma20 > 0 and vol_ratio > 1.2:
                s = 3; total += s; details.append(f"放量上涨（量比 {vol_ratio:.2f}），得 {s}/{max_s} 分")
            elif abs(price_vs_ma20) < 2 and 0.8 <= vol_ratio <= 1.2:
                s = 2; total += s; details.append(f"横盘缩量（量比 {vol_ratio:.2f}），得 {s}/{max_s} 分")
            elif price_vs_ma20 < 0 and vol_ratio > 1.2:
                s = 1; total += s; details.append(f"放量下跌（量比 {vol_ratio:.2f}），得 {s}/{max_s} 分")
                risks.append("volume_bearish")
            else:
                s = 2; total += s; details.append(f"量价一般（量比 {vol_ratio:.2f}），得 {s}/{max_s} 分")

        # MACD
        macd_cfg = cfg.get("macd", {})
        if macd_cfg.get("enabled", True) and len(closes) >= 26:
            macd = calculate_macd(closes)
            dif = macd.dif
            dea = macd.dea
            macd_bar = macd.bar
            max_s = macd_cfg.get("max_score", 3)
            if dif > dea and macd_bar > 0:
                s = 3; total += s; details.append(f"MACD 多头，得 {s}/{max_s} 分")
            elif (dif > dea) != (macd_bar > 0):
                s = 2; total += s; details.append(f"MACD 临界，得 {s}/{max_s} 分")
            else:
                s = 1; total += s; details.append(f"MACD 空头，得 {s}/{max_s} 分")

        return round(total, 1), "；".join(details), risks

    # —— 舆情维度 ——
    def score_sentiment(self, ctx: AnalysisContext) -> tuple[float, str, list[str]]:
        cfg = self.config.get("sentiment", {})
        if not cfg.get("enabled", True):
            return 0.0, "舆情维度已禁用", []
        es = ctx.enriched_sentiment
        score = float(cfg.get("base_score", 5))
        details = [f"基础分 {score}"]
        risks: list[str] = []

        if es and es.total_count > 0:
            msg_bonus = cfg.get("msg_count_bonus", {})
            if es.total_count >= 10:
                score += msg_bonus.get("sufficient", 2)
                details.append(f"消息 {es.total_count} 条（充足），+{msg_bonus.get('sufficient', 2)} 分")
            else:
                score += msg_bonus.get("partial", 1)
                details.append(f"消息 {es.total_count} 条（偏少），+{msg_bonus.get('partial', 1)} 分")

            pos_bonus = cfg.get("positive_ratio_bonus", {})
            pos_ratio = es.positive_count / es.total_count if es.total_count > 0 else 0
            if pos_ratio > 0.5:
                score += pos_bonus.get("high", 2)
                details.append(f"利好占比 {pos_ratio:.0%}，+{pos_bonus.get('high', 2)} 分")
            elif pos_ratio > 0.3:
                score += pos_bonus.get("medium", 1)
                details.append(f"利好占比 {pos_ratio:.0%}，+{pos_bonus.get('medium', 1)} 分")

            if es.negative_count > 0 and any(e.severity == "major" for e in es.all_items):
                pen = cfg.get("major_negative_penalty", -1)
                score += pen
                details.append(f"存在重大利空事件，{pen} 分")
                risks.append("major_negative_news")

            ann_bonus = cfg.get("announcement_bonus", 0)
            if ann_bonus and any(getattr(e, 'source', '') == "announcement" for e in es.all_items):
                score += ann_bonus
                details.append(f"包含官方公告，+{ann_bonus} 分")

        score = round(max(0.0, min(10.0, score)), 1)
        return score, "；".join(details), risks
