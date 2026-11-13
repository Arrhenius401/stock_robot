"""补全接口的唯一数据提供层：复用现有公告归档与成交额来源。"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from radar.stocks.announcement_discovery import discover_announcements
from radar.stocks.financial_extract import extract_cashflow_text
from radar.stocks.source_archive import collect_documents
from radar.stocks.turnover_source import (
    collect_turnover_payload,
    load_turnover_source,
    verify_turnover_payload,
)
from stock_robot.dividend_materials import DEFAULT_HOSTS


def annual_archive_provider(symbol: str, name: str | None, years: list[int], as_of: date,
                            workspace: Path) -> dict[int, dict[str, Any]]:
    """发现→归档→抽取：不手动下载 PDF，不绕过 annual_batch 的归档层。"""
    company = {'symbol': symbol, 'name': name or symbol}
    discovery = discover_announcements([company], as_of, Path(workspace) / 'discovery', mode='annual_report')
    documents = discovery.get('documents') if isinstance(discovery, dict) else None
    selected = [row for row in documents or [] if isinstance(row, dict) and row.get('fiscal_year') in years]
    archive_dir = Path(workspace) / 'archive'
    archived = collect_documents(selected, archive_dir, allowed_hosts=set(DEFAULT_HOSTS))
    result: dict[int, dict[str, Any]] = {}
    for item in archived.get('items', []):
        if not isinstance(item, dict) or item.get('status') not in {'downloaded', 'cached'}:
            continue
        year = item.get('fiscal_year')
        if type(year) is not int:
            continue
        path = archive_dir / str(item.get('file'))
        if path.is_symlink() or not path.is_file() or path.resolve().parent != archive_dir.resolve():
            continue
        text = '\n'.join(page.extract_text() or '' for page in PdfReader(str(path)).pages)
        parsed = extract_cashflow_text(text)
        compact = re.sub(r'\s+', '', text)
        headers = [compact[max(0, match.start() - 120):match.start() + 500]
                   for match in re.finditer('合并现金流量表', compact)]
        header = next((candidate for candidate in headers if '人民币' in candidate
                       and re.search(r'单位[:：]?(?:人民币)?元(?:[^万亿]|$)', candidate)), '')
        parsed['source'] = {'symbol': symbol, 'fiscal_year': year, 'disclosed_on': item.get('disclosed_on'),
                            'source_url': item.get('source_url'), 'content_sha256': item.get('sha256'),
                            'currency': 'CNY' if '人民币' in header else None,
                            'unit': 'CNY_yuan' if re.search(r'单位[:：]?(?:人民币)?元(?:[^万亿]|$)', header) else None}
        if year in result:
            raise ValueError('同年度年报归档存在多份原文，无法唯一补全')
        result[year] = parsed
    return result


def turnover_source_provider(db: Path, cache_dir: Path | None = None):
    """优先现有任务来源；缺失时调用与首轮相同的成交额入口并复核。"""
    def fetch(symbol: str, as_of: date) -> dict[str, Any] | None:
        source = load_turnover_source(db, symbol, as_of)
        if source.get('status') == 'current':
            payload = source.get('payload')
            return payload if isinstance(payload, dict) else None
        from radar.stocks.update_service import load_market_window
        days, trace = load_market_window(db.parent, as_of)
        calendar = trace.get('calendar') if isinstance(trace, dict) else None
        if days is None or not isinstance(calendar, dict) or len(days) != 60:
            return None
        root = cache_dir or db.parent / 'stock-updates' / 'cache'
        payload = collect_turnover_payload(symbol, as_of, root, days, calendar)
        return verify_turnover_payload(payload, symbol, as_of)
    return fetch
