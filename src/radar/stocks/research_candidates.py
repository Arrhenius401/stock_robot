"""将现有评估整理成只读研究摘要，不改变正式资格。"""
from __future__ import annotations

import logging
from datetime import UTC
from typing import Any

logger = logging.getLogger(__name__)


def classify_audit_clues(clues: Any, as_of: str) -> dict[str, Any]:
    """分类原文意见，不生成审核结论或正式资格。"""
    from datetime import date

    result = {'status': 'pending_verification', 'reviewed': False, 'formal_screening_enabled': False}
    try:
        cutoff = date.fromisoformat(as_of)
        if not isinstance(clues, dict) or clues.get('status') != 'available':
            return result
        annual = clues.get('annual')
        years = set(range(cutoff.year - 3, cutoff.year))
        if (not isinstance(annual, list) or len(annual) != 3
                or any(not isinstance(row, dict) or type(row.get('year')) is not int for row in annual)
                or {row['year'] for row in annual} != years):
            return result
        if any(row.get('status') != 'available' or row.get('updated_after_cutoff') is not False
               or not isinstance(row.get('opinion'), str) or not row['opinion'].strip() for row in annual):
            return result
        if any(row['opinion'].strip() != '标准无保留意见' for row in annual):
            result['status'] = 'other_opinion'
        elif any(row.get('other_opinion') not in (None, '') for row in annual):
            return result
        else:
            result['status'] = 'standard_opinions'
    except (ValueError, TypeError, OverflowError):
        return result
    return result


def summarize_research(item: dict[str, Any], pool_id: str) -> dict[str, Any]:
    """保留各条件原结论及理由，未知不能升级为通过。"""
    result: dict[str, Any] = {'symbol': item.get('symbol'), 'as_of': item.get('as_of'), 'pool_id': pool_id,
        'status': 'unavailable', 'passed': [], 'failed': [], 'pending': [], 'unconfigured_rules': [],
        'research_only': True, 'formal_screening_enabled': False, 'members_published': 0}
    pools = item.get('pools')
    pool = pools.get(pool_id) if isinstance(pools, dict) else None
    if item.get('evaluation_valid') is not True or not isinstance(pool, dict):
        return result
    common, style = item.get('checks'), pool.get('checks')
    if not isinstance(common, list) or not isinstance(style, list):
        return result
    for check in (*common, *style):
        if not isinstance(check, dict) or not isinstance(check.get('code'), str):
            continue
        outcome = check.get('outcome')
        bucket = 'passed' if outcome == 'passed' else 'failed' if outcome == 'failed' else 'pending'
        projected = {key: check.get(key) for key in ('code', 'outcome', 'reason', 'channel')}
        values = check.get('values')
        if isinstance(values, dict):
            projected['values'] = {key: values[key] for key in ('amount_yuan', 'date', 'average_amount_yuan',
                                     'observations', 'a_listed_on', 'third_anniversary') if key in values}
        structured = check.get('structured_check')
        if isinstance(structured, dict) and structured.get('amount_yuan') is not None:
            projected['amount_yuan'] = structured['amount_yuan']
        result[bucket].append(projected)
    sources = {}
    for name in ('financial_source', 'dividend_source', 'turnover_source', 'market_value_source',
                 'company_profile_source', 'industry_source'):
        source = item.get(name)
        if isinstance(source, dict):
            sources[name] = {key: source[key] for key in ('status', 'as_of', 'fetched_on', 'fetched_dates') if key in source}
    result['sources'] = sources
    rules = pool.get('unconfigured_rules')
    result['unconfigured_rules'] = [rule for rule in rules if isinstance(rule, str)] if isinstance(rules, list) else []
    references = pool.get('reference_rules')
    result['reference_rules'] = ({key: value for key, value in references.items()
                                  if isinstance(key, str) and value in ('disabled', 'pending_verification', 'research_reference')}
                                 if isinstance(references, dict) else {})
    result['status'] = 'research_candidate'
    return result


def extract_audit_clues(symbol: str, as_of: str, raw: bytes, meta: dict[str, Any]) -> dict[str, Any]:
    """从绑定原文提取年度意见线索，绝不生成审核结论。"""
    import hashlib
    import json
    from datetime import date, datetime, timedelta, timezone

    result: dict[str, Any] = {'symbol': symbol, 'as_of': as_of, 'status': 'unavailable', 'annual': [],
                              'reviewed': False, 'formal_screening_enabled': False}
    try:
        cutoff = date.fromisoformat(as_of)
        fetched = datetime.fromisoformat(meta['fetched_at'])
        if (meta.get('source_url') != 'https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/xjllbAjaxNew'
                or fetched.utcoffset() is None or fetched > datetime.now(UTC)
                or len(raw) != meta.get('bytes') or hashlib.sha256(raw).hexdigest() != meta.get('content_sha256')):
            return result
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get('success') is False or ('code' in data and data['code'] != 0):
            return result
        rows = data['data']
        if not isinstance(rows, list):
            return result
        annual = []
        for year in range(cutoff.year-3, cutoff.year):
            wanted = f'{year}-12-31'
            found = [row for row in rows if isinstance(row, dict) and row.get('SECURITY_CODE') == symbol
                     and str(row.get('REPORT_DATE', ''))[:10] == wanted]
            entry: dict[str, Any] = {'year': year, 'status': 'pending_verification'}
            if len(found) == 1:
                row = found[0]
                expected_secucode = symbol + ('.SH' if symbol.startswith('6') else '.SZ')
                try:
                    notice = date.fromisoformat(str(row.get('NOTICE_DATE', ''))[:10])
                    updated = date.fromisoformat(str(row.get('UPDATE_DATE', ''))[:10])
                    opinion = row.get('OPINION_TYPE')
                    identity_ok = row.get('SECUCODE') in (None, expected_secucode)
                    fetched_day = fetched.astimezone(timezone(timedelta(hours=8))).date()
                    if (identity_ok and date(year, 12, 31) < notice <= min(cutoff, fetched_day)
                            and isinstance(opinion, str) and 0 < len(opinion.strip()) <= 500
                            and (row.get('OSOPINION_TYPE') is None or
                                 isinstance(row.get('OSOPINION_TYPE'), str) and len(row['OSOPINION_TYPE']) <= 500)):
                        entry.update(status='available', opinion=opinion, other_opinion=row.get('OSOPINION_TYPE'),
                                     disclosed_on=notice.isoformat(), updated_on=updated.isoformat(),
                                     updated_after_cutoff=updated > cutoff)
                except (ValueError, TypeError) as exc:
                    logger.debug('年度审计线索日期待核验：%s', type(exc).__name__)

            annual.append(entry)
        result.update(status='historical' if fetched.astimezone(timezone(timedelta(hours=8))).date() > cutoff else 'available',
                      annual=annual, source={key: meta[key] for key in ('source_url', 'content_sha256', 'fetched_at')})
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, OverflowError):
        return result
    result['classification'] = classify_audit_clues(result, as_of)
    return result


def load_audit_clues(db: Any, symbol: str, as_of: Any) -> dict[str, Any]:
    """只读取当前选择的财务任务和摘要绑定的原文。"""
    from pathlib import Path

    from radar.stocks.financial_source import load_financial_task
    from radar.stocks.structured_collect import _safe_path

    result = {'symbol': symbol, 'as_of': as_of.isoformat(), 'status': 'unavailable', 'annual': [],
              'reviewed': False, 'formal_screening_enabled': False}
    selected = load_financial_task(db, symbol, as_of)
    if selected.source.get('status') not in ('current', 'historical') or not isinstance(selected.payload, dict):
        return result
    try:
        collection = selected.payload['collection']
        meta = collection['sources']['cashflow']
        root = (Path(db).absolute().parent / 'stock-updates/cache/structured-financial').resolve()
        path = Path(meta['raw_artifact']).absolute()
        _safe_path(path)
        if not path.resolve().is_relative_to(root) or not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
            return result
        result = extract_audit_clues(symbol, as_of.isoformat(), path.read_bytes(), meta)
        if selected.source.get('status') == 'historical' and result['status'] == 'available':
            result['status'] = 'historical'
            result['classification'] = classify_audit_clues(result, as_of.isoformat())
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return result
