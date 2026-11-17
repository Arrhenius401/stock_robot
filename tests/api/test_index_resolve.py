"""只读身份解析 HTTP 契约。"""
import pytest
from httpx import ASGITransport, AsyncClient

from api.app import create_app
from data.index_catalog_sources import (
    parse_hsi_catalog,
    save_catalog_aliases,
    save_catalog_snapshot,
)


@pytest.mark.asyncio
async def test_resolve_readonly_old_snapshot_and_aliases(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / '.stock_robot/index-catalog-hsi.json'
    items = parse_hsi_catalog({'code':'0','data':[{'indexCode':'02083.00','indexName':'恒生科技指数','indexShortName':'HSTECH','indexSeriesCode':'hstech','indexType':'PI','regionId':'hongkong'}]})
    async with AsyncClient(transport=ASGITransport(app=create_app(push=False)),base_url='http://test') as client:
        data = (await client.get('/api/v1/indices/resolve',params={'q':'HSTECH'})).json()
        assert data['status'] == 'not_found'
        assert data['catalog']['hsi_loaded'] is False
        assert not path.exists()
        save_catalog_snapshot(path,items)
        data = (await client.get('/api/v1/indices/resolve',params={'q':'恒生科技'})).json()
        assert data['status'] == 'ok'
        assert data['catalog']['hsi_aliases_loaded'] is False
        save_catalog_aliases(path,items)
        data = (await client.get('/api/v1/indices/resolve',params={'q':'HSTECH.HK'})).json()
        assert data['entry']['symbol'] == 'HSI:02083.00'
        assert data['entry']['aliases'] == ['HSTECH']
        assert data['catalog']['hsi_aliases_loaded'] is True
        assert (await client.get('/api/v1/indices/resolve',params={'q':'  '})).status_code == 422
        a = (await client.get('/api/v1/indices/resolve',params={'q':'000300'})).json()
        assert a['entry']['market'] == 'a-shares'
        assert a['entry']['return_type'] == 'price'
