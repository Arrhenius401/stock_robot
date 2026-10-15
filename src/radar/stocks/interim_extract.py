"""保守提取季度/半年报累计会计数据，模板命中不代表事实已审核。"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path
from typing import Any

from radar.stocks.admission import _day
from radar.stocks.financial_extract import (
    _LABELS,
    _NUMBER,
    _amount,
    _base,
    extract_archived_financials,
)
from radar.stocks.research_store import StockResearchError, _symbol, _url

_PERIODS = {'第一季度': (3, 31), '半年度': (6, 30), '第三季度': (9, 30)}
_ANCHOR = re.compile(r'(?:\A|(?<=[\n\f]))[ \t]*(?:[（(][一二三四五六七八九十]+[)）][ \t]*)?主要会计数据')


def _label_pattern(label: str) -> str:
    return r'\s*'.join(map(re.escape, label))


def _column_layout(header: str, months: int) -> tuple[int, int, int | None, str] | None:
    compact = re.sub(r'\s+', '', header)
    if '本报告期' not in compact or '上年同期' not in compact or compact.find('本报告期') > compact.find('上年同期'):
        return None
    adjusted = '调整' in compact
    if months == 9:
        if ('年初至报告期末' not in compact
                or compact.find('上年同期') > compact.find('年初至报告期末')
                or '本报告期比上年同期' not in compact[:compact.find('年初至报告期末')]):
            return None
        if adjusted:
            # 已探针的8列：前四列单季度，后四列累计；比较列明确为调整后。
            if '调整前调整后调整后调整前调整后调整后' not in compact:
                return None
            return 8, 4, 6, 'previous_adjusted_after'
        return 4, 2, None, ''
    if months == 6 and not re.search(r'1[－—\-~～至]6月', compact):
        return None
    if adjusted:
        if '调整后调整前' not in compact or compact.count('调整后') != 1 or compact.count('调整前') != 1:
            return None
        return 4, 0, 1, 'previous_adjusted_after'
    return 3, 0, 1, 'previous_unadjusted'


def _header_unit(header: str) -> str | None:
    declarations = list(re.finditer(r'(?P<label>单\s*位|币\s*种)\s*[：:]', header))
    units: list[str] = []
    currencies: list[str] = []
    for index, declaration in enumerate(declarations):
        finish = declarations[index + 1].start() if index + 1 < len(declarations) else len(header)
        fragment = header[declaration.end():finish].strip()
        token = re.match(r'\S+', fragment)
        if token is None:
            return None
        label = re.sub(r'\s+', '', declaration['label'])
        (units if label == '单位' else currencies).append(token[0])
    # 不选择首个声明；相同重复可用，任何冲突/未知单位币种都保持待审。
    if (not units or not currencies or len(set(units)) != 1 or units[0] not in {'元', '万元', '百万元'}
            or any(currency != '人民币' for currency in currencies)):
        return None
    return units[0]


def _valid_tokens(tokens: list[str], width: int, months: int) -> bool:
    if len(tokens) != width:
        return False
    percent_columns = {3, 7} if width == 8 else ({1, 3} if months == 9 else {width - 1})
    non_target = 4 if width == 8 else (2 if months == 9 else 0)
    for index, token in enumerate(tokens):
        if token == '不适用':
            if index < non_target:
                continue
            return False
        pattern = _NUMBER + (r'%?' if index in percent_columns else '')
        if len(token) > 100 or re.fullmatch(pattern, token) is None:
            return False
    return True


def extract_interim_text(pages: list[str], *, symbol: str, source_url: str,
                         disclosed_on: str, as_of: date) -> dict[str, Any]:
    """仅解析明确列布局；Q3强制使用年初累计，未知结构保留待审。"""
    result = _base(symbol, source_url, disclosed_on)
    result.update(fiscal_year=None, period_months=None, period_end=None, period_basis='year_to_date')
    if not isinstance(pages, list) or any(not isinstance(page, str) for page in pages):
        result['reasons'].append('页面必须是原文字符串列表')
        return result
    selected = pages[:20]
    raw = '\f'.join(selected)
    result['text_sha256'] = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    cutoff, disclosed = _day(as_of), _day(disclosed_on)
    try:
        _symbol(symbol)
        _url(source_url)
    except StockResearchError as exc:
        result['reasons'].append(str(exc))
        return result
    identity = re.sub(r'\s+', '', ''.join(selected[:2]))
    codes = re.findall(r'(?:公司代码|证券代码)[：:]?(\d{6})', identity)
    titles = set(re.findall(r'(\d{4})年(第一季度|半年度|第三季度)报告', identity))
    if not codes or any(code != symbol for code in codes) or len(titles) != 1:
        result['reasons'].append('公司身份或季度/半年报告期标题缺失、冲突')
        return result
    year_text, period_label = next(iter(titles))
    year = int(year_text)
    months, end_day = _PERIODS[period_label]
    if year < 2:
        result['reasons'].append('报告年度无效')
        return result
    period_end = date(year, months, end_day)
    result.update(fiscal_year=year, period_months=months, period_end=period_end.isoformat())
    if (cutoff is None or disclosed is None or not isinstance(disclosed_on, str)
            or not period_end < disclosed <= cutoff):
        result['reasons'].append('报告期尚未结束、披露日期无效或晚于研究截点')
        return result
    if re.search(r'(?:更正|取消|撤销|修订|更新|补充|变更|延期|终止|英文|摘要|english|summary)', identity[:500], flags=re.IGNORECASE):
        result['reasons'].append('修订、摘要或特殊报告版本须人工核验')
        return result
    labels = {**_LABELS, '_ignored_profit_total': '利润总额', '_ignored_basic_eps': '基本每股收益',
              '_ignored_diluted_eps': '稀释每股收益', '_ignored_roe': '加权平均净资产收益率'}
    alternatives = '|'.join(f'(?P<m{index}>{_label_pattern(label)})' for index, label in enumerate(labels.values()))
    matcher = re.compile(alternatives)
    metrics = list(labels)
    anchors = []
    for anchor in _ANCHOR.finditer(raw):
        heading = re.sub(r'\s+', '', raw[anchor.start():].splitlines()[0])
        if re.fullmatch(r'(?:[（(][一二三四五六七八九十]+[)）])?主要会计数据(?:和财务指标|及财务指标)?', heading) is None:
            # 变动情况/原因说明只有比率和文字，不是第二张金额表。
            continue
        following = list(matcher.finditer(raw, anchor.end()))
        if not following:
            continue
        header = raw[anchor.end():following[0].start()]
        if re.search(r'单位[：:]', header) is None or re.search(r'币种[：:]', header) is None:
            continue
        numeric_rows = False
        for index, match in enumerate(following[:5]):
            end = following[index + 1].start() if index + 1 < len(following) else len(raw)
            tokens = raw[match.end():end].strip().split()
            if len(tokens) >= 2 and all(token == '不适用' or re.fullmatch(_NUMBER + r'%?', token) for token in tokens):
                numeric_rows = True
                break
        if numeric_rows:
            anchors.append(anchor)
    if not anchors:
        result['reasons'].append('未找到带单位和数值列的独立主要会计数据表')
        return result
    first_metric = matcher.search(raw, anchors[0].end())
    if first_metric is None:
        result['reasons'].append('主要会计数据缺少金额行')
        return result
    if any(anchor.start() > first_metric.start() for anchor in anchors):
        result['reasons'].append('存在多个主要会计数据表，不能选择有利版本')
        return result
    table_start = anchors[0].start()
    section = raw[table_start:]
    # 余额表和非经常性明细不得混入本期损益/现金流列。
    boundary = re.search(r'(?:本报告期末|主要财务指标|非经常性损益项目|总资产)', section)
    if boundary is not None and boundary.start() > first_metric.start() - table_start:
        section = section[:boundary.start()]
    matches = list(matcher.finditer(section))
    if not matches:
        result['reasons'].append('主要会计数据行缺失')
        return result
    header = section[:matches[0].start()]
    unit = _header_unit(header)
    layout = _column_layout(header, months)
    if unit is None or layout is None:
        result['reasons'].append('人民币单位或累计/调整列布局不受支持')
        return result
    width, current_index, previous_index, previous_basis = layout
    adjusted = previous_basis == 'previous_adjusted_after'
    result['comparison_requires_review'] = adjusted
    counts: dict[str, int] = {}
    candidates: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        metric = metrics[int(str(match.lastgroup)[1:])]
        if metric.startswith('_ignored'):
            continue
        counts[metric] = counts.get(metric, 0) + 1
        finish = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        body = section[match.end():finish].strip()
        tokens = body.split()
        # 数值行已在页末完整结束时，不把下一页页眉/页码并入该行；不完整的跨页数值仍待审。
        if '\f' in body:
            before_page_break = body.split('\f', 1)[0].strip()
            if _valid_tokens(before_page_break.split(), width, months):
                body = before_page_break
                tokens = body.split()
        if not _valid_tokens(tokens, width, months):
            result['reasons'].append(f'{_LABELS[metric]}金额列缺失、占位、格式或列数不支持')
            continue
        excerpt = (match.group() + section[match.end():finish][:section[match.end():finish].find(body)] + body).strip()
        selected_columns = [(year, current_index, 'current')]
        if previous_index is not None:
            selected_columns.append((year - 1, previous_index, previous_basis))
        page_number = raw[:table_start + match.start()].count('\f') + 1
        for fiscal_year, column, comparison_basis in selected_columns:
            proposal = {
                'symbol': symbol, 'fiscal_year': fiscal_year, 'period_months': months,
                'period_end': date(fiscal_year, months, end_day).isoformat(), 'period_basis': 'year_to_date',
                'comparison_basis': comparison_basis, 'metric': metric, 'amount_yuan': _amount(tokens[column], unit),
                'currency': 'CNY', 'scope': 'reported_parent' if 'parent' in metric else 'consolidated',
                'source_unit': unit, 'source_url': source_url, 'disclosed_on': disclosed_on,
                'page_number': page_number, 'evidence_excerpt': excerpt, 'raw_columns': tokens,
                'raw_comparison_values': tokens, 'comparison_requires_review': adjusted,
                'requires_review': True, 'financial_verified': False, 'rule_enabled': False, 'status': 'matched_template',
            }
            candidates.append(proposal)
    for metric in _LABELS:
        if counts.get(metric, 0) != 1:
            result['reasons'].append(f'{_LABELS[metric]}缺失或出现多次，待核验')
    result['financial_proposals'] = [candidate for candidate in candidates if counts[candidate['metric']] == 1]
    current_metrics = {candidate['metric'] for candidate in result['financial_proposals'] if candidate['comparison_basis'] == 'current'}
    if current_metrics == set(_LABELS) and not result['reasons']:
        result['status'] = 'matched_template'
    return result


def extract_archived_interims(archive_result: dict[str, Any], archive_dir: Path, as_of: date) -> dict[str, Any]:
    """复用年报PDF归档校验边界，仅替换文本解析器。"""
    return extract_archived_financials(archive_result, archive_dir, as_of, text_extractor=extract_interim_text)
