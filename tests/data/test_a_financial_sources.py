"""A 股独立备用财务源的离线回放与身份边界。"""

import copy
import json
from datetime import date
from pathlib import Path

import pytest
import requests

from data.a_financial_sources import fetch_eastmoney_financial, fetch_sina_financial

RESOURCES = Path(__file__).parents[1] / "resources" / "data"


def load(provider, symbol):
    return json.loads((RESOURCES / f"a-financial-{provider}-{symbol}.json").read_text(encoding="utf-8"))


class Response:
    def __init__(self, payload):
        self.payload = payload
        self.text = f'<input id="hidctype" value="{payload.get("company_type", "")}"/>'

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def replay(monkeypatch, provider, symbol, *, mutate=None, failed=()):
    records = copy.deepcopy(load(provider, symbol)["responses"])
    if mutate is not None:
        mutate(records)
    calls = []

    def get(url, *, params, timeout):
        assert timeout == 10
        calls.append((url, params))
        prefix = "sh" if symbol.startswith("6") else "sz"
        if provider == "sina":
            assert params["paperCode"] == prefix + symbol
            assert params["num"] == "12" and params["type"] == "0"
            label = params["source"]
        elif "datacenter" in url:
            label = "importance"
        elif url.endswith("Index"):
            label = "company_type"
        else:
            assert params["code"] == (prefix + symbol).upper()
            assert params["reportType"] == "1" and params["reportDateType"] == "0"
            label = {"zcfzbAjaxNew": "balance", "lrbAjaxNew": "income", "xjllbAjaxNew": "cash"}[url.rsplit("/", 1)[-1]]
            assert len(params["dates"].split(",")) <= 5
        if label in failed:
            raise requests.Timeout("离线注入超时")
        candidates = [r for r in records if r["label"] == label]
        record = candidates[0]
        if provider == "em" and label in {"balance", "income", "cash"}:
            requested = set(params["dates"].split(","))
            record = next(r for r in candidates if {x["REPORT_DATE"][:10] for x in r["payload"]["data"]} <= requested)
        return Response(record["payload"])

    monkeypatch.setattr(requests, "get", get)
    fetch = fetch_sina_financial if provider == "sina" else fetch_eastmoney_financial
    return fetch(symbol), calls


@pytest.mark.parametrize("provider", ["sina", "em"])
@pytest.mark.parametrize("symbol", ["600519", "688326", "000001"])
def test_three_actual_samples_replay(monkeypatch, provider, symbol):
    package, calls = replay(monkeypatch, provider, symbol)
    assert len(package.rows) == 12
    assert package.positions == {}
    assert [r.fiscal_quarter for r in package.rows] == sorted(r.fiscal_quarter for r in package.rows)
    latest = package.rows[-1]
    assert latest.fiscal_quarter == date(2026, 6, 30)
    assert latest.report_currency == "CNY"
    assert latest.period_start == date(2026, 1, 1)
    assert latest.period_basis == "cumulative"
    assert latest.common_equity is None
    assert latest.cash_flow_basis == "reported"
    assert latest._data_source == package.source
    assert all(getattr(latest, field) is not None for field in (
        "revenue", "net_profit", "deducted_net_profit", "total_assets", "total_equity", "operating_cash_flow", "basic_eps", "roe",
    ))
    if symbol == "688326":
        assert latest.net_profit == pytest.approx(-381992695.69)
        assert latest.operating_cash_flow == pytest.approx(-656595619.24)
        assert latest.roe == pytest.approx(-0.0956)
    if symbol == "000001":
        assert latest.gross_margin is None
    assert len(calls) == (4 if provider == "sina" else 11)


@pytest.mark.parametrize("provider,failed", [("sina", "llb"), ("em", "cash")])
def test_failed_table_keeps_same_source_partial(monkeypatch, provider, failed):
    package, _ = replay(monkeypatch, provider, "688326", failed={failed})
    assert len(package.rows) == 12
    assert package.rows[-1].net_profit is not None and package.rows[-1].net_profit < 0
    assert all(r.operating_cash_flow is None and r.cash_flow_basis == "unknown" for r in package.rows)


@pytest.mark.parametrize("provider", ["sina", "em"])
def test_non_cny_table_rejected(monkeypatch, provider):
    def mutate(records):
        label = "llb" if provider == "sina" else "cash"
        for record in records:
            if record["label"] != label:
                continue
            payload = record["payload"]
            rows = payload["result"]["data"]["report_list"].values() if provider == "sina" else payload["data"]
            for row in rows:
                row["rCurrency" if provider == "sina" else "CURRENCY"] = "USD"
    package, _ = replay(monkeypatch, provider, "688326", mutate=mutate)
    assert package.rows and all(r.operating_cash_flow is None for r in package.rows)


def test_sina_duplicate_metric_conflict_rejects_table(monkeypatch):
    def mutate(records):
        row = next(iter(records[0]["payload"]["result"]["data"]["report_list"].values()))
        row["data"].extend([{"item_field": "PARENETP", "item_value": "1"}, {"item_field": "PARENETP", "item_value": "2"}])
    package, _ = replay(monkeypatch, "sina", "688326", mutate=mutate)
    assert package.rows[-1].net_profit is None


def test_sina_non_consolidated_table_rejected(monkeypatch):
    def mutate(records):
        for row in records[0]["payload"]["result"]["data"]["report_list"].values():
            row["rType"] = "母公司期末"
    package, _ = replay(monkeypatch, "sina", "688326", mutate=mutate)
    assert all(r.net_profit is None for r in package.rows)


def test_em_wrong_symbol_is_not_relabelled(monkeypatch):
    def mutate(records):
        for row in records[0]["payload"]["result"]["data"]:
            row["SECUCODE"] = "600519.SH"
    package, _ = replay(monkeypatch, "em", "688326", mutate=mutate)
    assert package.rows == []


def test_em_duplicate_conflicting_period_not_last_wins(monkeypatch):
    def mutate(records):
        rows = records[0]["payload"]["result"]["data"]
        rows.append({**rows[0], "PARENTNETPROFIT": 1})
    package, _ = replay(monkeypatch, "em", "688326", mutate=mutate)
    assert package.rows == []


@pytest.mark.parametrize("symbol", ["00700.HK", "../x", "", "688326.SH"])
@pytest.mark.parametrize("fetch", [fetch_sina_financial, fetch_eastmoney_financial])
def test_invalid_symbols_do_not_request(monkeypatch, symbol, fetch):
    def fail(*args, **kwargs):
        pytest.fail("无效标的不应发起 HTTP")
    monkeypatch.setattr(requests, "get", fail)
    assert fetch(symbol).rows == []


@pytest.mark.parametrize("provider", ["sina", "em"])
def test_zero_negative_and_small_percent_are_not_missing(monkeypatch, provider):
    def mutate(records):
        if provider == "sina":
            main = next(r for r in records if r["label"] == "gjzb")
            rows = main["payload"]["result"]["data"]["report_list"].values()
            for row in rows:
                for item in row["data"]:
                    if item["item_field"] == "ROEWEIGHTED":
                        item["item_value"] = "0.5"
                    elif item["item_field"] == "BIZTOTINCO":
                        item["item_value"] = "0"
            for row in records[0]["payload"]["result"]["data"]["report_list"].values():
                for item in row["data"]:
                    if item["item_field"] == "BIZTOTINCO":
                        item["item_value"] = "0"
        else:
            for row in records[0]["payload"]["result"]["data"]:
                row["ROEJQ"] = 0.5
                row["TOTALOPERATEREVE"] = 0
    package, _ = replay(monkeypatch, provider, "688326", mutate=mutate)
    latest = package.rows[-1]
    assert latest.revenue == 0
    assert latest.net_profit is not None and latest.net_profit < 0
    assert latest.roe == 0.005


@pytest.mark.parametrize("provider", ["sina", "em"])
@pytest.mark.parametrize("value", [None, "--", "NaN", "Infinity", True])
def test_invalid_numeric_values_remain_missing(monkeypatch, provider, value):
    def mutate(records):
        if provider == "sina":
            for row in records[0]["payload"]["result"]["data"]["report_list"].values():
                for item in row["data"]:
                    if item["item_field"] == "PARENETP":
                        item["item_value"] = value
        else:
            for row in records[0]["payload"]["result"]["data"]:
                row["PARENTNETPROFIT"] = value
    package, _ = replay(monkeypatch, provider, "688326", mutate=mutate)
    assert package.rows and all(row.net_profit is None for row in package.rows)


def test_sina_bank_gross_stays_inapplicable_when_other_tables_fail(monkeypatch):
    package, _ = replay(monkeypatch, "sina", "000001", failed={"lrb", "fzb"})
    assert package.rows and all(row.gross_margin is None for row in package.rows)


@pytest.mark.parametrize("provider", ["sina", "em"])
def test_bad_shape_isolated_to_table(monkeypatch, provider):
    def mutate(records):
        label = "llb" if provider == "sina" else "cash"
        for record in records:
            if record["label"] == label:
                record["payload"] = {"result": None} if provider == "sina" else {"data": [None]}
    package, _ = replay(monkeypatch, provider, "688326", mutate=mutate)
    assert package.rows and all(row.operating_cash_flow is None for row in package.rows)


def test_em_type_failure_keeps_main_summary(monkeypatch):
    package, calls = replay(monkeypatch, "em", "688326", failed={"company_type"})
    assert len(calls) == 2
    assert len(package.rows) == 12
    assert package.rows[-1].net_profit is not None and package.rows[-1].net_profit < 0
    assert package.rows[-1].total_assets is None


def test_em_unrequested_dates_rejected(monkeypatch):
    # 非正常日期响应需绕过回放对请求批次的匹配，直接注入网络响应。
    data = load("em", "688326")["responses"]
    cash = next(r for r in data if r["label"] == "cash")["payload"]
    cash["data"][0]["REPORT_DATE"] = "2000-12-31 00:00:00"
    original = requests.get
    package, _ = replay(monkeypatch, "em", "688326")
    assert package.rows[-1].operating_cash_flow is not None
    replay_get = requests.get

    def get(url, *, params, timeout):
        if url.endswith("xjllbAjaxNew"):
            return Response(cash)
        return replay_get(url, params=params, timeout=timeout)
    monkeypatch.setattr(requests, "get", get)
    rejected = fetch_eastmoney_financial("688326")
    assert rejected.rows and all(row.operating_cash_flow is None for row in rejected.rows)
    monkeypatch.setattr(requests, "get", original)


@pytest.mark.parametrize("symbol", ["600519", "688326", "000001"])
def test_sources_match_eight_disclosed_metrics_at_recorded_precision(monkeypatch, symbol):
    sina, _ = replay(monkeypatch, "sina", symbol)
    em, _ = replay(monkeypatch, "em", symbol)
    for left, right in zip(sina.rows, em.rows, strict=True):
        assert left.fiscal_quarter == right.fiscal_quarter
        for key in ("revenue", "net_profit", "deducted_net_profit", "total_assets", "total_equity", "operating_cash_flow", "basic_eps", "roe"):
            tolerance = 0.011
            if key == "basic_eps":
                tolerance = 0.00000101
            elif key == "roe":
                tolerance = 0.0000000051
            assert getattr(left, key) == pytest.approx(getattr(right, key), abs=tolerance, rel=0)
    # 样本数值相同不是跨源修订等效证据，不因此生成 positions。
    assert sina.positions == em.positions == {}


@pytest.mark.parametrize("provider", ["sina", "em"])
def test_same_source_profit_conflict_cannot_form_complete_row(monkeypatch, provider):
    def mutate(records):
        if provider == "sina":
            for row in records[-1]["payload"]["result"]["data"]["report_list"].values():
                for item in row["data"]:
                    if item["item_field"] == "PARENETP":
                        item["item_value"] = 1
        else:
            for record in records:
                if record["label"] == "income":
                    for row in record["payload"]["data"]:
                        row["PARENT_NETPROFIT"] = 1
    package, _ = replay(monkeypatch, provider, "688326", mutate=mutate)
    if provider == "sina":
        assert all(row.net_profit is None for row in package.rows)
    else:
        assert all(row.deducted_net_profit is None for row in package.rows)


def test_sina_cash_conflict_does_not_keep_direct_ocf(monkeypatch):
    def mutate(records):
        for row in records[-1]["payload"]["result"]["data"]["report_list"].values():
            for item in row["data"]:
                if item["item_field"] == "MANANETR":
                    item["item_value"] = 1
    package, _ = replay(monkeypatch, "sina", "688326", mutate=mutate)
    assert package.rows and all(row.operating_cash_flow is None for row in package.rows)


@pytest.mark.parametrize("difference,accepted", [(0.0000009, True), (0.1, False)])
def test_same_source_eps_comparison_respects_six_digit_precision(monkeypatch, difference, accepted):
    def mutate(records):
        for row in records[0]["payload"]["result"]["data"]["report_list"].values():
            for item in row["data"]:
                if item["item_field"] == "BASICEPS":
                    item["item_value"] = float(item["item_value"]) + difference
    package, _ = replay(monkeypatch, "sina", "688326", mutate=mutate)
    assert all((row.net_profit is not None) == accepted for row in package.rows)


def test_ocf_per_share_difference_is_preserved_not_forced_equal(monkeypatch):
    sina, _ = replay(monkeypatch, "sina", "600519")
    em, _ = replay(monkeypatch, "em", "600519")
    period = date(2025, 9, 30)
    a = next(row for row in sina.rows if row.fiscal_quarter == period)
    b = next(row for row in em.rows if row.fiscal_quarter == period)
    assert a.operating_cash_flow_per_share == 30.406678
    assert b.operating_cash_flow_per_share == 30.502044764572
    assert a.operating_cash_flow == b.operating_cash_flow
    assert sina.positions == em.positions == {}


def test_em_explicit_nonconsolidated_table_is_not_accepted(monkeypatch):
    def mutate(records):
        for record in records:
            if record["label"] == "balance":
                for row in record["payload"]["data"]:
                    row["REPORT_SCOPE"] = "母公司"
    package, _ = replay(monkeypatch, "em", "688326", mutate=mutate)
    assert package.rows and all(row.total_assets is None for row in package.rows)


def test_sina_explicit_wrong_symbol_not_relabelled(monkeypatch):
    def mutate(records):
        for row in records[0]["payload"]["result"]["data"]["report_list"].values():
            row["paperCode"] = "sh600519"
    package, _ = replay(monkeypatch, "sina", "688326", mutate=mutate)
    assert package.rows and all(row.net_profit is None for row in package.rows)


@pytest.mark.parametrize("provider", ["sina", "em"])
def test_explicit_failed_api_status_not_accepted(monkeypatch, provider):
    def mutate(records):
        if provider == "sina":
            records[0]["payload"]["result"]["status"]["code"] = 1
        else:
            records[0]["payload"]["success"] = False
    package, _ = replay(monkeypatch, provider, "688326", mutate=mutate)
    if provider == "sina":
        assert package.rows and all(row.net_profit is None for row in package.rows)
    else:
        assert package.rows == []
