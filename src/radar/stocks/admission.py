"""对已显式审核的规范事实计算共同条件，计算结果不发布股票池成员。"""

from __future__ import annotations

import ipaddress
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any
from urllib.parse import urlsplit

RULE_VERSION = 'common-approved-2026-10-15-v1'
_KINDS = ('security', 'capitalization', 'profitability', 'audit', 'governance', 'leadership', 'financial_quality')
_BJ = timezone(timedelta(hours=8))
_CAPITALIZATION_FLOOR = Decimal(20000000000)
# 超出全球企业经济规模的输入视为数据错误；不把该保护当作选股上限。
_MAX_ABSOLUTE_AMOUNT = Decimal('1e18')


def _choice(value: Any, options: set[str]) -> bool:
    return isinstance(value, str) and value in options


def _day(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.isoformat() == value else None
    return None


def _amount(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    if isinstance(value, str) and (not value.strip() or len(value) > 100):
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number.copy_abs() > _MAX_ABSOLUTE_AMOUNT or len(number.as_tuple().digits) > 50:
            return None
        # 指数表示也有解析预算；超出预算待核验，不引入新的正利润最低门槛。
        exponent = number.as_tuple().exponent
        if not isinstance(exponent, int) or not -1000 <= exponent <= 1000:
            return None
    except (InvalidOperation, ValueError, OverflowError):
        return None
    return number


def _source_valid(row: dict[str, Any], symbol: str, end: datetime) -> bool:
    if row.get('symbol') != symbol or row.get('confirmable') is not True or row.get('review_status') != 'confirmed':
        return False
    if not isinstance(row.get('evidence_id'), str) or not row['evidence_id']:
        return False
    source = row.get('source_url')
    digest = row.get('content_sha256')
    if not isinstance(source, str) or not isinstance(digest, str) or re.fullmatch('[0-9a-f]{64}', digest) is None:
        return False
    try:
        parsed = urlsplit(source)
        host = parsed.hostname or ''
        if (parsed.scheme != 'https' or not host or host == 'localhost' or host.endswith('.localhost')
                or parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443)
                or any(char.isspace() for char in source)):
            return False
        try:
            ipaddress.ip_address(host)
        except ValueError:
            domain = True
        else:
            domain = False
        if not domain:
            return False
    except ValueError:
        return False
    disclosed = _day(row.get('disclosed_on'))
    if disclosed is None or disclosed > end.date():
        return False
    review = row.get('latest_review')
    if not isinstance(review, dict) or review.get('decision') != 'confirmed':
        return False
    if review.get('symbol', symbol) != symbol or review.get('evidence_id', row['evidence_id']) != row['evidence_id']:
        return False
    reviewed_at = review.get('reviewed_at')
    if not isinstance(reviewed_at, str):
        return False
    try:
        reviewed = datetime.fromisoformat(reviewed_at)
        if reviewed.tzinfo is None or reviewed.utcoffset() is None or reviewed > end:
            return False
        return end - reviewed < timedelta(days=90)
    except (ValueError, OverflowError):
        return False


def _groups(symbol: str, evidence: Any, end: datetime, *, kinds: tuple[str, ...] = _KINDS,
            prefix: str = "admission_") -> dict[str, tuple[dict[str, Any] | None, list[str], str]]:
    result: dict[str, tuple[dict[str, Any] | None, list[str], str]] = {}
    for kind in kinds:
        rows = [row for row in evidence if isinstance(row, dict) and row.get('kind') == prefix + kind] if isinstance(evidence, list) else []
        identifiers = sorted({row['evidence_id'] for row in rows if isinstance(row.get('evidence_id'), str)})
        payloads: dict[str, dict[str, Any]] = {}
        valid_ids: set[str] = set()
        for row in rows:
            payload = row.get('payload')
            if not _source_valid(row, symbol, end) or not isinstance(payload, dict) or payload.get('symbol', symbol) != symbol:
                continue
            try:
                # 此处仅比较资料身份；非有限金额留给逐项校验，不能抹去同组其他确定性失败。
                serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=True)
            except (TypeError, ValueError, RecursionError):
                continue
            payloads[serialized] = payload
            valid_ids.add(row['evidence_id'])
        if len(payloads) == 1:
            result[kind] = (next(iter(payloads.values())), sorted(valid_ids), '')
        elif len(payloads) > 1:
            result[kind] = (None, sorted(valid_ids), '同组已审核事实存在冲突，须重新核验')
        else:
            result[kind] = (None, identifiers, '缺少未过期且原文追踪完整的已审核规范事实')
    return result


def evaluate_common_admission(symbol: str, evidence: list[dict[str, Any]], as_of: date, *,
                              market_window: list[date] | None = None) -> dict[str, Any]:
    """计算共同准入硬条件；缺失、冲突及失效事实均不能替换为零。"""
    cutoff = _day(as_of)
    checks: list[dict[str, Any]] = []
    result: dict[str, Any] = {'symbol': symbol if isinstance(symbol, str) else None, 'as_of': cutoff.isoformat() if cutoff else None,
                              'rule_version': RULE_VERSION, 'checks': checks, 'common_status': 'pending_verification',
                              'admission_status': 'pending_verification', 'formal_screening_enabled': False,
                              'evidence_ids': []}

    def add(code: str, outcome: str, reason: str, ids: list[str], values: dict[str, Any] | None = None) -> None:
        row: dict[str, Any] = {'code': code, 'outcome': outcome, 'reason': reason, 'evidence_ids': ids}
        if values is not None:
            row['values'] = values
        checks.append(row)

    if (cutoff is None or cutoff.year < 4 or not isinstance(symbol, str)
            or re.fullmatch(r'(?:60[0135]|688|000|001|002|003|30[012])\d{3}', symbol) is None):
        add('input', 'pending_verification', '公司代码或评估日期无效', [])
        return result
    groups = _groups(symbol, evidence, datetime.combine(cutoff, time.max, tzinfo=_BJ))
    years = list(range(cutoff.year - 3, cutoff.year))
    security, security_ids, security_reason = groups['security']
    issuer = security.get('issuer_type') if security else None
    if security is None:
        add('security', 'pending_verification', security_reason, security_ids)
    elif (_choice(security.get('exchange'), {'HKEX', 'BSE', 'NYSE', 'NASDAQ', 'AMEX', 'OTC', 'OTHER'})
          or _choice(security.get('security_type'), {'etf', 'index', 'fund', 'bond', 'preferred', 'preferred_share',
                                                    'ordinary_h', 'ordinary_b', 'h_share', 'b_share', 'cdr',
                                                    'depositary_receipt', 'reit', 'ordinary_us', 'convertible_bond', 'other'})):
        add('security', 'failed', '明确为其他市场或证券类型', security_ids)
    elif (not _choice(security.get('exchange'), {'SSE', 'SZSE'}) or security.get('security_type') != 'ordinary_a'
          or not _choice(issuer, {'ordinary', 'bank', 'securities', 'insurance'})):
        add('security', 'pending_verification', '市场、证券或发行人类型未核实', security_ids)
    else:
        add('security', 'passed', '沪深普通A股及发行人类型已核实', security_ids, {'issuer_type': issuer})
    listed = _day(security.get('a_listed_on')) if security else None
    if listed is None or listed > cutoff or listed.year > 9996:
        add('listing_age', 'pending_verification', security_reason or 'A股上市日缺失、无效或晚于截点', security_ids)
    else:
        try:
            anniversary = listed.replace(year=listed.year + 3)
        except ValueError:
            # 2月29日在非闰年的三周年按2月末处理。
            anniversary = date(listed.year + 3, 2, 28)
        add('listing_age', 'passed' if cutoff >= anniversary else 'failed',
            '按A股上市完整三周年计算', security_ids, {'a_listed_on': listed.isoformat(), 'third_anniversary': anniversary.isoformat()})
    parsed_window = [_day(item) for item in market_window] if isinstance(market_window, list) else []
    window = [item for item in parsed_window if item is not None]
    valid_window = (len(parsed_window) == 60 and len(window) == 60 and all(item <= cutoff for item in window)
                    and all(window[index] < window[index + 1] for index in range(59)))
    capital, cap_ids, cap_reason = groups['capitalization']
    cap_scope = (capital is not None and capital.get('currency') == 'CNY' and capital.get('scope') == 'company_all_shares'
                 and capital.get('share_classes_complete') is True)
    current = capital.get('current') if capital else None
    amount = _amount(current.get('amount_yuan')) if isinstance(current, dict) else None
    current_day = _day(current.get('date')) if isinstance(current, dict) else None
    daily = capital.get('daily') if capital else None
    current_daily_conflict = False
    if (cap_scope and valid_window and current_day == window[-1] and amount is not None
            and amount >= 0 and isinstance(daily, list)):
        for observation in daily:
            if not isinstance(observation, dict) or _day(observation.get('date')) != current_day:
                continue
            observed_amount = _amount(observation.get('amount_yuan'))
            if observed_amount is not None and observed_amount >= 0 and observed_amount != amount:
                current_daily_conflict = True
                break
    if current_daily_conflict:
        cap_reason = '当前市值与日序列同日全股类市值冲突，须重新核验'
    if current_daily_conflict or not cap_scope or not valid_window or current_day is None or current_day != window[-1] or amount is None or amount < 0:
        add('capitalization_current', 'pending_verification', cap_reason or '当前全股类人民币市值或市场时点未完整核实', cap_ids)
    else:
        add('capitalization_current', 'passed' if amount >= _CAPITALIZATION_FLOOR else 'failed',
            '当前公司总市值须不低于200亿元', cap_ids, {'amount_yuan': str(amount), 'date': current_day.isoformat()})
    amounts: list[Decimal] = []
    valid_daily = cap_scope and valid_window and not current_daily_conflict and isinstance(daily, list) and len(daily) == 60
    if valid_daily and isinstance(daily, list):
        by_date: dict[date, Decimal] = {}
        for row in daily:
            day = _day(row.get('date')) if isinstance(row, dict) else None
            value = _amount(row.get('amount_yuan')) if isinstance(row, dict) else None
            if day is None or day in by_date or value is None or value < 0:
                valid_daily = False
                break
            by_date[day] = value
        if set(by_date) != set(window):
            valid_daily = False
        amounts = list(by_date.values())
    if not valid_daily:
        add('capitalization_average_60d', 'pending_verification', cap_reason or '缺少完整且逐日匹配的60个市场交易日全股类市值', cap_ids)
    else:
        with localcontext() as context:
            context.prec = 80
            average = sum(amounts, Decimal(0)) / Decimal(60)
        add('capitalization_average_60d', 'passed' if average >= _CAPITALIZATION_FLOOR else 'failed',
            '60个市场交易日公司总市值均值须不低于200亿元', cap_ids,
            {'average_amount_yuan': str(average), 'observations': 60})
    profit, profit_ids, profit_reason = groups['profitability']
    profit_scope = profit is not None and profit.get('currency') == 'CNY' and profit.get('scope') == 'company_parent'
    annual = profit.get('annual') if profit else None
    for year in years:
        rows = [row for row in annual if isinstance(row, dict) and type(row.get('year')) is int and row['year'] == year] if isinstance(annual, list) else []
        row = rows[0] if len(rows) == 1 else None
        disclosed = _day(row.get('disclosed_on')) if row else None
        valid_period = (profit_scope and row is not None and disclosed is not None and date(year, 12, 31) < disclosed <= cutoff)
        for prefix, key in (('annual_parent_profit', 'parent_profit_yuan'), ('annual_deducted_profit', 'deducted_parent_profit_yuan')):
            value = _amount(row.get(key)) if row else None
            if not valid_period or value is None:
                add(f'{prefix}_{year}', 'pending_verification', profit_reason or '完整年度利润、披露日期或归母口径缺失/重复/无效', profit_ids)
            else:
                add(f'{prefix}_{year}', 'passed' if value > 0 else 'failed', '该完整年度利润须严格为正', profit_ids,
                    {'year': year, 'amount_yuan': str(value)})
    ttm = profit.get('ttm') if profit else None
    ttm = ttm if isinstance(ttm, dict) else {}
    period_end, disclosed = _day(ttm.get('period_end')), _day(ttm.get('disclosed_on'))
    valid_ttm = (profit_scope and ttm.get('latest_announced') is True and period_end is not None
                 and disclosed is not None and period_end <= disclosed <= cutoff)
    for prefix, key in (('ttm_parent_profit', 'parent_profit_yuan'), ('ttm_deducted_profit', 'deducted_parent_profit_yuan')):
        value = _amount(ttm.get(key))
        if not valid_ttm or value is None or period_end is None:
            add(prefix, 'pending_verification', profit_reason or '缺少独立、已披露且明确为最新的TTM归母利润', profit_ids)
        else:
            add(prefix, 'passed' if value > 0 else 'failed', '最新已公告TTM利润须严格为正', profit_ids,
                {'period_end': period_end.isoformat(), 'amount_yuan': str(value)})
    for kind in ('audit', 'governance', 'leadership', 'financial_quality'):
        payload, identifiers, missing_reason = groups[kind]
        required = ['basis']
        if kind == 'leadership':
            required.extend(['industry_level1', 'industry_level2', 'business_scope'])
        valid = payload is not None and all(isinstance(payload.get(key), str) and payload[key].strip() for key in required)
        if kind == 'audit' and payload is not None:
            supplied = payload.get('years')
            valid = valid and isinstance(supplied, list) and all(type(item) is int for item in supplied) and sorted(supplied) == years
        if kind == 'financial_quality' and payload is not None:
            valid = valid and _choice(issuer, {'ordinary', 'bank', 'securities', 'insurance'}) and payload.get('issuer_type') == issuer
        verdict = payload.get('verdict') if payload else None
        outcome = {'clear': 'passed', 'failed': 'failed', 'pending': 'pending_verification'}.get(verdict) if isinstance(verdict, str) else None
        if not valid or outcome is None or payload is None:
            add(kind, 'pending_verification', missing_reason or '专项依据、期间或发行人分支尚未完整核验', identifiers)
        else:
            add(kind, outcome, '已审核专项事实的判断；不替代其他共同条件', identifiers, {'basis': payload['basis']})
    statuses = {row['outcome'] for row in checks}
    result['common_status'] = 'failed' if 'failed' in statuses else ('pending_verification' if 'pending_verification' in statuses else 'passed')
    result['admission_status'] = 'excluded' if result['common_status'] == 'failed' else 'pending_verification'
    result['evidence_ids'] = sorted({identifier for row in checks for identifier in row['evidence_ids']})
    return result
