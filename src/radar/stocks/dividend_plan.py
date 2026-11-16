"""按年报利润分配章节独立判定“分红 / 不分红”，只用受控规则，不调用模型。"""
from __future__ import annotations

import re
from typing import Any

PLAN_RULE_VERSION = 'dividend-plan-2026-11-10-v1'
_NO_DIVIDEND = (
    '不派发现金红利', '不进行现金分红', '不分配现金红利', '不进行利润分配',
    '不分配不转增', '不送红股', '不以资本公积金转增股本', '不分配',
)
_POSITIVE = (
    r'每10股派(?:发)?现金红利?([0-9]+(?:\.[0-9]+)?)元',
    r'每10股派([0-9]+(?:\.[0-9]+)?)元',
    r'每股派(?:发)?现金红利?([0-9]+(?:\.[0-9]+)?)元',
    r'派发现金红利(?:人民币)?([0-9]+(?:\.[0-9]+)?)元',
)


def _compact(text: str) -> str:
    return re.sub(r'[\s\u3000]+', '', text)


def classify_dividend_plan_text(text: str, *, symbol: str, source_url: str, disclosed_on: str,
                               year: int) -> dict[str, Any]:
    """从年报“利润分配”章节判定：no_dividend / positive / pending。"""
    if not isinstance(text, str) or not text.strip():
        return {'symbol': symbol, 'year': year, 'outcome': 'pending', 'reason': '年报文本为空',
                'source_url': source_url, 'disclosed_on': disclosed_on,
                'rule_version': PLAN_RULE_VERSION}
    compact = _compact(text)
    start = compact.find('利润分配')
    section = compact[start:start + 4000] if start >= 0 else compact
    no_dividend = next((phrase for phrase in _NO_DIVIDEND if phrase in section), None)
    amounts: list[str] = []
    for pattern in _POSITIVE:
        amounts.extend(re.findall(pattern, section))
    positive = list(dict.fromkeys(amount for amount in amounts if float(amount) > 0))
    if no_dividend and not positive:
        outcome, reason = 'no_dividend', f'年报利润分配章节明确不派现：{no_dividend}'
    elif positive and not no_dividend:
        outcome, reason = 'positive', f'年报利润分配章节列示现金分红：每10股/每股口径 {positive[0]}'
    elif no_dividend and positive:
        outcome, reason = 'pending', '利润分配章节同时出现不分配和派现表述，需核验原文'
    else:
        outcome, reason = 'pending', '利润分配章节未找到明确的不分配或派现表述'
    return {'symbol': symbol, 'year': year, 'outcome': outcome, 'reason': reason,
            'no_dividend_phrase': no_dividend, 'positive_amounts': positive,
            'source_url': source_url, 'disclosed_on': disclosed_on,
            'rule_version': PLAN_RULE_VERSION}
