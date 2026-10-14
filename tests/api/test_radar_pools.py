"""池目录及真实空股票池的只读接口。"""

from fastapi.testclient import TestClient

from api.app import create_app


def test_catalog_and_members_do_not_initialize_data_or_tasks(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("只读股票接口不应初始化采集和任务")
    for name in ("Config", "RadarStore", "RadarRefresher", "UpdateStore",
                 "AkShareETFDataProvider"):
        monkeypatch.setattr(f"api.radar.{name}", forbidden)
    client = TestClient(create_app(core=None, push=False))
    response = client.get("/api/v1/radar/pools")
    assert response.status_code == 200
    rows = {item["id"]: item for item in response.json()}
    assert set(rows) == {"cn_hk_etf", "overseas_etf", "dividend_value", "growth_trend"}
    assert rows["cn_hk_etf"]["capabilities"] == {"update": True, "manage": False}
    for identifier in ("dividend_value", "growth_trend"):
        assert rows[identifier]["capabilities"] == {"update": False, "manage": False}
        assert rows[identifier]["rule_status"] == "unconfigured"
        response = client.get(f"/api/v1/radar/stocks/{identifier}/members")
        assert response.json() == {
            "pool_id": identifier, "revision": 0, "items": [], "industries": [],
            "status": "empty", "unavailable_reason": "选股规则和采集流程尚未配置",
        }


def test_unknown_or_etf_id_cannot_read_stock_members():
    client = TestClient(create_app(core=None, push=False))
    for identifier in ("missing", "cn_hk_etf"):
        assert client.get(f"/api/v1/radar/stocks/{identifier}/members").status_code == 404


def test_stock_definition_is_independent_and_update_is_unavailable():
    client = TestClient(create_app(core=None, push=False))
    response = client.get("/api/v1/radar/stocks/dividend_value")
    assert response.status_code == 200
    assert response.json()["rule_status"] == "unconfigured"
    assert "score_profile" not in response.json()
    assert client.post("/api/v1/radar/stocks/dividend_value/update").status_code == 503


def test_bad_stock_configuration_is_not_reported_as_empty(monkeypatch):
    from radar.stocks.pools import StockPoolConfigError

    def broken(_self):
        raise StockPoolConfigError("股票定义损坏")
    monkeypatch.setattr("api.radar.StockPoolRepository.load_all", broken)
    client = TestClient(create_app(core=None, push=False))
    for path in ("/api/v1/radar/pools", "/api/v1/radar/stocks/dividend_value/members"):
        response = client.get(path)
        assert response.status_code == 500
        assert response.json()["detail"] == "股票定义损坏"
