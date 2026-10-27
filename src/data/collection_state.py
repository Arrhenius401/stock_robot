"""共享结果状态与采集记录构造。"""
from datetime import datetime
from typing import Literal

from data.schemas import DataCollectionRecord, ResultState

State = Literal["complete", "degraded", "not_applicable", "unknown"]
CacheStatus = Literal["live", "cache", "stale", "unknown"]
CACHE_STATE: dict[ResultState, State] = {
    "complete": "complete", "partial": "degraded", "empty": "complete",
    "not_applicable": "not_applicable", "failed": "degraded", "unknown": "unknown",
}


def make_record(
    source: str, state: ResultState, *, diagnostic: DataCollectionRecord | None = None,
    collected_at: datetime | None = None, cache_status: CacheStatus = "live",
    error_kind: str = "", error_message: str = "",
) -> DataCollectionRecord:
    return DataCollectionRecord(
        source=source, collected_at=collected_at, cache_status=cache_status,
        error_kind=error_kind or (diagnostic.error_kind if diagnostic else ""),
        error_message=error_message or (diagnostic.error_message if diagnostic else ""),
        cache_state=CACHE_STATE[state], result_state=state,
    )


def with_result_state(record: DataCollectionRecord, state: ResultState) -> DataCollectionRecord:
    return record.model_copy(update={"cache_state": CACHE_STATE[state], "result_state": state})


def record_consistent(record: DataCollectionRecord) -> bool:
    return record.result_state == "unknown" or record.cache_state == CACHE_STATE.get(record.result_state)
