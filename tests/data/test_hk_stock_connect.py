"""港股通名单与申万归属分离，失败不得发布不完整名单。"""
import json
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

from data import hk_stock_connect as connect


def sample():
    rows = [{"f12": "00700", "f14": "腾讯控股"}, {"f12": "09988", "f14": "阿里巴巴-W"}]
    frame = pd.DataFrame({"代码": ["00700", "09988"], "名称": ["腾讯控股", "阿里巴巴-W"]})
    pages = [{"page": 1, "total": 2, "rows": rows}]
    return frame, pages


def snapshot():
    frame, pages = sample()
    return connect.build_snapshot(frame, pages, datetime(2026, 10, 24, tzinfo=UTC))


SSE_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "resources" / "hk_stock_connect" / "sse-hk-connect-eligible-sample.jsonp"
)


def sse_payload():
    return connect._sse_payload(SSE_FIXTURE.read_text(encoding="utf-8"))


def sse_snapshot():
    return connect.build_sse_snapshot(sse_payload(), datetime(2026, 10, 24, tzinfo=UTC))


def test_valid_snapshot_uses_names_not_industry_and_preserves_leading_zero():
    result = snapshot()
    assert result.source == "ak.stock_hk_ggt_components_em"
    assert result.data_date is None
    assert result.total == 2
    assert result.contains("00700.HK")
    assert not result.contains("000001")
    assert not result.contains("09999.HK")
    assert result.members[0].name == "腾讯控股"
    assert "sw_level1" not in result.members[0].model_dump()
    assert result.fetched_at.tzinfo is not None


@pytest.mark.parametrize("case", ["empty", "missing_column", "duplicate", "bad_code", "empty_name",
    "truncated", "wrong_page", "out_of_order", "changed_total", "changed_member", "no_pages",
    "naive_time"])
def test_invalid_or_incomplete_snapshot_is_rejected(case):
    frame, pages = sample()
    timestamp = datetime(2026, 10, 24, tzinfo=UTC)
    if case == "empty":
        frame = frame.iloc[:0]
    elif case == "missing_column":
        frame = frame.drop(columns="名称")
    elif case == "duplicate":
        frame.loc[1, "代码"] = "00700"
        pages[0]["rows"][1]["f12"] = "00700"
    elif case == "bad_code":
        frame["代码"] = frame["代码"].astype(object)
        frame.loc[0, "代码"] = 700
    elif case == "empty_name":
        frame.loc[0, "名称"] = " "
    elif case == "truncated":
        pages[0]["total"] = 3
    elif case == "wrong_page":
        pages[0]["page"] = 2
    elif case == "out_of_order":
        pages = [
            {"page": 2, "total": 2, "rows": [pages[0]["rows"][1]]},
            {"page": 1, "total": 2, "rows": [pages[0]["rows"][0]]},
        ]
    elif case == "changed_total":
        pages.append({"page": 2, "total": 3, "rows": []})
    elif case == "changed_member":
        pages[0]["rows"][0]["f12"] = "00005"
    elif case == "no_pages":
        pages = []
    elif case == "naive_time":
        timestamp = timestamp.replace(tzinfo=None)
    with pytest.raises(ValueError):
        connect.build_snapshot(frame, pages, timestamp)


def test_multiple_pages_must_cover_total_and_final_frame():
    frame, pages = sample()
    rows = pages[0]["rows"]
    pages = [{"page": 1, "total": 2, "rows": rows[:1]},
             {"page": 2, "total": 2, "rows": rows[1:]}]
    assert connect.build_snapshot(frame, pages, datetime.now(UTC)).total == 2


def test_sse_snapshot_filters_etf_and_records_official_source():
    result = sse_snapshot()
    assert result.source == connect.SSE_SOURCE
    assert result.source_url == connect.SSE_URL
    assert result.data_date == date(2026, 10, 23)
    assert result.total == 3
    assert result.pages[0].count == 3
    assert result.contains("00005.HK")
    assert result.contains("00700.HK")
    assert not result.contains("02800.HK")
    assert [member.name for member in result.members] == ["汇丰控股", "腾讯控股", "BABA-W"]


@pytest.mark.parametrize("case", [
    "no_page", "empty", "total_mismatch", "page_count", "etf_only",
    "duplicate", "bad_code", "blank_name", "mixed_dates", "bad_date", "naive_time",
])
def test_invalid_or_incomplete_sse_snapshot_is_rejected(case):
    payload = sse_payload()
    page = payload["pageHelp"]
    timestamp = datetime(2026, 10, 24, tzinfo=UTC)
    if case == "no_page":
        payload.pop("pageHelp")
    elif case == "empty":
        page["data"] = []
        page["total"] = 0
    elif case == "total_mismatch":
        page["total"] = 5
    elif case == "page_count":
        page["pageCount"] = 2
    elif case == "etf_only":
        for row in page["data"]:
            row["SECURITY_TYPE"] = "ETF"
    elif case == "duplicate":
        page["data"][0]["SECURITY_CODE"] = "00700"
    elif case == "bad_code":
        page["data"][0]["SECURITY_CODE"] = "500"
    elif case == "blank_name":
        page["data"][0]["ABBR_CN"] = ""
        page["data"][0]["ABBR_EN"] = "   "
    elif case == "mixed_dates":
        page["data"][0]["UPDATE_DATE"] = "2026-10-22"
    elif case == "bad_date":
        for row in page["data"]:
            row["UPDATE_DATE"] = "2026/10/23"
    elif case == "naive_time":
        timestamp = timestamp.replace(tzinfo=None)
    with pytest.raises((TypeError, ValueError)):
        connect.build_sse_snapshot(payload, timestamp)


@pytest.mark.parametrize("text", ["", "not jsonp", "cb();", "cb([1, 2]);", "cb({bad});"])
def test_invalid_sse_jsonp_is_rejected(text):
    with pytest.raises((TypeError, ValueError)):
        connect._sse_payload(text)


def test_fetch_sse_uses_official_endpoint_without_environment_proxy(mocker):
    response = mocker.Mock()
    response.text = SSE_FIXTURE.read_text(encoding="utf-8")
    response.raise_for_status = mocker.Mock()
    session = mocker.Mock()
    session.post.return_value = response
    factory = mocker.patch("data.hk_stock_connect.requests.Session", return_value=session)

    result = connect.fetch_sse_snapshot()

    assert result.total == 3
    assert result.source == connect.SSE_SOURCE
    assert session.trust_env is False
    session.post.assert_called_once()
    args, kwargs = session.post.call_args
    assert args[0] == connect.SSE_ENDPOINT
    assert kwargs["params"] == {"jsonCallBack": "cb"}
    assert kwargs["data"] == connect.SSE_FORM
    assert kwargs["timeout"] == (10, 30)
    assert kwargs["headers"]["Referer"] == connect.SSE_URL
    session.close.assert_called_once()
    factory.assert_called_once_with()


def test_fetch_sse_network_failure_is_wrapped(mocker):
    session = mocker.Mock()
    session.post.side_effect = requests.ConnectionError("网络断开")
    mocker.patch("data.hk_stock_connect.requests.Session", return_value=session)
    with pytest.raises(connect.HKStockConnectError, match="请求失败"):
        connect.fetch_sse_snapshot()
    session.close.assert_called_once()


def test_fetch_sse_invalid_payload_is_wrapped(mocker):
    response = mocker.Mock()
    response.text = "not jsonp"
    response.raise_for_status = mocker.Mock()
    session = mocker.Mock()
    session.post.return_value = response
    mocker.patch("data.hk_stock_connect.requests.Session", return_value=session)
    with pytest.raises(connect.HKStockConnectError, match="数据不完整"):
        connect.fetch_sse_snapshot()


@pytest.mark.parametrize("fails", [False, True])
def test_sdk_worker_calls_selected_function_and_restores_observer(mocker, fails):
    frame, pages = sample()
    request = mocker.Mock(return_value=SimpleNamespace(json=lambda: {
        "data": {"total": 2, "diff": pages[0]["rows"]}}))
    pagination = SimpleNamespace(__globals__={"request_with_retry": request})

    def fetcher():
        pagination.__globals__["request_with_retry"]("public-url", params={"pn": "1"})
        if fails:
            raise ConnectionError("测试SDK失败")
        return frame

    mocker.patch.dict(fetcher.__globals__, {"fetch_paginated_data": pagination})
    mocker.patch("akshare.stock_hk_ggt_components_em", new=fetcher)
    if fails:
        with pytest.raises(connect.HKStockConnectError):
            connect._collect_in_worker()
    else:
        assert connect._collect_in_worker().total == 2
    request.assert_called_once_with("public-url", params={"pn": "1"})
    assert pagination.__globals__["request_with_retry"] is request


@pytest.fixture
def files(tmp_path):
    mapping = tmp_path / "industry_mapping.csv"
    mapping.write_text("symbol,sw_level1,sw_level2,style_category,mapping_status\n"
                       "000001,银行,股份制银行Ⅱ,大金融,verified\n", encoding="utf-8")
    saved = tmp_path / "hk_stock_connect.snapshot.json"
    return mapping, saved


def test_publish_only_writes_snapshot_and_leaves_mapping_sentinel_unchanged(files):
    mapping, saved = files
    before = mapping.read_bytes()
    connect.publish_snapshot(snapshot(), saved)
    stored = connect.HKStockConnectSnapshot.model_validate_json(saved.read_text(encoding="utf-8"))
    assert stored.contains("00700.HK")
    assert mapping.read_bytes() == before


def test_publish_rejects_non_json_snapshot_path(files):
    _, saved = files

    with pytest.raises(ValueError, match="JSON"):
        connect.publish_snapshot(snapshot(), saved.with_suffix(".csv"))


def test_publish_accepts_official_sse_snapshot(files):
    mapping, saved = files
    before = mapping.read_bytes()
    connect.publish_snapshot(sse_snapshot(), saved)
    stored = connect.HKStockConnectSnapshot.model_validate_json(saved.read_text(encoding="utf-8"))
    assert stored.source == connect.SSE_SOURCE
    assert stored.data_date == date(2026, 10, 23)
    assert mapping.read_bytes() == before


def test_refresh_local_mapping_defaults_to_official_source(files, mocker):
    mapping, saved = files
    fetch = mocker.patch.object(connect, "fetch_sse_snapshot", return_value=sse_snapshot())
    connect.refresh_local_mapping(saved)
    fetch.assert_called_once()
    assert saved.exists()
    assert mapping.exists()


def test_refresh_result_success_and_failure_keep_old_snapshot(files, mocker):
    _, saved = files
    mocker.patch.object(connect, "fetch_sse_snapshot", return_value=sse_snapshot())
    result, record = connect.refresh_local_mapping_result(saved)
    assert result is not None and record.source == result.source
    assert (record.result_state, record.published) == ("complete", True)
    before = saved.read_bytes()
    mocker.patch.object(connect, "fetch_sse_snapshot", side_effect=connect.HKStockConnectError("失败"))
    result, record = connect.refresh_local_mapping_result(saved)
    assert result is None
    assert (record.result_state, record.published) == ("failed", False)
    assert saved.read_bytes() == before


def test_fetch_failure_leaves_both_files_untouched(files, mocker):
    mapping, saved = files
    saved.write_text("old successful snapshot", encoding="utf-8")
    before = mapping.read_bytes(), saved.read_bytes()
    mocker.patch.object(connect, "fetch_snapshot", side_effect=connect.HKStockConnectError("网络断开"))
    with pytest.raises(connect.HKStockConnectError):
        connect.refresh_local_mapping(saved, source="eastmoney")
    assert (mapping.read_bytes(), saved.read_bytes()) == before


def test_snapshot_write_failure_preserves_old_snapshot(files, mocker):
    mapping, saved = files
    saved.write_text("old successful snapshot", encoding="utf-8")
    before = mapping.read_bytes(), saved.read_bytes()
    mocker.patch.object(connect, "_atomic_write", side_effect=PermissionError("测试模拟名单发布失败"))
    with pytest.raises(OSError):
        connect.publish_snapshot(snapshot(), saved)
    assert (mapping.read_bytes(), saved.read_bytes()) == before


def test_worker_timeout_is_bounded_and_reported(mocker):
    run = mocker.patch.object(connect.subprocess, "run", side_effect=subprocess.TimeoutExpired("sdk", 45))
    with pytest.raises(connect.HKStockConnectError, match="超时"):
        connect.fetch_snapshot()
    assert run.call_args.kwargs["timeout"] == 45


def test_worker_result_must_be_validated_again(mocker):
    response = subprocess.CompletedProcess([], 0, snapshot().model_dump_json(), "")
    run = mocker.patch.object(connect.subprocess, "run", return_value=response)
    assert connect.fetch_snapshot().total == 2
    assert "--collect" in run.call_args.args[0]
    assert run.call_args.kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
    raw = json.loads(response.stdout)
    raw["total"] = 3
    response.stdout = json.dumps(raw)
    with pytest.raises(ValueError):
        connect.fetch_snapshot()
