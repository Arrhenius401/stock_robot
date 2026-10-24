"""行业分析模块 — 行业分类与同业对比"""
from typing import Any, Literal

from analysis.base import AnalysisModule
from data.hk_hsics_industry import (
    HsicsIndustryError,
    load_connect_symbols,
    load_hsics_members,
)
from data.schemas import AnalysisContext, AnalysisResult, SufficiencyLevel


class IndustryAnalyzer(AnalysisModule):
    @property
    def dimension(self) -> Literal["industry"]:
        return "industry"

    def _hk_unavailable(self, summary: str) -> AnalysisResult:
        return AnalysisResult(
            dimension=self.dimension, status="unavailable", summary=summary,
            metrics={}, score=None, score_detail="行业对比不计分",
        )

    def _analyze_hk(self, context: AnalysisContext) -> AnalysisResult:
        try:
            connect = load_connect_symbols()
            members = load_hsics_members()
        except (HsicsIndustryError, OSError):
            return self._hk_unavailable("港股通名单或恒生行业快照不可用")
        if not members:
            return self._hk_unavailable("恒生行业快照不可用")
        if context.symbol not in {code + ".HK" for code in connect}:
            return self._hk_unavailable("非港股通标的，不参与港股行业对比")
        member = members.get(context.symbol)
        if member is None or member.status != "mapped":
            return self._hk_unavailable("港股通标的，恒生行业待补齐")
        ind_data = context.industry_data
        if ind_data is None or not ind_data.industry:
            return self._hk_unavailable("恒生行业数据不可用")

        display_peers = ind_data.peers[:10]
        peer_total = len(ind_data.peers)
        metrics: dict[str, Any] = {
            "industry": ind_data.industry,
            "sector": ind_data.sector,
            "peers": display_peers,
            "peer_names": {
                symbol: name for symbol, name in ind_data.peer_names.items()
                if symbol in display_peers
            },
            "peer_total": peer_total,
            "peer_scope": ind_data.peer_scope,
            "peer_industry": ind_data.peer_industry,
            "classification_scheme": "恒生HSICS",
            "industry_source": "东财 F10 + 恒生每日成分 PDF",
            "industry_data_date": getattr(ind_data, "_data_date", ""),
            "industry_scheme_note": "分类口径：恒生HSICS二级（东财F10转载；HSCI PDF校验）",
            "target_pe": getattr(ind_data, "_target_pe", None),
            "target_pb": getattr(ind_data, "_target_pb", None),
        }
        if peer_total > 10:
            metrics["peer_display_note"] = f"仅展示前10家，统计基于全部{peer_total}家港股通同行"
        enriched = context.enriched_industry
        if enriched is not None:
            metrics.update({
                "peer_count": enriched.peer_count,
                "industry_median_pe": enriched.industry_median_pe,
                "industry_median_pb": enriched.industry_median_pb,
                "target_pe_premium": enriched.target_pe_premium,
                "target_pe_percentile": enriched.target_pe_percentile,
                "target_pb_percentile": enriched.target_pb_percentile,
                "peer_pe_sample": enriched.peer_pe_sample,
                "peer_pb_sample": enriched.peer_pb_sample,
                "top_peers": [peer.model_dump() for peer in enriched.top_peers],
            })
        comparison = getattr(enriched, "comparison", {}) if enriched is not None else {}
        if comparison:
            metrics["comparison"] = comparison
        enough = (
            enriched is not None and enriched.peer_count >= 5
            and (enriched.industry_median_pe is not None
                 or enriched.industry_median_pb is not None)
        )
        status = self._comparison_status(comparison) if comparison else "ok" if enough else "partial"
        summary = (
            f"恒生二级：{ind_data.industry}（{ind_data.sector}）"
            if enough else f"恒生二级同行样本不足：{ind_data.industry}"
        )
        return AnalysisResult(
            dimension=self.dimension, status=status, summary=summary,
            metrics=metrics, score=None, score_detail="行业对比不计分",
            industry_note="分类口径：恒生HSICS二级（东财F10转载；HSCI PDF校验）",
        )

    @staticmethod
    def _comparison_status(comparison: dict[str, Any]) -> Literal["ok", "partial"]:
        """逐项保留可用事实；来源失败或任一指标不足均明确标为部分可用。"""
        sections = [comparison.get("valuation", {})]
        sections.extend(comparison.get("periods", {}).values())
        metrics = [metric for section in sections for metric in section.get("metrics", [])]
        complete = bool(metrics) and all(metric.get("status") == "ok" for metric in metrics)
        return "ok" if complete and not comparison.get("errors") else "partial"

    def analyze(self, context: AnalysisContext,
                config: dict[str, Any] | None = None) -> AnalysisResult:
        if context.market == "hk":
            return self._analyze_hk(context)
        ind_data = context.industry_data
        if ind_data is None:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="行业数据不可用", metrics={},
                                  score_detail="行业对比不计分")

        metrics: dict[str, Any] = {
            "industry": ind_data.industry,
            "sector": ind_data.sector,
            "peers": ind_data.peers,
            # 仅供报告展示层将代码渲染为“公司名（代码）”，不单独展示。
            "peer_names": ind_data.peer_names,
            "peer_scope": ind_data.peer_scope,
            "peer_industry": ind_data.peer_industry,
        }
        status = "ok" if ind_data.industry and ind_data.industry != "未知" else "partial"
        summary = f"所属行业: {ind_data.industry}" if status == "ok" else "行业分类数据有限"

        enriched = context.enriched_industry
        comparison = getattr(enriched, "comparison", {}) if enriched is not None else {}
        if enriched is not None:
            metrics.update({
                "peer_count": enriched.peer_count,
                "industry_median_pe": enriched.industry_median_pe,
                "industry_median_pb": enriched.industry_median_pb,
                "target_pe_premium": enriched.target_pe_premium,
                "target_pe_percentile": enriched.target_pe_percentile,
                "target_pb_percentile": enriched.target_pb_percentile,
                "peer_pe_sample": enriched.peer_pe_sample,
                "peer_pb_sample": enriched.peer_pb_sample,
                "top_peers": [peer.model_dump() for peer in enriched.top_peers],
            })
        if comparison:
            metrics["comparison"] = comparison
            return AnalysisResult(
                dimension=self.dimension, status=self._comparison_status(comparison),
                summary=summary, metrics=metrics, score=None, score_detail="行业对比不计分",
            )

        if context.sufficiency and context.sufficiency.industry.level == SufficiencyLevel.INSUFFICIENT:
            return AnalysisResult(dimension=self.dimension, status="unavailable",
                                  summary="行业数据不足", metrics=metrics,
                                  score=None, score_detail="行业对比不计分")

        return AnalysisResult(dimension=self.dimension, status=status, summary=summary,
                              metrics=metrics, score=None, score_detail="行业对比不计分")
