from data.collection_state import (
    CACHE_STATE,
    make_record,
    record_consistent,
    with_result_state,
)
from data.schemas import DataCollectionRecord


def test_cache_state_projection_covers_all_result_states():
    assert CACHE_STATE == {
        "complete": "complete", "partial": "degraded", "empty": "complete",
        "not_applicable": "not_applicable", "failed": "degraded", "unknown": "unknown",
    }


def test_make_record_sets_state_and_preserves_diagnostic():
    diagnostic = DataCollectionRecord(error_kind="incomplete", error_message="缺项")
    record = make_record("测试源", "partial", diagnostic=diagnostic)
    assert (record.source, record.result_state, record.cache_state) == ("测试源", "partial", "degraded")
    assert (record.error_kind, record.error_message, record.collected_at) == ("incomplete", "缺项", None)
    assert with_result_state(record, "complete").cache_state == "complete"


def test_record_consistency_rejects_contradiction_but_accepts_old_records():
    assert record_consistent(DataCollectionRecord(result_state="complete", cache_state="complete"))
    assert not record_consistent(DataCollectionRecord(result_state="complete", cache_state="degraded"))
    assert record_consistent(DataCollectionRecord(result_state="unknown", cache_state="complete"))
