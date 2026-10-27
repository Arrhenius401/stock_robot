"""黄金来源的真实裁切样本与语义校验回归。"""
from datetime import date
from pathlib import Path
from typing import cast

import pytest

from gold.models import SourceKey
from gold.sources import (
    SourceParseError,
    parse_h10,
    parse_safe,
    parse_sge,
    parse_sina,
    parse_spdr,
    parse_treasury,
)

RESOURCES = Path(__file__).parents[1] / 'resources' / 'gold'


def test_sge_sorts_and_validates_ohlc():
    bars = parse_sge(b'{"time":[["2026-10-23",100,102,99,103],["2026-10-22",98,100,97,101]]}')
    assert bars[0].trade_date == date(2026, 10, 22)
    assert bars[-1].high == 103
    with pytest.raises(SourceParseError, match='重复'):
        parse_sge(b'{"time":[["2026-10-23",100,102,99,103],["2026-10-23",100,103,99,104]]}')


def test_sina_jsonp_and_invalid_future():
    bars = parse_sina(b'var _XAU=([{"date":"2026-10-23","open":"100","high":"103","low":"99","close":"102"}]);')
    assert bars[0].close == 102
    with pytest.raises(SourceParseError):
        parse_sina(b'var _XAU=([{"date":"2026-10-23","open":"nan","high":"103","low":"99","close":"102"}]);')


def test_treasury_negative_yield_is_valid():
    xml = b'<feed xmlns:d="urn:data"><entry><d:NEW_DATE>2020-01-02T00:00:00</d:NEW_DATE><d:TC_10YEAR>-0.05</d:TC_10YEAR></entry></feed>'
    assert parse_treasury(xml)[0].value == -0.05


def test_h10_does_not_treat_missing_as_zero():
    html = b'<html><pre>2026-10-22 6.9\n2026-10-23 ND</pre></html>'
    assert len(parse_h10(html)) == 1


def test_safe_quantity_not_dollar_valuation():
    html = '<table><tr><td>2026年</td><td>1月</td><td>2月</td></tr><tr><td>黄金（万盎司）</td><td>7300</td><td>7310</td></tr><tr><td>黄金（亿美元）</td><td>6000</td><td>6100</td></tr></table>'
    points = parse_safe(html.encode(), 2026)
    assert [point.value for point in points] == [7300,7310]
    assert points[-1].observation_date == date(2026,2,28)


def test_real_samples():
    assert parse_sge((RESOURCES/'sge-au9999-history.json').read_bytes())
    assert parse_sina((RESOURCES/'sina-xau-history.js').read_bytes())
    assert parse_treasury((RESOURCES/'treasury-us10y-real.xml').read_bytes())
    assert parse_h10((RESOURCES/'fed-broad-dollar.html').read_bytes())
    assert parse_h10((RESOURCES/'fed-cny-usd.html').read_bytes())
    assert parse_safe((RESOURCES/'safe-china-reserves.html').read_bytes(),2026)


def test_spdr_real_sample_main_and_optional_agree():
    content = (RESOURCES/'spdr-gld-holdings.xlsx').read_bytes()
    primary, name, _ = parse_spdr(content)
    backup, other, _ = parse_spdr(content, reader='openpyxl')
    assert primary == backup
    assert name == 'stdlib' and other == 'openpyxl'


def test_spdr_semantic_error_cannot_use_backup(monkeypatch):
    from gold import sources
    monkeypatch.setattr(sources, '_xlsx_rows', lambda _: [[['Date','Tonnes of Gold (oz)'],['2026-10-23',100]]])
    monkeypatch.setattr(sources, '_openpyxl_rows', lambda _: pytest.fail('语义失败不得绕过'))
    with pytest.raises(SourceParseError):
        parse_spdr(b'bad')


def test_spdr_optional_unavailable(monkeypatch):
    from gold import sources
    monkeypatch.setattr(sources, '_xlsx_rows', lambda _: (_ for _ in ()).throw(sources.WorkbookReadError('格式')))
    monkeypatch.setattr(sources, '_openpyxl_rows', lambda _: (_ for _ in ()).throw(sources.WorkbookReadError('可选后备读取器不可用')))
    with pytest.raises(SourceParseError,match='可选后备'):
        parse_spdr(b'bad')


def test_fetch_source_real_sample_adapters(monkeypatch, tmp_path):
    import re
    from datetime import UTC, datetime

    from gold import sources
    now = datetime(2026, 10, 26, 12, tzinfo=UTC)
    safe = (RESOURCES/'safe-china-reserves.html').read_bytes()
    payloads = {
        'domestic': (RESOURCES/'sge-au9999-history.json').read_bytes(),
        'international': (RESOURCES/'sina-xau-history.js').read_bytes(),
        'dollar': (RESOURCES/'fed-broad-dollar.html').read_bytes(),
        'fx': (RESOURCES/'fed-cny-usd.html').read_bytes(),
        'holdings': (RESOURCES/'spdr-gld-holdings.xlsx').read_bytes(),
    }
    def fetch(url, **kwargs):
        if 'Dailyhq' in url:
            return payloads['domestic']
        if 'getGlobalFuturesDailyKLine' in url:
            return payloads['international']
        if 'treasury.gov' in url:
            match = re.search(r'field_tdr_date_value=(\d+)',url)
            assert match is not None
            year = match.group(1)
            return (RESOURCES/'treasury-us10y-real.xml').read_bytes().replace(b'2026-',f'{year}-'.encode())
        if 'jrxwtfb' in url:
            return payloads['dollar']
        if 'dat00_ch' in url:
            return payloads['fx']
        if url.endswith('/gfcbzc/'):
            return b'<a href="/safe/2026/current.html">'+ '官方储备资产（2026）'.encode()+b'</a><a href="/safe/2025/prior.html">'+'官方储备资产（2025）'.encode()+b'</a>'
        if '/safe/2025/' in url:
            return safe.replace(b'2026.',b'2025.')
        if '/safe/2026/' in url:
            return safe
        if 'api.spdrgoldshares.com' in url:
            return payloads['holdings']
        return b'<a href="https://api.spdrgoldshares.com/api/v1/historical-archive?product=gld&amp;exchange=NYSE&amp;lang=en">archive</a>'
    monkeypatch.setattr(sources,'_fetch',fetch)
    for key in payloads.keys() | {'real_yield','china_reserves'}:
        series = sources.fetch_source(cast(SourceKey,key),now.isoformat(),str(tmp_path))
        assert series['status'] == 'ok'
        assert series['bars'] or series['observations']
        assert series['parser']
    assert sources.fetch_source('dollar',now.isoformat())['published_at'].startswith('2026-10-19')
    assert len(list(tmp_path.glob('*.xml'))) == 6


def test_spdr_read_failure_falls_back_same_bytes(monkeypatch):
    from gold import sources
    content = (RESOURCES/'spdr-gld-holdings.xlsx').read_bytes()
    observed = []
    backup = sources._openpyxl_rows
    monkeypatch.setattr(sources,'_xlsx_rows',lambda _: (_ for _ in ()).throw(sources.WorkbookReadError('主读取器失败')))
    def read_same_bytes(value):
        observed.append(value)
        return backup(value)
    monkeypatch.setattr(sources,'_openpyxl_rows',read_same_bytes)
    points, parser, notes = sources.parse_spdr(content)
    assert points and parser == 'openpyxl' and notes
    assert observed == [content]


def test_safe_duplicate_quantity_conflict_is_rejected():
    html = '<table><tr><td>2026.01</td></tr><tr><td>7419万盎司</td><td>7420万盎司</td></tr></table>'
    with pytest.raises(SourceParseError,match='冲突'):
        parse_safe(html.encode(),2026)


def test_spdr_multiple_candidates_rejected(monkeypatch):
    from gold import sources
    sheet = [['Date','Tonnes of Gold'],['2026-10-23',100]]
    monkeypatch.setattr(sources,'_xlsx_rows',lambda _: [sheet,sheet])
    with pytest.raises(SourceParseError,match='多个'):
        parse_spdr(b'valid-container')


def test_daily_technical_input_excludes_potential_intraday_values(monkeypatch):
    from gold import sources
    monkeypatch.setattr(sources, '_fetch', lambda *_, **__: b'var XAU=([{\"date\":\"2026-10-23\",\"open\":100,\"high\":103,\"low\":99,\"close\":102},{\"date\":\"2026-10-26\",\"open\":102,\"high\":105,\"low\":100,\"close\":104}]);')
    result = sources.fetch_source('international', '2026-10-26T20:00:00+08:00')
    assert result['bars'][-1]['trade_date'] == '2026-10-23'
    assert any('闭合时刻' in note for note in result['notes'])
    midnight = sources.fetch_source('international', '2026-10-27T00:01:00+08:00')
    assert midnight['bars'][-1]['trade_date'] == '2026-10-23'
    monkeypatch.setattr(sources, '_fetch', lambda *_, **__: b'{"time":[["2026-10-23",100,102,99,103],["2026-10-26",102,104,100,105]]}')
    before = sources.fetch_source('domestic', '2026-10-26T14:59:00+08:00')
    after = sources.fetch_source('domestic', '2026-10-26T15:30:00+08:00')
    assert before['bars'][-1]['trade_date'] == '2026-10-23'
    assert after['bars'][-1]['trade_date'] == '2026-10-26'


@pytest.mark.parametrize('repeated', [False, True])
def test_safe_missing_middle_month_is_never_shifted(repeated):
    header = '<tr><td>项目</td>' + ''.join(f'<td>2026.{month:02d}</td>' for month in range(1,10)) + '</tr>'
    values = []
    for month in range(1,10):
        value = '' if month == 3 else str(100 + month) + ('万盎司' if repeated else '')
        values.extend([value, value] if repeated else [value])
    label = '' if repeated else '黄金（万盎司）'
    html = '<table>'+header+'<tr><td>'+label+'</td>'+''.join(f'<td>{value}</td>' for value in values)+'</tr></table>'
    points = parse_safe(html.encode(),2026)
    assert [point.observation_date.month for point in points] == [1,2,4,5,6,7,8,9]
    assert points[-1].value == 109


def test_new_treasury_year_empty_feed_keeps_prior_recent_values(monkeypatch):
    from gold import sources
    def fetch(url, **kwargs):
        year = int(url.rsplit('=',1)[-1])
        return b'<feed />' if year == 2026 else (f'<feed><entry><NEW_DATE>{year}-12-31T00:00:00</NEW_DATE><TC_10YEAR>2.0</TC_10YEAR></entry></feed>').encode()
    monkeypatch.setattr(sources, '_fetch', fetch)
    result = sources.fetch_source('real_yield', '2026-01-01T10:00:00+08:00')
    assert result['observations'][-1]['observation_date'] == '2025-12-31'
    assert any('当前年度尚无' in note for note in result['notes'])
