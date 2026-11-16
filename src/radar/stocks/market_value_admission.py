"""供应商市值与人工共同资格的保守合并。"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any

from radar.stocks.admission import _day

_CODES = {'capitalization_current', 'capitalization_average_60d'}
_OUTCOMES = {'passed', 'failed', 'pending_verification'}


def merge_common_market_value(manual: dict[str, Any], source: dict[str, Any], as_of: date) -> dict[str, Any]:
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
    for check in result['checks']:
        code = check['code']
        if code not in _CODES:
            continue
        old = deepcopy(check)
        candidates = grouped.get(code, [])
        valid = len(candidates) == 1 and candidates[0].get('outcome') in _OUTCOMES
        numeric = deepcopy(candidates[0]) if current and valid else {
            'code': code, 'outcome': 'pending_verification', 'reason': '供应商市值缺失、损坏或非本次截点'}
        outcomes = {old['outcome'], numeric['outcome']}
        conflict = outcomes == {'passed', 'failed'}
        outcome = ('pending_verification' if conflict else
                   'failed' if 'failed' in outcomes else numeric['outcome'])
        reason = ('人工事实与供应商市值结论相反，保留双方待核验' if conflict else
                  '保留已知失败，供应商市值与人工事实分别留存' if outcome == 'failed' else
                  '供应商总市值结果，人工事实另行保留' if outcome == 'passed' else
                  '供应商市值待核验，不能回退人工通过')
        check.clear()
        check.update(code=code, outcome=outcome, channel='supplier_market_value', reason=reason,
                     manual_check=old, supplier_check=numeric)
        if historical and valid:
            historical_reason = f"历史供应商市值（{source['as_of']}）仅供参考，不作本次资格依据"
            if outcome == 'failed':
                historical_reason = f'保留已知人工失败；{historical_reason}'
            check.update(reference_as_of=source['as_of'], reference_check=deepcopy(candidates[0]),
                         reason=historical_reason)
    outcomes = {row['outcome'] for row in result['checks']}
    status = ('failed' if 'failed' in outcomes or manual['common_status'] == 'failed' else
              'pending_verification' if 'pending_verification' in outcomes else 'passed')
    result.update(common_status=status, admission_status='excluded' if status == 'failed' else 'pending_verification',
                  market_value_source=deepcopy(source))
    return result
