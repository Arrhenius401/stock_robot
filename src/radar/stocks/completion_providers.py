"""补全接口的唯一数据提供层：复用现有公告归档与成交额来源。"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from radar.stocks.announcement_discovery import discover_announcements
from radar.stocks.financial_extract import extract_cashflow_text
from radar.stocks.source_archive import collect_documents
from radar.stocks.turnover_source import load_turnover_source
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
        result[year] = extract_cashflow_text(text)
    return result


def turnover_source_provider(db: Path):
    """只返回现有 turnover_source 已校验的 payload；不新建东财日线通道。"""
    def fetch(symbol: str, as_of: date) -> dict[str, Any] | None:
        source = load_turnover_source(db, symbol, as_of)
        if source.get('status') != 'current':
            return None
        payload = source.get('payload')
        return payload if isinstance(payload, dict) else None
    return fetch
