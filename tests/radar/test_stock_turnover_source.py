"""候选成交额来源必须绑定原文、日期及完整市场窗口。"""
import base64
import hashlib
from datetime import UTC, datetime

import pytest

from tests.radar.test_stock_market_history import BODY, WINDOW, decoded


def payload():
    return {'symbol': '600900', 'as_of': '2026-10-15', 'window': [day.isoformat() for day in WINDOW],
            'fetched_at': datetime(2026, 10, 15, tzinfo=UTC).isoformat(),
            'url': 'https://finance.sina.com.cn/realstock/company/sh600900/hisdata_klc2/klc_kl.js',
            'sha256': hashlib.sha256(BODY).hexdigest(), 'raw': base64.b64encode(BODY).decode(),
            'calendar': {'snapshot_sha256': 'a' * 64}}


def test_recomputes_complete_turnover_from_raw(monkeypatch):
    from radar.stocks import market_history
    from radar.stocks.turnover_source import verify_turnover_payload

    monkeypatch.setattr(market_history, 'decode_sina_body', lambda *args: decoded())
    result = verify_turnover_payload(payload(), '600900', '2026-10-15')
    assert len(result['daily']) == 60
    assert result['scope'] == 'a_share_raw_turnover'


@pytest.mark.parametrize('mutation', ['hash', 'symbol', 'date', 'missing'])
def test_incomplete_or_unbound_turnover_never_passes(monkeypatch, mutation):
    from radar.stocks import market_history
    from radar.stocks.turnover_source import verify_turnover_payload

    data = payload()
    if mutation == 'hash': data['sha256'] = 'b' * 64
    elif mutation == 'symbol': data['symbol'] = '600036'
    elif mutation == 'date': data['as_of'] = '2026-10-14'
    monkeypatch.setattr(market_history, 'decode_sina_body', lambda *args: decoded()[:-1] if mutation == 'missing' else decoded())
    with pytest.raises(ValueError):
        verify_turnover_payload(data, '600900', '2026-10-15')


def test_task_source_archival_and_newer_failure(tmp_path, monkeypatch):
    from datetime import date, timedelta

    from radar.stocks import market_history
    from radar.stocks.turnover_source import VERSION, load_turnover_source
    from radar.stocks.update_runner import StockUpdateRunner
    from tests.radar.test_stock_financial_source import environment

    monkeypatch.setattr(market_history, 'decode_sina_body', lambda *args: decoded())
    from radar.stocks import update_service
    monkeypatch.setattr(update_service, 'load_market_window', lambda *args: (WINDOW, {'calendar': payload()['calendar']}))
    research, archive, tasks, clock = environment(tmp_path)
    cutoff = date(2026, 10, 15)
    snapshot = {'kind': 'turnover_review', 'pool_ids': ['dividend_value'], 'scope_complete': True,
                'scope_as_of': cutoff.isoformat(), 'scope_sha256': 'b'*64, 'source_run_id': 'scope',
                'companies': [{'symbol': '600900', 'name': '合成公司'}]}
    first = tasks.enqueue(kind='turnover_review', pool_ids=['dividend_value'], target_date=cutoff,
                          rule_version=VERSION, snapshot=snapshot)
    data = payload()
    def collect(symbol, as_of, cache, window, calendar):
        return data
    runner = StockUpdateRunner(tasks, tmp_path/'cache', tmp_path/'stock-updates/artifacts', turnover_collector=collect,
                               window_loader=lambda _: (None, None))
    finished = runner.execute_one('test')
    assert finished is not None and finished['status'] == 'completed'
    source = load_turnover_source(research.db_path, '600900', cutoff)
    assert source['status'] == 'current' and source['run_id'] == first['run_id']
    from stock_robot.stock_admission import evaluate_store
    inputs = archive.capture_inputs(symbol='600900')
    inputs['companies'][0].update(turnover_source=source, turnover_as_of=cutoff.isoformat())
    report = evaluate_store(research, cutoff, symbol='600900', market_window=WINDOW, include_styles=True,
                            turnover_sources={'600900': source})
    report['market_window_source'] = {'calendar': data['calendar']}
    saved = archive.save_evaluation(report, inputs, market_window=WINDOW)
    assert saved is not None
    monkeypatch.setattr(update_service, 'load_market_window', lambda *args: (WINDOW, {'calendar': {'snapshot_sha256': 'f'*64}}))
    assert load_turnover_source(research.db_path, '600900', cutoff)['status'] == 'invalid'
    reloaded = archive.get(saved['evaluation_id'])
    assert reloaded is not None and reloaded['freshness']['current'] is False
    clock[0] += timedelta(seconds=1)
    snapshot['pool_ids'] = ['growth_trend']
    newer = tasks.enqueue(kind='turnover_review', pool_ids=['growth_trend'], target_date=cutoff,
                          rule_version=VERSION, snapshot=snapshot)
    selected = load_turnover_source(research.db_path, '600900', cutoff)
    assert selected['status'] == 'not_attempted' and selected['run_id'] == newer['run_id']


def test_turnover_source_controls_style_without_changing_common_eligibility():
    from radar.stocks.admission import evaluate_common_admission
    from radar.stocks.style import evaluate_stock_styles
    from tests.radar.test_stock_style import AS_OF, check, facts
    from tests.radar.test_stock_style import WINDOW as STYLE_WINDOW

    rows = [row for row in facts() if row['kind'] != 'style_liquidity']
    common = evaluate_common_admission('600900', rows, AS_OF, market_window=STYLE_WINDOW)
    common['turnover_source'] = {'symbol': '600900', 'status': 'current', 'as_of': AS_OF.isoformat(),
        'evaluation_as_of': AS_OF.isoformat(), 'window_days': [day.isoformat() for day in STYLE_WINDOW],
        'payload': {'currency': 'CNY', 'scope': 'a_share_raw_turnover',
                    'daily': [{'date': day.isoformat(), 'amount_yuan': '99999999'} for day in STYLE_WINDOW]}}
    result = evaluate_stock_styles('600900', rows, AS_OF, market_window=STYLE_WINDOW, common_result=common)
    assert result['common_status'] == common['common_status']
    assert check(result['pools']['dividend_value'], 'liquidity')['outcome'] == 'warning'
    assert check(result['pools']['growth_trend'], 'liquidity')['outcome'] == 'observe'
    assert check(result['pools']['growth_trend'], 'liquidity')['channel'] == 'structured_turnover'


def test_rate_limit_stops_batch_and_task_can_resume(tmp_path):
    from datetime import date

    from radar.stocks.turnover_source import VERSION
    from radar.stocks.update_runner import StockUpdateRunner
    from tests.radar.test_stock_financial_source import environment

    _, _, tasks, _ = environment(tmp_path)
    cutoff = date(2026, 10, 15)
    snapshot = {'kind': 'turnover_review', 'pool_ids': ['dividend_value'], 'scope_complete': True,
                'scope_as_of': cutoff.isoformat(), 'scope_sha256': 'b'*64, 'source_run_id': 'scope',
                'companies': [{'symbol': '600900', 'name': '甲'}, {'symbol': '600901', 'name': '乙'}]}
    task = tasks.enqueue(kind='turnover_review', pool_ids=['dividend_value'], target_date=cutoff,
                         rule_version=VERSION, snapshot=snapshot)
    calls = []
    def limited(symbol, as_of, cache, window, calendar):
        calls.append(symbol)
        raise ValueError('来源429停止后续网络请求')
    runner = StockUpdateRunner(tasks, tmp_path/'cache', tmp_path/'stock-updates/artifacts', turnover_collector=limited,
                               window_loader=lambda _: (WINDOW, {'calendar': payload()['calendar']}))
    partial = runner.execute_one('test')
    assert partial is not None and partial['status'] == 'partial' and calls == ['600900']
    resumed = tasks.resume_turnover(task['run_id'], 'dividend_value')
    assert resumed['status'] == 'queued' and resumed['input_snapshot'] == task['input_snapshot']


def test_first_round_and_completion_share_eastmoney_source(tmp_path, monkeypatch):
    import json
    import types
    from datetime import date, datetime

    from radar.stocks import eastmoney_history, update_service
    from radar.stocks.completion_providers import turnover_source_provider
    from radar.stocks.turnover_source import VERSION, load_turnover_source
    from radar.stocks.update_runner import StockUpdateRunner
    from tests.radar.test_stock_financial_source import environment

    calendar = {'snapshot_sha256': 'a' * 64}
    lines = [f'{day.isoformat()},1,1,1,1,1,123456789' for day in WINDOW]
    body = json.dumps({'data': {'klines': lines}}).encode()
    monkeypatch.setattr(eastmoney_history, 'bounded_provider_call', lambda *args, **kwargs: body)
    monkeypatch.setattr(update_service, 'load_market_window', lambda *args: (WINDOW, {'calendar': calendar}))
    research, _, tasks, _ = environment(tmp_path)
    cutoff = date(2026, 10, 20)
    monkeypatch.setattr(eastmoney_history, 'datetime', types.SimpleNamespace(
        now=lambda tz=None: datetime(2026, 10, 20, 8, tzinfo=tz)))
    snapshot = {'kind': 'turnover_review', 'pool_ids': ['dividend_value'], 'scope_complete': True,
                'scope_as_of': cutoff.isoformat(), 'scope_sha256': 'b' * 64, 'source_run_id': 'scope',
                'companies': [{'symbol': '600900', 'name': '合成公司'}]}
    tasks.enqueue(kind='turnover_review', pool_ids=['dividend_value'], target_date=cutoff,
                  rule_version=VERSION, snapshot=snapshot)
    runner = StockUpdateRunner(tasks, tmp_path / 'cache', tmp_path / 'stock-updates/artifacts',
                               window_loader=lambda _: (WINDOW, {'calendar': calendar}))
    finished = runner.execute_one('test')
    assert finished is not None and finished['status'] == 'completed'
    source = load_turnover_source(research.db_path, '600900', cutoff)
    assert source['status'] == 'current'
    assert source['payload']['daily'][0]['amount_yuan'] == '123456789'
    payload = turnover_source_provider(research.db_path)('600900', cutoff)
    assert payload is not None and payload['provider'] == 'eastmoney' and len(payload['daily']) == 60


def test_collect_turnover_falls_back_when_eastmoney_fails(tmp_path, monkeypatch):
    import types
    from datetime import date

    from radar.stocks import eastmoney_history, market_history, turnover_source
    from tests.radar.test_stock_market_history import BODY, WINDOW, decoded

    def blocked(*args, **kwargs):
        raise RuntimeError('proxy blocked')

    monkeypatch.setattr(eastmoney_history, 'bounded_provider_call', blocked)
    cache = tmp_path / 'cache'
    folder = cache / 'market_history'
    folder.mkdir(parents=True)
    sha = hashlib.sha256(BODY).hexdigest()
    (folder / (sha + '.js')).write_bytes(BODY)
    item = {'symbol': '600900', 'status': 'downloaded', 'window_complete': True,
            'content_sha256': sha, 'raw_artifact': sha + '.js',
            'url': 'https://finance.sina.com.cn/realstock/company/sh600900/hisdata_klc2/klc_kl.js',
            'fetched_at': datetime(2026, 10, 15, tzinfo=UTC).isoformat()}
    monkeypatch.setattr(market_history, 'collect_stock_history_result',
                        lambda *args, **kwargs: types.SimpleNamespace(value={'items': [item]}))
    monkeypatch.setattr(market_history, 'decode_sina_body', lambda *args: decoded())
    payload = turnover_source.collect_turnover_payload(
        '600900', date(2026, 10, 15), cache, WINDOW, {'snapshot_sha256': 'a' * 64})
    assert payload['provider'] == 'sina'
    assert turnover_source.verify_turnover_payload(payload, '600900', '2026-10-15')['daily']
