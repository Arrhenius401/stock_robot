"""已确认结构化依据的保守合并：发行人、治理、财务质量。"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any

from radar.stocks.industry_scale import scale_passes

FORMAL_RULE_VERSION = 'formal-2026-11-09-v6'
_ISSUER_TYPES = {'ordinary', 'bank', 'securities', 'insurance'}
_GOVERNANCE_SERIOUS = ('财务造假', '虚假记载', '资金占用', '违规担保')


def _company_object(record: dict[str, Any]) -> bool:
    text = record.get('object') or ''
    return '上市公司' in text and '拟上市' not in text


def _refresh(result: dict[str, Any]) -> None:
    statuses = {row['outcome'] for row in result['checks']}
    result['common_status'] = ('failed' if 'failed' in statuses else
                               'pending_verification' if 'pending_verification' in statuses else 'passed')
    result['admission_status'] = 'excluded' if result['common_status'] == 'failed' else 'pending_verification'
    result['evidence_ids'] = sorted({identifier for row in result['checks']
                                     for identifier in row.get('evidence_ids', [])})


def _merge_check(result: dict[str, Any], code: str, outcome: str, reason: str, details: dict[str, Any]) -> None:
    for check in result['checks']:
        if check.get('code') != code:
            continue
        old = deepcopy(check)
        if old.get('outcome') == 'failed':
            outcome, reason = 'failed', '保留已审核失败'
        elif old.get('outcome') == 'passed' and outcome != 'passed':
            outcome, reason = 'pending_verification', '人工通过结论与结构化依据冲突，保留双方待核验'
        check.clear()
        check.update(code=code, outcome=outcome, channel='structured_policy', reason=reason,
                     manual_check=old, structured_check=details)


def merge_governance(result: dict[str, Any], source: dict[str, Any], as_of: date) -> dict[str, Any]:
    """公司对象纪律处分硬失败；监管措施待核验；未命中不能通过。"""
    merged = deepcopy(result)
    merged['governance_source'] = deepcopy(source)
    symbol = merged.get('symbol')
    bound = (source.get('status') == 'current' and source.get('symbol') == symbol
             and source.get('as_of') == as_of.isoformat())
    if not bound:
        _merge_check(merged, 'governance', 'pending_verification',
                     '监管来源缺失、历史或非本次截点，治理仍待核验', {'status': source.get('status')})
        _refresh(merged)
        return merged
    records = [row for row in source.get('records', []) if isinstance(row, dict)]
    cutoff3 = date(as_of.year - 3, as_of.month, as_of.day)
    cutoff5 = date(as_of.year - 5, as_of.month, as_of.day)

    def recent(row: dict[str, Any], cutoff: date) -> bool:
        try:
            return date.fromisoformat(str(row.get('listed_on'))) >= cutoff
        except ValueError:
            return False

    discipline = any(row.get('measure_category') == 'discipline' and _company_object(row) and recent(row, cutoff3)
                     for row in records)
    measures = any(row.get('measure_category') == 'regulatory_measure' and _company_object(row) and recent(row, cutoff3)
                   for row in records)
    serious = any(any(keyword in (row.get('title') or '') for keyword in _GOVERNANCE_SERIOUS) and recent(row, cutoff5)
                  for row in records)
    raw_evaluation = source.get('evaluation')
    evaluation: dict[str, Any] = raw_evaluation if isinstance(raw_evaluation, dict) else {}
    grade = evaluation.get('grade') if evaluation.get('status') == 'current' else None
    if serious or discipline:
        outcome = 'failed'
        reason = ('监管文书明确涉及财务造假、资金占用或违规担保，硬失败且不自动恢复'
                  if serious else '近三年公司对象纪律处分，硬失败')
    elif grade in {'C', 'D'}:
        outcome = 'failed'
        reason = '交易所年度信息披露评价C/D，治理不通过'
    elif measures:
        outcome = 'pending_verification'
        reason = '近三年公司对象监管措施，需人工复核性质和整改状态'
    elif grade in {'A', 'B'}:
        outcome = 'passed'
        reason = '交易所年度信息披露评价A/B，且监管列表未发现公司对象硬失败'
    else:
        outcome = 'pending_verification'
        reason = '监管列表未命中或仅工作函/未知类型，且交易所评价未覆盖或未发布，不能证明治理合格'
    _merge_check(merged, 'governance', outcome, reason,
                 {'records': len(records), 'discipline': discipline, 'measures': measures, 'serious': serious,
                  'as_of': source.get('as_of'), 'grade': grade, 'evaluation_status': evaluation.get('status')})
    _refresh(merged)
    return merged


def merge_financial_quality(result: dict[str, Any], source: dict[str, Any] | None, as_of: date,
                            issuer: str | None) -> dict[str, Any]:
    """按发行人类型应用已确认阈值；普通企业复用现金比率和盈利/审计条件。"""
    merged = deepcopy(result)
    if issuer not in _ISSUER_TYPES:
        _merge_check(merged, 'financial_quality', 'pending_verification', '发行人类型未核实，财务质量分支无法适用', {})
        _refresh(merged)
        return merged
    if (not isinstance(source, dict) or source.get('status') != 'current' or source.get('as_of') != as_of.isoformat()
            or source.get('evaluation_as_of', as_of.isoformat()) != as_of.isoformat()
            or source.get('symbol') != merged.get('symbol')):
        _merge_check(merged, 'financial_quality', 'pending_verification', '财务来源缺失、历史或非本次截点', {})
        _refresh(merged)
        return merged
    raw_financial: Any = source.get('financial')
    financial: dict[str, Any] = raw_financial if isinstance(raw_financial, dict) else {}
    expected_type = {'ordinary': '4', 'bank': '3', 'securities': '1', 'insurance': '2'}.get(issuer)
    source_type = financial.get('source_company_type')
    if source_type is not None and expected_type is not None and str(source_type) != expected_type:
        _merge_check(merged, 'financial_quality', 'pending_verification',
                     '财务数据公司类型与发行人类型不一致，不能套用该分支阈值',
                     {'issuer_type': issuer, 'source_company_type': source_type})
        _refresh(merged)
        return merged
    raw_metrics: Any = financial.get('type_metrics')
    metrics: dict[str, Any] = raw_metrics if isinstance(raw_metrics, dict) else {}
    report_day = metrics.get('report_date')
    if (isinstance(report_day, str) and report_day > as_of.isoformat()) or (
            metrics.get('currency') not in (None, 'CNY')):
        _merge_check(merged, 'financial_quality', 'pending_verification', '类型指标晚于研究截点或币种不符', {})
        _refresh(merged)
        return merged
    outcome, reason = 'pending_verification', '财务质量指标缺失或待核验'
    if issuer in ('bank', 'securities', 'insurance') and metrics.get('status') == 'available':
        if issuer == 'bank':
            car, provision, npl = metrics.get('bank_car'), metrics.get('bank_provision'), metrics.get('bank_npl_ratio')
            if (car is not None and provision is not None and npl is not None
                    and metrics.get('bank_car_basis') in (None, 'total_capital_adequacy')):
                outcome = ('passed' if car >= 10.5 and provision >= 150 and npl <= 0.05 else 'failed')
                reason = '银行总资本充足率≥10.5%、拨备覆盖率≥150%、不良率≤5%'
        elif issuer == 'securities':
            risk, liquidity = metrics.get('sec_risk_coverage'), metrics.get('sec_liquidity_coverage')
            if risk is not None and liquidity is not None:
                outcome = 'passed' if risk >= 100 and liquidity >= 100 else 'failed'
                reason = '证券风险覆盖率≥100%、流动性覆盖率≥100%'
        else:
            solvency = metrics.get('insurance_solvency')
            if solvency is not None:
                outcome = 'passed' if solvency >= 100 else 'failed'
                reason = '保险综合偿付能力≥100%'
    elif issuer == 'ordinary':
        checks = {row['code']: row['outcome'] for row in merged['checks']}
        profit_codes = [code for code in checks if code.startswith(('annual_', 'ttm_'))]
        profit_ok = bool(profit_codes) and all(checks[code] == 'passed' for code in profit_codes)
        if 'failed' in {checks.get(code) for code in profit_codes} | {checks.get('audit')}:
            outcome = 'failed'
            reason = '普通企业盈利或审计存在明确失败'
        elif profit_ok and checks.get('audit') == 'passed':
            outcome = 'passed'
            reason = '普通企业盈利和审计条件通过；现金比率只在对应池作为风格条件'
    _merge_check(merged, 'financial_quality', outcome, reason, {'issuer_type': issuer, 'metrics': metrics})
    _refresh(merged)
    return merged


def merge_leadership(result: dict[str, Any], source: dict[str, Any], as_of: date) -> dict[str, Any]:
    """仅第三方/协会明确份额或排名的披露可通过；其余待核验。"""
    merged = deepcopy(result)
    merged['leadership_source'] = deepcopy(source)
    claims = source.get('claims') if isinstance(source, dict) else None
    scale = source.get('scale') if isinstance(source, dict) else None
    threshold = source.get('scale_threshold') if isinstance(source, dict) else None
    min_peers = source.get('scale_min_peers') if isinstance(source, dict) else None
    scale_ok = (isinstance(threshold, int) and isinstance(min_peers, int)
                and scale_passes(scale, threshold, min_peers))
    bound = (isinstance(source, dict) and source.get('status') == 'current'
             and source.get('symbol') == merged.get('symbol') and source.get('as_of') == as_of.isoformat())
    if bound and isinstance(claims, list) and claims:
        _merge_check(merged, 'leadership', 'passed', '第三方/协会份额或排名已与同一原文、报告期和公告主体绑定',
                     {'claims': claims, 'scale': scale})
    elif bound and scale_ok:
        _merge_check(merged, 'leadership', 'passed',
                     f'候选池内申万二级当前总市值前{threshold}（同业≥{min_peers}家），仅作规模地位',
                     {'claims': claims or [], 'scale': scale, 'scale_threshold': threshold, 'scale_min_peers': min_peers})
    else:
        _merge_check(merged, 'leadership', 'pending_verification',
                     f'缺少可追溯的第三方/协会份额排名，且候选池内市值未进入前{threshold or 5}或同业不足{min_peers or 5}家', {})
    _refresh(merged)
    return merged
