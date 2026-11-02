"""保守提取实施公告中的字段建议，原文匹配不等于财务证据核实。"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import date
from decimal import Decimal, localcontext
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pypdf import PdfReader

logger = logging.getLogger(__name__)
_NUMBER = r'([0-9][0-9,]*(?:\.[0-9]+)?)'
_UNIT = r'(百万元|万元|元)'


def _base(symbol: str, source_url: str, disclosed_on: str) -> dict[str, Any]:
    return {'symbol': symbol, 'source_url': source_url, 'disclosed_on': disclosed_on,
            'status': 'pending_verification', 'event_proposals': [], 'reasons': [],
            'requires_review': True, 'rule_enabled': False, 'financial_verified': False}


def _money(number: str, unit: str = '元') -> str:
    with localcontext() as context:
        context.prec = max(50, len(number) + 10)
        value = Decimal(number.replace(',', '')) * {'元': 1, '万元': 10000, '百万元': 1000000}[unit]
    return format(value, 'f').rstrip('0').rstrip('.') if '.' in format(value, 'f') else format(value, 'f')


def _one_amount(pattern: str, text: str) -> str | None:
    values = {_money(match[0], match[1]) for match in re.findall(pattern + _NUMBER + _UNIT, text)}
    return next(iter(values)) if len(values) == 1 else None


def _paid_on(text: str) -> str | None:
    token = r'(20\d{2})[年/.-](\d{1,2})[月/.-](\d{1,2})日?'
    values: set[str] = set()
    for table in re.finditer(r'A股((?:20\d{2}[/.]\d{1,2}[/.]\d{1,2}|[－—-]){3,4})', text):
        dates = re.findall(token, table[1])
        if len(dates) >= 3:
            try:
                values.add(date(*map(int, dates[-1])).isoformat())
            except ValueError:
                logger.debug('公告日期表存在无效日期')
    for match in re.finditer(r'(?:除息日及现金红利发放日|现金红利发放日)(?:为|为：|：)?' + token, text):
        try:
            values.add(date(*map(int, match.groups())).isoformat())
        except ValueError:
            logger.debug('公告发放日存在无效日期')
    return next(iter(values)) if len(values) == 1 else None


def extract_dividend_text(text: str, *, symbol: str, source_url: str,
                          disclosed_on: str, as_of: date) -> dict[str, Any]:
    """仅匹配有限的实施公告模板；金额建议永不自动转换为已核实事件。"""
    result = _base(symbol, source_url, disclosed_on)
    result['text_sha256'] = hashlib.sha256(text.encode('utf-8')).hexdigest()
    compact = re.sub(r'\s+', '', text).replace('Ａ', 'A').replace('Ｈ', 'H').replace('：', ':')
    # 部分交易所 PDF 的加粗标题会重复每个汉字，只修正标题用于匹配。
    header = re.sub(r'([\u4e00-\u9fff])\1', r'\1', compact[:1500])
    try:
        parsed = urlsplit(source_url)
        if not re.fullmatch(r'\d{6}', symbol) or parsed.scheme != 'https' or not parsed.netloc:
            raise ValueError('股票代码或来源 URL 无效')
        disclosed = date.fromisoformat(disclosed_on)
        if disclosed > as_of:
            raise ValueError('公告晚于研究截至日期')
    except ValueError as exc:
        result['reasons'].append(str(exc))
        return result
    codes = re.findall(r'证券代码:*(\d{6})', header)
    if symbol not in codes or any(code != symbol for code in codes):
        result['reasons'].append('证券代码缺失、冲突或与候选不符')
        return result
    title = re.search(r'(20\d{2})年?(年度|中期|半年度)(?:A股)?(?:权益分派|利润分配|分红派息)实施公告', header)
    if not title or re.search(r'(?:取消|终止|更正|预案|议案).{0,20}实施公告|实施公告.{0,15}(?:更正|取消|终止)', header):
        result['reasons'].append('未匹配可支持的原始实施公告标题')
        return result
    year = int(title[1])
    period = 'annual' if title[2] == '年度' else 'interim'
    if year > as_of.year or (year == as_of.year and period == 'annual'):
        result['reasons'].append('财政年度尚未完成')
        return result
    explicit_year = re.findall(r'发放年度:(20\d{2})年?(年度|中期|半年度)', compact)
    if explicit_year and any(int(value) != year or ('annual' if part == '年度' else 'interim') != period
                             for value, part in explicit_year):
        result['reasons'].append('标题与发放年度冲突')
        return result
    bank_title = re.search(r'20\d{2}年?(?:年度|中期|半年度)A股分红派息实施公告', header) is not None
    # 上交所分配方案和深交所特别提示分段，排除历史审议金额与税后金额。
    scheme = re.search(r'3[.、]分配方案:(.*?)(?:三[、.]相关日期|三、)', compact)
    if bank_title and scheme is None:
        scheme = re.search(r'(?:\(三\)|（三）)分配方案:(.*?)(?:三[、.]相关日期|三、)', compact)
    tip = re.search(r'特特别别提提示示:?(.*?)(?:一一、、|一、股东)', compact)
    if tip is None:
        tip = re.search(r'(?:特别提示|重要内容提示):?(.*?)(?:一、股东)', compact)
    section = scheme[1] if scheme else tip[1] if tip else ''
    if not section:
        result['reasons'].append('实际分配方案章节缺失或不支持')
        return result
    current = re.split(r'公司已派发|连同本次|全年现金红利', section)[0]
    total = _one_amount(r'(?:共计派发现金红利|分红总金额为|合计派发现金红利)(?:人民币)?', current)
    a_total = _one_amount(r'(?:派发A股现金红利|A股共计派发现金红利)(?:人民币)?', current)
    share_class = 'A' if a_total or (total and 'A股' in compact and 'H股' not in compact) or 'A股利润分配方案' in current else 'ALL'
    amount = a_total or total
    approximate_amount = None
    if bank_title:
        candidates = re.findall(r'派发A股现金红利约人民币' + _NUMBER + r'(亿元|百万元|万元|元)', current)
        unique = set(candidates)
        if len(unique) == 1:
            number, unit = next(iter(unique))
            approximate_amount = {'value': number.replace(',', ''), 'unit': unit, 'currency': 'CNY'}
        # 公司总额不是A股金额；近似值不进入精确金额字段。
        share_class = 'A' if candidates or a_total else 'ALL'
        amount = None if candidates else a_total
    per_matches = re.findall(r'每股(?:派发)?现金红利(?:人民币)?' + _NUMBER + r'元', current)
    if not per_matches:
        tens = re.findall(r'每10股派发现金' + _NUMBER + r'元', current)
        per_matches = [format(Decimal(value.replace(',', '')) / Decimal(10), 'f') for value in tens]
    per_values = {value.replace(',', '') for value in per_matches}
    shares = re.findall(r'(?:可参与分配的A股股本|A股股本)(?:为)?' + _NUMBER + r'股', current)
    if not shares and 'H股' not in compact:
        shares = re.findall(r'总股本(?:为)?' + _NUMBER + r'股', current)
    # 回购场景只取公告明确写出的可参与股本，不自行计算。
    actual = re.findall(r'可参与分配的(?:A股)?(?:总股本|股本|总股数|股数)' + _NUMBER + r'股', current)
    actual_values = {value.replace(',', '') for value in actual}
    actual_conflict = len(actual_values) > 1
    eligible = actual[0] if len(actual_values) == 1 else shares[-1] if not actual and len(shares) == 1 and '回购' not in current else None
    if actual_conflict:
        result['reasons'].append('明确实际参与股本存在冲突，不能回退到总股本')
    if '回购' in current and not actual:
        result['reasons'].append('回购后实际参与股本缺失，不能以总股本代替')
    per_share = next(iter(per_values)) if len(per_values) == 1 else None
    relation_conflict = False
    if amount is not None and per_share is not None and eligible is not None:
        # 只核验公告已明确的三个金额字段，不据股本推算缺失的总额。
        with localcontext() as context:
            context.prec = max(50, len(amount) + len(per_share) + len(eligible) + 5)
            expected_amount = Decimal(per_share) * Decimal(eligible.replace(',', ''))
            relation_conflict = abs(expected_amount - Decimal(amount)) > Decimal('0.01')
        if relation_conflict:
            result['reasons'].append('实际股本乘每股金额与已披露总额矛盾（仅允许0.01元展示舍入差）')
    paid = _paid_on(compact)
    complete = amount is not None and paid is not None and share_class == 'A' and len(per_values) <= 1 and not relation_conflict and not actual_conflict
    if '回购' in current and eligible is None:
        complete = False
    if paid and not date(year, 1, 1) <= date.fromisoformat(paid) <= as_of:
        complete = False
    if share_class != 'A':
        result['reasons'].append('缺少明确A股分红金额或股类归属，需人工核验')
        return result
    proposal = {'symbol': symbol, 'fiscal_year': year, 'kind': 'current_distribution', 'distribution_period': period,
                'amount_scope': 'distribution_event', 'share_class': share_class,
                'amount_yuan': amount, 'per_share_yuan': per_share,
                **({'approximate_amount': approximate_amount} if bank_title else {}),
                'eligible_shares': eligible.replace(',', '') if eligible else None,
                'paid_on': paid if share_class == 'A' else None, 'source_url': source_url,
                'disclosed_on': disclosed_on, 'status': 'matched_template' if complete else 'pending_verification',
                'requires_review': True, 'financial_verified': False, 'rule_enabled': False,
                'evidence_excerpt': section[:1500]}
    result['event_proposals'].append(proposal)
    # 只保留A股事件，不输出公司全股类年度合计。
    interim = re.findall(str(year) + r'年度中期现金红利每股' + _NUMBER + r'元', section)
    if len(set(interim)) == 1 and not bank_title:
        result['event_proposals'].append({**proposal, 'kind': 'interim_reference',
            'amount_scope': 'distribution_event', 'amount_yuan': None, 'per_share_yuan': interim[0],
            'eligible_shares': None, 'paid_on': None, 'status': 'pending_verification'})
    result['status'] = proposal['status']
    if not complete:
        result['reasons'].append('金额、股类或派息日期不足或冲突，需人工核验')
    return result


def extract_archived_documents(archive_result: dict[str, Any], archive_dir: Path,
                               as_of: date) -> dict[str, Any]:
    """先验证 URL 对应文件名与 SHA，再逐份解析归档 PDF，不访问网络。"""
    output: dict[str, Any] = {'items': [], 'event_proposals': [], 'requires_review': True,
                             'financial_verified': False, 'rule_enabled': False}
    root = archive_dir.resolve()
    items = archive_result.get('items', [])
    if not isinstance(items, list):
        raise TypeError('归档 items 必须为列表')
    for item in items:
        if not isinstance(item, dict):
            output['items'].append({'status': 'pending_verification', 'reasons': ['归档行格式无效']})
            continue
        symbol, url = str(item.get('symbol', '')), str(item.get('source_url', ''))
        disclosed = str(item.get('disclosed_on', ''))
        result = _base(symbol, url, disclosed)
        try:
            if item.get('discovery_status') == 'pending_review':
                raise ValueError('公告发现结果尚待人工确认')
            expected = hashlib.sha256(url.encode('utf-8')).hexdigest() + '.pdf'
            path = root / expected
            if item.get('file') != expected or path.is_symlink() or path.resolve().parent != root:
                raise ValueError('公告文件名或路径不安全')
            if item.get('status') not in {'downloaded', 'cached'}:
                raise ValueError('公告未成功归档')
            if path.stat().st_size > 25 * 1024 * 1024:
                raise ValueError('PDF 超过文本提取大小上限')
            payload = path.read_bytes()
            if not payload.startswith(b'%PDF-') or hashlib.sha256(payload).hexdigest() != item.get('sha256'):
                raise ValueError('PDF 文件头或 SHA 校验失败')
            if len(payload) != item.get('bytes'):
                raise ValueError('PDF 长度校验失败')
            reader = PdfReader(BytesIO(payload))
            if len(reader.pages) > 30:
                raise ValueError('PDF 页数超出实施公告模板范围')
            text = '\f'.join(page.extract_text() or '' for page in reader.pages)
            result = extract_dividend_text(text, symbol=symbol, source_url=url,
                                           disclosed_on=disclosed, as_of=as_of)
            result['file_sha256'] = item['sha256']
            result['page_count'] = len(reader.pages)
        # PDF 解析是第三方边界，单份损坏不得中断候选批量研究。
        except Exception as exc:  # noqa: BLE001 第三方 PDF 与文件隔离边界，记录错误后保守待核验
            logger.warning('实施公告提取失败 %s: %s', symbol, exc)
            result['reasons'].append(str(exc))
        output['items'].append(result)
        output['event_proposals'].extend(result['event_proposals'])
    return output
