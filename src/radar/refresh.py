"""将 ETF 数据、评分和不可变快照串联的刷新服务。"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

import pandas as pd

from radar.data import RadarDataProvider
from radar.history import HistoryStore, HistorySynchronizer
from radar.models import SnapshotItem
from radar.score_profile import ScoreProfileRepository
from radar.scoring import score_etfs
from radar.store import RadarStore
from radar.universe import UniverseRepository

logger = logging.getLogger(__name__)


class RadarRefreshCancelled(RuntimeError):
    """在标的之间或发布之前取消，已有健康快照继续可读。"""


class RadarRefresher:
    """同步刷新一个指定标的池；查询端始终只读完成态快照。"""

    def __init__(self, repository: UniverseRepository, provider: RadarDataProvider, store: RadarStore):
        self.repository = repository
        self.provider = provider
        self.store = store
        self.history = HistorySynchronizer(HistoryStore(store.db_path), provider)

    def refresh(
        self, universe_id: str, *, full: bool = False, as_of: date | None = None,
        retry_symbols: set[str] | None = None,
        on_phase: Callable[[str], None] | None = None,
        on_item: Callable[[str, bool, str | None], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        trading_days: Sequence[date] | None = None,
    ) -> str:
        """刷新一个池，部分失败降级，全失败不发布。"""
        target_date = as_of or datetime.now().astimezone().date()
        universe = self.repository.active_on(universe_id, target_date)
        if not self.provider.supports(universe.asset_type):
            raise ValueError(f"数据提供者不支持资产类型: {universe.asset_type}")
        previous = self.store.latest_completed(universe.id)
        replaces = previous["run_id"] if previous and previous["as_of_date"] == target_date.isoformat() and previous["universe_version"] == universe.version and previous["score_profile"] == universe.score_profile else None
        run_id = self.store.create_run(
            universe_id=universe.id,
            universe_version=universe.version,
            score_profile=universe.score_profile,
            provider=type(self.provider).__name__,
            as_of_date=target_date.isoformat(),
            replaces_run_id=replaces,
        )
        try:
            reset = getattr(self.provider, "reset_circuit", None)
            if reset is not None:
                reset()
            histories: dict[str, Any] = {}
            data_sources: dict[str, str] = {}
            failures: dict[str, str] = {}
            # 请求长度不代表全历史，覆盖表记录实际范围。
            history_days = 2_000 if full else 500
            start = target_date - timedelta(days=history_days)
            if on_phase:
                on_phase("preparing")
            for instrument in universe.instruments:
                self._check_cancel(should_cancel)
                try:
                    history, source = self.history.prepare(
                        instrument.symbol, instrument.market, start, target_date,
                        reuse=retry_symbols is not None and instrument.symbol not in retry_symbols,
                        trading_days=trading_days,
                    )
                    histories[instrument.symbol] = history
                    data_sources[instrument.symbol] = source
                except Exception as exc:  # noqa: BLE001 — 单标的网络或 SDK 失败不阻断其他项
                    logger.warning("标的 %s 行情准备失败: %s", instrument.symbol, exc)
                    failures[instrument.symbol] = str(exc)
                if on_item:
                    on_item(instrument.symbol, instrument.symbol not in failures, failures.get(instrument.symbol))
            self._check_cancel(should_cancel)
            if not histories:
                failure_details = _summarize_provider_failures(failures.values())
                detail = "；".join(failure_details[:4])
                if len(failure_details) > 4:
                    detail += f"；另有 {len(failure_details) - 4} 条不同错误"
                message = "所有标的日线获取失败" + (f"：{detail}" if detail else "")
                self.store.fail_run(run_id, message)
                raise RuntimeError(f"{message}，未发布新快照")
            if on_phase:
                on_phase("scoring")
            categories = {item.symbol: item.category for item in universe.instruments if item.symbol in histories}
            profile = ScoreProfileRepository(Path(__file__).parents[2] / "config" / "radar_score_profiles").get(universe.score_profile)
            scores = score_etfs(histories, categories, profile.weights, profile).set_index("symbol")
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
                factors = {
                    field: None if pd.isna(row.get(field)) else float(row[field])
                    for key in profile.weights
                    for field in (key, f"{key}_percentile", f"{key}_contribution")
                }
                self.store.add_item(
                    run_id,
                    SnapshotItem(
                        symbol=instrument.symbol,
                        name=instrument.name,
                        category=instrument.category,
                        status="fresh",
                        observed_at=datetime.now().astimezone(),
                        market_date=date.fromisoformat(str(last["date"])[:10]),
                        source_run_id=run_id,
                        data_source=data_sources[instrument.symbol],
                        close=float(last["close"]),
                        amount=float(last["amount"]),
                        score=score,
                        rank=rank,
                        grade=grade,
                        factors=factors,
                    ),
                )
            self._check_cancel(should_cancel)
            if on_phase:
                on_phase("publishing")
            self._check_cancel(should_cancel)
            self.store.complete_run(run_id)
            return run_id
        except Exception as exc:  # 运行隔离边界：记录审计后重新抛出，不吞掉 SDK/评分错误
            logger.warning("雷达运行 %s 未发布: %s", run_id, exc)
            self.store.fail_run(run_id, str(exc))
            raise

    @staticmethod
    def _check_cancel(should_cancel: Callable[[], bool] | None) -> None:
        if should_cancel and should_cancel():
            raise RadarRefreshCancelled("采集已在安全边界取消，未发布新快照")



def _summarize_provider_failures(failures: Any) -> list[str]:
    """合并同一提供器的重复熔断信息，优先保留首次可诊断错误。"""
    representatives: dict[str, str] = {}
    for failure in failures:
        for item in str(failure).split("；"):
            provider, separator, message = item.partition(":")
            key = provider if separator else item
            previous = representatives.get(key)
            if previous is None or ("已熔断" in previous and "已熔断" not in message):
                representatives[key] = item
    return list(representatives.values())
