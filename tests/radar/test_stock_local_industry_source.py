"""股票池直接复用本地行业表，独立于在线资料任务。"""
import json
from datetime import date

import pytest

from radar.stocks.industry_source import load_industry_source


def mapping(tmp_path, monkeypatch, status='verified'):
    path = tmp_path / 'industry_mapping.csv'
    path.write_text('symbol,sw_level1,sw_level2,style_category,mapping_status\n'
                    f'600900,公用事业,电力,公用事业,{status}\n', encoding='utf-8')
    monkeypatch.setattr('utils.paths.industry_mapping_path', lambda: path)
    return path


def test_local_table_is_used_without_creating_state_or_reading_tasks(tmp_path, monkeypatch):
    path = mapping(tmp_path, monkeypatch)
    state = tmp_path / 'state'
    source = load_industry_source(state, '600900', date(2026, 11, 1))
    assert source['status'] == 'local_reference'
    assert source['industry'] == {'level1': '公用事业', 'level2': '电力'}
    assert source['as_of'] is None
    assert source['source']['provider'] == 'local_industry_mapping'
    assert not state.exists() and str(path) not in json.dumps(source)
    before = source['manifest_sha256']
    path.write_text(path.read_text(encoding='utf-8').replace('电力', '电力Ⅱ'), encoding='utf-8')
    assert load_industry_source(state, '600900', date(2026, 11, 1))['manifest_sha256'] != before


@pytest.mark.parametrize('status', ['missing', 'unverified'])
def test_unverified_mapping_is_not_presented_as_available(tmp_path, monkeypatch, status):
    mapping(tmp_path, monkeypatch, status)
    assert load_industry_source(tmp_path / 'state', '600900', date(2026, 11, 1))['status'] == 'unavailable'


def test_failed_online_task_does_not_block_local_table_and_hash_binds_archive(tmp_path, monkeypatch):
    from radar.stocks.reference_task import REFERENCE_VERSION
    from radar.stocks.research_store import StockRevisionConflict
    from stock_robot.stock_admission import evaluate_store
    from tests.radar.test_stock_financial_source import DAY, environment
    from tests.radar.test_stock_reference_task import snapshot
    path = mapping(tmp_path, monkeypatch)
    research, archive, tasks, _ = environment(tmp_path)
    tasks.enqueue(kind='reference_review', pool_ids=['growth_trend'], target_date=DAY,
                  rule_version=REFERENCE_VERSION, snapshot=snapshot())
    monkeypatch.setattr('radar.stocks.reference_task.select_reference_manifest', lambda *a, **kw: pytest.fail('本地行业不能受在线任务影响'))
    source = load_industry_source(tmp_path, '600900', DAY)
    assert source['status'] == 'local_reference'
    inputs = archive.capture_inputs(symbol='600900')
    inputs['companies'][0].update(industry_source=source, industry_as_of=DAY.isoformat())
    report = evaluate_store(research, DAY, symbol='600900', include_styles=True, industry_sources={'600900': source})
    saved = archive.save_evaluation(report, inputs)
    path.write_text(path.read_text(encoding='utf-8').replace('电力', '电力Ⅱ'), encoding='utf-8')
    with pytest.raises(StockRevisionConflict):
        archive.assert_current(saved['evaluation_id'])
