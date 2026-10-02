"""策略指数公开官方估值：实际交易日 PE 与独立月度快照。"""
import logging
from datetime import date, datetime
from typing import Any

from data.schemas import IndexValuationData
from index.enricher import compute_percentile
from index.strategy_data import StrategyDataProvider, finite_number

logger = logging.getLogger(__name__)
HISTORY_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"
CURRENT_URL = "https://www.csindex.com.cn/csindex-home/data-service/indexValuation"


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value.date() if isinstance(value, datetime) else value
    try:
        raw = str(value).strip()
        if len(raw) == 8 and raw.isdigit():
            raw = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
        return date.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


def _positive(value: Any) -> float | None:
    number = finite_number(value)
    return number if number is not None and number > 0 else None


def _start(end: date) -> date:
    # 闰日使用五年前同月最后有效日，而非固定 1825 天。
    try:
        return end.replace(year=end.year - 5)
    except ValueError:
        return end.replace(year=end.year - 5, day=28)


class StrategyValuationProvider(StrategyDataProvider):
    """复用有超时的网络与 SQLite 缓存，保留每个指标的来源和日期。"""

    def _factsheet(self, symbol: str, provider: str, end: date) -> dict:
        from index.valuation_factsheet import fetch_factsheet
        return fetch_factsheet(symbol, provider, end, self.timeout)

    def _current_pb(self, symbol: str, end: date) -> dict:
        names = {"000015": "红利指数", "000922": "中证红利"}
        if symbol not in names:
            return {}
        data = self._get_json(CURRENT_URL).get("data") or {}
        for row in data.get("indexValuations") or []:
            if row.get("indexName") == names[symbol]:
                sampled = _date(row.get("tradeDate"))
                if sampled is not None and 0 <= (end - sampled).days <= 14:
                    return {"symbol": symbol, "as_of": sampled, "pb": row.get("pb"),
                            "pb_basis": "官方每日估值", "source_url": CURRENT_URL}
        return {}

    def _source_snapshot(self, kind: str, symbol: str, provider: str, end: date, source: Any) -> dict:
        """每个来源单独缓存；未披露数值仍是成功响应。"""
        key = f"{provider}:{symbol}:{end}"
        cached = self._read_cached(kind, key)
        if cached is not None:
            if not cached["ok"]:
                raise RuntimeError(cached["error"])
            return cached["value"]
        try:
            snapshot = source()
            sampled = _date(snapshot.get("as_of")) if snapshot else None
            max_age = 14 if kind == "valuation_daily_pb" else 62
            if not snapshot:
                self._write_cached(kind, key, {"ok": True, "value": {}}, ttl=300)
                return {}
            if snapshot.get("symbol") != symbol or sampled is None or not 0 <= (end - sampled).days <= max_age:
                raise ValueError("官方快照代码、日期无效或已过期")
            self._write_cached(kind, key, {"ok": True, "value": snapshot})
            return snapshot
        except Exception as exc:  # noqa: BLE001, RUF100 — 快照网络与 PDF 解析边界独立降级
            logger.warning("官方估值源 %s %s 失败: %s", kind, symbol, exc)
            self._write_cached(kind, key, {"ok": False, "error": str(exc)}, ttl=300)
            raise

    def fetch(self, symbol: str, provider: str = "csi", end: date | None = None) -> IndexValuationData | None:
        end = end or datetime.now().astimezone().date()
        notes = ["公开官方数据未提供可验证的 PB 日频历史，PB 历史分位不可用"]
        history: dict[date, float | None] = {}
        if provider != "cni":
            try:
                for row in self.fetch_official_history(symbol, end):
                    sampled = _date(row.get("tradeDate"))
                    if (str(row.get("indexCode")) != symbol or sampled is None
                        or not _start(end) <= sampled <= end or sampled.weekday() >= 5
                        or _positive(row.get("close")) is None):
                        continue
                    history[sampled] = _positive(row.get("peg"))
                if not history:
                    notes.append("官方日频滚动 PE 数据不可用")
            except Exception as exc:  # noqa: BLE001 — 官方网络边界独立降级
                logger.warning("策略估值历史源失败 %s: %s", symbol, exc)
                notes.append("官方日频滚动 PE 请求失败，请稍后重试")
        else:
            notes.append("国证公开单张 PE 未声明 TTM，不能计算滚动 PE 历史分位")
        snapshots = []
        for source in (self._factsheet,):
            try:
                snapshot = self._source_snapshot("valuation_factsheet", symbol, provider, end, lambda source=source: source(symbol, provider, end))
                if snapshot:
                    snapshots.append(snapshot)
                else:
                    notes.append("官方月度估值单张暂不可用或未提供估值")
            except Exception as exc:  # noqa: BLE001 — 单张网络和 PDF 边界独立降级
                logger.warning("策略估值单张失败 %s: %s", symbol, exc)
                notes.append("官方月度估值单张请求失败，请稍后重试")
        if symbol in {"000015", "000922"}:
            try:
                snapshot = self._source_snapshot("valuation_daily_pb", symbol, provider, end, lambda: self._current_pb(symbol, end))
                if snapshot:
                    snapshots.append(snapshot)
            except Exception as exc:  # noqa: BLE001 — 每日估值网络边界独立降级
                logger.warning("官方每日 PB 源失败 %s: %s", symbol, exc)
        latest = max(history) if history else None
        pe = history[latest] if latest else None
        samples = sorted((d, value) for d, value in history.items() if value is not None)
        dates = [latest] if latest else []
        values: dict[str, Any] = {"pe_ttm": pe, "pe_as_of": latest if pe is not None else None,
            "pe_sample_count": len(samples), "pe_basis": "滚动市盈率（官方日频 peg）" if history else "",
            "pe_source_url": HISTORY_URL if history else "", "valuation_valid": False,
            "percentile_sample_start": samples[0][0] if samples else None,
            "percentile_sample_end": samples[-1][0] if samples else None}
        if latest and (end - latest).days > 14:
            notes.append(f"官方日频 PE 数据过期，最新日期为 {latest}，不计算当前分位")
        elif pe is not None and len(samples) >= 252:
            values["pe_percentile"] = compute_percentile(pe, [v for _, v in samples])
            values["valuation_valid"] = True
        elif history:
            notes.append("最新交易日 PE 缺失或有效样本不足 252 个交易日，不计算历史分位")
        for snapshot in snapshots:
            sampled = _date(snapshot.get("as_of"))
            if snapshot.get("symbol") != symbol or sampled is None or not 0 <= (end - sampled).days <= (14 if snapshot.get("source_url") == CURRENT_URL else 62):
                notes.append("官方快照代码、日期无效或已过期，已拒绝使用")
                continue
            pb = _positive(snapshot.get("pb"))
            if pb is None:
                notes.append("官方单张未披露 PB 或布局不可解析")
            if pb is not None and (values.get("pb_as_of") is None or sampled >= values["pb_as_of"]):
                values.update(pb=pb, pb_as_of=sampled, pb_basis=snapshot.get("pb_basis") or "官方月度单张",
                              pb_source_url=snapshot.get("source_url") or "")
                dates.append(sampled)
            snapshot_pe = _positive(snapshot.get("pe_snapshot"))
            if snapshot_pe is not None:
                values.update(pe_snapshot=snapshot_pe, pe_snapshot_as_of=sampled,
                              pe_snapshot_basis=snapshot.get("pe_basis") or "官方单张未声明 TTM",
                              pe_snapshot_source_url=snapshot.get("source_url") or "")
                if not history:
                    values.update(pe_basis=snapshot.get("pe_basis") or "官方单张未声明 TTM",
                                  pe_source_url=snapshot.get("source_url") or "")
                dates.append(sampled)
        if values.get("pb") is None:
            notes.append("当前官方 PB 快照不可用")
        result = IndexValuationData(symbol=symbol, date=max(dates) if dates else end, valuation_notes=notes, **values)
        return result
