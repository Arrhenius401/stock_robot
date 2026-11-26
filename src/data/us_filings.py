"""SEC submissions 公告，正文失败保留可追溯元数据。"""
import json
import logging
import re
from datetime import date
from io import BytesIO
from typing import Any
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from data.us_sec import USER_AGENT, _source_cache

logger = logging.getLogger(__name__)


def resolve_cik(ticker: str) -> int:
    cache = _source_cache()
    saved = cache.get("sec_tickers", "all", "v1")
    if saved is None:
        response = requests.get("https://www.sec.gov/files/company_tickers.json",
                                headers={"User-Agent": USER_AGENT}, timeout=10)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("SEC ticker 映射格式异常")
        cache.put("sec_tickers", "all", "v1", json.dumps(payload), ttl_seconds=30*86400)
    else:
        payload = json.loads(saved)
    symbol = ticker.upper().replace(".", "-")
    for row in payload.values():
        if row.get("ticker", "").upper().replace(".", "-") == symbol:
            return int(row["cik_str"])
    raise ValueError(f"SEC 未找到 CIK: {ticker}")


def parse_submissions(payload: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    recent = payload.get("filings", {}).get("recent", {})
    cik = int(payload["cik"])
    rows = []
    for form, filed, accession, document in zip(
        *(recent.get(key, []) for key in ("form", "filingDate", "accessionNumber", "primaryDocument")),
        strict=False,
    ):
        if form not in {"8-K", "10-Q"} or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", str(accession)):
            continue
        try:
            date.fromisoformat(filed)
        except (ValueError, TypeError):
            continue
        if not document or "/" in document or "\\" in document or document in {".", ".."}:
            continue
        rows.append({"form": form, "filingDate": filed, "accessionNumber": accession,
                     "title": f"SEC {form} ({filed})", "source": "SEC EDGAR",
                     "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{quote(document)}",
                     "content": ""})
    return sorted(rows, key=lambda row: row["filingDate"], reverse=True)[:max(0, limit)]


def fetch_filings(ticker: str) -> list[dict[str, Any]]:
    cik = resolve_cik(ticker)
    response = requests.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json",
                            headers={"User-Agent": USER_AGENT}, timeout=10)
    response.raise_for_status()
    rows = parse_submissions(response.json())
    for row in rows:
        try:
            body = requests.get(row["url"], headers={"User-Agent": USER_AGENT}, timeout=10)
            body.raise_for_status()
            if row["url"].lower().endswith(".pdf"):
                from pypdf import PdfReader

                text = ""
                for page in PdfReader(BytesIO(body.content)).pages:
                    text += page.extract_text() or ""
                    if len(text) >= 1000:
                        break
            else:
                soup = BeautifulSoup(body.content, "html.parser")
                for tag in soup(["script", "style"]):
                    tag.decompose()
                text = soup.get_text(" ", strip=True)
            row["content"] = text[:1000]
        except Exception as error:  # noqa: BLE001 — 第三方正文解析隔离，元数据仍可展示
            logger.warning("SEC 正文失败 %s: %s", row["url"], type(error).__name__)
    return rows
