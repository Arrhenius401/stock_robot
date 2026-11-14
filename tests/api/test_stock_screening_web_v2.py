"""网页确认绑定与统一流水线接入；不发真实网络请求。"""
from datetime import date
from types import SimpleNamespace

import pytest

from radar.stocks.research_store import StockRevisionConflict
from radar.stocks.screening_web import ScreeningService, write_snapshot
from radar.stocks.screening_web_pipeline import WebPipeline


def pipeline(tmp_path):
    scope = {'companies': [{'symbol': '000001', 'name': '平安银行'}], 'scope_sha256': 'a'*64}
    evidence = {'governance': None, 'industry_scale': None, 'peer_universe': {'company_count': 626}}
    report = {'items': [{'symbol': '000001', 'name': '平安银行', 'evaluation_valid': True,
                         'checks': [{'code': 'audit', 'outcome': 'passed'}],
                         'pools': {'dividend_value': {'checks': [{'code': 'cash_review', 'outcome': 'passed'}]}}}]}
    commits = []
    calls = []
    def build(pool, cutoff, **kwargs):
        calls.append((pool, cutoff))
        return {'as_of': cutoff.isoformat(), 'report': report, 'rule_version': 'test', 'evidence': {},
                'stage_errors': [], 'shared': {'governance': {'current': True}, 'industry_scale': {'current': True}}}
    value = SimpleNamespace(state=tmp_path, db=tmp_path/'radar.db', scope=lambda _: scope,
                            evidence=lambda _: evidence, build=build,
                            commit=lambda pool, payload, **kw: commits.append((pool, payload)),
                            _shared_status=lambda _: {})
    return value, commits, calls


def test_preview_is_read_only_and_distinguishes_pool_and_complete_peer_scope(tmp_path):
    core, commits, calls = pipeline(tmp_path)
    bridge = WebPipeline(core)
    preview = bridge.preview('dividend_value', date(2026, 11, 7))
    assert preview['company_count'] == 1
    assert preview['shared_stages'][1]['symbol_count'] == 626
    assert preview['shared_stages'][0]['status'] == 'refresh_required'
    assert not list(tmp_path.iterdir()) and not commits and not calls
    core.scope = lambda _: {'companies': [{'symbol': '600000'}]}
    assert bridge.preview('dividend_value', date(2026, 11, 7))['preview_sha256'] != preview['preview_sha256']


def test_start_stale_preview_has_no_write_or_collection(tmp_path):
    core, commits, calls = pipeline(tmp_path)
    service = ScreeningService(tmp_path, pipeline=WebPipeline(core))
    with pytest.raises(StockRevisionConflict):
        service.start('dividend_value', 'screening', preview_sha256='0'*64)
    assert not list(tmp_path.iterdir()) and not commits and not calls


@pytest.mark.parametrize('shared_success', [True, False])
def test_pipeline_update_commits_once_only_after_shared_success(tmp_path, shared_success):
    core, commits, calls = pipeline(tmp_path)
    bridge = WebPipeline(core)
    preview = bridge.preview('dividend_value', date(2026, 11, 7))
    original = core.build
    def build(*args, **kwargs):
        payload = original(*args, **kwargs)
        payload['shared']['governance']['current'] = shared_success
        return payload
    core.build = build
    outcome = bridge.execute({'pool_id': 'dividend_value', 'as_of': '2026-11-07', 'run_id': 'new'},
                             preview, None, lambda *_: None)
    assert len(calls) == 1
    assert len(commits) == int(shared_success)
    assert outcome['status'] == ('completed' if shared_success else 'partial')
    assert bool(outcome['stage_errors']) is not shared_success


def test_snapshot_changed_during_pipeline_keeps_newer_result(tmp_path):
    core, commits, _ = pipeline(tmp_path)
    bridge = WebPipeline(core)
    preview = bridge.preview('dividend_value', date(2026, 11, 7))
    original = core.build
    def build(*args, **kwargs):
        result = original(*args, **kwargs)
        write_snapshot(tmp_path, 'dividend_value', result)
        return result
    core.build = build
    with pytest.raises(StockRevisionConflict):
        bridge.execute({'pool_id': 'dividend_value', 'as_of': '2026-11-07', 'run_id': 'new'},
                       preview, None, lambda *_: None)
    assert commits == []


@pytest.mark.parametrize('provider_status', ['complete', 'pending_verification'])
def test_completion_provider_failure_preserves_results_and_old_snapshot(tmp_path, monkeypatch, provider_status):
    # 来源失败与明确负证据不同：来源失败必须可见，不能标成已完成。
    core, commits, _ = pipeline(tmp_path)
    bridge = WebPipeline(core)
    preview = bridge.preview('dividend_value', date(2026, 11, 7))
    firm = {'symbol': '000001', 'condition': 'cash_review', 'group': 'style',
            'executable': {'mode': 'firm', 'provider': 'annual_report'}}
    monkeypatch.setattr('radar.stocks.screening_web_pipeline.build_completion_plan_v2',
                        lambda *args, **kwargs: {'items': [firm]})
    result = {'symbol': '000001', 'condition': 'cash_review', 'status': provider_status,
              'reason': '年报来源超时' if provider_status != 'complete' else '来源已验证'}

    def apply(pool, report, plan, cutoff, run_id, progress):
        progress(1, 1, [result], 'completion')
        return report, []

    core._apply = apply
    outcome = bridge.execute({'pool_id': 'dividend_value', 'as_of': '2026-11-07', 'run_id': 'new'},
                             preview, {'items': [firm]}, lambda *_: None)
    success = provider_status == 'complete'
    assert len(commits) == int(success)
    assert outcome['status'] == ('completed' if success else 'partial')
    assert outcome['results'] == [result]
    assert bool(outcome['stage_errors']) is not success
    if not success:
        assert outcome['stage_errors'] == [{'stage': 'completion', 'error': '年报来源超时'}]
