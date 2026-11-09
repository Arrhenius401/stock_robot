"""读取严格第三方/协会引用的行业地位披露，并复读原始 F10 与公告响应。"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

_REQUIRED = ('symbol', 'scope', 'metric', 'value', 'source_name', 'quote', 'report_date', 'source_url',
             'raw_file', 'raw_sha256', 'disclosed_on', 'announcement_title', 'announcement_url',
             'announcement_art_code', 'disclosure_file', 'disclosure_sha256',
             'announcement_raw_file', 'announcement_raw_sha256')
_NUMBERS = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
_NEGATIONS = ('未进入', '未跻身', '未位列', '未排名', '未入选', '不在', '未能', '未达到', '未及',
              '没有进入', '并未', '未获评', '未评为')
_QUALIFIERS = ('超过', '逾', '近', '约', '不足', '低于', '不到', '至少')
_REPORT_TITLES = {(3, 31): ('第一季度报告', '一季度报告'),
                  (6, 30): ('半年度报告', '中期报告'),
                  (9, 30): ('第三季度报告', '三季度报告'),
                  (12, 31): ('年度报告',)}
_ANNOUNCEMENT_API = 'https://np-anotice-stock.eastmoney.com/api/security/ann'
_EVIDENCE_FIELDS = ('status', 'as_of', 'symbol', 'observation', 'claims', 'file_sha256', 'reason')


def _norm(value: Any) -> str:
    if not isinstance(value, str):
        return ''
    return re.sub(r'[\s\u3000]+', '', unicodedata.normalize('NFKC', value))


def _number(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    if value == '百':
        return 100
    if '十' in value:
        tens, _, ones = value.partition('十')
        if tens and tens not in _NUMBERS:
            return None
        if ones and ones not in _NUMBERS:
            return None
        return (_NUMBERS[tens] if tens else 1) * 10 + (_NUMBERS[ones] if ones else 0)
    return _NUMBERS.get(value)


def _clauses(text: str) -> list[str]:
    return [clause for sentence in re.split(r'[。；;]', text) for clause in re.split(r'[,，]', sentence) if clause]


def _ranks(clause: str) -> list[int]:
    """提取明确排名；不带前/第的“十强企业”等称号不当作公司排名。"""
    found: list[int] = []
    for match in re.finditer(r'第([一二三四五六七八九十百\d]+)(?:名|位|强)?', clause):
        number = _number(match.group(1))
        if number is not None:
            found.append(number)
    for match in re.finditer(r'前([一二三四五六七八九十百\d]+)(?:名|位|强)?', clause):
        number = _number(match.group(1))
        if number is not None:
            found.append(number)
    for match in re.finditer(r'(?:排名|位列|位居|名列)(?:第)?([一二三四五六七八九十百\d]+)(?:名|位)?', clause):
        number = _number(match.group(1))
        if number is not None:
            found.append(number)
    return found


_MARKETS = ('全球', '中国', '国内', '全国', '海外', '亚太', '欧洲', '美洲', '亚洲', '世界')


def _scope_parts(scope: str) -> tuple[str, str]:
    for market in _MARKETS:
        if scope.startswith(market) and len(scope) > len(market):
            return market, scope[len(market):]
    return '', scope


def _scope_matches(clause: str, scope: str) -> bool:
    """口径可写在报告标题，但主句至少要有业务核心；市场词须在同一原文出现。"""
    if scope in clause:
        return True
    market, core = _scope_parts(scope)
    return bool(market and core and core in clause)


def _scope_ranks(quote: str, scope: str, value: str) -> list[list[int]]:
    """把排名限定在含口径与数值的同句或后续小句，避免抓取其他公司排名。"""
    clauses = _clauses(quote)
    results: list[list[int]] = []
    for index, clause in enumerate(clauses):
        if not _scope_matches(clause, scope):
            continue
        ranks = _ranks(clause)
        if value in clause:
            if not ranks:
                # 排名常接在数值之后一到两个小句，遇到带排名的其他口径即停止。
                for offset in range(1, 4):
                    if index + offset >= len(clauses):
                        break
                    following = clauses[index + offset]
                    if any(marker in following for marker in ('公司', '集团', '股份')):
                        # 后续小句出现新的公司主体时，不能把它的排名安到本公司。
                        break
                    if _ranks(following):
                        ranks = _ranks(following)
                        break
                    if _scope_matches(following, scope):
                        break
            results.append(sorted(set(ranks)))
        elif ranks and index > 0 and value in clauses[index - 1] and not _scope_matches(clauses[index - 1], scope):
            results.append(sorted(set(ranks)))
    return results


def _value_supported(text: str, value: str) -> bool:
    """份额前的“超过/约/不足”等限定词必须写入数值本身，不能当作精确值。"""
    start = 0
    found = False
    while True:
        index = text.find(value, start)
        if index < 0:
            return found
        found = True
        prefix = text[max(0, index - 2):index]
        if any(prefix.endswith(qualifier) for qualifier in _QUALIFIERS):
            return False
        if value[:1] in '超过逾近约不足低于不到至少':
            return True
        start = index + len(value)


def _official_url(value: str, symbol: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    if parsed.scheme != 'https' or parsed.hostname != 'emweb.securities.eastmoney.com':
        return False
    if parsed.path != '/PC_HSF10/BusinessAnalysis/PageAjax':
        return False
    code = ('SH' if symbol.startswith('6') else 'SZ') + symbol
    return parse_qs(parsed.query).get('code') == [code]


def _row_identity(row: dict[str, Any], symbol: str) -> bool:
    code = row.get('SECURITY_CODE')
    secucode = row.get('SECUCODE')
    if code is not None and code != symbol:
        return False
    if secucode is not None and str(secucode).split('.')[0] != symbol:
        return False
    return code is not None or secucode is not None


def _raw_reviews(state_dir: Path, claim: dict[str, Any], symbol: str) -> str:
    raw_file = claim.get('raw_file')
    expected = f"raw/{symbol}.json"
    if not isinstance(raw_file, str) or raw_file != expected:
        raise ValueError('行业地位原始文件路径不符')
    path = state_dir / 'stock-leadership' / raw_file
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != claim.get('raw_sha256'):
        raise ValueError('行业地位原始文件摘要不符')
    payload = json.loads(raw)
    rows = payload.get('jyps') if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise TypeError('行业地位原始披露结构不符')
    matches = [row for row in rows if isinstance(row, dict) and _row_identity(row, symbol)
               and str(row.get('REPORT_DATE') or '')[:10] == claim.get('report_date')]
    if len(matches) != 1:
        raise ValueError('行业地位报告期在原文中缺失或重复')
    return str(matches[0].get('BUSINESS_REVIEW') or '')


def _valid_announcement_raw(state_dir: Path, claim: dict[str, Any], symbol: str) -> bool:
    raw_file = claim.get('announcement_raw_file')
    expected = f"announcements/{symbol}.json"
    if not isinstance(raw_file, str) or raw_file != expected:
        return False
    try:
        raw = (state_dir / 'stock-leadership' / raw_file).read_bytes()
        if hashlib.sha256(raw).hexdigest() != claim.get('announcement_raw_sha256'):
            return False
        payload = json.loads(raw)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    data = payload.get('data') if isinstance(payload, dict) else None
    items = data.get('list') if isinstance(data, dict) else None
    if not isinstance(items, list):
        return False
    matches = [item for item in items if isinstance(item, dict)
               and item.get('art_code') == claim.get('announcement_art_code')]
    if len(matches) != 1:
        return False
    item = matches[0]
    codes = item.get('codes')
    if not isinstance(codes, list) or not any(
            isinstance(code, dict) and code.get('stock_code') == symbol for code in codes):
        return False
    if item.get('title') != claim.get('announcement_title'):
        return False
    return str(item.get('notice_date') or '')[:10] == claim.get('disclosed_on')


def _valid_disclosure(state_dir: Path, claim: dict[str, Any], symbol: str) -> bool:
    disclosure_file = claim.get('disclosure_file')
    expected = f"disclosures/{symbol}.json"
    if not isinstance(disclosure_file, str) or disclosure_file != expected:
        return False
    try:
        raw = (state_dir / 'stock-leadership' / disclosure_file).read_bytes()
        if hashlib.sha256(raw).hexdigest() != claim.get('disclosure_sha256'):
            return False
        item = json.loads(raw)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    if not isinstance(item, dict):
        return False
    art_code = claim.get('announcement_art_code')
    if (item.get('art_code') != art_code or item.get('title') != claim.get('announcement_title')
            or item.get('notice_date') != claim.get('disclosed_on') or item.get('source_api') != _ANNOUNCEMENT_API):
        return False
    if item.get('url') != claim.get('announcement_url'):
        return False
    try:
        captured = datetime.fromisoformat(item.get('captured_at', ''))
        notice = date.fromisoformat(claim['disclosed_on'])
    except (ValueError, TypeError):
        return False
    if (captured.tzinfo is None or captured.utcoffset() is None or captured.date() < notice
            or captured > datetime.now().astimezone()):
        return False
    parsed = urlsplit(claim['announcement_url'])
    if (parsed.scheme != 'https' or parsed.hostname != 'data.eastmoney.com'
            or parsed.path != f'/notices/detail/{symbol}/{art_code}.html'):
        return False
    return _valid_announcement_raw(state_dir, claim, symbol)


def _period_title_matches(title: str, report: date) -> bool:
    expected = _REPORT_TITLES.get((report.month, report.day))
    return expected is not None and any(item in title for item in expected)


def _valid_claim(state_dir: Path, claim: Any, symbol: str, cutoff: date) -> bool:
    if not isinstance(claim, dict) or any(not isinstance(claim.get(key), str) or not claim[key].strip() for key in _REQUIRED):
        return False
    if claim.get('symbol') != symbol or claim.get('metric') not in {'share', 'rank'}:
        return False
    rank = claim.get('rank')
    if type(rank) is not int or rank < 1 or rank > 3:
        return False
    try:
        report = date.fromisoformat(claim['report_date'])
        disclosed = date.fromisoformat(claim['disclosed_on'])
    except ValueError:
        return False
    if (report.month, report.day) not in _REPORT_TITLES or not report < disclosed <= cutoff:
        return False
    if not _period_title_matches(claim['announcement_title'], report):
        return False
    if not _official_url(claim['source_url'], symbol):
        return False
    try:
        text = _raw_reviews(state_dir, claim, symbol)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    quote = claim['quote']
    if quote not in text:
        return False
    if _norm(claim['source_name']) not in _norm(text):
        return False
    scope = _norm(claim['scope'])
    value = _norm(claim['value'])
    if len(scope) < 2 or scope not in _norm(quote) or value not in _norm(quote):
        return False
    if any(negation in _norm(quote) for negation in _NEGATIONS):
        return False
    if not _value_supported(_norm(quote), value):
        return False
    scoped = _scope_ranks(_norm(quote), scope, value)
    if not scoped or any(item != [rank] for item in scoped):
        return False
    return _valid_disclosure(state_dir, claim, symbol)


def load_leadership_source(state_dir: Path, symbol: str, as_of: date) -> dict[str, Any]:
    out: dict[str, Any] = {'symbol': symbol, 'as_of': as_of.isoformat(), 'status': 'unavailable',
                           'observation': 'unknown', 'claims': [], 'reason': '行业地位来源缺失'}
    root = state_dir / 'stock-leadership'
    path = root / 'leadership.json'
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get('as_of') != as_of.isoformat() or not isinstance(payload.get('symbols'), dict):
            raise ValueError('行业地位来源截点或结构不符')
        claims = payload['symbols'].get(symbol, [])
        if not isinstance(claims, list) or not all(_valid_claim(root.parent, item, symbol, as_of) for item in claims):
            raise ValueError('行业地位声明无效')
        out.update(status='current', observation='observed' if claims else 'not_observed', claims=claims,
                   file_sha256=hashlib.sha256(raw).hexdigest(),
                   reason='声明、原文、报告期、排名、公告主体与披露日期已逐项绑定')
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return out
