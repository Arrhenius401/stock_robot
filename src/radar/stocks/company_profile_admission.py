"""官方名录上市年限与人工共同资格的保守合并。"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any

from radar.stocks.admission import _day

_CODES = {'listing_age'}
_OUTCOMES = {'passed', 'failed', 'pending_verification'}


def _manual_failure(check: dict[str, Any]) -> bool:
    """保留上游合并时嵌套保存的人工失败。"""
    if check.get('outcome') == 'failed':
        return True
    nested = check.get('manual_check')
    return isinstance(nested, dict) and _manual_failure(nested)


def merge_common_profile(manual: dict[str, Any], source: dict[str, Any], as_of: date) -> dict[str, Any]:
    """仅同截点供应商结果参与判断；历史结果仅作参考。"""
    result = deepcopy(manual)
    rows = source.get('checks')
    grouped: dict[str, list[dict[str, Any]]] = {}
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get('code'), str) and row['code'] in _CODES:
                grouped.setdefault(row['code'], []).append(row)
    current = source.get('status') == 'current' and source.get('as_of') == as_of.isoformat()
    source_day = _day(source.get('as_of'))
    historical = source.get('status') == 'historical' and source_day is not None and source_day < as_of
    security_conflict = False
    for check in result['checks']:
        code = check['code']
        market = source.get('security_market')
        if code == 'security' and isinstance(market, dict):
            symbol = result.get('symbol')
            issuer = market.get('issuer_type')
            allowed_issuer = issuer in {'ordinary', 'bank', 'securities', 'insurance'}
            valid_market = (current and source.get('symbol') == symbol and
                            source.get('evaluation_as_of') == as_of.isoformat() and
                            market.get('symbol') == symbol and market.get('status') == 'verified' and
                            market.get('security_type') == 'ordinary_a' and
                            (issuer is None or allowed_issuer) and
                            ((isinstance(symbol, str) and symbol.startswith('6') and market.get('exchange') == 'SSE') or
                             (isinstance(symbol, str) and symbol.startswith(('0', '3')) and market.get('exchange') == 'SZSE')))
            check['profile_market'] = deepcopy(market)
            if not valid_market:
                check['profile_market']['status'] = 'historical' if source.get('status') == 'historical' else 'pending_verification'
            old = deepcopy(check)
            manual_issuer = (check.get('values') or {}).get('issuer_type') if isinstance(check.get('values'), dict) else None
            if valid_market and check['outcome'] == 'failed':
                security_conflict = True
                check.update(outcome='pending_verification', reason='人工证券身份与官方名录冲突，保留双方待核验',
                             manual_check=old)
            elif valid_market and allowed_issuer:
                if check['outcome'] == 'passed' and manual_issuer and manual_issuer != issuer:
                    security_conflict = True
                    check.update(outcome='pending_verification', reason='人工发行人类型与官方名录冲突，保留双方待核验',
                                 manual_check=old)
                else:
                    check.update(outcome='passed', channel='official_issuer',
                                 reason='官方名录已核验普通A股市场身份及发行人类型',
                                 values={'issuer_type': issuer}, manual_check=old)
            elif valid_market and issuer is None and check['outcome'] == 'pending_verification':
                check['reason'] = '官方名录已核验普通A股市场身份，发行人类型仍未分类待核验'
            continue
        if code not in _CODES:
            continue
        old = deepcopy(check)
        candidates = grouped.get(code, [])
        valid = len(candidates) == 1 and candidates[0].get('outcome') in _OUTCOMES
        numeric = deepcopy(candidates[0]) if current and valid else {
            'code': code, 'outcome': 'pending_verification', 'reason': '官方名录上市年限缺失、损坏或非本次截点'}
        outcomes = {old['outcome'], numeric['outcome']}
        conflict = outcomes == {'passed', 'failed'}
        outcome = ('pending_verification' if conflict else
                   'failed' if 'failed' in outcomes else numeric['outcome'])
        reason = ('人工事实与官方名录上市年限结论相反，保留双方待核验' if conflict else
                  '保留已知失败，官方名录上市年限与人工事实分别留存' if outcome == 'failed' else
                  '官方名录上市年限结果，人工事实另行保留' if outcome == 'passed' else
                  '官方名录上市年限待核验，不能回退人工通过')
        check.clear()
        check.update(code=code, outcome=outcome, channel='official_listing', reason=reason,
                     manual_check=old, profile_check=numeric)
        if historical and valid:
            historical_reason = f"历史官方名录上市年限（{source['as_of']}）仅供参考，不作本次资格依据"
            if outcome == 'failed':
                historical_reason = f'保留已知人工失败；{historical_reason}'
            check.update(reference_as_of=source['as_of'], reference_check=deepcopy(candidates[0]),
                         reason=historical_reason)
    outcomes = {row['outcome'] for row in result['checks']}
    other_manual_failure = any(row.get('code') != 'security' and _manual_failure(row)
                               for row in manual['checks'])
    retain_manual_failure = manual['common_status'] == 'failed' and (not security_conflict or other_manual_failure)
    status = ('failed' if 'failed' in outcomes or retain_manual_failure else
              'pending_verification' if 'pending_verification' in outcomes else 'passed')
    result.update(common_status=status, admission_status='excluded' if status == 'failed' else 'pending_verification',
                  company_profile_source=deepcopy(source))
    return result
