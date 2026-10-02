"""官方指数单张月末估值；快照不会扩充日频历史。"""
import calendar
import re
from datetime import date
from io import BytesIO
from itertools import pairwise

import requests
from pypdf import PdfReader

from index.strategy_data import finite_number


def _positive(value: str | None) -> float | None:
    number = finite_number(value)
    return number if number is not None and number > 0 else None


def parse_factsheet_text(text: str, symbol: str, provider: str, end: date) -> dict:
    """只认明确代码、报告日期与同一行标签；列错位时保留缺失。"""
    reported_code = None
    lines = text.splitlines()
    for position, line in enumerate(lines):
        if "指数代码" not in line:
            continue
        after_label = line.split("指数代码", 1)[1]
        match = re.search(r"(?<![A-Za-z0-9])([A-Z0-9]{6})(?![A-Za-z0-9])", after_label)
        if match:
            reported_code = match[1]
        elif position + 1 < len(lines):
            # 中证布局可将全称、代码放在表头紧随的一行；不扫描后续正文。
            codes = re.findall(r"(?<![A-Za-z0-9])([A-Z0-9]{6})(?![A-Za-z0-9])", lines[position + 1])
            if len(codes) == 1:
                reported_code = codes[0]
        break
    if reported_code != symbol:
        raise ValueError("官方单张指数代码字段与目标不符")
    dates = []
    for match in re.finditer(r"(20\d{2})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?", text):
        year, month = int(match[1]), int(match[2])
        day = int(match[3]) if match[3] else calendar.monthrange(year, month)[1]
        dates.append(date(year, month, day))
    if not dates:
        raise ValueError("官方单张缺少报告日期")
    as_of = max(dates)
    if as_of > end or (end - as_of).days > 62:
        raise ValueError("官方单张日期超前或超过两个月，不能作为当前估值")
    if provider == "cni":
        match = re.search(r"估值数据\s+PE\s+PB\s+ROE\s+([-+\d.]+|--)\s+([-+\d.]+|--)", text)
        pe, pb = (_positive(match[1]), _positive(match[2])) if match else (None, None)
        if match is None:
            # 布局模式下行业表与估值表同排，按表头列位置跳过左侧行业数字。
            lines = text.splitlines()
            for header, values in pairwise(lines):
                heading = re.search(r"\bPE\s+PB\s+ROE\b", header)
                if heading is None:
                    continue
                cells = values[max(0, heading.start() - 3):].split()
                if len(cells) >= 3:
                    pe, pb = _positive(cells[0]), _positive(cells[1])
                break
        pe_basis = "官方月度单张PE（未声明TTM）"
        pb_basis = "官方月度单张PB"
    else:
        def value(label: str) -> float | None:
            match = re.search(rf"{label}[ \t]+([-+\d.]+|--)(?=[ \t\r\n%]|$)", text)
            return _positive(match[1]) if match else None
        pe, pb = value("滚动市盈率"), value("市净率")
        pe_basis = "官方月末单张滚动PE（计算用股本）"
        pb_basis = "官方月末单张PB（计算用股本）"
    return {"symbol": symbol, "as_of": as_of, "pb": pb, "pe_snapshot": pe,
            "pe_basis": pe_basis, "pb_basis": pb_basis}


def fetch_factsheet(symbol: str, provider: str, end: date, timeout: float = 12) -> dict:
    """读取目录核验的官方单张；失败由上层按源隔离并记录。"""
    from data.index_mapping import IndexMapping
    entry = IndexMapping().lookup(symbol)
    if entry is None or not entry.source_url:
        raise ValueError("指数目录缺少官方单张地址")
    response = requests.get(entry.source_url, timeout=timeout)
    response.raise_for_status()
    reader = PdfReader(BytesIO(response.content))
    text = "\n".join(page.extract_text(extraction_mode="layout") for page in reader.pages)
    result = parse_factsheet_text(text, symbol, provider, end)
    result["source_url"] = entry.source_url
    return result
