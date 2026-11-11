"""东财日线成交额：60 日窗口、停牌 0 成交、原始哈希校验。"""
import base64
import hashlib
import json
from datetime import date, timedelta

from radar.stocks.eastmoney_history import (
    eastmoney_url,
    verify_eastmoney_turnover_payload,
)
from radar.stocks.turnover_source import verify_turnover_payload

DAY = date(2026, 11, 7)


def window():
    start = DAY - timedelta(days=89)
    return [start + timedelta(days=index) for index in range(60)]


def payload():
    days = window()
    traded = days[:55]
    klines = [f'{day.isoformat()},1,1,1,1,100,120000000' for day in traded]
    raw = json.dumps({'data': {'klines': klines}}).encode()
    return {'symbol': '002155', 'as_of': DAY.isoformat(), 'provider': 'eastmoney',
            'url': eastmoney_url('002155'), 'window': [day.isoformat() for day in days],
            'calendar': {'snapshot_sha256': 'a' * 64}, 'fetched_at': '2026-11-11T00:00:00+00:00',
            'sha256': hashlib.sha256(raw).hexdigest(), 'raw': base64.b64encode(raw).decode(),
            'daily': [], 'backfill': True}


def test_eastmoney_payload_verifies_and_fills_suspension_zero():
    result = verify_eastmoney_turnover_payload(payload(), '002155', DAY)
    assert len(result['daily']) == 60
    assert sum(1 for row in result['daily'] if row['amount_yuan'] == '0') == 5
    assert sum(float(row['amount_yuan']) for row in result['daily']) == 55 * 120000000


def test_eastmoney_payload_goes_through_turnover_source_branch():
    result = verify_turnover_payload(payload(), '002155', DAY.isoformat())
    assert len(result['daily']) == 60


def test_eastmoney_url_or_hash_mismatch_is_rejected():
    broken = payload()
    broken['url'] = broken['url'].replace('002155', '600000')
    try:
        verify_eastmoney_turnover_payload(broken, '002155', DAY)
    except ValueError as exc:
        assert '身份' in str(exc)
    else:
        raise AssertionError('must reject')
    broken = payload()
    broken['sha256'] = 'b' * 64
    try:
        verify_eastmoney_turnover_payload(broken, '002155', DAY)
    except ValueError as exc:
        assert '摘要' in str(exc)
    else:
        raise AssertionError('must reject hash')
