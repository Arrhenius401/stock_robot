"""中证行情与 PE 共用的历史产物，覆盖区间独立于实际交易记录。"""
import json
import logging
import time
from datetime import date, timedelta
from typing import Any

from index.strategy_data import _date, finite_number

logger = logging.getLogger(__name__)
HISTORY_URL = "https://www.csindex.com.cn/csindex-home/perf/index-perf"


def history_start(end: date) -> date:
    """五个日历年，兼容闰日。"""
    try:
        return end.replace(year=end.year - 5)
    except ValueError:
        return end.replace(year=end.year - 5, day=28)


class OfficialHistoryStore:
    """持久产物用于增量更新，短期状态用于限制上游请求。"""
    def __init__(self, provider: Any):
        self.provider = provider

    def fetch(self, symbol: str, end: date) -> list[dict]:
        start = history_start(end)
        status_key = f"{symbol}:{end}"
        status = self.provider._read_cached("csi_history_status", status_key)
        raw = self.provider._cache.get_stale("csi_history_artifact", symbol, "v2")
        artifact: Any = json.loads(raw) if raw else None
        covered_start = _date(artifact["covered_start"]) if artifact else None
        covered_end = _date(artifact["covered_end"]) if artifact else None
        covered = bool(covered_start and covered_end and covered_start <= start and end <= covered_end)
        now = time.time()
        calibration_due = bool(artifact and (
            now - artifact["calibrated_at"] >= 30 * 86400
            or (end - date.fromisoformat(artifact["calibrated_end"])).days >= 30))
        # 新成功产物可覆盖旧失败，但历史切片也必须遵守周期校准。
        if covered and covered_end and end < covered_end and not calibration_due:
            return self._slice(artifact["rows"], symbol, start, end)
        if status is not None and not status["ok"]:
            raise RuntimeError(status["error"])
        if status is not None and covered:
            return self._slice(artifact["rows"], symbol, start, end)
        full = (artifact is None or covered_start is None or covered_start > start
                or calibration_due
                or covered_end is None or end < covered_end)
        request_start = start if full or covered_end is None else max(start, covered_end - timedelta(days=9))
        previous: dict = artifact or {}
        try:
            response = self.provider._get_json(HISTORY_URL, {
                "indexCode": symbol, "startDate": request_start.strftime("%Y%m%d"),
                "endDate": end.strftime("%Y%m%d"),
            })
            incoming = response.get("data")
            if not isinstance(incoming, list):
                raise TypeError("官方历史响应缺少行情列表")
            rows = self._slice(incoming, symbol, request_start, end)
            # 空响应无法证明重叠区间已被校准，保留既有成功历史。
            if not rows:
                raise ValueError("官方历史响应没有有效交易记录")
            retained = [] if full else [r for r in previous["rows"] if (_date(r.get("tradeDate")) or date.min) < request_start]
            merged = self._slice(retained + rows, symbol, start if full or covered_start is None else covered_start, end)
            value = {"covered_start": start.isoformat() if full else previous["covered_start"], "covered_end": end.isoformat(),
                     "calibrated_at": now if full else previous["calibrated_at"],
                     "calibrated_end": end.isoformat() if full else previous["calibrated_end"], "rows": merged}
            self.provider._write_cached("csi_history_artifact", symbol, value, ttl=-1)
            self.provider._write_cached("csi_history_status", status_key, {"ok": True})
            return self._slice(merged, symbol, start, end)
        except Exception as exc:  # noqa: BLE001, RUF100 — 官方网络及响应边界，保留成功产物并显式降级
            logger.warning("共享官方历史 %s 失败: %s", symbol, exc)
            message = f"官方历史请求失败或响应无效: {exc}"
            self.provider._write_cached("csi_history_status", status_key, {"ok": False, "error": message}, ttl=300)
            raise RuntimeError(message) from exc

    @staticmethod
    def _slice(rows: list[dict], symbol: str, start: date, end: date) -> list[dict]:
        found = {}
        for row in rows:
            sampled = _date(row.get("tradeDate"))
            close = finite_number(row.get("close"))
            if (str(row.get("indexCode")) == symbol and sampled is not None
                    and start <= sampled <= end and sampled.weekday() < 5 and close is not None and close > 0):
                found[sampled] = row
        return [found[d] for d in sorted(found)]
