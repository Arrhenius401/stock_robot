"""恒生 HSICS 行业快照 — 东财 F10 二级标签 + HSCI 每日成分 PDF 校验。

只处理港股通股票，不写入申万映射，也不把港股与 A 股同行池混合。
"""
import argparse
import csv
import hashlib
import io
import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests
from filelock import FileLock
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from pypdf import PdfReader

from utils.paths import (
    hk_hsics_industry_path,
    hk_hsics_snapshot_path,
    hk_stock_connect_path,
)

logger = logging.getLogger(__name__)

F10_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
F10_COLUMNS = "SECUCODE,SECURITY_CODE,ORG_NAME,BELONG_INDUSTRY,INDUSTRY_TYPE"
F10_REPORT = "RPT_HKF10_INFO_ORGPROFILE"
HSI_DAILY_URL = (
    "https://www.hsi.com.hk/api/wsit-hsi-ddoc-ea-public-website-proxy/"
    "v1/product-data/pub/series/metadata/v2?data=dailyReport&seriesCode=hsci"
)
HSI_DOWNLOAD_URL = (
    "https://www.hsi.com.hk/api/wsit-hsi-ddoc-ea-public-website-proxy/"
    "v1/content/gcs/download"
)
HSI_COUNTS_URL = (
    "https://www.hsi.com.hk/api/wsit-hsi-ddoc-ea-public-website-proxy/"
    "v1/product-data/pub/constituent/metadata/v2?data=rt&seriesCode=industry&language=en-hk"
)
CSV_COLUMNS = ["symbol", "hsics_sector", "hsics_industry", "mapping_status", "in_hsci"]
EXPECTED_SECTOR_COUNT = 31
EXPECTED_INDUSTRY_COUNT = 12


class HsicsIndustryError(RuntimeError):
    """恒生行业快照构建/读取失败。"""


@dataclass(frozen=True)
class F10Profile:
    symbol: str
    name: str
    sector: str
    gics: str


@dataclass(frozen=True)
class HsciPdfMember:
    symbol: str
    name: str
    industry_code: str
    industry: str


@dataclass(frozen=True)
class HsicsMember:
    symbol: str
    sector: str
    industry: str
    status: str
    in_hsci: bool


class HsicsSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    source: str
    source_urls: dict[str, str]
    data_date: date
    fetched_at: AwareDatetime
    csv_sha256: str = ""
    total: int = Field(ge=0, strict=True)
    sector_count: int = Field(ge=0, strict=True)
    industry_count: int = Field(ge=0, strict=True)
    crosswalk: dict[str, str]
    validation: dict[str, Any] = Field(default_factory=dict)


def canonical_industry(value: str) -> str:
    english = "".join(char for char in value if char.isascii()).strip()
    english = english.replace("&", "and")
    english = re.sub(r"\s+", " ", english)
    return english.replace("Telecommunicati ons", "Telecommunications")


def parse_hsci_pdf_text(text: str) -> dict[str, HsciPdfMember]:
    """解析 HSCI 每日成分 PDF 文本；重复代码保留第一次出现。"""
    normalized = re.sub(r"\s+", " ", text)
    pattern = re.compile(
        r"\b(\d{4,5})\.HK\s+(.+?)\s+Hong Kong 香港\s+(\d{1,3})\s+(.+?)\s+"
        r"(?:HKD|USD|CNY|JPY|EUR|GBP)\s+"
    )
    members: dict[str, HsciPdfMember] = {}
    for match in pattern.finditer(normalized):
        code = match.group(1).zfill(5)
        industry = canonical_industry(match.group(4))
        if not industry:
            raise HsicsIndustryError("HSCI 成分行缺少行业名称")
        members.setdefault(code, HsciPdfMember(
            symbol=code,
            name=match.group(2).strip(),
            industry_code=match.group(3),
            industry=industry,
        ))
    if not members:
        raise HsicsIndustryError("HSCI 成分 PDF 未解析到任何成员")
    return members


def build_crosswalk(pairs: Mapping[str, str] | list[tuple[str, str]]) -> dict[str, str]:
    values = pairs.items() if isinstance(pairs, Mapping) else pairs
    grouped: dict[str, set[str]] = {}
    for sector, industry in values:
        grouped.setdefault(sector, set()).add(industry)
    for sector, industries in grouped.items():
        if len(industries) > 1:
            raise HsicsIndustryError(
                f"二级 {sector} 对应多个一级行业: {sorted(industries)}")
    return {sector: next(iter(industries)) for sector, industries in grouped.items()}


def build_members(f10: Mapping[str, F10Profile], pdf: Mapping[str, HsciPdfMember],
                  crosswalk: Mapping[str, str], connect: set[str]) -> list[HsicsMember]:
    members: list[HsicsMember] = []
    for code in sorted(connect):
        profile = f10.get(code)
        sector = profile.sector.strip() if profile else ""
        industry = crosswalk.get(sector, "")
        in_hsci = code in pdf
        expected = pdf[code].industry if in_hsci else ""
        if not sector or not industry or (expected and expected != industry):
            members.append(HsicsMember(
                symbol=code + ".HK", sector=sector, industry="",
                status="missing", in_hsci=in_hsci,
            ))
            continue
        members.append(HsicsMember(
            symbol=code + ".HK", sector=sector, industry=industry,
            status="mapped", in_hsci=in_hsci,
        ))
    return members


def build_snapshot(rows: list[HsicsMember], crosswalk: Mapping[str, str], *,
                   data_date: date, fetched_at: datetime,
                   source_urls: Mapping[str, str],
                   validation: Mapping[str, Any] | None = None) -> HsicsSnapshot:
    mapped = [row for row in rows if row.status == "mapped"]
    return HsicsSnapshot(
        source="eastmoney.f10 + hsi.daily_pdf",
        source_urls=dict(source_urls),
        data_date=data_date,
        fetched_at=fetched_at,
        total=len(rows),
        sector_count=len({row.sector for row in mapped}),
        industry_count=len({row.industry for row in mapped}),
        crosswalk=dict(crosswalk),
        validation=dict(validation or {}),
    )


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def publish_snapshot(snapshot: HsicsSnapshot, rows: list[HsicsMember],
                     csv_path: Path, json_path: Path) -> HsicsSnapshot:
    rows = sorted(rows, key=lambda row: row.symbol)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({
            "symbol": row.symbol,
            "hsics_sector": row.sector,
            "hsics_industry": row.industry,
            "mapping_status": row.status,
            "in_hsci": "true" if row.in_hsci else "false",
        })
    csv_bytes = buffer.getvalue().encode("utf-8")
    digest = hashlib.sha256(csv_bytes).hexdigest()
    published = snapshot.model_copy(update={"csv_sha256": digest})
    json_bytes = published.model_dump_json(indent=2).encode("utf-8")

    old_csv = csv_path.read_bytes() if csv_path.exists() else None
    old_json = json_path.read_bytes() if json_path.exists() else None
    with FileLock(str(csv_path) + ".lock", timeout=10):
        _atomic_write(csv_path, csv_bytes)
        try:
            _atomic_write(json_path, json_bytes)
        except OSError:
            if old_csv is not None:
                _atomic_write(csv_path, old_csv)
            elif csv_path.exists():
                csv_path.unlink()
            if old_json is not None:
                _atomic_write(json_path, old_json)
            elif json_path.exists():
                json_path.unlink()
            raise
    return published


def load_members(csv_path: Path, json_path: Path) -> dict[str, HsicsMember]:
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HsicsIndustryError("恒生行业快照元数据不可读") from exc
    actual = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    if payload.get("csv_sha256") != actual:
        raise HsicsIndustryError("恒生行业 CSV 哈希与元数据不一致")
    members: dict[str, HsicsMember] = {}
    with csv_path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != CSV_COLUMNS:
            raise HsicsIndustryError("恒生行业 CSV 列结构不符合预期")
        for row in reader:
            symbol = row["symbol"]
            if symbol in members:
                raise HsicsIndustryError(f"恒生行业 CSV 存在重复代码: {symbol}")
            members[symbol] = HsicsMember(
                symbol=symbol,
                sector=row["hsics_sector"],
                industry=row["hsics_industry"],
                status=row["mapping_status"],
                in_hsci=row["in_hsci"].lower() == "true",
            )
    return members


def latest_daily_pdf(payload: Any) -> tuple[str, str]:
    series = payload if isinstance(payload, list) else [payload]
    candidates: list[tuple[str, str]] = []
    for entry in series:
        if not isinstance(entry, dict):
            continue
        for report in entry.get("reportList") or []:
            if not isinstance(report, dict) or report.get("reportType") != "con-pdf-1":
                continue
            for item in report.get("reportDate") or []:
                if isinstance(item, dict) and item.get("url") and item.get("date"):
                    candidates.append((str(item["url"]), str(item["date"])))
    if not candidates:
        raise HsicsIndustryError("恒生每日成分报告不存在")
    return max(candidates, key=lambda item: item[1])


def validate_baseline(members: Mapping[str, HsciPdfMember],
                      counts: Mapping[str, int]) -> dict[str, Any]:
    actual: dict[str, int] = {}
    for member in members.values():
        actual[member.industry] = actual.get(member.industry, 0) + 1
    if actual != dict(counts):
        raise HsicsIndustryError(
            f"HSCI 行业数量与官方不一致: {actual} != {dict(counts)}")
    return {"total": len(members), "industry_counts": actual}


def fetch_hsci_counts(session: requests.Session) -> dict[str, int]:
    response = session.get(HSI_COUNTS_URL, timeout=(8, 25))
    response.raise_for_status()
    groups = ((response.json() or {}).get("data") or {}).get("numOfConsList") or []
    counts: dict[str, int] = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        name = str(group.get("indexName") or "")
        name = name.removeprefix("Hang Seng Composite Industry Index - ")
        name = canonical_industry(name)
        if name:
            counts[name] = int(group.get("count") or 0)
    if not counts:
        raise HsicsIndustryError("HSCI 官方行业数量不可用")
    return counts


def fetch_f10_sectors(session: requests.Session,
                      symbols: set[str]) -> dict[str, F10Profile]:
    profiles: dict[str, F10Profile] = {}
    for index, symbol in enumerate(sorted(symbols), 1):
        for attempt in range(3):
            try:
                profile = fetch_f10_profile(session, symbol)
                profiles[profile.symbol] = profile
                break
            except requests.RequestException as exc:
                if attempt == 2:
                    raise HsicsIndustryError(
                        f"东财 F10 抓取失败: {symbol}") from exc
                time.sleep(1.5 * (attempt + 1))
        if index % 25 == 0:
            print(f"F10 progress {index}/{len(symbols)}", flush=True)
        time.sleep(0.25)
    return profiles


def fetch_hsci_pdf(session: requests.Session) -> tuple[bytes, str]:
    response = session.get(HSI_DAILY_URL, timeout=(8, 25))
    response.raise_for_status()
    resource, data_date = latest_daily_pdf(response.json())
    download = session.get(HSI_DOWNLOAD_URL, params={"resource": resource},
                           timeout=(8, 90))
    download.raise_for_status()
    return download.content, data_date


def parse_hsci_pdf_bytes(data: bytes) -> dict[str, HsciPdfMember]:
    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise HsicsIndustryError("HSCI 成分 PDF 无法解析") from exc
    return parse_hsci_pdf_text(text)


def refresh(csv_path: Path, json_path: Path, *,
            connect_symbols: set[str],
            session: requests.Session | None = None,
            strict_counts: bool = True) -> HsicsSnapshot:
    """抓取并发布 HSICS 快照；任何结构冲突都不覆盖旧文件。"""
    session = session or requests.Session()
    f10 = fetch_f10_sectors(session, connect_symbols)
    pdf_bytes, pdf_date = fetch_hsci_pdf(session)
    pdf_members = parse_hsci_pdf_bytes(pdf_bytes)
    counts = fetch_hsci_counts(session)
    baseline = validate_baseline(pdf_members, counts)

    pairs = [
        (f10[code].sector, pdf_members[code].industry)
        for code in sorted(connect_symbols)
        if code in f10 and code in pdf_members and f10[code].sector
    ]
    crosswalk = build_crosswalk(pairs)
    sectors = {profile.sector for profile in f10.values() if profile.sector}
    unmapped = sorted(sectors - set(crosswalk))
    if unmapped:
        raise HsicsIndustryError(f"存在未映射的二级行业: {unmapped}")
    if strict_counts and len(crosswalk) != EXPECTED_SECTOR_COUNT:
        raise HsicsIndustryError(f"二级行业数量不符合预期: {len(crosswalk)}")
    industries = {crosswalk[sector] for sector in crosswalk}
    if strict_counts and len(industries) != EXPECTED_INDUSTRY_COUNT:
        raise HsicsIndustryError(f"一级行业数量不符合预期: {len(industries)}")

    mismatches = [
        code for code in sorted(connect_symbols)
        if code in f10 and code in pdf_members and f10[code].sector
        and crosswalk.get(f10[code].sector) != pdf_members[code].industry
    ]
    if mismatches:
        raise HsicsIndustryError(f"二级到一级映射与 HSCI 不一致: {mismatches[:5]}")

    rows = build_members(f10, pdf_members, crosswalk, connect_symbols)
    missing = sum(1 for row in rows if row.status != "mapped")
    if missing:
        raise HsicsIndustryError(f"存在 {missing} 只港股通缺少行业，拒绝覆盖旧快照")
    validation = {
        "baseline_total": baseline["total"],
        "parent_mismatches": len(mismatches),
        "unmapped_sectors": len(unmapped),
        "missing_rows": missing,
        "non_hsci_covered": sum(
            1 for row in rows if not row.in_hsci and row.status == "mapped"),
    }
    snapshot = build_snapshot(
        rows, crosswalk,
        data_date=date.fromisoformat(pdf_date),
        fetched_at=datetime.now().astimezone(),
        source_urls={"f10": F10_URL, "hsci": HSI_DAILY_URL},
        validation=validation,
    )
    return publish_snapshot(snapshot, rows, csv_path, json_path)


def load_hsics_members(csv_path: Path | None = None,
                       json_path: Path | None = None) -> dict[str, HsicsMember]:
    csv_path = csv_path or hk_hsics_industry_path()
    json_path = json_path or hk_hsics_snapshot_path()
    if not csv_path.exists() or not json_path.exists():
        return {}
    return load_members(csv_path, json_path)


def load_connect_symbols(path: Path | None = None) -> set[str]:
    path = path or hk_stock_connect_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HsicsIndustryError("港股通成员快照不可读") from exc
    members = payload.get("members") if isinstance(payload, dict) else None
    if not isinstance(members, list) or not members:
        raise HsicsIndustryError("港股通成员快照没有成员")
    symbols: set[str] = set()
    for member in members:
        symbol = str(member.get("symbol") or "") if isinstance(member, dict) else ""
        if not re.fullmatch(r"\d{5}\.HK", symbol):
            raise HsicsIndustryError(f"港股通成员代码格式错误: {symbol!r}")
        symbols.add(symbol.removesuffix(".HK"))
    return symbols


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="手动更新恒生 HSICS 港股通行业快照")
    parser.add_argument("--csv", type=Path, help="行业映射 CSV 路径")
    parser.add_argument("--json", type=Path, help="快照元数据 JSON 路径")
    args = parser.parse_args(argv)
    csv_path = args.csv or hk_hsics_industry_path(writable=True)
    json_path = args.json or hk_hsics_snapshot_path(writable=True)
    try:
        symbols = load_connect_symbols()
        snapshot = refresh(csv_path, json_path, connect_symbols=symbols)
    except (HsicsIndustryError, OSError, ValueError) as exc:
        logger.exception("恒生 HSICS 快照更新失败，旧文件保留")
        print(f"更新失败: {exc}")
        return 1
    print(json.dumps({
        "total": snapshot.total,
        "sectors": snapshot.sector_count,
        "industries": snapshot.industry_count,
        "data_date": snapshot.data_date.isoformat(),
        "fetched_at": snapshot.fetched_at.isoformat(),
        "csv": str(csv_path),
        "json": str(json_path),
    }, ensure_ascii=False))
    return 0


def fetch_f10_profile(session: requests.Session, symbol: str) -> F10Profile:
    secucode = symbol if symbol.endswith(".HK") else symbol.zfill(5) + ".HK"
    params = {
        "reportName": F10_REPORT,
        "columns": F10_COLUMNS,
        "quoteColumns": "",
        "filter": f'(SECUCODE="{secucode}")',
        "pageNumber": "1",
        "pageSize": "200",
        "sortTypes": "",
        "sortColumns": "",
        "source": "F10",
        "client": "PC",
        "v": "04748497219912483",
    }
    response = session.get(F10_URL, params=params, timeout=(8, 25))
    response.raise_for_status()
    data = ((response.json() or {}).get("result") or {}).get("data") or []
    row = data[0] if data else {}
    sector = str(row.get("BELONG_INDUSTRY") or "").strip()
    if not sector:
        raise HsicsIndustryError(f"东财 F10 缺少行业: {secucode}")
    return F10Profile(
        symbol=secucode.removesuffix(".HK"),
        name=str(row.get("ORG_NAME") or ""),
        sector=sector,
        gics=str(row.get("INDUSTRY_TYPE") or ""),
    )


if __name__ == "__main__":
    raise SystemExit(main())
