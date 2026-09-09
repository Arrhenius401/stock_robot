"""配置雷达的固定多基准目录与日线读取。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import yaml
from pydantic import BaseModel, ValidationError

from data.akshare import _ak_csindex


class RadarBenchmarkError(Exception):
    """雷达基准配置或历史行情不可用。"""


class RadarBenchmark(BaseModel):
    """一条人民币计价、只用于比较的指数基准。"""

    id: str
    name: str
    symbol: str


class RadarBenchmarkCatalog(BaseModel):
    """版本化固定基准目录。"""

    version: int
    benchmarks: list[RadarBenchmark]


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


def fetch_benchmark_closes(benchmark: RadarBenchmark, start: date, end: date) -> pd.Series:
    """读取中证日线并转换为以交易日为索引的收盘序列。"""
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
    frame = frame.loc[~duplicate_dates.to_numpy()].sort_values("date")
    if frame.empty:
        raise RadarBenchmarkError(f"基准 {benchmark.name} 在请求区间无有效数据")
    return pd.Series(
        frame["close"].to_numpy(),
        index=pd.Index(frame["date"].dt.date.tolist(), name="date"),
        name=benchmark.id,
    )
