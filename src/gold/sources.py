"""黄金公开来源；请求、语义解析和原始口径在同一边界内完成。"""
from __future__ import annotations

import calendar
import importlib
import json
import logging
import math
import re
import zipfile
from datetime import date, datetime, timedelta
from html import unescape
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import requests

from gold.models import GoldBar, GoldObservation, GoldSeries, SourceKey

logger = logging.getLogger(__name__)


class SourceParseError(ValueError):
    """来源字段或数据语义不符合已核验口径。"""


class WorkbookReadError(ValueError):
    """工作簿容器或单元格读取失败，允许尝试可选读取器。"""


SOURCE_INFO: dict[str, tuple[str, str, str, str]] = {
    'domestic': ('Au99.99', '上海黄金交易所', 'https://www.sge.com.cn/graph/Dailyhq', '人民币元/克'),
    'international': ('国际黄金 XAU', '新浪 CFD 参考行情', 'https://finance.sina.com.cn/futures/quotes/XAU.shtml', '美元/金衡盎司'),
    'real_yield': ('美国10年期实际国债收益率', '美国财政部', 'https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml', '%'),
    'dollar': ('名义广义美元指数', '美联储 H.10', 'https://www.federalreserve.gov/releases/h10/summary/jrxwtfb_nb.htm', '指数'),
    'fx': ('美元兑人民币参考汇率', '美联储 H.10', 'https://www.federalreserve.gov/releases/h10/Hist/dat00_ch.htm', '人民币/美元'),
    'china_reserves': ('中国官方黄金储备数量', '国家外汇管理局', 'https://www.safe.gov.cn/safe/gfcbzc/', '万盎司'),
    'holdings': ('北美ETF资金样本（SPDR）', 'SPDR GLD Historical Archive', 'https://www.spdrgoldshares.com/usa/gld/', '吨'),
    'global_reserves': ('全球已披露官方黄金持有量', 'IMF IRFCL 候选', 'https://data.imf.org/en/Resource-Pages/IMF-API', '数量口径未验收'),
}


def empty_series(key: SourceKey, now: datetime, error: str) -> GoldSeries:
    name, source, url, unit = SOURCE_INFO[key]
    return GoldSeries(key=key, name=name, source=source, source_url=url, unit=unit,
                      frequency='monthly' if 'reserves' in key else 'daily',
                      fetched_at=now, status='unavailable', error=error)


def _number(value: Any, *, positive: bool = True) -> float:
    try:
        result = float(str(value).replace(',', '').strip())
    except (ValueError, TypeError) as exc:
        raise SourceParseError(f'数值无效: {value!r}') from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise SourceParseError('数值必须有限，数量和价格必须为正数')
    return result


def _day(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ('%Y-%m-%d', '%d-%b-%Y', '%d-%b-%y', '%m/%d/%Y'):
        try:
            return datetime.strptime(text[:10] if fmt == '%Y-%m-%d' else text, fmt).astimezone().date()
        except ValueError:
            continue
    raise SourceParseError(f'日期无效: {text!r}')


def _deduplicate(points: list[Any], field: str) -> list[Any]:
    unique: dict[date, Any] = {}
    for point in points:
        day = getattr(point, field)
        if day in unique and unique[day] != point:
            raise SourceParseError(f'同日重复数据冲突: {day}')
        unique[day] = point
    if not unique:
        raise SourceParseError('未找到有效数据，缺失不能填零')
    return [unique[day] for day in sorted(unique)]


def parse_sge(content: bytes, *, start: date | None = None) -> list[GoldBar]:
    try:
        rows = json.loads(content)['time']
        if start is not None:
            rows = [row for row in rows if _day(row[0]) >= start]
        bars = [GoldBar(trade_date=_day(row[0]), open=_number(row[1]), close=_number(row[2]),
                        low=_number(row[3]), high=_number(row[4])) for row in rows]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise SourceParseError(f'上金所日线无效: {exc}') from exc
    return _deduplicate(bars, 'trade_date')


def parse_sina(content: bytes, *, start: date | None = None) -> list[GoldBar]:
    text = content.decode('utf-8-sig')
    payload_start, end = text.find('(['), text.rfind('])')
    if payload_start < 0 or end < payload_start:
        raise SourceParseError('新浪 JSONP 包装不符合预期')
    try:
        rows = json.loads(text[payload_start + 1:end + 1])
        if start is not None:
            rows = [row for row in rows if _day(row['date']) >= start]
        bars = [GoldBar(trade_date=_day(row['date']), open=_number(row['open']), high=_number(row['high']),
                        low=_number(row['low']), close=_number(row['close'])) for row in rows]
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceParseError(f'新浪日线无效: {exc}') from exc
    return _deduplicate(bars, 'trade_date')


def parse_treasury(content: bytes) -> list[GoldObservation]:
    try:
        root = ET.fromstring(content)
        points = []
        for entry in root.iter():
            if entry.tag.rsplit('}', 1)[-1] != 'entry':
                continue
            fields = {node.tag.rsplit('}', 1)[-1]: node.text for node in entry.iter()}
            value = fields.get('TC_10YEAR')
            if value is None or not value.strip():
                continue
            points.append(GoldObservation(observation_date=_day(fields.get('NEW_DATE')),
                                          value=_number(value, positive=False)))
    except (ET.ParseError, ValueError) as exc:
        raise SourceParseError(f'财政部 XML 无效: {exc}') from exc
    return _deduplicate(points, 'observation_date')


class _Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == 'tr':
            self._row = []
        elif tag in ('td', 'th') and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ('td', 'th') and self._cell is not None and self._row is not None:
            self._row.append(' '.join(''.join(self._cell).split()))
            self._cell = None
        elif tag == 'tr' and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def _table_rows(content: bytes) -> list[list[str]]:
    parser = _Tables()
    parser.feed(content.decode('utf-8-sig'))
    return parser.rows


def parse_h10(content: bytes) -> list[GoldObservation]:
    points = []
    rows = _table_rows(content)
    # pre 格式仅作为官方旧表兼容；日期和值必须位于同一行。
    if not rows:
        rows = [line.split() for line in re.sub('<[^>]+>', '', content.decode()).splitlines()]
    for row in rows:
        if len(row) != 2:
            continue
        try:
            day = _day(row[0])
        except SourceParseError:
            continue
        if row[1].strip().upper() in ('ND', 'N/A', '.', ''):
            continue
        points.append(GoldObservation(observation_date=day, value=_number(row[1])))
    return _deduplicate(points, 'observation_date')


def parse_safe(content: bytes, year: int) -> list[GoldObservation]:
    rows = _table_rows(content)
    months: list[int] = []
    for row in rows:
        found = [int(match) for cell in row for match in re.findall(rf'{year}[.年/-](\d{{1,2}})', cell)]
        if found:
            months = found
            break
        found = [int(match) for cell in row for match in re.findall(r'^(\d{1,2})月$', cell)]
        if found:
            months = found
            break
    if not months or len(set(months)) != len(months) or any(month < 1 or month > 12 for month in months):
        raise SourceParseError('外管局月份表头缺失或冲突')
    candidates: list[list[float | None]] = []
    for row in rows:
        # 空月份必须占据原列位；不能压缩后与月份 zip，避免伪造连续月序列。
        quantities = [re.fullmatch(r'([\d,.]+)\s*万盎司', cell) for cell in row]
        labelled = bool(row and '黄金' in row[0] and '万盎司' in row[0])
        if not labelled and not any(quantities):
            continue
        cells = row if quantities[0] else row[1:]
        if len(cells) not in (len(months), 2 * len(months)):
            raise SourceParseError('外管局数量列无法与月份严格对应')
        values: list[float | None] = []
        for cell in cells:
            if cell.strip().upper() in ('', 'ND', 'N/A', '.', '-', '--'):
                values.append(None)
                continue
            match = re.fullmatch(r'([\d,.]+)\s*万盎司', cell)
            if not labelled and match is None:
                raise SourceParseError('外管局黄金数量单位缺失')
            values.append(_number(match.group(1) if match else cell))
        if len(values) == 2 * len(months):
            if any(values[index] != values[index + 1] for index in range(0, len(values), 2)):
                raise SourceParseError('外管局重复币种数量冲突')
            values = values[::2]
        candidates.append(values)
    if len(candidates) != 1:
        raise SourceParseError('外管局黄金数量行缺失或有多个候选')
    points = [GoldObservation(observation_date=date(year, month, calendar.monthrange(year, month)[1]), value=value)
              for month, value in zip(months, candidates[0], strict=True) if value is not None]
    return _deduplicate(points, 'observation_date')


def _xlsx_rows(content: bytes) -> list[list[list[Any]]]:
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            shared = []
            if 'xl/sharedStrings.xml' in archive.namelist():
                shared = [''.join(node.itertext()) for node in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
            workbook = ET.fromstring(archive.read('xl/workbook.xml'))
            # 1904日期系统必须由读取器一致转换，不能默认为1900。
            epoch = date(1899, 12, 30)
            for node in workbook.iter():
                if node.tag.endswith('workbookPr') and node.attrib.get('date1904') in ('1', 'true'):
                    epoch = date(1904, 1, 1)
            styles: set[int] = set()
            if 'xl/styles.xml' in archive.namelist():
                style_root = ET.fromstring(archive.read('xl/styles.xml'))
                formats = {int(node.attrib['numFmtId']): node.attrib.get('formatCode', '')
                           for node in style_root.iter() if node.tag.endswith('numFmt')}
                xfs = next((node for node in style_root if node.tag.endswith('cellXfs')), [])
                for index, node in enumerate(xfs):
                    number_id = int(node.attrib.get('numFmtId', 0))
                    code = formats.get(number_id, '').lower()
                    if 14 <= number_id <= 22 or ('y' in code and 'd' in code):
                        styles.add(index)
            result = []
            for name in archive.namelist():
                if not re.fullmatch(r'xl/worksheets/sheet\d+\.xml', name):
                    continue
                rows = []
                for row in ET.fromstring(archive.read(name)).iter():
                    if row.tag.rsplit('}', 1)[-1] != 'row':
                        continue
                    cells: list[Any] = []
                    for cell in row:
                        reference = cell.attrib.get('r', '')
                        column = 0
                        for character in re.sub(r'\d', '', reference):
                            column = column * 26 + ord(character) - 64
                        if not column:
                            raise WorkbookReadError('单元格列引用缺失')
                        while len(cells) < column:
                            cells.append(None)
                        node = next((child for child in cell if child.tag.rsplit('}', 1)[-1] in ('v', 'is')), None)
                        if node is None:
                            continue
                        value: Any = ''.join(node.itertext())
                        cell_type = cell.attrib.get('t')
                        if cell_type == 's':
                            value = shared[int(value)]
                        elif cell_type not in ('inlineStr', 'str', 'e'):
                            value = float(value)
                            if int(cell.attrib.get('s', 0)) in styles:
                                value = epoch + timedelta(days=value)
                        cells[column - 1] = value
                    rows.append(cells)
                result.append(rows)
            return result
    except (zipfile.BadZipFile, ET.ParseError, KeyError, IndexError, ValueError, OSError) as exc:
        raise WorkbookReadError(f'标准库工作簿读取失败: {exc}') from exc


def _openpyxl_rows(content: bytes) -> list[list[list[Any]]]:
    try:
        package = importlib.import_module('openpyxl')
        if not str(package.__version__).startswith('3.1.'):
            raise WorkbookReadError('可选后备读取器不可用：仅支持 openpyxl 3.1.x')
        workbook = package.load_workbook(BytesIO(content), read_only=True, data_only=True)
        try:
            return [[list(row) for row in sheet.iter_rows(values_only=True)] for sheet in workbook]
        finally:
            workbook.close()
    except (ImportError, OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        raise WorkbookReadError(f'可选后备读取器不可用: {exc}') from exc


def _holdings_points(sheets: list[list[list[Any]]]) -> list[GoldObservation]:
    candidates = []
    for rows in sheets:
        for index, row in enumerate(rows[:30]):
            headers = [str(value or '').strip().casefold() for value in row]
            if 'date' not in headers:
                continue
            units = [column for column, value in enumerate(headers) if value == 'tonnes of gold']
            if len(units) == 1:
                candidates.append((rows[index + 1:], headers.index('date'), units[0]))
    if len(candidates) != 1:
        raise SourceParseError('SPDR 日期与 Tonnes of Gold 语义表头缺失或存在多个冲突候选')
    rows, date_column, value_column = candidates[0]
    points = []
    for row in rows:
        if max(date_column, value_column) >= len(row) or row[date_column] is None:
            continue
        value = row[value_column]
        if value is None or str(value).strip().casefold() in ('us holiday', 'holiday', 'n/a', ''):
            continue
        points.append(GoldObservation(observation_date=_day(row[date_column]), value=_number(value)))
    return _deduplicate(points, 'observation_date')


def parse_spdr(content: bytes, *, reader: str = 'auto') -> tuple[list[GoldObservation], str, list[str]]:
    notes: list[str] = []
    if reader == 'openpyxl':
        try:
            sheets = _openpyxl_rows(content)
        except WorkbookReadError as exc:
            raise SourceParseError(str(exc)) from exc
        parser = 'openpyxl'
    else:
        try:
            sheets = _xlsx_rows(content)
            parser = 'stdlib'
        except WorkbookReadError as exc:
            notes.append(str(exc))
            logger.warning('SPDR主读取器失败，尝试同一字节的可选后备: %s', exc)
            try:
                sheets = _openpyxl_rows(content)
                parser = 'openpyxl'
            except WorkbookReadError as backup:
                raise SourceParseError(f'{exc}; {backup}') from backup
    # 语义检查置于读取降级边界之外，禁止使用后备绕过单位等校验。
    return _holdings_points(sheets), parser, notes


def _fetch(url: str, *, data: dict[str, str] | None = None) -> bytes:
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.sge.com.cn/sjzx/mrhq',
               'X-Requested-With': 'XMLHttpRequest'}
    response = requests.post(url, data=data, headers=headers, timeout=15) if data is not None else requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    return response.content


def _published(content: bytes) -> datetime | None:
    text = re.sub('<[^>]+>', ' ', content.decode('utf-8-sig'))
    match = re.search(r'Release Date:\s*(?:\w+,\s*)?([A-Z][a-z]+ \d{1,2}, \d{4})', text)
    if match:
        # H.10 页面只给日期；保留当地日期，不捏造具体发布时间。
        day = datetime.strptime(match.group(1), '%B %d, %Y').replace(tzinfo=ZoneInfo('America/New_York'))
        return day
    return None


def fetch_source(key: SourceKey, now_iso: str, history_dir: str | None = None, *, historical_cache_seconds: int = 2592000) -> dict[str, Any]:
    """在可终止进程中运行；整个来源的多请求也受父进程单源预算约束。"""
    now = datetime.fromisoformat(now_iso)
    cutoff = date(now.year - 5, now.month, min(now.day, calendar.monthrange(now.year - 5, now.month)[1])) - timedelta(days=30)
    result = empty_series(key, now, '')
    result.status, result.error = 'ok', None
    url = result.source_url
    if key == 'global_reserves':
        return empty_series(key, now, 'IMF 全球数量序列尚未通过接入验收；未自动扫描国家或以美元估值替代数量').model_dump(mode='json')
    if key == 'domestic':
        result.bars = parse_sge(_fetch(url, data={'instid': 'Au99.99'}), start=cutoff)
        result.parser = 'sge-json'
    elif key == 'international':
        endpoint = 'https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_XAU=/GlobalFuturesService.getGlobalFuturesDailyKLine?symbol=XAU'
        result.bars = parse_sina(_fetch(endpoint), start=cutoff)
        result.parser = 'sina-jsonp'
        result.notes.append('新浪页面标注CFD参考行情，非官方基准价')
        result.metadata['request_url'] = endpoint
    elif key == 'real_yield':
        points = []
        for year in range(now.year, now.year - 7, -1):
            path = Path(history_dir) / f'treasury-real-{year}.xml' if history_dir else None
            old_year = year < now.year
            if path and old_year and path.exists() and now.timestamp() - path.stat().st_mtime < historical_cache_seconds:
                content = path.read_bytes()
            else:
                content = _fetch(f'{url}?data=daily_treasury_real_yield_curve&field_tdr_date_value={year}')
                if year == now.year:
                    try:
                        root = ET.fromstring(content)
                    except ET.ParseError as exc:
                        raise SourceParseError('财政部当前年度 XML 无效') from exc
                    if (root.tag.rsplit('}', 1)[-1] == 'feed'
                            and not any(node.tag.rsplit('}', 1)[-1] == 'entry' for node in root.iter())):
                        result.notes.append('当前年度尚无日观察值，继续读取上一年度；仍按原观察日判断新鲜度')
                        continue
                parse_treasury(content)
                if path and old_year:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
            points.extend(parse_treasury(content))
        result.observations = _deduplicate(points, 'observation_date')
        result.parser = 'treasury-xml'
    elif key in ('dollar', 'fx'):
        content = _fetch(url)
        result.observations = parse_h10(content)
        result.published_at = _published(content)
        result.parser = 'fed-html'
        result.notes.append('H.10为日观察值、按周发布；发布日期仅精确到当地日期')
        result.metadata['published_precision'] = 'date'
        if key == 'fx':
            result.notes.append('纽约中午买入参考汇率，非黄金收盘时同步可成交价')
    elif key == 'china_reserves':
        directory = _fetch(url).decode('utf-8-sig')
        points = []
        pages = []
        for year in (now.year, now.year - 1):
            match = re.search(r'<a\b[^>]*href=[\"\x27]([^\"\x27]+)[\"\x27][^>]*>\s*官方储备资产[（(]' + str(year) + r'[）)]', directory, re.IGNORECASE)
            if match is None:
                raise SourceParseError(f'外管局目录未找到{year}年度官方储备资产')
            page_url = urljoin(url, unescape(match.group(1)))
            points.extend(parse_safe(_fetch(page_url), year))
            pages.append(page_url)
        result.observations = _deduplicate(points, 'observation_date')
        result.parser = 'safe-html'
        result.metadata['annual_pages'] = pages
        result.notes.append('仅中国官方数量，不代表全球央行需求；非美元估值')
    elif key == 'holdings':
        page = _fetch(url).decode('utf-8-sig')
        links = re.findall(r'href=[\"\x27]([^\"\x27]+historical-archive[^\"\x27]*)[\"\x27]', page, re.IGNORECASE)
        links = list(dict.fromkeys(unescape(link) for link in links if 'product=gld' in unescape(link)))
        if len(links) != 1:
            raise SourceParseError('SPDR官方下载链接缺失或冲突')
        endpoint = urljoin(url, links[0])
        if not endpoint.startswith('https://api.spdrgoldshares.com/'):
            raise SourceParseError('SPDR下载链接域名未经验证')
        result.observations, result.parser, notes = parse_spdr(_fetch(endpoint))
        result.notes.extend(notes)
        result.notes.append('北美ETF资金样本，不代表全球资金总体或持有人所在地')
        result.metadata['request_url'] = endpoint
    if key in ('real_yield', 'dollar', 'holdings'):
        cutoff -= timedelta(days=220)
    if key == 'china_reserves':
        cutoff = date(now.year - 1, 1, 1)
    if any(bar.trade_date > now.date() for bar in result.bars) or any(point.observation_date > now.date() for point in result.observations):
        raise SourceParseError('来源包含未来观察日期，拒绝当前序列')
    local = now.astimezone(ZoneInfo('Asia/Shanghai'))
    if key == 'international':
        # 提供商未给可信的闭合时区；纽约日期至少滞后两个自然日，避免跨上海午夜放行盘中条目。
        completed_through = now.astimezone(ZoneInfo('America/New_York')).date() - timedelta(days=2)
        result.bars = [bar for bar in result.bars if bar.trade_date <= completed_through]
        result.notes.append('提供商日线闭合时刻未核验；保守排除纽约当前日和前一日，金价参考可能额外滞后一日')
        result.metadata['completed_daily_policy'] = 'new_york_date_minus_two'
    elif key == 'domestic' and (local.hour, local.minute) < (15, 30):
        # 参考行情未提供可信的当日闭合标记；宁可延后一日，不能把盘中值称为收盘。
        result.bars = [bar for bar in result.bars if bar.trade_date < local.date()]
        result.notes.append('已排除可能未完成的当日日线；技术指标仅使用已完成日线参考值')
        result.metadata['completed_daily_policy'] = 'sge_after_1530_shanghai'
    result.bars = [bar for bar in result.bars if bar.trade_date >= cutoff]
    result.observations = [point for point in result.observations if point.observation_date >= cutoff]
    if not result.bars and not result.observations:
        raise SourceParseError('来源没有所需历史区间内的有效值')
    return result.model_dump(mode='json')
