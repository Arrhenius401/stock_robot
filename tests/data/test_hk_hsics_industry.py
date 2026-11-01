"""恒生 HSICS 行业快照构建器单元测试。"""
import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from data import hk_hsics_industry as hsics

FIXTURE = Path(__file__).resolve().parents[1] / "resources/hk_hsics/hsci-constituent-sample.txt"
FETCHED_AT = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def profile(symbol: str, sector: str, name: str = "公司", gics: str = "GICS") -> hsics.F10Profile:
    return hsics.F10Profile(symbol=symbol, name=name, sector=sector, gics=gics)


def pdf_member(code: str, industry: str) -> hsics.HsciPdfMember:
    return hsics.HsciPdfMember(symbol=code, name="公司", industry_code="00", industry=industry)


def rows_for_test() -> list[hsics.HsicsMember]:
    return hsics.build_members(
        {
            "00001": profile("00001", "综合企业"),
            "00005": profile("00005", "银行"),
            "00700": profile("00700", "软件服务"),
            "09988": profile("09988", "专业零售"),
            "00038": profile("00038", "工业工程"),
        },
        {
            "00001": pdf_member("00001", "Conglomerates"),
            "00005": pdf_member("00005", "Financials"),
            "00700": pdf_member("00700", "Information Technology"),
            "09988": pdf_member("09988", "Consumer Discretionary"),
        },
        {
            "综合企业": "Conglomerates",
            "银行": "Financials",
            "软件服务": "Information Technology",
            "专业零售": "Consumer Discretionary",
            "工业工程": "Industrials",
        },
        {"00001", "00005", "00700", "09988", "00038"},
    )


def test_parse_pdf_text_keeps_first_occurrence_and_canonical_industry():
    members = hsics.parse_hsci_pdf_text(FIXTURE.read_text(encoding="utf-8"))

    assert set(members) == {"00001", "00005", "00700", "09988"}
    assert members["00700"].industry == "Information Technology"
    assert members["09988"].industry == "Consumer Discretionary"
    assert members["00005"].industry == "Financials"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Financials 金融業", "Financials"),
        ("Telecommunicati ons 電訊業", "Telecommunications"),
        ("Consumer Discretionary 非必需性消費", "Consumer Discretionary"),
        ("Properties & Construction 地產建築業", "Properties and Construction"),
    ],
)
def test_canonical_industry_strips_chinese(raw, expected):
    assert hsics.canonical_industry(raw) == expected


def test_build_crosswalk_requires_unique_parent():
    crosswalk = hsics.build_crosswalk([
        ("软件服务", "Information Technology"),
        ("资讯科技器材", "Information Technology"),
        ("银行", "Financials"),
    ])

    assert crosswalk == {
        "软件服务": "Information Technology",
        "资讯科技器材": "Information Technology",
        "银行": "Financials",
    }
    with pytest.raises(hsics.HsicsIndustryError, match="多个一级行业"):
        hsics.build_crosswalk([
            ("软件服务", "Information Technology"),
            ("软件服务", "Consumer Discretionary"),
        ])


def test_build_members_covers_connect_and_marks_non_hsci():
    rows = rows_for_test()
    by_symbol = {row.symbol: row for row in rows}

    assert set(by_symbol) == {"00001.HK", "00005.HK", "00700.HK", "09988.HK", "00038.HK"}
    assert by_symbol["00700.HK"].sector == "软件服务"
    assert by_symbol["00700.HK"].industry == "Information Technology"
    assert by_symbol["00038.HK"].status == "mapped"
    assert by_symbol["00038.HK"].in_hsci is False
    assert all(row.status == "mapped" for row in rows)


def test_build_members_marks_missing_sector_or_parent_as_missing():
    rows = hsics.build_members(
        {"00001": profile("00001", ""), "00005": profile("00005", "未知二级")},
        {},
        {},
        {"00001", "00005"},
    )

    assert {row.status for row in rows} == {"missing"}


def test_publish_and_load_roundtrip(tmp_path):
    rows = rows_for_test()
    snapshot = hsics.build_snapshot(
        rows,
        {
            "综合企业": "Conglomerates",
            "银行": "Financials",
            "软件服务": "Information Technology",
            "专业零售": "Consumer Discretionary",
            "工业工程": "Industrials",
        },
        data_date=date(2026, 10, 23),
        fetched_at=FETCHED_AT,
        source_urls={"f10": "https://example.com/f10", "hsci": "https://example.com/hsci"},
    )
    csv_path = tmp_path / "hk_hsics_industry.csv"
    json_path = tmp_path / "hk_hsics_industry.snapshot.json"

    published = hsics.publish_snapshot(snapshot, rows, csv_path, json_path)
    loaded = hsics.load_members(csv_path, json_path)
    payload = json.loads(json_path.read_text(encoding="utf-8"))

    assert set(loaded) == {"00001.HK", "00005.HK", "00700.HK", "09988.HK", "00038.HK"}
    assert payload["total"] == 5
    assert payload["sector_count"] == 5
    assert payload["industry_count"] == 5
    assert payload["csv_sha256"] == hashlib.sha256(csv_path.read_bytes()).hexdigest()
    assert published.csv_sha256 == payload["csv_sha256"]
    assert "hsics_sector" in csv_path.read_text(encoding="utf-8").splitlines()[0]

    csv_path.write_text(csv_path.read_text(encoding="utf-8").replace("00700.HK", "00701.HK"),
                        encoding="utf-8")
    with pytest.raises(hsics.HsicsIndustryError, match="哈希"):
        hsics.load_members(csv_path, json_path)


def test_csv_digest_is_stable_across_line_endings():
    payload = b"symbol,hsics_sector\n00700.HK,\xe8\xbd\xaf\xe4\xbb\xb6\xe6\x9c\x8d\xe5\x8a\xa1\n"

    assert hsics._csv_digest(payload) == hsics._csv_digest(payload.replace(b"\n", b"\r\n"))


def test_latest_daily_pdf_picks_newest_con_pdf():
    payload = [{
        "seriesCode": "hsci",
        "reportList": [{
            "reportType": "con-pdf-1",
            "reportDate": [
                {"url": "/old.pdf", "date": "2026-10-22"},
                {"url": "/new.pdf", "date": "2026-10-23"},
            ],
        }],
    }]

    assert hsics.latest_daily_pdf(payload) == ("/new.pdf", "2026-10-23")


def test_validate_baseline_matches_official_counts():
    members = {
        "00001": pdf_member("00001", "Conglomerates"),
        "00005": pdf_member("00005", "Financials"),
    }

    summary = hsics.validate_baseline(members, {"Conglomerates": 1, "Financials": 1})
    assert summary["total"] == 2
    with pytest.raises(hsics.HsicsIndustryError, match="行业数量"):
        hsics.validate_baseline(members, {"Conglomerates": 2})


def test_fetch_hsci_counts_parses_official_groups(mocker):
    response = mocker.Mock()
    response.json.return_value = {"data": {"numOfConsList": [
        {"indexName": "Hang Seng Composite Industry Index - Energy", "count": 16},
        {"indexName": "Hang Seng Composite Industry Index - Financials", "count": 56},
        {"indexName": "Hang Seng Composite Industry Index - Properties & Construction", "count": 52},
    ]}}
    response.raise_for_status = mocker.Mock()
    session = mocker.Mock()
    session.get.return_value = response

    assert hsics.fetch_hsci_counts(session) == {
        "Energy": 16,
        "Financials": 56,
        "Properties and Construction": 52,
    }


def test_refresh_aborts_on_crosswalk_conflict_and_preserves_files(tmp_path, mocker):
    csv_path = tmp_path / "hk_hsics_industry.csv"
    json_path = tmp_path / "hk_hsics_industry.snapshot.json"
    csv_path.write_text("old-csv", encoding="utf-8")
    json_path.write_text("old-json", encoding="utf-8")
    mocker.patch.object(hsics, "fetch_f10_sectors", return_value={
        "00001": profile("00001", "软件服务"),
        "00005": profile("00005", "软件服务"),
    })
    mocker.patch.object(hsics, "fetch_hsci_pdf", return_value=(b"pdf", "2026-10-23"))
    mocker.patch.object(hsics, "parse_hsci_pdf_bytes", return_value={
        "00001": pdf_member("00001", "Information Technology"),
        "00005": pdf_member("00005", "Consumer Discretionary"),
    })
    mocker.patch.object(hsics, "fetch_hsci_counts",
                        return_value={"Information Technology": 1, "Consumer Discretionary": 1})

    with pytest.raises(hsics.HsicsIndustryError, match="多个一级行业"):
        hsics.refresh(csv_path, json_path, connect_symbols={"00001", "00005"})
    assert csv_path.read_text(encoding="utf-8") == "old-csv"
    assert json_path.read_text(encoding="utf-8") == "old-json"


def test_refresh_result_preserves_failure_and_records_success(tmp_path, mocker):
    csv_path, json_path = tmp_path / "industry.csv", tmp_path / "industry.json"
    csv_path.write_text("old-csv", encoding="utf-8")
    json_path.write_text("old-json", encoding="utf-8")
    refresh = mocker.patch.object(hsics, "refresh", side_effect=hsics.HsicsIndustryError("失败"))
    result, record = hsics.refresh_result(csv_path, json_path, connect_symbols={"00001"})
    assert result is None and (record.result_state, record.published) == ("failed", False)
    assert csv_path.read_text(encoding="utf-8") == "old-csv"
    assert json_path.read_text(encoding="utf-8") == "old-json"
    expected = mocker.Mock()
    refresh.side_effect = None
    refresh.return_value = expected
    result, record = hsics.refresh_result(csv_path, json_path, connect_symbols={"00001"})
    assert result is expected and (record.result_state, record.published) == ("complete", True)


def test_refresh_publishes_mapping(tmp_path, mocker):
    mocker.patch.object(hsics, "fetch_f10_sectors", return_value={
        "00001": profile("00001", "综合企业"),
        "00005": profile("00005", "银行"),
        "00700": profile("00700", "软件服务"),
    })
    mocker.patch.object(hsics, "fetch_hsci_pdf", return_value=(b"pdf", "2026-10-23"))
    mocker.patch.object(hsics, "parse_hsci_pdf_bytes", return_value={
        "00001": pdf_member("00001", "Conglomerates"),
        "00005": pdf_member("00005", "Financials"),
        "00700": pdf_member("00700", "Information Technology"),
    })
    mocker.patch.object(hsics, "fetch_hsci_counts", return_value={
        "Conglomerates": 1, "Financials": 1, "Information Technology": 1,
    })
    csv_path = tmp_path / "hk_hsics_industry.csv"
    json_path = tmp_path / "hk_hsics_industry.snapshot.json"

    snapshot, record = hsics.refresh_result(
        csv_path, json_path, connect_symbols={"00001", "00005", "00700"},
        strict_counts=False,
    )

    assert snapshot is not None
    assert (record.result_state, record.published) == ("complete", True)
    assert snapshot.total == 3
    assert snapshot.sector_count == 3
    assert snapshot.industry_count == 3
    assert hsics.load_members(csv_path, json_path)["00700.HK"].sector == "软件服务"
    assert json.loads(json_path.read_text(encoding="utf-8"))["validation"]["parent_mismatches"] == 0


def test_hsics_paths_are_beside_mapping():
    from utils.paths import (
        hk_hsics_industry_path,
        hk_hsics_snapshot_path,
        industry_mapping_path,
    )

    assert hk_hsics_industry_path().name == "hk_hsics_industry.csv"
    assert hk_hsics_snapshot_path().name == "hk_hsics_industry.snapshot.json"
    assert hk_hsics_industry_path().parent == industry_mapping_path().parent


def test_load_hsics_members_returns_empty_when_snapshot_missing(tmp_path):
    assert hsics.load_hsics_members(
        tmp_path / "hk_hsics_industry.csv",
        tmp_path / "hk_hsics_industry.snapshot.json",
    ) == {}


def test_installed_paths_fall_back_per_hsics_pair(monkeypatch, tmp_path):
    from utils import paths

    monkeypatch.setattr(paths, "_source_root", lambda: None)
    resource_root = tmp_path / "resources"
    bundled = resource_root / "data"
    bundled.mkdir(parents=True)
    for name in ("hk_hsics_industry.csv", "hk_hsics_industry.snapshot.json",
                 "hk_stock_connect.snapshot.json"):
        (bundled / name).write_text("bundled", encoding="utf-8")
    monkeypatch.setattr(paths, "resource_root", lambda: resource_root)
    local = tmp_path / ".stock_robot/data"
    local.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    assert paths.hk_hsics_industry_path() == bundled / "hk_hsics_industry.csv"
    assert paths.hk_hsics_snapshot_path() == bundled / "hk_hsics_industry.snapshot.json"
    (local / "hk_hsics_industry.csv").write_text("local", encoding="utf-8")
    assert paths.hk_hsics_industry_path() == bundled / "hk_hsics_industry.csv"
    (local / "hk_hsics_industry.snapshot.json").write_text("local", encoding="utf-8")
    assert paths.hk_hsics_industry_path() == local / "hk_hsics_industry.csv"
    assert paths.hk_hsics_snapshot_path() == local / "hk_hsics_industry.snapshot.json"
    assert paths.hk_stock_connect_path() == bundled / "hk_stock_connect.snapshot.json"
    (local / "hk_stock_connect.snapshot.json").write_text("local", encoding="utf-8")
    assert paths.hk_stock_connect_path() == local / "hk_stock_connect.snapshot.json"


def test_load_connect_symbols_from_membership_snapshot(tmp_path):
    path = tmp_path / "hk_stock_connect.snapshot.json"
    path.write_text(json.dumps({"members": [
        {"symbol": "00700.HK", "name": "腾讯"},
        {"symbol": "00005.HK", "name": "汇丰"},
    ]}, ensure_ascii=False), encoding="utf-8")

    assert hsics.load_connect_symbols(path) == {"00700", "00005"}


def test_main_failure_returns_one_without_refresh(tmp_path, mocker):
    mocker.patch.object(hsics, "load_connect_symbols", return_value={"00001"})
    mocker.patch.object(hsics, "refresh", side_effect=hsics.HsicsIndustryError("测试失败"))

    code = hsics.main([
        "--csv", str(tmp_path / "hk_hsics_industry.csv"),
        "--json", str(tmp_path / "hk_hsics_industry.snapshot.json"),
    ])

    assert code == 1


def test_fetch_f10_profile_uses_trimmed_report(mocker):
    response = mocker.Mock()
    response.json.return_value = {
        "result": {"data": [{
            "ORG_NAME": "腾讯控股有限公司",
            "BELONG_INDUSTRY": "软件服务",
            "INDUSTRY_TYPE": "通讯服务-媒体与娱乐",
        }]}
    }
    response.raise_for_status = mocker.Mock()
    session = mocker.Mock()
    session.get.return_value = response

    result = hsics.fetch_f10_profile(session, "00700.HK")

    assert result.symbol == "00700"
    assert result.sector == "软件服务"
    assert result.gics == "通讯服务-媒体与娱乐"
    params = session.get.call_args.kwargs["params"]
    assert params["reportName"] == "RPT_HKF10_INFO_ORGPROFILE"
    assert params["filter"] == '(SECUCODE="00700.HK")'
    assert "BELONG_INDUSTRY" in params["columns"]
    assert "ORG_PROFILE" not in params["columns"]


def test_fetch_f10_profile_requires_hk_suffix_and_industry(mocker):
    response = mocker.Mock()
    response.json.return_value = {"result": {"data": []}}
    response.raise_for_status = mocker.Mock()
    session = mocker.Mock()
    session.get.return_value = response

    with pytest.raises(hsics.HsicsIndustryError, match="缺少行业"):
        hsics.fetch_f10_profile(session, "00700")
    assert session.get.call_args.kwargs["params"]["filter"] == '(SECUCODE="00700.HK")'


def test_refresh_rejects_missing_f10_rows(tmp_path, mocker):
    mocker.patch.object(hsics, "fetch_f10_sectors", return_value={
        "00001": profile("00001", ""),
    })
    mocker.patch.object(hsics, "fetch_hsci_pdf", return_value=(b"pdf", "2026-10-23"))
    mocker.patch.object(hsics, "parse_hsci_pdf_bytes", return_value={})
    mocker.patch.object(hsics, "fetch_hsci_counts", return_value={})
    csv_path = tmp_path / "hk_hsics_industry.csv"
    json_path = tmp_path / "hk_hsics_industry.snapshot.json"
    csv_path.write_text("old-csv", encoding="utf-8")
    json_path.write_text("old-json", encoding="utf-8")

    with pytest.raises(hsics.HsicsIndustryError, match="缺少行业"):
        hsics.refresh(
            csv_path, json_path, connect_symbols={"00001"}, strict_counts=False)
    assert csv_path.read_text(encoding="utf-8") == "old-csv"
    assert json_path.read_text(encoding="utf-8") == "old-json"


def test_publish_rolls_back_on_json_failure(tmp_path, mocker):
    rows = rows_for_test()
    crosswalk = {
        "综合企业": "Conglomerates",
        "银行": "Financials",
        "软件服务": "Information Technology",
        "专业零售": "Consumer Discretionary",
        "工业工程": "Industrials",
    }
    snapshot = hsics.build_snapshot(
        rows, crosswalk, data_date=date(2026, 10, 23), fetched_at=FETCHED_AT,
        source_urls={"f10": "x", "hsci": "y"})
    csv_path = tmp_path / "hk_hsics_industry.csv"
    json_path = tmp_path / "hk_hsics_industry.snapshot.json"
    csv_path.write_text("old-csv", encoding="utf-8")
    json_path.write_text("old-json", encoding="utf-8")
    atomic = hsics._atomic_write

    def fail_json(path, data):
        if path == json_path:
            raise OSError("测试模拟 JSON 写入失败")
        atomic(path, data)

    mocker.patch.object(hsics, "_atomic_write", side_effect=fail_json)
    with pytest.raises(OSError):
        hsics.publish_snapshot(snapshot, rows, csv_path, json_path)
    assert csv_path.read_text(encoding="utf-8") == "old-csv"
    assert json_path.read_text(encoding="utf-8") == "old-json"


def test_refresh_result_does_not_claim_rollback_success_when_rollback_fails(tmp_path, mocker):
    rows = rows_for_test()
    crosswalk = {"综合企业": "Conglomerates", "银行": "Financials",
                 "软件服务": "Information Technology", "专业零售": "Consumer Discretionary",
                 "工业工程": "Industrials"}
    snapshot = hsics.build_snapshot(rows, crosswalk, data_date=date(2026, 10, 23),
                                   fetched_at=FETCHED_AT, source_urls={"f10": "x", "hsci": "y"})
    csv_path, json_path = tmp_path / "industry.csv", tmp_path / "industry.json"
    csv_path.write_text("old-csv", encoding="utf-8")
    json_path.write_text("old-json", encoding="utf-8")
    atomic = hsics._atomic_write

    def fail_publish_and_rollback(path, data):
        if path == json_path or data == b"old-csv":
            raise OSError("模拟发布和回滚失败")
        atomic(path, data)

    mocker.patch.object(hsics, "_atomic_write", side_effect=fail_publish_and_rollback)
    mocker.patch.object(hsics, "refresh", side_effect=lambda *a, **kw: hsics.publish_snapshot(
        snapshot, rows, csv_path, json_path))
    result, record = hsics.refresh_result(csv_path, json_path, connect_symbols={"00001"})
    assert result is None and (record.result_state, record.published) == ("failed", False)
    assert csv_path.read_text(encoding="utf-8") != "old-csv"
    assert "保留旧快照" not in record.error_message


def test_production_hsics_snapshot_has_expected_shape():
    members = hsics.load_hsics_members()

    assert len(members) == 666
    mapped = [member for member in members.values() if member.status == "mapped"]
    assert len(mapped) == 666
    assert len({member.sector for member in mapped}) == 31
    assert len({member.industry for member in mapped}) == 12
    assert sum(1 for member in mapped if not member.in_hsci) == 107
