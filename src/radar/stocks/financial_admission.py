"""结构化盈利与已审核共同条件的保守合并。"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any


def merge_profitability(manual: list[dict[str, Any]], financial: dict[str, Any], codes: set[str]) -> list[dict[str, Any]]:
    """只替换指定盈利条件；确定失败优先，缺失不能回退人工通过。"""
    numeric = {row['code']: row for row in financial.get('financial_numeric_checks', [])}
    checks = deepcopy(manual)
    for check in checks:
        code = check['code']
        if code not in codes:
            continue
        old = deepcopy(check)
        structured = deepcopy(numeric.get(code, {'code': code, 'outcome': 'pending_verification', 'reason': '采集产物缺失、损坏或尚未处理'}))
        # 两渠道确定冲突也保留已知失败，不能利用另一渠道通过将失败抹去。
        outcomes = {old['outcome'], structured['outcome']}
        outcome = ('pending_verification' if outcomes == {'passed', 'failed'}
                   else ('failed' if 'failed' in outcomes else structured['outcome']))
        reason = ('两方正负冲突待核验，保留人工事实与结构化数值双方'
                  if outcomes == {'passed', 'failed'} else
                  ('保留已知失败；结构化数值与人工事实分别留存' if outcome == 'failed' else
                   ('已校验结构化盈利数值，人工事实另行保留' if outcome == 'passed' else
                    '结构化资料待补，不能回退人工通过')))
        check.clear()
        check.update(code=code, outcome=outcome, channel='structured_financial',
                     reason=reason,
                     manual_check=old, structured_check=structured)
    return checks


def merge_common_financial(manual: dict[str, Any], source: dict[str, Any], as_of: date) -> dict[str, Any]:
    """仅当前截点的有效产物参与八项盈利条件，原人工失败保留。"""
    result = deepcopy(manual)
    codes = {f'annual_{metric}_{year}' for year in range(as_of.year - 3, as_of.year)
             for metric in ('parent_profit', 'deducted_profit')} | {'ttm_parent_profit', 'ttm_deducted_profit'}
    financial = source.get('financial') if source.get('status') == 'current' and source.get('as_of') == as_of.isoformat() else None
    result['checks'] = merge_profitability(manual['checks'], financial or {}, codes)
    if source.get('status') == 'historical' and isinstance(source.get('financial'), dict):
        references = {row['code']: row for row in source['financial'].get('financial_numeric_checks', [])}
        for check in result['checks']:
            if check['code'] not in codes:
                continue
            reference = deepcopy(references.get(check['code'], {'code': check['code'], 'outcome': 'pending_verification',
                                                               'reason': '该年度条件不在历史截点已复核范围内'}))
            label = {'passed': '通过', 'failed': '不满足', 'pending_verification': '待补'}.get(reference['outcome'], '待补')
            check.update(reference_as_of=source['as_of'], reference_check=reference,
                         reason=f"历史财务参考（{source['as_of']}）：{label}，仅供参考，不作本次资格依据")
    outcomes = {row['outcome'] for row in result['checks']}
    status = ('failed' if 'failed' in outcomes or manual['common_status'] == 'failed'
              else ('pending_verification' if 'pending_verification' in outcomes else 'passed'))
    result.update(common_status=status, admission_status='excluded' if status == 'failed' else 'pending_verification',
                  manual_common=deepcopy(manual), financial_source=deepcopy(source), financial_verified=False)
    return result
