"""恒生目录 API 的身份与分页契约。"""
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from api.app import create_app
from data.index_catalog_sources import parse_hsi_catalog, save_catalog_snapshot
from tests.api.test_app import make_core


@pytest.mark.asyncio
async def test_hsi_catalog_and_analysis_share_official_identity(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    items = parse_hsi_catalog({'code': '0', 'data': [
        {'indexCode': '01067.00', 'indexName': '恒生高股息率指数', 'indexType': 'PI',
         'regionId': 'hongkong', 'indexSeriesCode': 'hshdyi'},
    ]})
    save_catalog_snapshot(tmp_path / '.stock_robot/index-catalog-hsi.json', items)
    core = make_core()
    received = []

    def run(targets):
        received.extend(targets)
        return SimpleNamespace(reports=[], compare=None, errors=[])

    monkeypatch.setattr(core.index_pipeline, 'run', run)
    async with AsyncClient(transport=ASGITransport(app=create_app(core=core, push=False)),
                           base_url='http://test') as client:
        response = await client.get('/api/v1/indices', params={'q': '高股息率', 'market': 'hk', 'limit': 1})
        assert response.status_code == 200
        data = response.json()
        assert data['indices_total'] == 1
        assert data['indices'][0]['symbol'] == 'HSI:01067.00'
        assert data['indices'][0]['capability'] == 'unknown'
        response = await client.post('/api/v1/index', json={'symbol': 'HSI:01067.00'})
        assert response.status_code == 200
        assert received[0].official_index_code == '01067.00'
        assert received[0].market == 'hk'


@pytest.mark.asyncio
async def test_unknown_hsi_identity_does_not_fall_back_to_a_share(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=create_app(core=make_core(), push=False)),
                           base_url='http://test') as client:
        response = await client.post('/api/v1/index', json={'symbol': 'HSI:01067.00', 'index_style': 'broad'})
        assert response.status_code == 422
        assert '刷新目录' in response.json()['detail']
        response = await client.post('/api/v1/index', json={'symbol': 'CSI:999999', 'index_style': 'broad'})
        assert response.status_code == 422
        assert '刷新目录' in response.json()['detail']


@pytest.mark.asyncio
async def test_unverified_cni_index_is_visible_but_not_analyzed(tmp_path, monkeypatch):
    from data.index_catalog_sources import parse_cni_catalog

    monkeypatch.chdir(tmp_path)
    items = parse_cni_catalog({'code': 200, 'data': {'total': 1, 'rows': [
        {'indexcode': '980092', 'indexname': '目录测试条目'},
    ]}})
    save_catalog_snapshot(tmp_path / '.stock_robot/index-catalog-cni.json', items)
    async with AsyncClient(transport=ASGITransport(app=create_app(core=make_core(), push=False)),
                           base_url='http://test') as client:
        response = await client.get('/api/v1/indices', params={'market': 'unknown', 'q': '目录测试'})
        assert response.json()['indices'][0]['symbol'] == 'CNI:980092'
        response = await client.post('/api/v1/index', json={'symbol': 'CNI:980092'})
        assert response.status_code == 422
        assert '尚未核验' in response.json()['detail']


@pytest.mark.asyncio
async def test_refresh_keeps_stale_source_snapshot_on_failure(tmp_path, monkeypatch):
    import data.index_catalog_sources as sources

    monkeypatch.chdir(tmp_path)
    items = sources.parse_cni_catalog({'code': 200, 'data': {'total': 1, 'rows': [
        {'indexcode': '980092', 'indexname': '国证样本'},
    ]}})
    save_catalog_snapshot(tmp_path / '.stock_robot/index-catalog-cni.json', items)

    def fail():
        raise ValueError('源解析失败')

    monkeypatch.setattr(sources, 'fetch_cni_catalog', fail)
    async with AsyncClient(transport=ASGITransport(app=create_app(core=make_core(), push=False)),
                           base_url='http://test') as client:
        response = await client.post('/api/v1/indices/refresh', params={'source': 'cni'})
        assert response.status_code == 200
        assert response.json() == {'source': 'cni', 'state': 'stale', 'total': 1}
        assert (await client.get('/api/v1/indices', params={'q': '国证样本'})).json()['indices_total'] == 1


@pytest.mark.asyncio
async def test_verified_legacy_csi_identity_uses_existing_a_share_pipeline(tmp_path, monkeypatch):
    from data.index_catalog_sources import CatalogEntry

    monkeypatch.chdir(tmp_path)
    save_catalog_snapshot(tmp_path / '.stock_robot/index-catalog-csi1.json', [
        CatalogEntry(identity='csi:000300', code='000300', name='沪深300',
                     provider='csi', market='unknown', return_type='unknown'),
    ])
    core = make_core()
    received = []

    def run(targets):
        received.extend(targets)
        return SimpleNamespace(reports=[], compare=None, errors=[])

    monkeypatch.setattr(core.index_pipeline, 'run', run)
    async with AsyncClient(transport=ASGITransport(app=create_app(core=core, push=False)),
                           base_url='http://test') as client:
        response = await client.post('/api/v1/index', json={'symbol': 'CSI:000300'})
        assert response.status_code == 200
        assert (received[0].symbol, received[0].market) == ('000300', 'a-shares')
