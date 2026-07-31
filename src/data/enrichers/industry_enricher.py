"""行业充实器 — 同业对比与行业中位数计算"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import statistics
from data.enricher import DataEnricher
from data.schemas import (
    AnalysisContext, DimensionSufficiency, SufficiencyLevel,
    EnrichedIndustry, PeerComparison,
)

logger = logging.getLogger(__name__)


def _fetch_peer_valuation(code: str) -> tuple[str, float | None, float | None]:
    """查询单只股票的 PE/PB，返回 (code, pe_ttm, pb)"""
    from data.akshare import parse_cn_number
    try:
        if code.startswith("6"):
            xq = f"SH{code}"
        else:
            xq = f"SZ{code}"
        from data.akshare import _ak_individual_spot_xq
        spot_df = _ak_individual_spot_xq(xq)
        pe_ttm = None
        pb = None
        if spot_df is not None and "item" in spot_df.columns and "value" in spot_df.columns:
            pe_row = spot_df[spot_df["item"] == "市盈率(动)"]
            pb_row = spot_df[spot_df["item"] == "市净率"]
            if not pe_row.empty:
                pe_ttm = parse_cn_number(pe_row["value"].iloc[0])
            if not pb_row.empty:
                pb = parse_cn_number(pb_row["value"].iloc[0])
        return code, pe_ttm, pb
    except Exception:
        return code, None, None


class IndustryEnricher(DataEnricher):
    def enrich(self, ctx: AnalysisContext) -> AnalysisContext:
        ind_data = ctx.industry_data

        if ind_data is None or (not ind_data.industry or ind_data.industry == "未知"):
            ctx.sufficiency.industry = DimensionSufficiency(
                level=SufficiencyLevel.INSUFFICIENT, reason="行业数据完全缺失",
                sample_count=0, score_weight=0.0,
            )
            return ctx

        top_peers = ind_data.top_peers or []

        # 并行查询同行 PE/PB（取代原来 _fetch_industry 中的串行查询）
        peer_valuations: dict[str, tuple[float | None, float | None]] = {}
        if top_peers:
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {executor.submit(_fetch_peer_valuation, p.symbol): p.symbol for p in top_peers}
                for future in as_completed(futures):
                    code, pe, pb = future.result()
                    peer_valuations[code] = (pe, pb)

        # 构建带 PE/PB 的同行对比列表
        peer_comparisons = []
        for p in top_peers:
            pe, pb = peer_valuations.get(p.symbol, (None, None))
            peer_comparisons.append(PeerComparison(
                symbol=p.symbol, name=p.name, market_cap=p.market_cap,
                pe_ttm=pe, pb=pb,
            ))

        # 全行业有效同行数（不止 top 5）
        peer_count = len(ind_data.peers) if ind_data.peers else len(top_peers)
        valid_head_count = len(peer_comparisons)

        if peer_count >= 8 and valid_head_count >= 4:
            level = SufficiencyLevel.SUFFICIENT
            weight = 1.0
            reason = f"行业有效可比公司 {peer_count} 家，头部 {valid_head_count} 家数据完整"
        elif peer_count >= 3 or valid_head_count >= 2:
            level = SufficiencyLevel.PARTIAL
            weight = 0.5
            reason = f"可比较公司 {peer_count} 家（3-7），横向对比参考价值有限"
        else:
            level = SufficiencyLevel.INSUFFICIENT
            weight = 0.0
            reason = f"可比较公司仅 {peer_count} 家（<3），无法进行有效对比"

        # 计算行业中位数 PE/PB（从头部同行中取有效值）
        peer_pes = [p.pe_ttm for p in peer_comparisons if p.pe_ttm is not None and p.pe_ttm > 0]
        peer_pbs = [p.pb for p in peer_comparisons if p.pb is not None and p.pb > 0]

        industry_median_pe = statistics.median(peer_pes) if peer_pes else None
        industry_median_pb = statistics.median(peer_pbs) if peer_pbs else None

        # 计算目标 PE 溢价/折价
        target_pe = ctx.valuation_data.pe_ttm if ctx.valuation_data else None
        target_pe_premium = None
        if target_pe is not None and industry_median_pe is not None and industry_median_pe > 0:
            target_pe_premium = (target_pe - industry_median_pe) / industry_median_pe * 100

        # 计算目标市值排名（从采集层传入的排名）
        target_market_cap_rank = getattr(ind_data, '_target_rank', None)

        ctx.enriched_industry = EnrichedIndustry(
            peer_count=peer_count,
            top_peers=peer_comparisons,
            industry_median_pe=industry_median_pe,
            industry_median_pb=industry_median_pb,
            target_pe_premium=round(target_pe_premium, 1) if target_pe_premium is not None else None,
            target_market_cap_rank=target_market_cap_rank,
        )

        ctx.sufficiency.industry = DimensionSufficiency(
            level=level, reason=reason, sample_count=peer_count, score_weight=weight,
        )
        return ctx
