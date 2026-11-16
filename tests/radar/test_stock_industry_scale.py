"""候选池内同行业市值排名与 scale.json 边界。"""
import hashlib
from datetime import date

from radar.stocks.industry_scale import (
    build_industry_scale,
    load_industry_scale,
    save_industry_scale,
    scale_passes,
)

DAY_STR = '2026-11-07'
SOURCE = {'universe': 'candidate_pool_626', 'scope_sha256': 'a' * 64,
          'industry_mapping_sha256': 'b' * 64, 'market_value_manifests_sha256': 'c' * 64}


def rows(count=6, industry='半导体'):
    return [{'symbol': f'60000{index}', 'industry': industry,
             'market_cap_yuan': str((count - index + 1) * 100),
             'market_cap_date': '2026-11-06', 'market_value_manifest_sha256': 'd' * 64}
            for index in range(1, count + 1)]


def payload(count=6, industry='半导体'):
    return build_industry_scale(rows(count, industry), as_of=date(2026, 11, 7), source=SOURCE)


def test_rank_top_three_and_peer_count():
    value = payload()
    assert value['symbols']['600001']['rank'] == 1 and value['symbols']['600001']['peer_count'] == 6
    assert value['symbols']['600003']['rank'] == 3
    assert value['symbols']['600004']['rank'] == 4
    assert scale_passes(value['symbols']['600001'], 3, 5)
    assert not scale_passes(value['symbols']['600004'], 3, 5)


def test_peer_count_below_minimum_does_not_pass():
    value = payload(count=4)
    assert value['symbols']['600001']['peer_count'] == 4
    assert not scale_passes(value['symbols']['600001'], 3, 5)


def test_missing_market_cap_is_not_ranked_as_zero():
    data = rows(5)
    data[0]['market_cap_yuan'] = ''
    value = build_industry_scale(data, as_of=date(2026, 11, 7), source=SOURCE)
    assert value['symbols']['600001']['rank'] is None
    assert value['symbols']['600001']['peer_count'] == 4
    assert not scale_passes(value['symbols']['600001'], 3, 5)


def test_save_and_load_scale(tmp_path):
    value = payload()
    path = tmp_path / 'stock-leadership' / 'scale.json'
    save_industry_scale(path, value)
    loaded = load_industry_scale(tmp_path, '600001', date(2026, 11, 7))
    assert loaded['status'] == 'current' and loaded['scale']['rank'] == 1
    assert loaded['file_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_scale_wrong_cutoff_source_or_digest_is_unavailable(tmp_path):
    value = payload()
    path = tmp_path / 'stock-leadership' / 'scale.json'
    save_industry_scale(path, value)
    assert load_industry_scale(tmp_path, '600001', date(2026, 11, 8))['status'] == 'unavailable'

    bad = payload()
    bad['source']['scope_sha256'] = 'bad'
    save_industry_scale(path, bad)
    assert load_industry_scale(tmp_path, '600001', date(2026, 11, 7))['status'] == 'unavailable'

    bad = payload()
    bad['source_sha256'] = 'f' * 64
    save_industry_scale(path, bad)
    assert load_industry_scale(tmp_path, '600001', date(2026, 11, 7))['status'] == 'unavailable'
