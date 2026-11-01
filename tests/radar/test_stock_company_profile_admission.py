"""上市年限接线的冲突及历史边界。"""
from copy import deepcopy
from datetime import date

from radar.stocks.admission import evaluate_common_admission
from radar.stocks.company_profile_admission import merge_common_profile


def test_current_and_historical_are_separate():
    day = date(2026, 10, 18)
    manual = evaluate_common_admission('600900', [], day)
    original = deepcopy(manual)
    source = {'status': 'current', 'as_of': day.isoformat(), 'checks': [{'code': 'listing_age', 'outcome': 'passed'}]}
    merged = merge_common_profile(manual, source, day)
    assert next(c for c in merged['checks'] if c['code'] == 'listing_age')['outcome'] == 'passed'
    assert manual == original
    source['status'] = 'historical'
    old = merge_common_profile(manual, source, date(2026, 10, 19))
    check = next(c for c in old['checks'] if c['code'] == 'listing_age')
    assert check['outcome'] == 'pending_verification'
    assert check['reference_check']['outcome'] == 'passed'


def test_manual_failure_is_retained_on_conflict():
    day = date(2026, 10, 18)
    manual = evaluate_common_admission('600900', [], day)
    manual['common_status'] = 'failed'
    next(c for c in manual['checks'] if c['code'] == 'listing_age')['outcome'] = 'failed'
    source = {'status': 'current', 'as_of': day.isoformat(), 'checks': [{'code': 'listing_age', 'outcome': 'passed'}]}
    merged = merge_common_profile(manual, source, day)
    assert merged['common_status'] == 'failed'
    assert next(c for c in merged['checks'] if c['code'] == 'listing_age')['outcome'] == 'pending_verification'


def test_profile_and_industry_archive_binding(tmp_path, monkeypatch):
    import pytest

    from radar.stocks.evaluation_store import StockEvaluationStore
    from radar.stocks.research_store import StockResearchStore, StockRevisionConflict
    from stock_robot.stock_admission import evaluate_store

    day = date(2026, 10, 18)
    store = StockResearchStore(tmp_path / 'radar.db')
    store.import_companies([{'symbol': '600900', 'name': '长江电力'}], source_sha256='a' * 64,
                           source_label='合成', data_as_of=day)
    archive = StockEvaluationStore(store)
    source = {'symbol': '600900', 'evaluation_as_of': day.isoformat(), 'as_of': day.isoformat(),
              'status': 'current', 'checks': [{'code': 'listing_age', 'outcome': 'passed'}]}
    industry = {'symbol': '600900', 'evaluation_as_of': day.isoformat(), 'as_of': day.isoformat(),
                'status': 'current', 'industry': {'level1': '公用事业', 'level2': '电力'}}
    live = [industry]
    monkeypatch.setattr('radar.stocks.evaluation_store.load_company_profile_source', lambda *_, **__: source)
    monkeypatch.setattr('radar.stocks.evaluation_store.load_industry_source', lambda *_, **__: live[0])
    before = evaluate_store(store, day, symbol='600900', include_styles=True)
    report = evaluate_store(store, day, symbol='600900', include_styles=True,
                            profile_sources={'600900': source}, industry_sources={'600900': industry})
    assert report['items'][0]['industry_source'] == industry
    assert report['items'][0]['common_status'] == before['items'][0]['common_status']
    inputs = archive.capture_inputs(symbol='600900')
    inputs['companies'][0].update(company_profile_source=source, profile_as_of=day.isoformat(),
                                   industry_source=industry, industry_as_of=day.isoformat())
    saved = archive.save_evaluation(report, inputs)
    live[0] = {**industry, 'manifest_sha256': 'b' * 64}
    with pytest.raises(StockRevisionConflict):
        archive.assert_current(saved['evaluation_id'])
