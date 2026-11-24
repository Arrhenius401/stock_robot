import json
from datetime import date
from pathlib import Path

import pytest

from data.us_sec import parse_company_concept, single_quarters


def payload():
    return json.loads((Path(__file__).parents[1] / "resources/us_sec/brk-b-revenues-20261124.json").read_text(encoding="utf-8"))["payload"]


def test_parse_and_derive_single_quarters():
    facts = parse_company_concept(payload(), "RevenueFromContractWithCustomerExcludingAssessedTax")
    quarters = single_quarters(facts)
    q3 = next(item for item in quarters if item.end == date(2026, 9, 30))
    assert q3.value == pytest.approx(74322000000)
    assert q3.basis == "累计差额"
    assert q3.currency == "USD"


def test_sec_rejects_wrong_concept():
    data = payload()
    data["tag"] = "Revenues"
    with pytest.raises(ValueError, match="概念"):
        parse_company_concept(data, "RevenueFromContractWithCustomerExcludingAssessedTax")


def test_sec_direct_and_derived_quarters_must_agree():
    data = payload()
    for row in data["units"]["USD"]:
        if row.get("start") == "2026-07-01" and row.get("end") == "2026-09-30":
            row["val"] += 1000000000
    with pytest.raises(ValueError, match="冲突"):
        single_quarters(parse_company_concept(data, "RevenueFromContractWithCustomerExcludingAssessedTax"))
