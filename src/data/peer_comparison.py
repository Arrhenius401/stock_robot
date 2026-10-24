"""同步完成同行比较，不在报告生成后异步补齐内容。"""
import time
from datetime import datetime

from data.cache import CacheManager
from data.peer_comparison_fetch import (
    COLLECTION_BUDGET,
    fetch_a_industry,
    fetch_financial_rows,
)
from data.peer_comparison_rules import (
    finite,
    iso_date,
    metric_summary,
    period_comparison,
    valuation_date,
)
from data.schemas import (
    AnalysisContext,
    DimensionSufficiency,
    EnrichedIndustry,
    PeerComparison,
    SufficiencyLevel,
)
from utils.numbers import is_positive_finite

__all__ = ["PeerComparisonService", "fetch_a_industry"]


class PeerComparisonService:
    def __init__(self, cache: CacheManager | None = None):
        self.cache = cache

    def enrich(self, ctx: AnalysisContext, *, refresh: bool = False) -> AnalysisContext:
        data = ctx.industry_data
        # 旧头部样本统计不能悄悄进入新比较；明确许可前不增加网络请求。
        ctx.enriched_industry = EnrichedIndustry()
        if data is None:
            return ctx
        if not data.comparison_ready or data.peer_scope not in ("申万三级", "港股通·恒生HSICS二级"):
            ctx.sufficiency.industry = DimensionSufficiency(
                level=SufficiencyLevel.INSUFFICIENT, reason="未取得已验证的细分同行池",
                sample_count=0, score_weight=0,
            )
            return ctx

        symbols = list(dict.fromkeys(s for s in data.peers if s != ctx.symbol))
        snapshot = data.comparison_snapshot
        target = snapshot.get("target", {})
        target_date = valuation_date(target.get("as_of"))
        target_reason = ""
        if target.get("symbol") != ctx.symbol:
            target_reason = "目标估值身份不匹配或未记录，未生成估值比较"
        elif not target_date or target_date != valuation_date(snapshot.get("as_of")):
            target_reason = "目标估值日期无效、缺失、在未来或冲突，未生成估值比较"
        by_code = {p["symbol"]: p for p in snapshot.get("peers", []) if p.get("symbol") in symbols}
        peers = []
        for symbol in symbols:
            row = {"symbol": symbol, "name": data.peer_names.get(symbol, ""),
                   **by_code.get(symbol, {})}
            if symbol not in by_code:
                row["reason"] = "未取得估值"
            row["as_of"] = iso_date(row.get("as_of"))
            if target_reason:
                row["reason"] = target_reason
            elif row["as_of"] != target_date:
                row["reason"] = "同行估值日期不同或未记录，不参与同日统计"
            peers.append(row)
        valuation = {"as_of": target_date if not target_reason else "", "source": snapshot.get("source", ""),
                     "cache_status": snapshot.get("cache_status", "unknown"), "peers": peers,
                     "metrics": [metric_summary(key, target.get(key), peers, ctx.symbol)
                                 for key in ("pe_ttm", "pb")]}
        if target_reason:
            for metric in valuation["metrics"]:
                metric["reason"] = target_reason
        known = max(ctx.financial_data or [], key=lambda r: r.fiscal_quarter, default=None)
        latest = known.fiscal_quarter.isoformat() if known else ""
        start = str((known.fiscal_quarter.year if known else datetime.now().astimezone().year) - 2) + "-01-01"
        deadline = data._comparison_deadline or time.monotonic() + COLLECTION_BUDGET
        rows, errors = fetch_financial_rows([ctx.symbol, *symbols], ctx.market, start=start,
                                             deadline=deadline, cache=self.cache, refresh=refresh)
        # 金融企业的通用净利率适用性未经验证，保留原值并明确不参与比较。
        financial_sector = any(word in data.industry + data.sector for word in ("银行", "保险"))
        if financial_sector:
            rows = [{**r, "excluded_reasons": {"net_margin": "金融行业净利率口径未验证，不参与比较"}}
                    for r in rows]
        periods = {kind: period_comparison(kind, rows, ctx.symbol, symbols,
                   expected_latest=latest, expected_start=known.period_start.isoformat()
                   if known and known.period_start else "", expected_basis=known.period_basis if known else "")
                   for kind in ("annual", "latest")}
        if financial_sector:
            for period in periods.values():
                metric = next(m for m in period["metrics"] if m["key"] == "net_margin")
                metric.update(median=None, percentile=None, sample_count=0, status="insufficient",
                              reason="金融行业净利率口径未验证，不参与比较")
        for period in periods.values():
            for peer in period["peers"]:
                if not peer.get("name"):
                    peer["name"] = data.peer_names.get(peer["symbol"], "")
        comparison = {"version": 1, "scope": data.peer_scope + "·" + data.peer_industry,
                      "peer_count": len(symbols), "valuation": valuation, "periods": periods,
                      "errors": list(dict.fromkeys([*snapshot.get("errors", []), *errors,
                                                     *([target_reason] if target_reason else [])])),
                      "notes": [*snapshot.get("notes", []),
                                "分位表示数值位置，采用低于目标比例加相等值比例的一半，不直接等同经营优劣。",
                                "各指标独立统计，至少 5 家有效同行；目标不计入分母。"]}
        pe, pb = valuation["metrics"]
        premium = None
        if (pe["median"] is not None and pe["target"] is not None
                and is_positive_finite(pe["target"]) and is_positive_finite(pe["median"])):
            premium = finite((pe["target"] / pe["median"] - 1) * 100)
        ctx.enriched_industry = EnrichedIndustry(
            comparison=comparison, peer_count=len(symbols),
            top_peers=[PeerComparison(symbol=p["symbol"], name=p.get("name", ""),
                        market_cap=p.get("market_cap"), pe_ttm=p.get("pe_ttm"), pb=p.get("pb")) for p in peers[:5]],
            industry_median_pe=pe["median"], industry_median_pb=pb["median"],
            target_pe_percentile=pe["percentile"], target_pb_percentile=pb["percentile"],
            target_pe_premium=premium, peer_pe_sample=pe["sample_count"], peer_pb_sample=pb["sample_count"],
        )
        all_metrics = [*valuation["metrics"], *(m for p in periods.values() for m in p["metrics"])]
        complete = all(m["status"] == "ok" for m in all_metrics) and not comparison["errors"]
        usable = any(m["target"] is not None or m["sample_count"] for m in all_metrics)
        ctx.sufficiency.industry = DimensionSufficiency(
            level=SufficiencyLevel.SUFFICIENT if complete else SufficiencyLevel.PARTIAL if usable else SufficiencyLevel.INSUFFICIENT,
            reason="同行估值和经营比较已完成" if complete else "部分同行数据缺失、期间不匹配或有效样本不足",
            sample_count=len(symbols), score_weight=1 if complete else 0.5 if usable else 0,
        )
        return ctx
