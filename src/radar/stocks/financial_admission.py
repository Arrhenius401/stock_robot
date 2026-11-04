"""结构化盈利与已审核共同条件的保守合并。"""
from __future__ import annotations

import re
from copy import deepcopy
from datetime import date, datetime
from typing import Any

from radar.stocks.admission import _BJ
from radar.stocks.research_candidates import classify_audit_clues


def _audit_request_valid(clues: Any, symbol: str, as_of: date) -> bool:
    """审计判断须绑定证券、年度及明确的结构化请求。"""
    try:
        if not isinstance(clues, dict) or not isinstance(clues.get('source'), dict):
            return False
        meta = clues['source']
        params = meta['request_params']
        if not isinstance(params, dict):
            return False
        market = 'SH' if symbol.startswith('6') else 'SZ'
        fetched = datetime.fromisoformat(meta['fetched_at'])
        if (meta.get('source_url') != 'https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/xjllbAjaxNew'
                or meta.get('version') != 'eastmoney-structured-financial-v1' or meta.get('as_of') != as_of.isoformat()
                or not isinstance(meta.get('content_sha256'), str)
                or re.fullmatch(r'[0-9a-f]{64}', meta['content_sha256']) is None
                or fetched.utcoffset() is None or fetched.astimezone(_BJ).date() > as_of
                or params.get('code') != market + symbol or params.get('reportType') != '1'
                or params.get('reportDateType') != '0' or params.get('companyType') not in ('1', '2', '3', '4')):
            return False
        requested = params['dates'].split(',')
        dates = [date.fromisoformat(value) for value in requested]
        years = set(range(as_of.year - 3, as_of.year))
        if len(dates) != len(set(dates)) or any(day > as_of for day in dates):
            return False
        if not {date(year, 12, 31) for year in years}.issubset(dates):
            return False
        annual = clues.get('annual')
        return isinstance(annual, list) and all(
            isinstance(row, dict) and row.get('security_code') == symbol and row.get('secucode') == symbol + '.' + market
            and row.get('report_type') == '年报' and row.get('report_date_name') == f"{row.get('year')}年报"
            for row in annual)
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return False


def _merge_audit(checks: list[dict[str, Any]], source: dict[str, Any], symbol: str, as_of: date) -> None:
    """三年审计意见仅作为结构化条件，不伪造人工审核。"""
    clues = source.get('audit_clues')
    bound = (source.get('status') == 'current' and source.get('symbol') == symbol
             and source.get('as_of') == as_of.isoformat() and source.get('evaluation_as_of') == as_of.isoformat()
             and isinstance(clues, dict) and clues.get('symbol') == symbol and clues.get('as_of') == as_of.isoformat())
    classification = classify_audit_clues(clues if bound else None, as_of.isoformat())
    structured_outcome = ('passed' if classification['status'] == 'standard_opinions'
                          and _audit_request_valid(clues, symbol, as_of) else 'pending_verification')
    for check in checks:
        if check.get('code') != 'audit':
            continue
        old = deepcopy(check)
        conflict = old.get('outcome') == 'failed' and structured_outcome == 'passed'
        outcome = ('pending_verification' if conflict else 'failed' if old.get('outcome') == 'failed'
                   else structured_outcome)
        reason = ('人工审计事实与结构化意见冲突，待核验' if conflict else
                  '保留已审核审计失败' if outcome == 'failed' else
                  '三年主要审计意见均为标准无保留；人工审核记录另行保留' if outcome == 'passed' else
                  '审计来源缺失、历史、非标准或口径不明，待核验')
        check.clear()
        check.update(code='audit', outcome=outcome, channel='structured_audit', reason=reason,
                     manual_check=old, structured_check={'outcome': structured_outcome, 'classification': classification,
                                                         'source': deepcopy(clues.get('source')) if bound and isinstance(clues, dict) else None})


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
    _merge_audit(result['checks'], source, str(manual.get('symbol', '')), as_of)
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
    audit_conflict = any(row.get('code') == 'audit' and row.get('channel') == 'structured_audit'
                         and row.get('outcome') == 'pending_verification'
                         and isinstance(row.get('manual_check'), dict)
                         and row['manual_check'].get('outcome') == 'failed'
                         and row.get('structured_check', {}).get('outcome') == 'passed'
                         for row in result['checks'])
    other_manual_failure = any(row.get('code') != 'audit' and row.get('outcome') == 'failed'
                               for row in manual['checks'])
    preserve_manual_failure = (manual['common_status'] == 'failed' and
                               (not audit_conflict or other_manual_failure))
    status = ('failed' if 'failed' in outcomes or preserve_manual_failure
              else ('pending_verification' if 'pending_verification' in outcomes else 'passed'))
    result.update(common_status=status, admission_status='excluded' if status == 'failed' else 'pending_verification',
                  manual_common=deepcopy(manual), financial_source=deepcopy(source), financial_verified=False)
    return result
