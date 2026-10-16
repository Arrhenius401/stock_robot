"""指数初筛严格解析、完整性与来源保留回归。"""
import io
from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook

from radar.stocks.index_scope import (
    INDEX_SOURCES,
    build_index_scope,
    parse_index_constituents,
)

AS_OF = date(2026, 10, 16)
CSI_HEADER = ['日期Date', '指数代码 Index Code', '指数名称 Index Name',
              '成份券代码Constituent Code', '成份券名称Constituent Name', '交易所Exchange']
CNI_HEADER = ['日期', '样本代码', '样本简称', '所属行业', '总市值(亿元)', '权重（%）']


def _book(rows, header=None):
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(header or CSI_HEADER)
    for row in rows:
        sheet.append(row)
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def _csi(code='000510', symbols=('000001',), **kwargs):
    return parse_index_constituents(_book([
        ['20261016', code, INDEX_SOURCES[code]['name'], symbol, '样本公司',
         '上海证券交易所' if symbol.startswith(('6',)) else '深圳证券交易所']
        for symbol in symbols]), code, AS_OF, **kwargs)


@pytest.mark.parametrize('code', ['000510', '000922', 'H30269', '000688'])
def test_full_synthetic_production_count(code):
    symbols = [f'600{number:03}' for number in range(INDEX_SOURCES[code]['count'])]
    result = _csi(code, symbols)
    assert result['complete']
    assert result['row_count'] == INDEX_SOURCES[code]['count']
    assert result['effective_date'] is None


def test_cni_full_synthetic_retains_older_snapshot():
    raw = _book([['2026-09-30', f'300{number:03}', '创业公司', '信息技术', '10', '0.1']
                 for number in range(100)], CNI_HEADER)
    result = parse_index_constituents(raw, '399006', AS_OF)
    assert result['complete']
    assert result['snapshot_date'] == '2026-09-30'
    assert result['rows'][0]['effective_date'] is None


@pytest.mark.parametrize(('column', 'value'), [
    (0, '20261017'), (0, '错误日期'), (1, '000922'), (2, '错误指数'),
    (3, '900001'), (3, '689009'), (3, '920001'), (4, ' '), (5, '上海证券交易所'),
])
def test_invalid_identity_never_complete(column, value):
    row = ['20261016', '000510', '中证A500', '000001', '平安银行', '深圳证券交易所']
    row[column] = value
    result = parse_index_constituents(_book([row]), '000510', AS_OF, expected_count=1)
    assert not result['complete']
    assert result['rows'] == []


def test_mixed_dates_and_duplicates_not_complete():
    rows = [['20261016', '000510', '中证A500', '000001', '平安银行', '深圳证券交易所'],
            ['20260930', '000510', '中证A500', '000001', '平安银行', '深圳证券交易所']]
    result = parse_index_constituents(_book(rows), '000510', AS_OF, expected_count=2)
    assert not result['complete']
    assert result['rows'] == []
    assert result['snapshot_date'] is None


def test_numeric_code_restores_zero_and_keeps_raw_value():
    row = ['20261016', 510, '中证A500', 1, '平安银行', '深圳证券交易所']
    result = parse_index_constituents(_book([row]), '000510', AS_OF, expected_count=1)
    assert result['complete']
    assert result['rows'][0]['symbol'] == '000001'
    assert result['rows'][0]['source_fields']['成份券代码Constituent Code'] == 1


@pytest.mark.parametrize(('symbol', 'exchange'), [('689009', '上海证券交易所'), ('920599', '北京证券交易所')])
def test_verified_out_of_scope_rows_are_auditable(symbol, exchange):
    row = ['20261016', '000510', '中证A500', symbol, '范围排除公司', exchange]
    result = parse_index_constituents(_book([row]), '000510', AS_OF, expected_count=1)
    assert result['complete']
    assert result['rows'] == []
    assert result['scope_exclusions'][0]['symbol'] == symbol
    scope = build_index_scope([result], AS_OF)
    assert scope['companies'] == []
    assert len(scope['scope_exclusions']) == 1
    assert len(scope['pools']['dividend_value']['scope_exclusions']) == 1


def test_truncated_actual_cni_keeps_original_snapshot():
    path = Path(__file__).parents[1] / 'resources/radar/cni-399006-constituents-20260930.xlsx'
    result = parse_index_constituents(path.read_bytes(), '399006', AS_OF)
    assert result['row_count'] == 3
    assert not result['complete']
    assert result['snapshot_date'] == '2026-09-30'
    assert result['rows'][0]['symbol'] == '300661'
    assert result['rows'][0]['source_fields']['样本简称'] == '圣邦股份'
    full_slice = parse_index_constituents(path.read_bytes(), '399006', AS_OF, expected_count=3)
    assert full_slice['complete']
    assert full_slice['rows'][2]['symbol'] == '302132'


@pytest.mark.parametrize(('code', 'excluded_symbol'), [('000922', '920599'), ('000688', '689009')])
def test_actual_csi_out_of_scope_rows(code, excluded_symbol):
    path = Path(__file__).parents[1] / f'resources/radar/csi-{code}-constituents-20261016.xlsx'
    partial = parse_index_constituents(path.read_bytes(), code, AS_OF)
    assert not partial['complete']
    assert partial['row_count'] == 2
    result = parse_index_constituents(path.read_bytes(), code, AS_OF, expected_count=2)
    assert result['complete']
    assert len(result['rows']) == 1
    assert result['scope_exclusions'][0]['symbol'] == excluded_symbol
    assert result['scope_exclusions'][0]['source_fields']['成份券代码Constituent Code'] == excluded_symbol


def test_union_preserves_all_sources_and_remains_research():
    items = [_csi(code, expected_count=1) for code in ('000510', '000922', 'H30269', '000688')]
    cni = parse_index_constituents(_book([['2026-09-30', '300001', '公司', '制造', '1', '1']], CNI_HEADER),
                                   '399006', AS_OF, expected_count=1)
    items.append(cni)
    result = build_index_scope(items, AS_OF)
    assert result['scope_complete']
    company = result['companies'][0]
    assert company['symbol'] == '000001'
    assert set(company['sources']) == {'000510', '000922', 'H30269', '000688'}
    assert set(company['pool_ids']) == {'dividend_value', 'growth_trend'}
    assert len(company['research_hints']) == 4
    assert company['admission_status'] == 'pending_verification'
    assert result['members_published'] == 0
    assert result['financial_verified'] is False
    assert result['index_removal_triggers_exit'] is False


def test_missing_source_partial_and_no_market_fallback():
    result = build_index_scope([_csi(expected_count=1)], AS_OF)
    assert result['status'] == 'partial'
    assert len(result['companies']) == 1
    assert all(pool['status'] == 'partial' for pool in result['pools'].values())
    assert build_index_scope([], AS_OF)['companies'] == []


def test_duplicate_sources_and_tampered_count_cannot_complete():
    item = _csi(expected_count=1)
    assert build_index_scope([item, item], AS_OF)['companies'] == []
    tampered = deepcopy(item)
    tampered['rows'] *= 2
    assert build_index_scope([tampered], AS_OF)['companies'] == []


@pytest.mark.parametrize(('field', 'value'), [('symbol', '920001'), ('snapshot_date', '2026-10-17'),
                                           ('name', ''), ('exchange', 'SSE')])
def test_tampered_rows_cannot_complete(field, value):
    item = _csi(expected_count=1)
    item['rows'][0][field] = value
    assert build_index_scope([item], AS_OF)['companies'] == []


def test_unknown_symbol_with_null_exchange_cannot_enter_scope():
    item = _csi(expected_count=1)
    item['rows'][0].update(symbol='900001', exchange=None)
    assert build_index_scope([item], AS_OF)['companies'] == []


def test_formula_and_resource_budget_rejected():
    with pytest.raises(ValueError, match='公式'):
        parse_index_constituents(_book([['20261016', '000510', '中证A500', '=1', '公司', '深圳证券交易所']]),
                                 '000510', AS_OF)
    with pytest.raises(ValueError, match='4MiB'):
        parse_index_constituents(b'x' * (4*1024*1024+1), '000510', AS_OF)
    with pytest.raises(ValueError, match='工作簿'):
        parse_index_constituents(b'<html>error</html>', '000510', AS_OF)


def test_missing_header_rejected():
    result = parse_index_constituents(_book([['value']], ['错误列']), '000510', AS_OF)
    assert not result['complete']
    assert result['diagnostics']
