"""美股同分类、市值分层的 TTM 比较；未证实营收不派生净利率。"""
import statistics
from datetime import date
from typing import Any

from data.enricher import DataEnricher
from data.schemas import (
    AnalysisContext,
    DimensionSufficiency,
    EnrichedIndustry,
    PeerComparison,
    SufficiencyLevel,
)
from data.us_industry import SOURCE, _positive
from utils.numbers import is_finite_number


def _metric(key: str, label: str, target: float | None, values: list[float], unit: str = "") -> dict[str, Any]:
    target = target if is_finite_number(target) else None
    values = [value for value in values if is_finite_number(value)]
    enough = len(values) >= 5
    return {"key": key, "label": label, "target": target, "unit": unit,
            "sample_count": len(values), "median": statistics.median(values) if enough else None,
            "percentile": (sum(value < target for value in values) + sum(value == target for value in values) / 2) / len(values) * 100
            if enough and target is not None else None,
            "status": "ok" if enough and target is not None else "insufficient",
            "reason": "" if enough and target is not None else "有效同行不足五家或目标缺失"}


class UsIndustryEnricher(DataEnricher):
    def enrich(self, ctx: AnalysisContext) -> AnalysisContext:
        if ctx.market != "us":
            return ctx
        ctx.enriched_industry = None
        data = ctx.industry_data
        if data is None:
            ctx.sufficiency.industry = DimensionSufficiency(level=SufficiencyLevel.INSUFFICIENT,
                reason="行业数据缺失", score_weight=0)
            return ctx
        snapshot = data.comparison_snapshot
        target = snapshot.get("target", {})
        if not isinstance(target, dict) or target.get("basis_verified") is not True:
            target = {}
        cap = _positive(target.get("market_cap"))
        raw_quotes = snapshot.get("quotes", {})
        candidates = list(raw_quotes.values()) if isinstance(raw_quotes, dict) else []
        candidates = [row for row in candidates if isinstance(row, dict) and row.get("symbol") in data.peers and row.get("symbol") != ctx.symbol
                      and row.get("basis_verified") is True and all(_positive(row.get(key)) is not None for key in ("market_cap", "pe", "pb"))]
        # 缓存中的数字字符串必须规范化后再比较，不能通过校验后仍按字符串计算。
        candidates = [{**row, **{key: _positive(row[key]) for key in ("market_cap", "pe", "pb")}}
                      for row in candidates]
        selected = []
        relaxed = False
        if cap is not None:
            for low, high in ((.1, 5), (.01, 20)):
                selected = [row for row in candidates if low * cap <= row["market_cap"] <= high * cap]
                if len(selected) >= 5:
                    break
            if len(selected) < 5:
                selected = candidates
                relaxed = True
            selected = sorted(selected, key=lambda row: abs(row["market_cap"] / cap - 1))[:10]
        pe, pb = _positive(target.get("pe")), _positive(target.get("pb"))
        pe_metric = _metric("pe_ttm", "PE(TTM)", pe, [row["pe"] for row in selected])
        pb_metric = _metric("pb", "PB", pb, [row["pb"] for row in selected])
        operating = snapshot.get("operating", {})
        operating = operating if isinstance(operating, dict) else {}
        target_op = operating.get(ctx.symbol) or {}
        metrics = []
        for key, label in (("revenue_growth_yoy", "TTM营收同比"), ("net_margin", "TTM净利率")):
            values = []
            for peer in selected:
                row = operating.get(peer["symbol"]) or {}
                try:
                    comparable = (row.get("basis") == "TTM" and row.get("source") and row.get("start")
                                  and abs((date.fromisoformat(row["end"]) - date.fromisoformat(target_op["end"])).days) <= 100)
                except (ValueError, TypeError, KeyError):
                    comparable = False
                value = row.get(key)
                if comparable and isinstance(value, (int, float)) and not isinstance(value, bool):
                    values.append(value)
            metrics.append(_metric(key, label, target_op.get(key), values, "percent"))
        ready = (target_op.get("basis") == "TTM" and all(target_op.get(k) for k in ("start", "end", "source"))
                 and all(m["status"] == "ok" for m in metrics))
        reason = "" if ready else "TTM经营期间未核实或至少两项各五家有效同行不足"
        notes = ["东财美股 F10 行业（BELONG_INDUSTRY）；经营指标为 TTM 派生，不是年度+最新两期。",
                 "仅美股上市纯字母代码；优先目标市值0.1至5倍，不足五家放宽0.01至20倍，最多十家。",
                 "经营指标采用F10连续单季TTM；PB仅作估值比较，不派生ROE。"]
        if relaxed:
            notes.append("市值分层不足，已放宽到全部有效同行，按距目标市值最近选取，最多十家。")
        sources = sorted({row.get("source") or "来源未记录" for row in [target, *selected]})
        valuation_source = sources[0] if len(sources) == 1 else "混合来源（逐行标注）"
        comparison = {"version": 1, "scope": SOURCE + "·" + data.industry, "peer_count": len(selected),
            "valuation": {"as_of": snapshot.get("as_of"), "source": valuation_source,
                          "show_source_rows": True, "target": {**target, "symbol": ctx.symbol},
                          "peers": [{**row, "pe_ttm": row["pe"]} for row in selected],
                          "metrics": [pe_metric, pb_metric]},
            "periods": {"latest": {**target_op, "start": target_op.get("start"), "end": target_op.get("end"),
                         "basis": "TTM", "label": "TTM最新期", "source": target_op.get("source"),
                         "metrics": metrics, "reason": reason}},
            "notes": notes, "errors": [reason] if reason else []}
        enough = len(selected) >= 5
        data.comparison_ready = enough
        ctx.enriched_industry = EnrichedIndustry(peer_count=len(selected),
            top_peers=[PeerComparison(symbol=row["symbol"], name=row.get("name") or "", market_cap=row["market_cap"],
                pe_ttm=row["pe"], pb=row["pb"], roe=None,
                net_margin=(operating[row["symbol"]]["net_margin"] / 100
                            if isinstance(operating.get(row["symbol"]), dict)
                            and is_finite_number(operating[row["symbol"]].get("net_margin")) else None)) for row in selected],
            industry_median_pe=pe_metric["median"], industry_median_pb=pb_metric["median"],
            target_pe_premium=(pe / pe_metric["median"] - 1) * 100 if pe and pe_metric["median"] else None,
            target_pe_percentile=pe_metric["percentile"], target_pb_percentile=pb_metric["percentile"],
            peer_pe_sample=len(selected), peer_pb_sample=len(selected), comparison=comparison)
        ctx.sufficiency.industry = DimensionSufficiency(
            level=SufficiencyLevel.SUFFICIENT if ready else SufficiencyLevel.PARTIAL if len(selected) >= 3 else SufficiencyLevel.INSUFFICIENT,
            reason=reason if enough else "行业对比样本不足；" + reason,
            sample_count=len(selected), score_weight=1 if ready else .5 if len(selected) >= 3 else 0)
        return ctx
