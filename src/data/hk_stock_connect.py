"""手动采集完整港股通名单（上交所官方优先，东财可选）；成员资格不等同于申万行业归属。"""
import argparse
import csv
import io
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal, cast

import requests
from filelock import FileLock, Timeout
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from data.industry_mapping_builder import MAPPING_COLUMNS
from utils.paths import industry_mapping_path

logger = logging.getLogger(__name__)
SOURCE = "ak.stock_hk_ggt_components_em"
SOURCE_URL = "https://quote.eastmoney.com/center/gridlist.html#hk_components"
SSE_SOURCE = "sse.hkexsc.eligible"
SSE_URL = "https://www.sse.com.cn/services/hkexsc/disclo/eligible/"
SSE_ENDPOINT = "https://query.sse.com.cn/commonQuery.do"
SSE_SQL_ID = "COMMON_SSE_JYFW_HGT_XXPL_BDZQQD_L"
SSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": SSE_URL,
    "Accept": "*/*",
}
SSE_FORM = {
    "sqlId": SSE_SQL_ID,
    "isPagination": "true",
    "pageHelp.pageSize": "10000",
    "pageHelp.pageNo": "1",
    "pageHelp.beginPage": "1",
    "pageHelp.cacheSize": "1",
    "pageHelp.endPage": "1",
    "keyword": "",
}
MAX_PAGES = 20
SnapshotSource = Literal["sse", "eastmoney"]


class HKStockConnectError(RuntimeError):
    """名单采集失败，不能据此清空已有成员。"""


class HKConnectMember(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    symbol: str = Field(pattern=r"^[0-9]{5}\.HK$")
    name: str = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("股票名称为空")
        return value.strip()


class HKConnectPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    page: int = Field(ge=1, strict=True)
    total: int = Field(ge=1, strict=True)
    count: int = Field(ge=1, strict=True)


class HKStockConnectSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    source: Literal["ak.stock_hk_ggt_components_em", "sse.hkexsc.eligible"] = SOURCE
    source_url: Literal[
        "https://quote.eastmoney.com/center/gridlist.html#hk_components",
        "https://www.sse.com.cn/services/hkexsc/disclo/eligible/",
    ] = SOURCE_URL
    data_date: date | None = None
    fetched_at: AwareDatetime
    total: int = Field(ge=1, strict=True)
    pages: tuple[HKConnectPage, ...] = Field(min_length=1)
    members: tuple[HKConnectMember, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_source_metadata(self):
        expected = {SOURCE: SOURCE_URL, SSE_SOURCE: SSE_URL}
        if expected.get(self.source) != self.source_url:
            raise ValueError("名单来源与来源页面不匹配")
        if (self.source == SSE_SOURCE) != (self.data_date is not None):
            raise ValueError("上交所名单必须带数据日期，东财名单不带独立数据日期")
        return self

    @model_validator(mode="after")
    def validate_completeness(self):
        symbols = [member.symbol for member in self.members]
        if len(symbols) != self.total or len(set(symbols)) != self.total:
            raise ValueError("成员数量、唯一代码数与上游总数不一致")
        if symbols != sorted(symbols):
            raise ValueError("成员代码未按规范排序")
        page_size = self.pages[0].count
        if len(self.pages) != (self.total + page_size - 1) // page_size:
            raise ValueError("分页数量与上游总数不一致")
        for index, page in enumerate(self.pages, 1):
            expected = min(page_size, self.total - (index - 1) * page_size)
            if page.page != index or page.total != self.total or page.count != expected:
                raise ValueError("分页不连续、总数变化或页面截断")
        return self

    def contains(self, symbol: str) -> bool:
        """仅回答采集时名单内是否有该代码，不把旧分类当成员资格。"""
        return any(member.symbol == symbol for member in self.members)


def _member(code: Any, name: Any) -> HKConnectMember:
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{5}", code):
        raise ValueError(f"港股代码格式错误: {code!r}")
    return HKConnectMember(symbol=code + ".HK", name=name)


def _sse_member(row: dict[str, Any]) -> HKConnectMember:
    """中文简称可能只含全角空格，去除后才回退英文简称。"""
    name = row.get("ABBR_CN")
    if not isinstance(name, str) or not name.strip():
        name = row.get("ABBR_EN")
    return _member(row.get("SECURITY_CODE"), name)


def build_snapshot(frame: Any, pages: list[dict[str, Any]], fetched_at: datetime) -> HKStockConnectSnapshot:
    """对比SDK最终表格与实际分页成员；不读取行情或猜行业。"""
    if frame is None or not {"代码", "名称"} <= set(frame.columns) or frame.empty or not pages:
        raise ValueError("接口未返回有效完整名单")
    members = [_member(code, name) for code, name in frame[["代码", "名称"]].itertuples(index=False, name=None)]
    raw_members = [_member(row["f12"], row["f14"]) for page in pages for row in page["rows"]]
    if sorted((member.symbol, member.name) for member in members) != sorted(
            (member.symbol, member.name) for member in raw_members):
        raise ValueError("SDK表格与分页原始成员不一致")
    return HKStockConnectSnapshot(
        fetched_at=fetched_at,
        total=pages[0]["total"],
        pages=tuple(HKConnectPage(page=page["page"], total=page["total"], count=len(page["rows"])) for page in pages),
        members=tuple(sorted(members, key=lambda member: member.symbol)),
    )


def _sse_payload(text: str) -> dict[str, Any]:
    """解析上交所JSONP响应；结构不符即失败，不回退到猜测解析。"""
    match = re.fullmatch(r"\s*cb\((.*)\)\s*;?\s*", text, flags=re.DOTALL)
    if match is None:
        raise ValueError("上交所名单响应不是预期的JSONP格式")
    payload = json.loads(match.group(1))
    if not isinstance(payload, dict):
        raise TypeError("上交所名单响应结构无效")
    return payload


def build_sse_snapshot(payload: Any, fetched_at: datetime) -> HKStockConnectSnapshot:
    """从官方名单构建股票成员快照；ETF等非股票标的不进入行业候选池。"""
    page = payload.get("pageHelp") if isinstance(payload, dict) else None
    if not isinstance(page, dict):
        raise TypeError("上交所名单缺少 pageHelp")
    rows = page.get("data")
    if not isinstance(rows, list):
        raise TypeError("上交所名单 data 不是列表")
    if not rows:
        raise ValueError("上交所名单为空")
    if page.get("total") != len(rows) or page.get("pageCount") != 1:
        raise ValueError("上交所名单分页不完整")
    stocks = [
        row for row in rows
        if isinstance(row, dict) and row.get("SECURITY_TYPE") == "股票"
    ]
    if not stocks:
        raise ValueError("上交所名单没有股票记录")
    dates = {str(row.get("UPDATE_DATE") or "").strip() for row in stocks}
    if len(dates) != 1 or "" in dates:
        raise ValueError("上交所名单数据日期缺失或混用")
    data_date = date.fromisoformat(next(iter(dates)))
    members = tuple(sorted(
        (_sse_member(row) for row in stocks),
        key=lambda member: member.symbol,
    ))
    if len({member.symbol for member in members}) != len(members):
        raise ValueError("上交所名单存在重复代码")
    return HKStockConnectSnapshot(
        source=SSE_SOURCE,
        source_url=SSE_URL,
        data_date=data_date,
        fetched_at=fetched_at,
        total=len(members),
        pages=(HKConnectPage(page=1, total=len(members), count=len(members)),),
        members=members,
    )


def fetch_sse_snapshot() -> HKStockConnectSnapshot:
    """单次请求上交所官方名单；失败保留既有文件，不重试、不混用其他分类。"""
    session = requests.Session()
    session.trust_env = False  # 官方接口直连，避免本机代理出口被限流
    try:
        response = session.post(
            SSE_ENDPOINT, params={"jsonCallBack": "cb"}, data=SSE_FORM,
            headers=SSE_HEADERS, timeout=(10, 30),
        )
        response.raise_for_status()
        text = response.text
    except requests.RequestException as exc:
        raise HKStockConnectError("上交所港股通名单请求失败") from exc
    finally:
        session.close()
    try:
        return build_sse_snapshot(_sse_payload(text), datetime.now().astimezone())
    except (TypeError, ValueError) as exc:
        raise HKStockConnectError("上交所港股通名单数据不完整") from exc


def _collect_in_worker() -> HKStockConnectSnapshot:
    """只在独立进程观察SDK分页，不修改共享分析进程或安装包。"""
    import akshare as ak

    fetcher: Any = ak.stock_hk_ggt_components_em
    pagination: Any = fetcher.__globals__.get("fetch_paginated_data")
    globals_: dict[str, Any] = getattr(pagination, "__globals__", {})
    original = globals_.get("request_with_retry")
    if not callable(original):
        raise HKStockConnectError("AkShare分页实现已变化，停止采集以免误判完整性")
    pages: list[dict[str, Any]] = []

    def observe(*args: Any, **kwargs: Any) -> Any:
        if len(pages) >= MAX_PAGES:
            raise HKStockConnectError("港股通分页超出本轮采集预算")
        response: Any = original(*args, **kwargs)
        payload = response.json()
        data = payload["data"]
        rows = data["diff"]
        if not isinstance(rows, list) or not rows:
            raise ValueError("分页返回为空或格式变化")
        pages.append({"page": int(kwargs["params"]["pn"]), "total": data["total"], "rows": rows})
        return response

    globals_["request_with_retry"] = observe
    try:
        frame = fetcher()
    except Exception as exc:
        logger.exception("AkShare港股通名单采集失败")
        raise HKStockConnectError("港股通接口未取得完整名单") from exc
    finally:
        globals_["request_with_retry"] = original
    return build_snapshot(frame, pages, datetime.now().astimezone())


def fetch_snapshot() -> HKStockConnectSnapshot:
    """隔离SDK重试并限制整次采集时长；失败不触碰正式文件。"""
    environment = os.environ.copy()
    source = str(Path(__file__).resolve().parents[1])
    environment["PYTHONPATH"] = source + os.pathsep + environment.get("PYTHONPATH", "")
    environment["PYTHONIOENCODING"] = "utf-8"
    try:
        result = subprocess.run(
            [sys.executable, "-m", "data.hk_stock_connect", "--collect"],
            capture_output=True, text=True, encoding="utf-8", timeout=45,
            env=environment, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HKStockConnectError("港股通名单采集超时（45秒），旧数据保留") from exc
    if result.returncode != 0:
        logger.warning("港股通采集进程失败: %s", result.stderr[-2000:])
        raise HKStockConnectError("港股通接口未取得完整名单，旧数据保留")
    return HKStockConnectSnapshot.model_validate_json(result.stdout)


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
        try:
            Path(name).unlink(missing_ok=True)
        except OSError:
            logger.warning("名单更新临时文件未能清理: %s", name, exc_info=True)


def publish_snapshot(snapshot: HKStockConnectSnapshot, mapping_path: Path, snapshot_path: Path) -> None:
    """先补待分类占位，最后发布资格快照；正常失败恢复原映射。"""
    snapshot = HKStockConnectSnapshot.model_validate_json(snapshot.model_dump_json())
    if mapping_path.resolve() == snapshot_path.resolve():
        raise ValueError("成员快照不能覆盖行业映射表")
    with FileLock(str(mapping_path.with_suffix(".lock")), timeout=10):
        original = mapping_path.read_bytes()
        reader = csv.DictReader(io.StringIO(original.decode("utf-8")))
        if reader.fieldnames != MAPPING_COLUMNS:
            raise ValueError("行业映射表列结构不符合预期，停止更新")
        rows = list(reader)
        symbols = {row["symbol"] for row in rows}
        if len(symbols) != len(rows):
            raise ValueError("已有行业映射含重复代码，停止更新")
        for member in snapshot.members:
            if member.symbol not in symbols:
                rows.append({"symbol": member.symbol, "sw_level1": "", "sw_level2": "",
                             "style_category": "", "mapping_status": "missing"})
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=MAPPING_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        _atomic_write(mapping_path, buffer.getvalue().encode("utf-8"))
        try:
            _atomic_write(snapshot_path, snapshot.model_dump_json(indent=2).encode("utf-8"))
        except OSError as exc:
            try:
                _atomic_write(mapping_path, original)
            except OSError as rollback:
                logger.exception("名单发布及行业映射恢复均失败")
                raise HKStockConnectError("名单发布失败且映射恢复失败，请核查文件") from ExceptionGroup(
                    "名单发布与恢复异常", [exc, rollback])
            raise


def _fetch_snapshot_for(source: SnapshotSource) -> HKStockConnectSnapshot:
    return fetch_sse_snapshot() if source == "sse" else fetch_snapshot()


def refresh_local_mapping(mapping_path: Path, snapshot_path: Path,
                          source: SnapshotSource = "sse") -> HKStockConnectSnapshot:
    snapshot = _fetch_snapshot_for(source)
    publish_snapshot(snapshot, mapping_path, snapshot_path)
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="手动更新港股通名单及待补齐行业占位，不推断申万分类")
    parser.add_argument("--collect", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--source", choices=("sse", "eastmoney"), default="sse",
                        help="名单来源：sse=上交所官方；eastmoney=AkShare东财")
    parser.add_argument("--mapping", type=Path, help="已有行业映射CSV路径")
    parser.add_argument("--snapshot", type=Path, help="成员资格JSON路径，默认与映射同目录")
    args = parser.parse_args()
    try:
        if args.collect:
            print(_collect_in_worker().model_dump_json())
        else:
            snapshot = _fetch_snapshot_for(cast(SnapshotSource, args.source))
            mapping = args.mapping or industry_mapping_path(writable=True)
            target = args.snapshot or mapping.with_name("hk_stock_connect.snapshot.json")
            publish_snapshot(snapshot, mapping, target)
            print(json.dumps({"members": snapshot.total, "source": snapshot.source,
                              "data_date": snapshot.data_date.isoformat() if snapshot.data_date else None,
                              "fetched_at": snapshot.fetched_at.isoformat(),
                              "mapping": str(mapping), "snapshot": str(target)}, ensure_ascii=False))
    except (HKStockConnectError, OSError, ValueError, Timeout, ValidationError):
        logger.exception("港股通名单更新未完成，不能当作空名单或全部调出")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
