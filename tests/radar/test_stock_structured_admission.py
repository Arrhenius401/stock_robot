"""结构化盈利接入只读资格报告，不修改旧审核入口。"""
import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from radar.stocks.index_scope import POOL_INDEX_CODES
from radar.stocks.research_store import StockResearchStore
from stock_robot.stock_structured_admission import (
    build_structured_admission,
    merge_profitability,
)
from stock_robot.stock_structured_financial import run_structured_batch


def saved(tmp_path):
    samples = json.loads((Path(__file__).parents[1]/'resources/radar/eastmoney-structured-financial-samples.json').read_bytes())
    companies = [{'symbol': s['symbol'], 'name': s['symbol'], 'pool_ids': list(POOL_INDEX_CODES), 'source_indexes': ['000510']} for s in samples]
    scope = {'as_of': '2026-10-16', 'scope_complete': True, 'status': 'complete', 'companies': companies,
             'pools': {p: {'status': 'complete', 'complete': True, 'index_codes': list(codes), 'companies': [c['symbol'] for c in companies]} for p, codes in POOL_INDEX_CODES.items()}}
    path = tmp_path/'scope.json'
    path.write_text(json.dumps(scope), encoding='utf8')
    workspace = tmp_path/'financial'
    by_symbol = {s['symbol']: s for s in samples}
    run_structured_batch(companies, workspace, date(2026, 10, 17), input_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), collector=lambda symbol, *_a, **_k: by_symbol[symbol])
    db = tmp_path/'research.db'
    StockResearchStore(db)
    return path, workspace/'summary.json', db


def test_four_real_samples_read_only(tmp_path):
    scope, summary, db = saved(tmp_path)
    before = db.read_bytes()
    report = build_structured_admission(scope, summary, db)
    assert db.read_bytes() == before
    assert len(report['items']) == 4
    assert report['summary']['pending_verification'] == 4
    assert report['members_published'] == 0
    for row in report['items']:
        checks = row['checks']
        assert sum(c.get('channel') == 'structured_financial' for c in checks) == 8
        assert all(c['outcome'] == 'pending_verification' for c in checks if c.get('channel') != 'structured_financial')
        assert all(c['outcome'] == 'pending_verification' for c in row['manual_common']['checks'])


@pytest.mark.parametrize('mutation', ['hash', 'scope', 'pool', 'date', 'company'])
def test_binding_rejected(tmp_path, mutation):
    scope, summary, db = saved(tmp_path)
    d = json.loads(scope.read_bytes())
    if mutation == 'scope': d['scope_complete'] = False
    if mutation == 'pool': d['companies'][0]['pool_ids'] = ['dividend_value']
    if mutation == 'date': d['as_of'] = '2026-10-18'
    if mutation == 'company': d['companies'].pop()
    scope.write_text(json.dumps(d)+' ', encoding='utf8')
    if mutation != 'hash':
        for path in [summary, summary.parent/'manifest.json']:
            value = json.loads(path.read_bytes()); value['input_sha256'] = hashlib.sha256(scope.read_bytes()).hexdigest(); path.write_text(json.dumps(value), encoding='utf8')
    with pytest.raises(ValueError): build_structured_admission(scope, summary, db)


def test_corruption_and_unattempted_keep_eight_pending(tmp_path):
    scope, summary, db = saved(tmp_path)
    d = json.loads(summary.read_bytes())
    Path(d['items'][0]['artifact']['path']).write_bytes(b'{}')
    d['items'].pop()
    summary.write_text(json.dumps(d), encoding='utf8')
    report = build_structured_admission(scope, summary, db)
    assert len(report['items']) == 4
    assert sum(all(c['outcome'] == 'pending_verification' for c in r['checks']) for r in report['items']) == 2


@pytest.mark.parametrize(('manual', 'structured', 'expected'), [('failed','passed','pending_verification'), ('passed','failed','pending_verification'), ('passed','pending_verification','pending_verification'), ('pending_verification','passed','passed')])
def test_merge_conservative(manual, structured, expected):
    checks = merge_profitability([{'code':'ttm_parent_profit','outcome':manual,'reason':'人工','evidence_ids':['x']}], {'financial_numeric_checks':[{'code':'ttm_parent_profit','outcome':structured}]}, {'ttm_parent_profit'})
    assert checks[0]['outcome'] == expected
    assert checks[0]['manual_check']['evidence_ids'] == ['x']

@pytest.mark.parametrize('target', ['scope', 'summary', 'db', 'wal', 'source'])
def test_cli_rejects_destructive_output(tmp_path, target):
    from click.testing import CliRunner

    from stock_robot.stock_structured_admission import structured_admission_command
    scope, summary, db = saved(tmp_path)
    output = {'scope': scope, 'summary': summary, 'db': db, 'wal': Path(str(db)+'-wal'), 'source': Path(__file__).parents[2]/'src/forbidden.json'}[target]
    result = CliRunner().invoke(structured_admission_command, ['--scope',str(scope),'--financial-summary',str(summary),'--db',str(db),'--output',str(output)])
    assert result.exit_code != 0


def test_cli_exports_only_report(tmp_path):
    from click.testing import CliRunner

    from stock_robot.stock_structured_admission import structured_admission_command
    scope, summary, db = saved(tmp_path)
    output = tmp_path/'report.json'
    result = CliRunner().invoke(structured_admission_command, ['--scope',str(scope),'--financial-summary',str(summary),'--db',str(db),'--output',str(output)])
    assert result.exit_code == 0, result.output
    assert json.loads(output.read_bytes())['formal_screening_enabled'] is False


def test_manual_failure_survives_structured_conflict(tmp_path, monkeypatch):
    import stock_robot.stock_structured_admission as module
    scope, summary, db = saved(tmp_path)
    original = module.evaluate_common_admission

    def evaluate(*args, **kwargs):
        result = original(*args, **kwargs)
        row = next(c for c in result['checks'] if c['code'] == 'ttm_parent_profit')
        row.update(outcome='failed', reason='已审核非正利润', evidence_ids=['manual-evidence'])
        result['common_status'] = 'failed'
        return result

    monkeypatch.setattr(module, 'evaluate_common_admission', evaluate)
    report = build_structured_admission(scope, summary, db)
    assert report['summary']['failed'] == 4
    for row in report['items']:
        check = next(c for c in row['checks'] if c['code'] == 'ttm_parent_profit')
        assert check['outcome'] == 'pending_verification'
        assert row['manual_common']['common_status'] == 'failed'
        assert row['admission_status'] == 'excluded'


def test_historical_manual_failure_not_expired_by_wall_clock(tmp_path):
    from datetime import UTC, datetime

    scope, summary, db = saved(tmp_path)
    store = StockResearchStore(db, clock=lambda: datetime(2026, 7, 20, tzinfo=UTC))
    store.import_companies([{'symbol': '000100', 'name': '000100'}], source_sha256='a'*64,
                           source_label='合成样本', data_as_of=date(2026, 7, 20))
    company = store.get_company('000100')
    assert company is not None
    added = store.add_evidence('000100', kind='admission_governance', payload={'verdict': 'failed', 'basis': '合成治理失败'},
                              source_url='https://example.com/fact', disclosed_on=date(2026, 7, 19), content_sha256='b'*64,
                              expected_revision=company['revision'])
    store.save_review('000100', added['evidence_id'], expected_revision=added['revision'], decision='confirmed',
                      reviewer='合成审核人', reason='合成测试')
    later = StockResearchStore(db, read_only=True, clock=lambda: datetime(2027, 1, 1, tzinfo=UTC))
    assert next(r for r in later.list_evidence('000100') if r['kind'] == 'admission_governance')['review_status'] == 'expired'
    report = build_structured_admission(scope, summary, db)
    row = next(r for r in report['items'] if r['symbol'] == '000100')
    assert row['manual_common']['common_status'] == 'failed'
    assert row['common_status'] == 'failed'


@pytest.mark.parametrize('filename', ['summary.json', 'manifest.json'])
def test_replacement_during_screen_rejected(tmp_path, monkeypatch, filename):
    import stock_robot.stock_structured_admission as module

    scope, summary, db = saved(tmp_path)
    original = module.screen_saved_batch

    def replace(path):
        result = original(path)
        target = summary.parent/filename
        target.write_bytes(target.read_bytes()+b' ')
        return result

    monkeypatch.setattr(module, 'screen_saved_batch', replace)
    with pytest.raises(ValueError, match='快照'):
        build_structured_admission(scope, summary, db)
