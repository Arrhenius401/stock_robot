"""港股恒生二级同行池单元测试。"""

from data import hk_hsics_peers as peers
from data.hk_hsics_industry import HsicsMember


def member(symbol: str, sector: str, industry: str, status: str = "mapped") -> HsicsMember:
    return HsicsMember(
        symbol=symbol, sector=sector, industry=industry, status=status, in_hsci=True)


MEMBERS = {
    "00700.HK": member("00700.HK", "软件服务", "Information Technology"),
    "00268.HK": member("00268.HK", "软件服务", "Information Technology"),
    "00909.HK": member("00909.HK", "软件服务", "Information Technology"),
    "00005.HK": member("00005.HK", "银行", "Financials"),
    "09988.HK": member("09988.HK", "专业零售", "Consumer Discretionary", "missing"),
}
CONNECT = {"00700.HK", "00268.HK", "00909.HK", "00005.HK", "09988.HK"}


def pool_row(symbol: str, name: str, pe, pb, type_name: str = "软件服务") -> dict:
    return {
        "CORRE_SECUCODE": symbol,
        "CORRE_SECURITY_NAME": name,
        "PE_TTM": pe,
        "PB_MQR": pb,
        "TYPE_NAME": type_name,
        "TYPE_ID": "403010002",
        "REPORT_DATE": "2026-10-23 00:00:00",
    }


def test_build_industry_data_filters_connect_mapped_same_sector():
    rows = [
        pool_row("00700.HK", "腾讯控股", 13.15, 2.68),
        pool_row("00268.HK", "金蝶国际", 30.0, 4.0),
        pool_row("00909.HK", "明源云", -5.0, None),
        pool_row("00005.HK", "汇丰控股", 12.0, 1.8, type_name="软件服务"),
        pool_row("01111.HK", "非港股通", 8.0, 1.2),
        pool_row("09988.HK", "阿里巴巴", 22.0, 1.6),
    ]

    data = peers.build_industry_data("00700.HK", rows, MEMBERS, CONNECT)

    assert data is not None
    assert data.industry == "软件服务"
    assert data.sector == "Information Technology"
    assert data.peer_scope == "港股通·恒生HSICS二级"
    assert data.peer_industry == "软件服务"
    assert data.peers == ["00268.HK", "00909.HK"]
    assert data.peer_names == {"00268.HK": "金蝶国际", "00909.HK": "明源云"}
    assert data._target_pe == 13.15
    assert data._target_pb == 2.68
    assert data._data_date == "2026-10-23"
    assert set(data._peer_valuations) == {"00268.HK", "00909.HK"}
    assert data._peer_valuations["00909.HK"].pe_ttm == -5.0


def test_build_industry_data_requires_target_in_connect():
    rows = [pool_row("00700.HK", "腾讯控股", 13.15, 2.68)]
    assert peers.build_industry_data(
        "00700.HK", rows, MEMBERS, CONNECT - {"00700.HK"}) is None


def test_build_industry_data_returns_none_for_non_member_or_missing_mapping():
    assert peers.build_industry_data("00700.HK", [], MEMBERS, CONNECT) is None
    assert peers.build_industry_data("01111.HK", [], MEMBERS, CONNECT) is None
    assert peers.build_industry_data("09988.HK", [
        pool_row("09988.HK", "阿里巴巴", 22.0, 1.6, "专业零售"),
    ], MEMBERS, CONNECT) is None
    assert peers.build_industry_data("00700.HK", [
        pool_row("00700.HK", "腾讯控股", 13.15, 2.68, "其他行业"),
    ], MEMBERS, CONNECT) is None


def test_fetch_sector_pool_paginates_and_returns_report_date(mocker):
    response_one = mocker.Mock()
    response_one.json.return_value = {
        "success": True,
        "result": {"count": 2, "pages": 2, "data": [pool_row("00700.HK", "腾讯", 13, 2)]},
    }
    response_two = mocker.Mock()
    response_two.json.return_value = {
        "success": True,
        "result": {"count": 2, "pages": 2, "data": [pool_row("00268.HK", "金蝶", 30, 4)]},
    }
    session = mocker.Mock()
    session.get.side_effect = [response_one, response_two]

    rows, report_date = peers.fetch_sector_pool(session, "00700.HK")

    assert len(rows) == 2
    assert report_date == "2026-10-23"
    assert session.get.call_count == 2
    assert session.get.call_args_list[1].kwargs["params"]["pageNumber"] == "2"


def test_fetch_sector_pool_uses_latest_report_date(mocker):
    old = pool_row("00700.HK", "腾讯", 13, 2)
    old["REPORT_DATE"] = "2026-10-22 00:00:00"
    latest = pool_row("00700.HK", "腾讯", 13, 2)
    response = mocker.Mock()
    response.json.return_value = {
        "success": True,
        "result": {"count": 2, "pages": 1, "data": [old, latest]},
    }
    session = mocker.Mock()
    session.get.return_value = response

    rows, report_date = peers.fetch_sector_pool(session, "00700.HK")

    assert len(rows) == 1
    assert rows[0]["REPORT_DATE"].startswith("2026-10-23")
    assert report_date == "2026-10-23"


def test_cli_registry_registers_hk_industry_adapter():
    from stock_robot.cli import _get_registry

    sources = _get_registry().get_data_sources("hk", "industry")

    assert any(isinstance(source, peers.HKIndustryAdapter) for source in sources)


def test_adapter_supports_only_hk_industry():
    adapter = peers.HKIndustryAdapter()

    assert adapter.supports("hk", "industry") is True
    assert adapter.supports("a-shares", "industry") is False
    assert adapter.supports("hk", "valuation") is False
