"""将已归档现金字段补入原财务来源，复用完整现金专项而非旁路判断。"""
from copy import deepcopy
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from radar.stocks.cash_special import (
    _identity,
    _published,
    cash_special_metrics,
    cash_special_verdict,
)


def completion_cash_verdict(source: dict[str, Any], collection: dict[str, Any], evidence: dict[str, Any],
                            cutoff: date, symbol: str, issuer: str | None) -> dict[str, Any]:
    """年报必须保留真实披露日、SHA及元人民币口径；利息只从原main/profit取。"""
    pending = {'outcome': 'pending_verification', 'reason': '现金补全来源、币种单位或披露日期尚未完整核验'}
    if (source.get('status') != 'current' or source.get('as_of') != cutoff.isoformat()
            or source.get('symbol') != symbol or evidence.get('symbol') != symbol
            or evidence.get('as_of') != cutoff.isoformat()):
        return pending
    merged = deepcopy(collection)
    records = merged.get('records')
    years = evidence.get('years')
    if not isinstance(records, dict) or not isinstance(years, dict):
        return pending
    original = records.get('cashflow') or []
    cash = []
    for year in range(cutoff.year - 3, cutoff.year):
        annual = years.get(str(year)) or {}
        meta, fields = annual.get('source') or {}, annual.get('fields') or {}
        try:
            published = date.fromisoformat(meta.get('disclosed_on', ''))
            digest = meta.get('content_sha256') or ''
            if (meta.get('symbol') != symbol or meta.get('fiscal_year') != year
                    or meta.get('currency') != 'CNY' or meta.get('unit') != 'CNY_yuan'
                    or not date(year, 12, 31) < published <= cutoff
                    or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest)
                    or urlsplit(meta.get('source_url') or '').scheme != 'https'):
                return pending
        except (ValueError, TypeError):
            return pending
        # 只继承原规则认可的同公司、人民币、截点前已披露行，不能重标无效原值。
        old = [r for r in original if isinstance(r, dict) and r.get('CURRENCY') == 'CNY'
               and _identity(r, symbol) and _published(r, cutoff) is not None
               and str(r.get('REPORT_DATE', ''))[:10] == f'{year}-12-31']
        if len(old) > 1:
            return pending
        row = deepcopy(old[0]) if old else {}
        if row.get('END_CASH') in (None, ''):
            for alias in ('END_CASH_EQUIVALENTS', 'END_CCE'):
                if row.get(alias) not in (None, ''):
                    row['END_CASH'] = row[alias]
                    break
        for key, native in [('netcash_operate', 'NETCASH_OPERATE'), ('construct_long_asset', 'CONSTRUCT_LONG_ASSET'),
                            ('assign_dividend_profit', 'ASSIGN_DIVIDEND_PORFIT'), ('end_cash', 'END_CASH')]:
            if row.get(native) in (None, ''):
                row[native] = fields.get(key)
        row.update(SECURITY_CODE=symbol, CURRENCY='CNY', REPORT_DATE=f'{year}-12-31',
                   NOTICE_DATE=published.isoformat(), UPDATE_DATE=published.isoformat())
        cash.append(row)
    records['cashflow'] = cash
    rebound = deepcopy(source)
    rebound['financial'] = {**source.get('financial', {}), 'cash_special': cash_special_metrics(merged, cutoff, symbol)}
    return cash_special_verdict(rebound, issuer, cutoff, symbol)
