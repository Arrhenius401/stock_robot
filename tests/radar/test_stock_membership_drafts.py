"""评估引用的成员草案需持久化且始终不能绕过发布门槛。"""

from datetime import UTC, date, datetime
from typing import Any

import pytest

from radar.stocks.membership_drafts import StockMembershipDraftStore
from radar.stocks.pools import StockPoolRepository
from radar.stocks.research_store import (
    StockResearchError,
    StockResearchStore,
    StockRevisionConflict,
)
from stock_robot.stock_admission import evaluate_store

AS_OF = date(2026, 10, 15)


def prepare(tmp_path):
    from radar.stocks.evaluation_store import StockEvaluationStore

    research = StockResearchStore(tmp_path / "research.db", clock=lambda: datetime(2026, 10, 15, 1, tzinfo=UTC))
    research.import_companies([{"symbol": "600900", "name": "合成公司"}], source_sha256="a" * 64, source_label="合成研究", data_as_of=AS_OF)
    archive = StockEvaluationStore(research)
    inputs = archive.capture_inputs()
    report = evaluate_store(research, AS_OF, include_styles=True)
    saved = archive.save_evaluation(report, inputs)
    directory = tmp_path / "pools"
    directory.mkdir()
    for index, name in enumerate(("dividend_value", "growth_trend")):
        (directory / f"{name}.yaml").write_text(f"id: {name}\nname: 合成池\ndescription: 合成测试\ndisplay_order: {index}\n", encoding="utf-8")
    drafts = StockMembershipDraftStore(archive, StockPoolRepository(directory))
    return drafts, saved["evaluation_id"], research


def changes(pool="dividend_value") -> list[dict[str, Any]]:
    return [{"pool_id": pool, "symbol": "600900", "action": "add", "reason": "合成关注理由"}]


def create(store, evaluation_id, **kwargs):
    return store.create(evaluation_id=evaluation_id, changes=changes(), request_id="request-1", actor="合成用户", **kwargs)


def test_create_blocked_draft_and_replay_without_publishing(tmp_path):
    store, evaluation_id, research = prepare(tmp_path)
    before = research.summary()
    first = create(store, evaluation_id)
    second = create(store, evaluation_id)
    assert first["draft_id"] == second["draft_id"]
    assert first["revision"] == 1
    assert first["status"] == "blocked"
    assert first["publication_enabled"] is False
    assert first["members_published"] == 0
    assert first["evaluation_current"] is True
    assert "formal_screening_disabled" in first["changes"][0]["blockers"]
    assert len(second["events"]) == 1
    assert research.summary() == before
    assert store.list_drafts()["total"] == 1


def test_same_request_different_payload_conflicts(tmp_path):
    store, evaluation_id, _ = prepare(tmp_path)
    create(store, evaluation_id)
    with pytest.raises(StockResearchError):
        store.create(evaluation_id=evaluation_id, changes=changes("growth_trend"), request_id="request-1", actor="合成用户")
    assert store.list_drafts()["total"] == 1


def test_cancel_is_revision_checked_and_preserves_history(tmp_path):
    store, evaluation_id, _ = prepare(tmp_path)
    first = create(store, evaluation_id)
    cancelled = store.cancel(first["draft_id"], expected_revision=1, actor="合成用户", reason="取消关注")
    assert cancelled["status"] == "cancelled"
    assert cancelled["revision"] == 2
    assert [event["operation"] for event in cancelled["events"]] == ["created", "cancelled"]
    with pytest.raises(StockRevisionConflict):
        store.cancel(first["draft_id"], expected_revision=1, actor="合成用户", reason="重复旧请求")
    current = store.get(first["draft_id"])
    assert current is not None
    assert len(current["events"]) == 2


def test_company_revision_change_marks_old_draft_stale_and_blocks_new(tmp_path):
    store, evaluation_id, research = prepare(tmp_path)
    first = create(store, evaluation_id)
    current = research.get_company("600900")
    assert current is not None
    research.add_evidence("600900", kind="research_hint", payload={"text": "新资料"}, source_url="https://example.com/new.pdf",
                          disclosed_on=AS_OF, content_sha256="b" * 64, expected_revision=current["revision"])
    stale = store.get(first["draft_id"])
    assert stale is not None
    assert stale["evaluation_current"] is False
    with pytest.raises(StockRevisionConflict):
        store.create(evaluation_id=evaluation_id, changes=changes(), request_id="request-2", actor="合成用户")
    assert store.list_drafts()["total"] == 1
    assert create(store, evaluation_id)["draft_id"] == first["draft_id"]


@pytest.mark.parametrize("mutation", ["unknown_pool", "other_company", "duplicate", "restore", "remove", "empty", "unknown_field", "empty_reason"])
def test_invalid_batch_never_partially_writes(tmp_path, mutation):
    store, evaluation_id, _ = prepare(tmp_path)
    payload = changes()
    if mutation == "unknown_pool":
        payload.append({**payload[0], "pool_id": "unknown"})
    elif mutation == "other_company":
        payload.append({**payload[0], "symbol": "000001"})
    elif mutation == "duplicate":
        payload *= 2
    elif mutation in {"restore", "remove"}:
        payload[0]["action"] = mutation
    elif mutation == "empty":
        payload = []
    elif mutation == "unknown_field":
        payload[0]["eligible"] = True
    else:
        payload[0]["reason"] = " "
    with pytest.raises(StockResearchError):
        store.create(evaluation_id=evaluation_id, changes=payload, request_id="invalid", actor="合成用户")
    assert store.list_drafts()["total"] == 0


def test_two_pools_share_company_but_preserve_separate_draft_changes(tmp_path):
    store, evaluation_id, _ = prepare(tmp_path)
    result = store.create(evaluation_id=evaluation_id, changes=changes()+changes("growth_trend"), request_id="two-pools", actor="测试")
    assert len(result["changes"]) == 2
    assert result["members_published"] == 0


@pytest.mark.parametrize("page,size", [(0,15),(1,0),(True,15),(1,False)])
def test_invalid_pagination_is_rejected(tmp_path, page, size):
    store, _, _ = prepare(tmp_path)
    with pytest.raises(StockResearchError):
        store.list_drafts(page=page, page_size=size)


def test_unknown_draft_and_huge_page(tmp_path):
    store, _, _ = prepare(tmp_path)
    assert store.get("a" * 64) is None
    assert store.list_drafts(page=10**100)["items"] == []
    with pytest.raises(StockResearchError):
        store.cancel("a" * 64, expected_revision=1, actor="用户", reason="取消")


def test_concurrent_replays_create_one_draft_and_one_event(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    store, evaluation_id, _ = prepare(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: create(store, evaluation_id), range(2)))
    assert len({result["draft_id"] for result in results}) == 1
    assert store.list_drafts()["total"] == 1
    assert len(results[-1]["events"]) == 1


def test_partial_evaluation_cannot_become_draft_basis(tmp_path):
    store, _, research = prepare(tmp_path)
    inputs = store.evaluations.capture_inputs()
    report = evaluate_store(research, AS_OF, include_styles=True)
    report["errors"] = [{"symbol": "600900", "reason": "合成失败"}]
    report["items"][0]["evaluation_valid"] = False
    saved = store.evaluations.save_evaluation(report, inputs)
    assert saved["status"] == "partial"
    with pytest.raises(StockResearchError):
        create(store, saved["evaluation_id"])
    assert store.list_drafts()["total"] == 0


def test_draft_schema_version_does_not_silently_migrate_unknown_version(tmp_path):
    store, _, research = prepare(tmp_path)
    with research._write() as connection:
        connection.execute("UPDATE stocks_member_draft_versions SET version=2")
    with pytest.raises(StockResearchError):
        StockMembershipDraftStore(store.evaluations, store.pools)
