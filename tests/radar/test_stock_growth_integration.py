"""成长参考研究不改变资格，并绑定档案来源。"""
from datetime import date

import pytest

from radar.stocks.evaluation_store import StockEvaluationStore
from radar.stocks.research_store import (
    StockResearchError,
    StockResearchStore,
    StockRevisionConflict,
)
from stock_robot.stock_admission import evaluate_store


def test_reference_preserves_status_and_archive_invalidates(tmp_path, monkeypatch):
    day = date(2026, 10, 18)
    store = StockResearchStore(tmp_path / 'radar.db')
    store.import_companies([{'symbol': '600900', 'name': '长江电力'}], source_sha256='a' * 64,
                           source_label='合成', data_as_of=day)
    archive = StockEvaluationStore(store)
    source = {'symbol': '600900', 'evaluation_as_of': day.isoformat(), 'as_of': '2026-10-17',
              'status': 'historical', 'research': {'paths': {'sustained_growth': 'met_reference'}}}
    live = [source]
    monkeypatch.setattr('radar.stocks.evaluation_store.load_growth_source', lambda *_: live[0])
    before = evaluate_store(store, day, symbol='600900', include_styles=True)
    report = evaluate_store(store, day, symbol='600900', include_styles=True, growth_sources={'600900': source})
    for pool, value in before['items'][0]['pools'].items():
        actual = report['items'][0]['pools'][pool]
        for key in ('admission_status', 'style_status', 'opportunity_status'):
            assert actual[key] == value[key]
    assert report['items'][0]['pools']['growth_trend']['growth_research'] == source
    inputs = archive.capture_inputs(symbol='600900')
    inputs['companies'][0].update(growth_source=source, growth_as_of=day.isoformat())
    report['items'][0]['pools']['growth_trend']['growth_research'] = {**source, 'symbol': '000001'}
    with pytest.raises(StockResearchError, match='成长研究展示'):
        archive.save_evaluation(report, inputs)
    report['items'][0]['pools']['growth_trend']['growth_research'] = source
    saved = archive.save_evaluation(report, inputs)
    live[0] = {**source, 'manifest_sha256': 'b' * 64}
    with pytest.raises(StockRevisionConflict):
        archive.assert_current(saved['evaluation_id'])
