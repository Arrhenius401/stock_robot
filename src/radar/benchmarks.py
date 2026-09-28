"""配置雷达的固定多基准目录与日线读取。"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import ClassVar, cast

import pandas as pd
import yaml
from pydantic import BaseModel, ValidationError

from data.akshare import _ak_csindex

logger = logging.getLogger(__name__)


class RadarBenchmarkError(Exception):
    """雷达基准配置或历史行情不可用。"""


class LocalBenchmarkUnavailable(RadarBenchmarkError):
    """本地基准仓没有覆盖所请求区间。"""


class RadarBenchmark(BaseModel):
    """一条人民币计价、只用于比较的指数基准。"""

    id: str
    name: str
    symbol: str


class RadarBenchmarkCatalog(BaseModel):
    """版本化固定基准目录。"""

    version: int
    benchmarks: list[RadarBenchmark]


class LocalBenchmarkStore:
    """用户导入的基准收盘序列，用于离线回测与比较。"""

    _COLUMNS: ClassVar[dict[str, str]] = {
        "日期": "date", "交易日期": "date", "date": "date", "Date": "date",
        "收盘": "close", "收盘价": "close", "close": "close", "Close": "close",
    }

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def import_csv(self, benchmark_id: str, source_path: Path) -> tuple[int, date, date]:
        """标准化并按日期合并一份基准 CSV。"""
        if not source_path.is_file():
            raise RadarBenchmarkError(f"本地基准文件不存在: {source_path}")
        imported = self._normalize(self._read_csv(source_path))
        target = self._path_for(benchmark_id)
        if target.exists():
            existing = self._normalize(self._read_csv(target))
            imported = pd.concat((existing, imported), ignore_index=True).drop_duplicates(subset="date", keep="last")
        normalized = imported.sort_values("date").reset_index(drop=True)
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        normalized.to_csv(temporary, index=False, encoding="utf-8")
        temporary.replace(target)
        return len(normalized), cast(date, normalized.iloc[0]["date"]), cast(date, normalized.iloc[-1]["date"])

    def fetch_closes(self, benchmark: RadarBenchmark, start: date, end: date) -> pd.Series:
        """读取本地覆盖区间的收盘序列。"""
        path = self._path_for(benchmark.id)
        if not path.exists():
            raise LocalBenchmarkUnavailable(f"本地未导入基准 {benchmark.name}")
        frame = self._normalize(self._read_csv(path))
        result = frame[(frame["date"] >= start) & (frame["date"] <= end)]
        if result.empty:
            raise LocalBenchmarkUnavailable(f"本地基准 {benchmark.name} 在请求区间无数据")
        return pd.Series(list(result["close"]), index=pd.Index(list(result["date"]), name="date"), name=benchmark.id)

    def _path_for(self, benchmark_id: str) -> Path:
        if benchmark_id not in {"money_fund", "csi_300", "csi_all_bond"}:
            raise RadarBenchmarkError(f"未知雷达基准: {benchmark_id}")
        return self.directory / f"{benchmark_id}.csv"

    @staticmethod
    def _read_csv(path: Path) -> pd.DataFrame:
        try:
            return pd.read_csv(path, encoding="utf-8-sig")
        except UnicodeDecodeError:
            try:
                return pd.read_csv(path, encoding="gbk")
            except (OSError, UnicodeDecodeError, pd.errors.ParserError) as exc:
                raise RadarBenchmarkError(f"读取本地基准 CSV 失败: {path.name}") from exc
        except (OSError, pd.errors.ParserError) as exc:
            raise RadarBenchmarkError(f"读取本地基准 CSV 失败: {path.name}") from exc

    def _normalize(self, frame: pd.DataFrame) -> pd.DataFrame:
        normalized = frame.rename(columns=self._COLUMNS).copy()
        if {"date", "close"} - set(normalized.columns):
            raise RadarBenchmarkError("本地基准 CSV 必须包含日期和收盘列")
        try:
            normalized["date"] = pd.to_datetime(normalized["date"], errors="raise").dt.date
        except (TypeError, ValueError) as exc:
            raise RadarBenchmarkError("本地基准 CSV 日期格式无效") from exc
        normalized["close"] = pd.to_numeric(normalized["close"], errors="coerce")
        if bool(normalized["close"].isna().any()):
            raise RadarBenchmarkError("本地基准 CSV 含有无效收盘价")
        return cast(pd.DataFrame, normalized[["date", "close"]])


def load_benchmarks(path: Path) -> RadarBenchmarkCatalog:
    """加载并校验雷达专用基准配置。"""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        catalog = RadarBenchmarkCatalog.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise RadarBenchmarkError("读取雷达基准配置失败") from exc
    expected = {"money_fund", "csi_300", "csi_all_bond"}
    actual = {item.id for item in catalog.benchmarks}
    if actual != expected:
        raise RadarBenchmarkError("雷达基准配置必须且只能包含货币基金、沪深300和中证全债")
    return catalog


def fetch_benchmark_closes(
    benchmark: RadarBenchmark,
    start: date,
    end: date,
    *,
    local_directory: Path | None = None,
) -> pd.Series:
    """读取中证日线并转换为以交易日为索引的收盘序列。"""
    if local_directory is not None:
        try:
            return LocalBenchmarkStore(local_directory).fetch_closes(benchmark, start, end)
        except LocalBenchmarkUnavailable:
            logger.debug("本地基准未覆盖 %s，降级请求网络数据", benchmark.id)
    try:
        raw = _ak_csindex(
            symbol=benchmark.symbol,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
    except Exception as exc:  # 上游 AkShare 边界需统一为领域错误
        raise RadarBenchmarkError(f"基准 {benchmark.name} 行情请求失败") from exc
    if raw is None or raw.empty:
        raise RadarBenchmarkError(f"基准 {benchmark.name} 在请求区间无数据")
    date_column = "date" if "date" in raw.columns else "日期"
    close_column = "close" if "close" in raw.columns else "收盘"
    if date_column not in raw.columns or close_column not in raw.columns:
        raise RadarBenchmarkError(f"基准 {benchmark.name} 缺少日期或收盘字段")
    frame = pd.DataFrame({
        "date": pd.to_datetime(raw[date_column], errors="coerce"),
        "close": pd.to_numeric(raw[close_column], errors="coerce"),
    }).dropna()
    frame = frame[(frame["date"].dt.date >= start) & (frame["date"].dt.date <= end)]
    duplicate_dates = pd.Series(frame["date"]).duplicated(keep="last")
    frame = frame.loc[~duplicate_dates].sort_values("date")
    if frame.empty:
        raise RadarBenchmarkError(f"基准 {benchmark.name} 在请求区间无有效数据")
    return pd.Series(
        frame["close"].to_numpy(),
        index=pd.Index(frame["date"].dt.date.tolist(), name="date"),
        name=benchmark.id,
    )
