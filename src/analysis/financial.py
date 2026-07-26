"""财务分析模块 — 营收、利润、ROE 趋势分析"""
from analysis.base import AnalysisModule
from data.schemas import AnalysisContext, AnalysisResult


class FinancialAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> str:
        return "financial"

    def analyze(self, context: AnalysisContext) -> AnalysisResult:
        financials = context.financial_data or []
        if not financials:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="财务数据不可用", metrics={})

        sorted_data = sorted(financials, key=lambda x: x.fiscal_quarter, reverse=True)
        latest = sorted_data[0]

        metrics = {
            "latest_quarter": latest.fiscal_quarter.isoformat(),
            "revenue": latest.revenue,
            "net_profit": latest.net_profit,
            "total_assets": latest.total_assets,
            "total_equity": latest.total_equity,
            "operating_cash_flow": latest.operating_cash_flow,
            "roe": latest.roe,
            "gross_margin": latest.gross_margin,
        }

        if len(sorted_data) >= 2:
            prev_year = sorted_data[-1] if len(sorted_data) >= 5 else sorted_data[1]
            if (latest.revenue is not None and prev_year.revenue is not None
                    and prev_year.revenue > 0):
                metrics["revenue_growth_yoy"] = round((latest.revenue - prev_year.revenue) / prev_year.revenue, 4)
            if (latest.net_profit is not None and prev_year.net_profit is not None
                    and prev_year.net_profit > 0):
                metrics["profit_growth_yoy"] = round((latest.net_profit - prev_year.net_profit) / prev_year.net_profit, 4)

        roe_trend = []
        for d in sorted_data[:8]:
            if d.roe is not None:
                roe_trend.append({"quarter": d.fiscal_quarter.isoformat(), "roe": round(d.roe, 4)})
        metrics["roe_trend"] = roe_trend

        status = "partial" if len(sorted_data) < 3 else "ok"
        summary = self._build_summary(metrics, status)

        # ===== 打分逻辑 =====
        from data.schemas import SufficiencyLevel

        if context.sufficiency and context.sufficiency.financial.level == SufficiencyLevel.INSUFFICIENT:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="财务数据不足", metrics=metrics,
                                  score=None, score_detail="财务维度数据不足，跳过打分")

        score = 0.0
        score_parts = []
        risk_flags = []

        # 1. ROE 水平 (3分)
        roe = latest.roe
        if roe is not None:
            if roe < 0:
                score_parts.append(f"ROE {roe*100:.1f}%（负数），得 0/3 分")
                risk_flags.append("roe_low")
            elif roe >= 0.15:
                score += 3; score_parts.append(f"ROE {roe*100:.1f}%（≥15%），得 3/3 分")
            elif roe >= 0.10:
                score += 2; score_parts.append(f"ROE {roe*100:.1f}%（10-15%），得 2/3 分")
            elif roe >= 0.05:
                score += 1; score_parts.append(f"ROE {roe*100:.1f}%（5-10%），得 1/3 分")
            else:
                score += 0; score_parts.append(f"ROE {roe*100:.1f}%（<5%），得 0/3 分")
        else:
            score_parts.append("ROE 数据缺失，得 0/3 分")

        # 2. 资产负债率 (2分)
        latest_fin = sorted_data[0]
        asset_liability = None
        if latest_fin.total_assets and latest_fin.total_equity and latest_fin.total_equity > 0:
            asset_liability = (1 - latest_fin.total_equity / latest_fin.total_assets) * 100
        if asset_liability is not None:
            if 40 <= asset_liability <= 70:
                score += 2; score_parts.append(f"资产负债率 {asset_liability:.0f}%（适中），得 2/2 分")
            elif 20 <= asset_liability < 40 or 70 < asset_liability <= 90:
                score += 1; score_parts.append(f"资产负债率 {asset_liability:.0f}%（偏高/偏低），得 1/2 分")
            else:
                score += 0; score_parts.append(f"资产负债率 {asset_liability:.0f}%（极端），得 0/2 分")
                if asset_liability > 90:
                    risk_flags.append("high_debt")
        else:
            score_parts.append("资产负债率数据缺失，得 0/2 分")

        # 3. 经营现金流/净利润匹配 (3分)
        ocf = latest_fin.operating_cash_flow
        np_val = latest_fin.net_profit
        if np_val is not None and np_val <= 0:
            score += 0; score_parts.append("净利润为负，现金流匹配子项 0/3 分")
            risk_flags.append("cash_flow_mismatch")
        elif ocf is not None and np_val is not None and np_val > 0:
            ratio = ocf / np_val
            if ratio > 0.8:
                score += 3; score_parts.append(f"经营现金流/净利润 {ratio:.2f}（>0.8），得 3/3 分")
            elif ratio >= 0.5:
                score += 2; score_parts.append(f"经营现金流/净利润 {ratio:.2f}（0.5-0.8），得 2/3 分")
            else:
                score += 1; score_parts.append(f"经营现金流/净利润 {ratio:.2f}（<0.5），得 1/3 分")
                risk_flags.append("cash_flow_mismatch")
        else:
            score_parts.append("经营现金流数据缺失，得 0/3 分")

        # 4. 毛利率稳定性 (2分)
        gross_margins = [d.gross_margin for d in sorted_data[:4] if d.gross_margin is not None]
        if gross_margins:
            if any(gm < 0 for gm in gross_margins):
                score += 0; score_parts.append("存在负毛利率，得 0/2 分")
            else:
                gm_range = max(gross_margins) - min(gross_margins) if len(gross_margins) >= 2 else 0
                if gm_range < 0.05:
                    score += 2; score_parts.append(f"近4期毛利率波动 {gm_range*100:.1f}pp（<5pp），得 2/2 分")
                elif gm_range < 0.15:
                    score += 1; score_parts.append(f"近4期毛利率波动 {gm_range*100:.1f}pp（5-15pp），得 1/2 分")
                else:
                    score += 0; score_parts.append(f"近4期毛利率波动 {gm_range*100:.1f}pp（>15pp），得 0/2 分")
        else:
            score_parts.append("毛利率数据缺失，得 0/2 分")

        score = round(score, 1)
        score_detail = "；".join(score_parts)
        return AnalysisResult(dimension=self.dimension, status=status, summary=summary,
                              metrics=metrics, score=score, score_detail=score_detail,
                              risk_flags=risk_flags)

    def _build_summary(self, metrics: dict, status: str) -> str:
        parts = []
        rev_growth = metrics.get("revenue_growth_yoy")
        if rev_growth is not None:
            direction = "增长" if rev_growth > 0 else "下降"
            parts.append(f"营收同比{direction}{abs(rev_growth)*100:.1f}%")
        profit_growth = metrics.get("profit_growth_yoy")
        if profit_growth is not None:
            direction = "增长" if profit_growth > 0 else "下降"
            parts.append(f"净利润同比{direction}{abs(profit_growth)*100:.1f}%")
        roe = metrics.get("roe")
        if roe is not None:
            parts.append(f"ROE {roe*100:.1f}%")
        return "；".join(parts) if parts else "财务指标数据不足"
