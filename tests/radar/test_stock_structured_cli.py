"""结构化采集入口绑定清单、隔离错误，不隐式审核或公告回退。"""

import json
from datetime import date

import pytest
from click.testing import CliRunner

from stock_robot.stock_structured_financial import (
    run_structured_batch,
    structured_collect_command,
)


def test_batch_stops_source_after_429_and_persists_prior_results(tmp_path):
    calls = []

    def collector(symbol, _cache_dir, **_kwargs):
        calls.append(symbol)
        return {'symbol': symbol, 'as_of': '2026-10-17', 'status': 'partial',
                'records': {}, 'sources': {}, 'source_stopped': True, 'errors': ['HTTP 429']}

    companies = [{'symbol': symbol, 'name': symbol} for symbol in ['000100', '300750']]
    result = run_structured_batch(companies, tmp_path, date(2026, 10, 17), collector=collector)
    assert calls == ['000100']
    assert result['source_stopped'] is True
    assert result['summary']['not_attempted'] == 1
    assert result['members_published'] == 0
    assert (tmp_path / 'summary.json').is_file()
    assert (tmp_path / result['items'][0]['artifact']['path']).is_file()


def test_manifest_cannot_be_reused_with_other_roster_or_cutoff(tmp_path):
    companies = [{'symbol': '000100', 'name': 'TCL科技'}]

    def collector(symbol, _cache_dir, **_kwargs):
        return {'symbol': symbol, 'as_of': '2026-10-17', 'status': 'partial', 'records': {}, 'sources': {}}

    run_structured_batch(companies, tmp_path, date(2026, 10, 17), collector=collector)
    with pytest.raises(ValueError, match='绑定'):
        run_structured_batch(companies, tmp_path, date(2026, 10, 18), collector=collector)


def test_company_failures_are_data_gaps_and_other_companies_continue(tmp_path):
    def collector(symbol, _cache_dir, **_kwargs):
        if symbol == '000100':
            raise ValueError('财务表格式变动')
        return {'symbol': symbol, 'as_of': '2026-10-17', 'status': 'partial', 'records': {}, 'sources': {}}

    companies = [{'symbol': symbol, 'name': symbol} for symbol in ['000100', '300750']]
    result = run_structured_batch(companies, tmp_path, date(2026, 10, 17), collector=collector)
    assert result['summary']['attempted'] == 2
    assert result['summary']['data_complete'] == 0
    assert result['summary']['data_incomplete'] == 2
    assert result['items'][0]['status'] == 'source_error'
    assert '淘汰' not in result['items'][0]['error']


def test_cli_reads_explicit_roster_without_database_option(tmp_path, monkeypatch):
    source = tmp_path / 'companies.json'
    source.write_text(json.dumps({'companies': [{'symbol': '000100', 'name': 'TCL科技'}]}), encoding='utf-8')
    seen = {}

    def run(companies, workspace, as_of, **options):
        seen.update(companies=companies, workspace=workspace, as_of=as_of, options=options)
        return {'summary': {'attempted': 1, 'data_complete': 0, 'not_attempted': 0}}

    monkeypatch.setattr('stock_robot.stock_structured_financial.run_structured_batch', run)
    result = CliRunner().invoke(structured_collect_command, ['--input', str(source), '--workspace', str(tmp_path/'output'), '--as-of', '2026-10-17'])
    assert result.exit_code == 0, result.output
    assert seen['companies'] == [{'symbol': '000100', 'name': 'TCL科技'}]
    assert seen['as_of'] == date(2026, 10, 17)
    assert '不代表入池' in result.output
    assert '--db' not in CliRunner().invoke(structured_collect_command, ['--help']).output


def test_cli_rejects_input_within_output_before_calling_collector(tmp_path):
    source = tmp_path / 'companies.json'
    source.write_text('[]', encoding='utf-8')
    result = CliRunner().invoke(structured_collect_command, ['--input', str(source), '--workspace', str(tmp_path), '--as-of', '2026-10-17'])
    assert result.exit_code != 0
    assert '输入' in result.output


def test_cli_rejects_conflicting_company_names(tmp_path):
    companies = [{'symbol': '000100', 'name': '甲'}, {'symbol': '000100', 'name': '乙'}]
    with pytest.raises(ValueError, match='冲突'):
        run_structured_batch(companies, tmp_path, date(2026, 10, 17))


def test_cross_company_response_cannot_become_usable_artifact(tmp_path):
    def collector(_symbol, _cache_dir, **_kwargs):
        return {'symbol': '600900', 'as_of': '2026-10-17', 'status': 'complete', 'records': {}, 'sources': {}}

    result = run_structured_batch([{'symbol': '000100', 'name': 'TCL科技'}], tmp_path, date(2026, 10, 17), collector=collector)
    assert result['items'][0]['status'] == 'source_error'
    assert result['summary']['data_complete'] == 0


def test_registered_in_real_cli():
    from stock_robot.cli import main

    result = CliRunner().invoke(main, ['radar', 'stock-financial-collect', '--help'])
    assert result.exit_code == 0
    assert '--workspace' in result.output


def test_workspace_junction_is_rejected_before_output_write(tmp_path, monkeypatch):
    monkeypatch.setattr(type(tmp_path), 'is_junction', lambda path: path == tmp_path, raising=False)
    with pytest.raises(ValueError, match='联接'):
        run_structured_batch([{'symbol': '000100', 'name': 'TCL科技'}], tmp_path, date(2026, 10, 17))
    assert not (tmp_path / 'manifest.json').exists()
