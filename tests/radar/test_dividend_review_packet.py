"""审核资料包保留缺口与建议来源，不自动填入规范证据。"""

from copy import deepcopy
from datetime import date
from typing import Any

import pytest

from radar.stocks.dividend_review import build_review_packet

CUTOFF = date(2026, 10, 14)


def inputs() -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any]]:
    companies = [{"symbol": "600900", "name": "长江电力"}, {"symbol": "000333", "name": "美的集团"}]
    proposal = {"symbol": "600900", "source_url": "https://static.cninfo.com.cn/a.pdf",
                "disclosed_on": "2026-07-01", "fiscal_year": 2025, "amount_yuan": "100",
                "share_class": "A", "status": "matched_template", "requires_review": True}
    item = {"symbol": "600900", "source_url": proposal["source_url"], "disclosed_on": proposal["disclosed_on"],
            "file_sha256": "a" * 64, "text_sha256": "b" * 64, "status": "matched_template",
            "event_proposals": [proposal], "reasons": []}
    return companies, {"items": [item], "event_proposals": [proposal]}, {"companies": []}, {"items": []}


def test_packet_keeps_empty_companies_and_does_not_promote_proposals():
    args = inputs()
    original = deepcopy(args)
    packet = build_review_packet(*args, CUTOFF)
    assert len(packet["companies"]) == 2
    first, second = packet["companies"]
    assert first["years"] == [2023, 2024, 2025]
    assert first["proposals"][0]["fields"]["amount_yuan"] == "100"
    assert first["proposals"][0]["origin"]["file_sha256"] == "a" * 64
    assert first["evidence"]["events"] == first["evidence"]["profits"] == first["evidence"]["coverage"] == []
    assert second["proposals"] == []
    assert second["research_status"] == "pending_verification"
    assert "全部股类范围尚未核实" in second["unresolved"]
    assert packet["requires_review"] is True
    assert packet["rule_enabled"] is packet["financial_verified"] is False
    assert args == original
    first["proposals"][0]["fields"]["amount_yuan"] = "changed"
    assert args == original


def test_proposal_ids_are_stable_deduplicate_exact_proposals_and_bind_original():
    companies, extraction, prepared, research = inputs()
    original = build_review_packet(companies, extraction, prepared, research, CUTOFF)
    extraction["items"].append(deepcopy(extraction["items"][0]))
    repeated = build_review_packet(companies[::-1], extraction, prepared, research, CUTOFF)
    proposal = next(row for row in repeated["companies"] if row["symbol"] == "600900")["proposals"]
    assert len(proposal) == 1
    assert proposal[0]["proposal_id"] == original["companies"][0]["proposals"][0]["proposal_id"]
    extraction["items"][0]["file_sha256"] = "c" * 64
    changed = build_review_packet(companies, extraction, prepared, research, CUTOFF)
    assert len(changed["companies"][0]["proposals"]) == 2
    assert changed["revision_sha256"] != original["revision_sha256"]


@pytest.mark.parametrize("field,value", [("symbol", "000333"), ("source_url", "https://other.example/a.pdf"),
                                         ("disclosed_on", "2026-08-01"), ("amount_yuan", float("nan"))])
def test_bad_or_cross_company_proposal_is_rejected_with_diagnostic(field, value):
    args = inputs()
    args[1]["items"][0]["event_proposals"][0][field] = value
    packet = build_review_packet(*args, CUTOFF)
    assert packet["companies"][0]["proposals"] == []
    assert packet["diagnostics"]


def test_flattened_suggestions_cannot_bypass_item_provenance():
    args = inputs()
    args[1]["items"] = []
    packet = build_review_packet(*args, CUTOFF)
    assert packet["summary"]["proposals"] == 0


def test_failed_document_is_retained_even_without_suggestions():
    args = inputs()
    args[1]["items"][0].update(status="pending_verification", reasons=["PDF SHA不符"], event_proposals=[])
    packet = build_review_packet(*args, CUTOFF)
    document = packet["companies"][0]["documents"][0]
    assert document["reasons"] == ["PDF SHA不符"]
    assert packet["companies"][0]["proposals"] == []


def test_supplied_evidence_is_preserved_with_presence_only_counts():
    companies, extraction, prepared, research = inputs()
    prepared["companies"] = [{"symbol": "600900", "required_share_classes": ["A", "H"],
                              "profits": [{"year": 2025, "amount_yuan": "999"}],
                              "coverage": [{"year": 2025, "complete": False}],
                              "events": [{"fiscal_year": 2025, "amount_yuan": "888"}]}]
    research["items"] = [{"symbol": "600900", "status": "pending_verification", "reasons": ["H股日期未核实"]}]
    packet = build_review_packet(companies, extraction, prepared, research, CUTOFF)
    first = packet["companies"][0]
    assert first["evidence"]["events"][0]["amount_yuan"] == "888"
    assert first["annual_checks"][-1]["evidence_row_counts"] == {"profits": 1, "coverage": 1, "events": 1}
    assert first["annual_checks"][-1]["presence_only"] is True
    assert "H股日期未核实" in first["unresolved"]
    assert first["proposals"][0]["fields"]["amount_yuan"] == "100"


@pytest.mark.parametrize("target", ["companies", "prepared", "research"])
def test_unknown_or_duplicate_company_inputs_fail(target):
    companies, extraction, prepared, research = inputs()
    if target == "companies":
        companies.append(companies[0])
    elif target == "prepared":
        prepared["companies"] = [{"symbol": "999999"}]
    else:
        research["items"] = [{"symbol": "999999"}]
    with pytest.raises(ValueError):
        build_review_packet(companies, extraction, prepared, research, CUTOFF)


def test_untrusted_suggestion_cannot_mark_itself_verified():
    args = inputs()
    args[1]["items"][0]["event_proposals"][0].update(requires_review=False, financial_verified=True, rule_enabled=True)
    proposal = build_review_packet(*args, CUTOFF)["companies"][0]["proposals"][0]
    assert proposal["requires_review"] is proposal["fields"]["requires_review"] is True
    assert proposal["financial_verified"] is proposal["fields"]["financial_verified"] is False
    assert proposal["fields"]["rule_enabled"] is False


def test_malformed_or_orphan_document_does_not_drop_other_companies():
    args = inputs()
    args[1]["items"].extend([None, {"symbol": {}}, {"symbol": "999999"}])
    packet = build_review_packet(*args, CUTOFF)
    assert len(packet["companies"]) == 2
    assert packet["summary"]["proposals"] == 1
    assert len(packet["diagnostics"]) == 3


@pytest.mark.parametrize("key", ["file_sha256", "text_sha256", "status"])
def test_missing_original_digest_or_extraction_status_rejects_suggestions(key):
    args = inputs()
    args[1]["items"][0].pop(key)
    packet = build_review_packet(*args, CUTOFF)
    assert packet["summary"]["proposals"] == 0
    assert packet["diagnostics"]
    assert len(packet["companies"]) == 2


def test_nonfinite_origin_does_not_destroy_other_company_suggestions():
    companies, extraction, prepared, research = inputs()
    original = deepcopy(extraction["items"][0])
    extraction["items"][0]["reasons"] = [float("nan")]
    extraction["items"].append(original)
    packet = build_review_packet(companies, extraction, prepared, research, CUTOFF)
    assert packet["summary"]["proposals"] == 1
    assert packet["diagnostics"]
    assert packet["companies"][0]["documents"][0]["reasons"] == ["NaN"]


@pytest.mark.parametrize("status", [{}, []])
def test_invalid_status_is_isolated_without_losing_normal_proposal(status):
    companies, extraction, prepared, research = inputs()
    original = deepcopy(extraction["items"][0])
    extraction["items"][0]["status"] = status
    extraction["items"].append(original)
    packet = build_review_packet(companies, extraction, prepared, research, CUTOFF)
    assert packet["summary"]["proposals"] == 1
    assert len(packet["diagnostics"]) == 1
