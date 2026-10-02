"""原有雷达数据库中的日线仓；记录实际覆盖而非推断全历史。"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pandas as pd

from radar.data import FallbackETFDataProvider, RadarDataError, RadarDataProvider

logger = logging.getLogger(__name__)

_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount"]


@dataclass(frozen=True)
class HistoryKey:
    """数据源、上市市场和复权口径共同构成不可混用的序列。"""
    source: str
    market: str
    adjustment: str
    symbol: str

    @property
    def values(self) -> tuple[str, str, str, str]:
        return self.source, self.market, self.adjustment, self.symbol


def normalize_history(frame: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """排序去重并阻止非法数值进入评分和缓存。"""
    required = {"date", "open", "high", "low", "close", "amount"}
    if missing := required - set(frame.columns):
        raise RadarDataError(f"日线缺少字段: {', '.join(sorted(missing))}")
    result = frame.copy()
    try:
        result["date"] = pd.to_datetime(result["date"], errors="raise").dt.date
    except (ValueError, TypeError) as exc:
        raise RadarDataError("日线日期格式无效") from exc
    if "volume" not in result:
        result["volume"] = float("nan")
    for column in _COLUMNS[1:]:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = cast(pd.DataFrame, result.loc[(result["date"] >= start) & (result["date"] <= end), :])
    result = result.drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)
    if result.empty:
        raise RadarDataError("日线在请求区间没有数据")
    prices: Any = result[["open", "high", "low", "close", "amount"]]
    if prices.isna().any().any() or not ((prices < float("inf")) & (prices > -float("inf"))).all().all():
        raise RadarDataError("日线含有无效价格或成交额")
    if (result[["open", "high", "low", "close"]] <= 0).any().any() or (result["amount"] < 0).any():
        raise RadarDataError("日线含有无效价格或成交额")
    if (result["high"] < result[["open", "close", "low"]].max(axis=1)).any() or (result["low"] > result[["open", "close"]].min(axis=1)).any():
        raise RadarDataError("日线最高最低价格关系无效")
    return cast(pd.DataFrame, result[_COLUMNS])


class HistoryStore:
    """同库增加行情与覆盖表，用户导入文件保持独立。"""
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS daily_prices (
                    source TEXT NOT NULL, market TEXT NOT NULL, adjustment TEXT NOT NULL,
                    symbol TEXT NOT NULL, date TEXT NOT NULL,
                    open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL,
                    PRIMARY KEY(source, market, adjustment, symbol, date)
                );
                CREATE TABLE IF NOT EXISTS daily_coverage (
                    source TEXT NOT NULL, market TEXT NOT NULL, adjustment TEXT NOT NULL,
                    symbol TEXT NOT NULL, first_date TEXT, last_date TEXT, row_count INTEGER,
                    requested_start TEXT NOT NULL, requested_end TEXT NOT NULL,
                    missing_dates TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL,
                    PRIMARY KEY(source, market, adjustment, symbol)
                );
                CREATE TABLE IF NOT EXISTS daily_target_series (
                    symbol TEXT NOT NULL, market TEXT NOT NULL, provider TEXT NOT NULL,
                    target_date TEXT NOT NULL, source TEXT NOT NULL, adjustment TEXT NOT NULL,
                    PRIMARY KEY(symbol, market, provider, target_date)
                );
                CREATE TABLE IF NOT EXISTS daily_selected_series (
                    symbol TEXT NOT NULL, market TEXT NOT NULL, provider TEXT NOT NULL,
                    source TEXT NOT NULL, adjustment TEXT NOT NULL,
                    PRIMARY KEY(symbol, market, provider)
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        return conn

    def read(self, key: HistoryKey, start: date, end: date) -> pd.DataFrame:
        with closing(self._connect()) as conn:
            rows = conn.execute("""SELECT date, open, high, low, close, volume, amount FROM daily_prices
                WHERE source=? AND market=? AND adjustment=? AND symbol=? AND date BETWEEN ? AND ? ORDER BY date""",
                (*key.values, start.isoformat(), end.isoformat())).fetchall()
        result = pd.DataFrame([dict(row) for row in rows], columns=_COLUMNS)
        if not result.empty:
            result["date"] = pd.to_datetime(result["date"]).dt.date
        return result

    def coverage(self, key: HistoryKey) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM daily_coverage WHERE source=? AND market=? AND adjustment=? AND symbol=?", key.values).fetchone()
        return {**dict(row), "missing_dates": json.loads(row["missing_dates"]), "complete_since_inception": False} if row else None

    def select(self, key: HistoryKey, provider: str, target: date | None = None) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("INSERT OR REPLACE INTO daily_selected_series VALUES (?, ?, ?, ?, ?)", (key.symbol, key.market, provider, key.source, key.adjustment))
            if target is not None:
                conn.execute("INSERT OR REPLACE INTO daily_target_series VALUES (?, ?, ?, ?, ?, ?)", (key.symbol, key.market, provider, target.isoformat(), key.source, key.adjustment))

    def selected(self, symbol: str, market: str, provider: str, target: date | None = None) -> HistoryKey | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM daily_target_series WHERE symbol=? AND market=? AND provider=? AND target_date=?", (symbol, market, provider, target.isoformat())).fetchone() if target else None
            if row is None:
                row = conn.execute("SELECT * FROM daily_selected_series WHERE symbol=? AND market=? AND provider=?", (symbol, market, provider)).fetchone()
        return HistoryKey(row["source"], market, row["adjustment"], symbol) if row else None

    def merge(self, key: HistoryKey, frame: pd.DataFrame, start: date, end: date, *, invalidate: bool = False, missing_dates: Sequence[date] = ()) -> None:
        """修订重叠行，复权变更时原子清除旧基准数据。"""
        normalized = normalize_history(frame, start, end)
        now = datetime.now().astimezone().isoformat()
        with closing(self._connect()) as conn, conn:
            previous = conn.execute("SELECT requested_start, requested_end FROM daily_coverage WHERE source=? AND market=? AND adjustment=? AND symbol=?", key.values).fetchone()
            if invalidate:
                conn.execute("DELETE FROM daily_prices WHERE source=? AND market=? AND adjustment=? AND symbol=?", key.values)
            values = [(*key.values, row[0].isoformat(), *(None if pd.isna(value) else float(value) for value in row[1:])) for row in normalized.itertuples(index=False, name="Price")]
            conn.executemany("INSERT OR REPLACE INTO daily_prices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
            coverage = conn.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM daily_prices WHERE source=? AND market=? AND adjustment=? AND symbol=?", key.values).fetchone()
            requested_start = min(start.isoformat(), previous["requested_start"]) if previous and not invalidate else start.isoformat()
            requested_end = max(end.isoformat(), previous["requested_end"]) if previous and not invalidate else end.isoformat()
            conn.execute("INSERT OR REPLACE INTO daily_coverage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (*key.values, coverage[0], coverage[1], coverage[2], requested_start, requested_end, json.dumps([d.isoformat() for d in missing_dates]), now))


class HistorySynchronizer:
    """补齐评分所需区间和内部缺口，正常刷新重取尾部重叠。"""
    def __init__(self, store: HistoryStore, provider: RadarDataProvider):
        self.store = store
        self.provider = provider
        self.provider_id = type(provider).__name__

    def prepare(self, symbol: str, market: str, start: date, end: date, *, reuse: bool = False, trading_days: Sequence[date] | None = None) -> tuple[pd.DataFrame, str]:
        key = self.store.selected(symbol, market, self.provider_id, end)
        coverage = self.store.coverage(key) if key else None
        # 同步范围保留原实际覆盖前缀；评分仍只返回调用者窗口。
        sync_start = min(start, date.fromisoformat(coverage["first_date"])) if coverage else start
        cached = self.store.read(key, sync_start, end) if key else pd.DataFrame(columns=_COLUMNS)
        known_gaps = {date.fromisoformat(day) for day in coverage["missing_dates"] if date.fromisoformat(day) <= end} if coverage else set()
        if reuse and key is not None:
            if coverage is None or coverage["requested_start"] > start.isoformat():
                raise RadarDataError("缓存未覆盖请求开始日期，需要扩展历史行情")
            self._validate(cached, sync_start, end, trading_days)
            unresolved = known_gaps - set(cached["date"])
            if unresolved:
                raise RadarDataError("行情缺失交易日: " + ", ".join(str(day) for day in sorted(unresolved)[:5]))
            return self.store.read(key, start, end), key.source
        request_start = sync_start
        if coverage and coverage["requested_start"] <= start.isoformat() and not cached.empty:
            request_start = max(sync_start, end - timedelta(days=10))
        missing = set(self._gaps(cached, sync_start, end, trading_days)) | (known_gaps - set(cached["date"]))
        if missing:
            request_start = min(request_start, min(missing))
        downloaded, source, adjustment = self._fetch(symbol, request_start, end, trading_days)
        new_key = HistoryKey(source, market, adjustment, symbol)
        if key != new_key and request_start > sync_start:
            downloaded, source, adjustment = self._fetch(symbol, sync_start, end, trading_days)
            new_key = HistoryKey(source, market, adjustment, symbol)
            request_start = sync_start
        downloaded = normalize_history(downloaded, request_start, end)
        old = self.store.read(new_key, sync_start, end)
        changed_adjustment = adjustment in {"qfq", "hfq", "unknown"} and self._overlap_changed(old, downloaded)
        sync_end = end
        if changed_adjustment:
            previous_coverage = self.store.coverage(new_key)
            request_start = min(sync_start, date.fromisoformat(previous_coverage["first_date"])) if previous_coverage else sync_start
            # 较早历史查询也必须重建已缓存尾部，避免原子失效删除最近目标日。
            sync_end = max(end, date.fromisoformat(previous_coverage["last_date"])) if previous_coverage else end
            downloaded, refetched_source, refetched_adjustment = self._fetch(symbol, request_start, sync_end, trading_days)
            if (refetched_source, refetched_adjustment) != (source, adjustment):
                raise RadarDataError("复权修订重取时数据源发生变化，请重试")
            downloaded = normalize_history(downloaded, request_start, sync_end)
            # 重建成功且完整后才能失效旧序列；失败时保留原健康缓存。
            self._validate(downloaded, request_start, sync_end, trading_days)
            rebuild_gaps = {date.fromisoformat(day) for day in previous_coverage["missing_dates"]} if previous_coverage else set()
            if unresolved := rebuild_gaps - set(downloaded["date"]):
                raise RadarDataError("行情缺失交易日: " + ", ".join(str(day) for day in sorted(unresolved)[:5]))
        combined = downloaded if changed_adjustment or old.empty else pd.concat([old, downloaded], ignore_index=True).drop_duplicates("date", keep="last").sort_values("date")
        gaps = set(self._gaps(combined, sync_start, sync_end, trading_days))
        if key == new_key:
            gaps |= known_gaps - set(combined["date"])
        self.store.merge(new_key, downloaded, request_start, sync_end, invalidate=changed_adjustment, missing_dates=sorted(gaps))
        self.store.select(new_key, self.provider_id)
        result = self.store.read(new_key, sync_start, end)
        self._validate(result, sync_start, end, trading_days)
        if gaps:
            raise RadarDataError("行情缺失交易日: " + ", ".join(str(day) for day in sorted(gaps)[:5]))
        self.store.select(new_key, self.provider_id, end)
        return self.store.read(new_key, start, end), source

    def read_or_sync(self, symbol: str, market: str, start: date, end: date, *, trading_days: Sequence[date] | None = None) -> tuple[pd.DataFrame, str]:
        """查询先使用覆盖缓存；仅缺口、陈旧或更早范围才补齐。"""
        try:
            return self.prepare(symbol, market, start, end, reuse=True, trading_days=trading_days)
        except RadarDataError as exc:
            logger.debug("历史区间缓存需补齐 %s: %s", symbol, exc)
        return self.prepare(symbol, market, start, end, trading_days=trading_days)

    def _fetch(self, symbol: str, start: date, end: date, trading_days: Sequence[date] | None = None) -> tuple[pd.DataFrame, str, str]:
        if isinstance(self.provider, FallbackETFDataProvider):
            frame, source = self.provider.fetch_daily_with_source(symbol, start, end, require_target=True, trading_days=trading_days)
        else:
            frame = self.provider.fetch_daily(symbol, start, end)
            source = str(getattr(self.provider, "source_label", type(self.provider).__name__))
        adjustment = str(frame.attrs.get("adjustment", getattr(self.provider, "adjustment", "unknown")))
        return frame, source, adjustment

    @staticmethod
    def _overlap_changed(old: pd.DataFrame, new: pd.DataFrame) -> bool:
        if old.empty:
            return False
        joined = old.merge(new, on="date", suffixes=("_old", "_new"))
        return any(bool(((joined[f"{column}_old"] - joined[f"{column}_new"]).abs() > 1e-8).any()) for column in ("open", "high", "low", "close"))

    @staticmethod
    def _gaps(frame: pd.DataFrame, start: date, end: date, trading_days: Sequence[date] | None) -> list[date]:
        if not trading_days or frame.empty:
            return []
        actual_start = max(start, min(frame["date"]))
        return sorted({d for d in trading_days if actual_start <= d <= end} - set(frame["date"]))

    def _validate(self, frame: pd.DataFrame, start: date, end: date, trading_days: Sequence[date] | None) -> None:
        if frame.empty or frame.iloc[-1]["date"] != end:
            raise RadarDataError(f"行情尚未更新到目标交易日 {end.isoformat()}")
        if gaps := self._gaps(frame, start, end, trading_days):
            raise RadarDataError(f"行情缺失交易日: {', '.join(d.isoformat() for d in gaps[:5])}")
