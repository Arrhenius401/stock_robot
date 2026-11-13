"""CLI：显示快照、生成计划与补全干跑都能在命令行完成。"""
from __future__ import annotations

import json

from click.testing import CliRunner

from radar.stocks.screening_web import write_snapshot
from stock_robot.cli import main

POOL = 'dividend_value'


def report() -> dict:
    return {'items': [{
        'symbol': '600001', 'name': '测试公司', 'common_status': 'pending_verification',
        'evaluation_valid': True,
        'checks': [{'code': 'governance', 'outcome': 'pending_verification', 'reason': '监管来源缺失'},
                   {'code': 'leadership', 'outcome': 'pending_verification', 'reason': '缺少同业排名'}],
        'pools': {POOL: {'checks': [{'code': 'dividend_history', 'outcome': 'passed', 'reason': ''}]}},
        'industry_source': {}}], 'as_of': '2026-11-13', 'members_published': 0}


def _snapshot(state, as_of='2026-11-13') -> None:
    write_snapshot(state, POOL, {'as_of': as_of, 'report': report(), 'run_id': 'run-1',
                                 'rule_version': 'formal-2026-11-09-v6',
                                 'evidence': {'governance': {'status': 'unavailable'}, 'industry_scale': {'status': 'unavailable'}}})


def test_show_reports_counts(tmp_path):
    _snapshot(tmp_path)
    result = CliRunner().invoke(main, ['radar', 'stock-screening-show', '--state-dir', str(tmp_path), '--pool', POOL])
    assert result.exit_code == 0, result.output
    value = json.loads(result.output)
    assert value['counts'] == {'screened': 1, 'passed': 0, 'pending': 1, 'failed': 0}
    assert value['items'][0]['status'] == 'pending'


def test_plan_marks_shared_stages(tmp_path):
    _snapshot(tmp_path)
    result = CliRunner().invoke(main, ['radar', 'stock-screening-plan', '--state-dir', str(tmp_path), '--pool', POOL])
    assert result.exit_code == 0, result.output
    plan = json.loads(result.output)
    assert plan['version'] == 'completion-plan-v2'
    assert [stage['stage'] for stage in plan['shared_stages']] == ['governance', 'industry_scale']
    assert plan['snapshot_sha256'] and plan['plan_sha256']


def test_complete_dry_run_checks_plan_binding(tmp_path):
    _snapshot(tmp_path)
    runner = CliRunner()
    plan = json.loads(runner.invoke(main, ['radar', 'stock-screening-plan', '--state-dir', str(tmp_path),
                                           '--pool', POOL]).output)
    plan_path = tmp_path / 'plan.json'
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
    result = runner.invoke(main, ['radar', 'stock-screening-complete', '--state-dir', str(tmp_path),
                                  '--pool', POOL, '--plan', str(plan_path), '--dry-run'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)['dry_run'] is True
    assert json.loads(result.output)['items'] == []


def test_show_requires_snapshot(tmp_path):
    result = CliRunner().invoke(main, ['radar', 'stock-screening-show', '--state-dir', str(tmp_path), '--pool', POOL])
    assert result.exit_code != 0
    assert '没有筛选快照' in result.output
