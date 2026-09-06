"""将 ETF 数据、评分和不可变快照串联的刷新服务。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal, cast

import pandas as pd

from radar.data import RadarDataProvider
from radar.models import SnapshotItem
from radar.scoring import score_etfs
from radar.store import RadarStore
from radar.universe import UniverseRepository


class RadarRefresher:
    """同步刷新一个指定标的池；查询端始终只读完成态快照。"""

    def __init__(self, repository: UniverseRepository, provider: RadarDataProvider, store: RadarStore):
        self.repository = repository
        self.provider = provider
        self.store = store

    def refresh(self, universe_id: str, *, full: bool = False, as_of: date | None = None) -> str:
        """刷新一个池，部分失败降级，全失败不发布。"""
        target_date = as_of or datetime.now().astimezone().date()
        universe = self.repository.active_on(universe_id, target_date)
        if not self.provider.supports(universe.asset_type):
            raise ValueError(f"数据提供者不支持资产类型: {universe.asset_type}")
        run_id = self.store.create_run(
            universe_id=universe.id,
            universe_version=universe.version,
            score_profile=universe.score_profile,
            provider=type(self.provider).__name__,
            as_of_date=target_date.isoformat(),
        )
        histories: dict[str, Any] = {}
        failures: dict[str, str] = {}
        # 当前评分窗口最多依赖 61 个交易日；完整窗口让上游修订可被重新纳入。
        # 数据源未提供可靠的增量游标，默认也只拉取有限窗口而非全历史。
        history_days = 2_000 if full else 500
        start = target_date - timedelta(days=history_days)
        for instrument in universe.instruments:
            try:
                histories[instrument.symbol] = self.provider.fetch_daily(instrument.symbol, start, target_date)
            except Exception as exc:  # noqa: BLE001 — 数据源单标的失败不应阻断其他标的
                failures[instrument.symbol] = str(exc)
        if not histories:
            self.store.fail_run(run_id, "所有标的日线获取失败")
            raise RuntimeError("所有标的日线获取失败，未发布新快照")
        categories = {item.symbol: item.category for item in universe.instruments if item.symbol in histories}
        scores = score_etfs(histories, categories).set_index("symbol")
        for instrument in universe.instruments:
            if instrument.symbol in failures:
                previous = self.store.copy_latest_healthy_item(universe.id, instrument.symbol)
                if previous is not None:
                    self.store.add_item(run_id, previous.model_copy(update={"status": "stale", "source_run_id": previous.source_run_id or run_id, "error_summary": failures[instrument.symbol]}))
                else:
                    self.store.add_item(run_id, SnapshotItem(symbol=instrument.symbol, name=instrument.name, category=instrument.category, status="failed", error_summary=failures[instrument.symbol]))
                continue
            row: Any = scores.loc[instrument.symbol]
            history: Any = histories[instrument.symbol]
            last: Any = history.iloc[-1]
            grade = cast(Literal["偏好", "观察", "谨慎", "unavailable"], str(row["grade"]))
            score = None if pd.isna(row["score"]) else float(row["score"])
            rank = None if pd.isna(row["rank"]) else int(row["rank"])
            self.store.add_item(run_id, SnapshotItem(symbol=instrument.symbol, name=instrument.name, category=instrument.category, status="fresh", observed_at=datetime.now().astimezone(), source_run_id=run_id, close=float(last["close"]), amount=float(last["amount"]), score=score, rank=rank, grade=grade))
        self.store.complete_run(run_id)
        return run_id
