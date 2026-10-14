"""分红支付率的完整性、股类覆盖与研究计算边界。"""

from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from radar.stocks.dividend_payout import evaluate_company

AS_OF = date(2026, 10, 14)
SOURCE = "https://example.com/implemented"


def company(profits=(100, 100, 100), dividends=(30, 30, 30)):
    return {
        "symbol": "600900", "name": "样本", "required_share_classes": ["A"],
        "profits": [{"year": year, "amount_yuan": str(profit), "currency": "CNY",
                     "scope": "company_ordinary", "source_url": SOURCE,
                     "disclosed_on": "2026-05-18"}
                    for year, profit in zip((2023, 2024, 2025), profits, strict=True)],
        "coverage": [{"year": year, "complete": True, "source_url": SOURCE,
                      "disclosed_on": "2026-07-20"} for year in (2023, 2024, 2025)],
        "events": [{"event_id": f"{year}-annual", "fiscal_year": year, "share_class": "A",
                    "amount_yuan": str(dividend), "currency": "CNY", "source_url": SOURCE,
                    "disclosed_on": f"{year + 1}-07-01", "paid_on": f"{year + 1}-07-19"}
                   for year, dividend in zip((2023, 2024, 2025), dividends, strict=True)],
    }


def assert_pending(record):
    result = evaluate_company(record, AS_OF)
    assert result["status"] == "pending_verification"
    assert result["meets_reference_condition"] is None
    assert result["reasons"]
    assert result["rule_enabled"] is False
    return result


def test_cypc_verified_actual_amounts_and_evidence():
    record = company(("27244616815.27", "32496172808.65", "34502809176.39"),
                     ("20063938527.12", "23073529306.19", "24468217716"))
    record["name"] = "长江电力"
    result = evaluate_company(record, AS_OF)
    assert result["status"] == "complete"
    assert result["annual"][0]["dividend_yuan"] == "20063938527.12"
    assert Decimal(result["annual_average"]).quantize(Decimal("0.0001")) == Decimal("0.7185")
    assert result["meets_reference_condition"] is True
    assert result["rule_enabled"] is False
    assert result["evidence"]["events"] == record["events"]


def test_minsheng_ordinary_share_profit_scope():
    record = company((31507000000, 27980000000, 27451000000),
                     (9457000000, 8406000000, 8274000000))
    record["required_share_classes"] = ["A", "H"]
    for event in record["events"]:
        event["share_class"] = "ALL"
    result = evaluate_company(record, AS_OF)
    assert Decimal(result["annual_average"]).quantize(Decimal("0.0001")) == Decimal("0.3007")
    assert result["meets_reference_condition"] is True
    record["profits"][0]["scope"] = "parent_total_including_preferred"
    assert_pending(record)


def test_annual_average_is_not_ratio_of_cumulative_amounts():
    result = evaluate_company(company((1, 100, 100), ("2.1", 50, 50)), AS_OF)
    assert Decimal(result["annual_average"]) > 1
    assert Decimal(result["cumulative_ratio"]) < 1
    assert result["meets_reference_condition"] is False


@pytest.mark.parametrize("dividends", [(0, 0, 0), (100, 100, 100), (20, 20, 100)])
def test_strict_zero_and_one_reference_boundaries(dividends):
    result = evaluate_company(company(dividends=dividends), AS_OF)
    assert result["status"] == "complete"
    assert result["meets_reference_condition"] is False


def test_multiple_events_and_exact_duplicate_do_not_double_count():
    record = company()
    extra = deepcopy(record["events"][0])
    extra.update(event_id="2023-interim", amount_yuan="10")
    record["events"].extend([extra, deepcopy(extra)])
    result = evaluate_company(record, AS_OF)
    assert result["annual"][0]["dividend_yuan"] == "40"
    assert len(result["evidence"]["events"]) == 4


def test_a_h_coverage_per_event_not_just_per_year():
    record = company()
    record["required_share_classes"] = ["A", "H"]
    assert_pending(record)
    for event in list(record["events"]):
        other = deepcopy(event)
        other.update(share_class="H", amount_yuan="5")
        record["events"].append(other)
    assert evaluate_company(record, AS_OF)["annual"][0]["dividend_yuan"] == "35"
    record["events"].pop()
    assert_pending(record)


def test_all_and_class_rows_conflict_in_same_event():
    record = company()
    other = deepcopy(record["events"][0])
    other["share_class"] = "ALL"
    record["events"].append(other)
    assert_pending(record)


def test_event_identity_conflicts_even_across_fiscal_years():
    record = company()
    other = deepcopy(record["events"][0])
    other["amount_yuan"] = "31"
    record["events"].append(other)
    assert_pending(record)
    other["fiscal_year"] = 2024
    assert_pending(record)


@pytest.mark.parametrize("group,field,value", [
    ("events", "event_id", ""), ("events", "amount_yuan", "NaN"),
    ("events", "amount_yuan", "Infinity"), ("events", "amount_yuan", -1),
    ("events", "amount_yuan", True), ("events", "currency", "HKD"),
    ("events", "paid_on", "2027-01-01"), ("events", "disclosed_on", "2027-01-01"),
    ("events", "paid_on", "not-date"), ("events", "source_url", ""),
    ("events", "share_class", "PREFERRED"),
    ("profits", "amount_yuan", 0), ("profits", "amount_yuan", "-1"),
    ("profits", "amount_yuan", "NaN"), ("profits", "scope", "consolidated"),
    ("profits", "disclosed_on", "2027-01-01"),
    ("coverage", "complete", False), ("coverage", "complete", 1),
    ("coverage", "disclosed_on", "2027-01-01"),
])
def test_invalid_or_unimplemented_evidence_is_pending(group, field, value):
    record = company()
    record[group][0][field] = value
    assert_pending(record)


def test_years_are_fiscal_years_and_never_current_incomplete_year():
    record = company()
    record["profits"][0]["year"] = 2026
    assert_pending(record)
    record = company()
    record["events"][0]["fiscal_year"] = 2026
    assert_pending(record)


@pytest.mark.parametrize("field", ["profits", "coverage", "events", "required_share_classes"])
def test_missing_collections_do_not_crash_batch(field):
    record = company()
    record[field] = None
    assert_pending(record)


def test_conflicting_profit_and_incomplete_fiscal_coverage_are_pending():
    record = company()
    record["profits"].append({**record["profits"][0], "amount_yuan": "101"})
    assert_pending(record)
    record = company()
    record["coverage"].pop()
    assert_pending(record)


def test_verified_complete_year_without_dividend_can_be_rejected():
    record = company()
    record["events"] = []
    result = evaluate_company(record, AS_OF)
    assert result["status"] == "complete"
    assert result["meets_reference_condition"] is False


def test_input_is_not_mutated_and_bad_row_does_not_crash():
    record = company()
    before = deepcopy(record)
    evaluate_company(record, AS_OF)
    assert record == before
    record["events"].append(None)
    assert_pending(record)


def test_explicit_fiscal_year_total_and_component_events_are_pending():
    record = company()
    record["events"][0]["amount_scope"] = "fiscal_year_total"
    component = {**record["events"][0], "event_id": "2023-interim",
                 "amount_scope": "event", "amount_yuan": "10"}
    record["events"].append(component)
    assert_pending(record)


def test_total_component_coverage_is_not_guessed_from_identifiers():
    record = company()
    total = record["events"][0]
    total["covered_event_ids"] = ["2023-interim", "2023-final"]
    record["events"].append({**total, "event_id": "2023-interim",
                             "covered_event_ids": [], "amount_yuan": "10"})
    assert_pending(record)


def test_amounts_preserve_precision_when_summing_large_event_values():
    record = company()
    record["events"][0]["amount_yuan"] = "123456789012345678901234567890.12"
    record["events"].append({**record["events"][0], "event_id": "2023-interim", "amount_yuan": "0.01"})
    result = evaluate_company(record, AS_OF)
    assert result["annual"][0]["dividend_yuan"] == "123456789012345678901234567890.13"


def test_fiscal_year_total_can_cover_a_and_h_in_same_identity():
    record = company()
    record["required_share_classes"] = ["A", "H"]
    for event in list(record["events"]):
        event["amount_scope"] = "fiscal_year_total"
        record["events"].append({**event, "share_class": "H", "amount_yuan": "10"})
    result = evaluate_company(record, AS_OF)
    assert result["status"] == "complete"
    assert [row["dividend_yuan"] for row in result["annual"]] == ["40", "40", "40"]


@pytest.mark.parametrize("field,value", [("amount_scope", "unknown"),
                                        ("covered_event_ids", "not-a-list"),
                                        ("covered_event_ids", [None])])
def test_invalid_explicit_coverage_metadata_is_pending(field, value):
    record = company()
    record["events"][0][field] = value
    assert_pending(record)


@pytest.mark.parametrize("field", ["profits", "coverage", "events"])
def test_impossible_fiscal_evidence_date_is_pending(field):
    record = company()
    record[field][-1]["disclosed_on"] = "1900-01-01"
    if field == "events":
        record[field][-1]["paid_on"] = "1900-01-01"
    assert_pending(record)


@pytest.mark.parametrize("metadata,value", [("amount_scope", "fiscal_year_total"), ("covered_event_ids", ["prior-event"])])
def test_share_class_amount_coverage_metadata_conflict_is_pending(metadata, value):
    record = company()
    record["required_share_classes"] = ["A", "H"]
    for event in list(record["events"]):
        other = deepcopy(event)
        other["share_class"] = "H"
        other[metadata] = value
        record["events"].append(other)
    assert_pending(record)


def test_later_report_can_prove_earlier_paid_event():
    record = company()
    record["events"][0]["disclosed_on"] = "2026-05-18"
    assert evaluate_company(record, AS_OF)["status"] == "complete"