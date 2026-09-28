"""配置雷达固定窗口稳健性回测的切分和汇总。"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    """一个预先确定的连续回测窗口。"""

    start_date: date
    end_date: date
    available: bool
    reason: str | None = None


def fixed_windows(start_date: date, end_date: date, window_years: int) -> tuple[WalkForwardWindow, ...]:
    """按自然年长度切分连续、不重叠窗口，并显式保留末尾不足窗口。"""
    if start_date >= end_date:
        raise ValueError("start 必须早于 end")
    if window_years < 1:
        raise ValueError("window-years 必须至少为 1")

    windows: list[WalkForwardWindow] = []
    current_start = start_date
    while current_start <= end_date:
        expected_end = _add_years(current_start, window_years) - timedelta(days=1)
        current_end = min(expected_end, end_date)
        available = expected_end <= end_date
        windows.append(WalkForwardWindow(
            start_date=current_start,
            end_date=current_end,
            available=available,
            reason=None if available else f"不足 {window_years} 年固定窗口",
        ))
        current_start = expected_end + timedelta(days=1)
    return tuple(windows)


def summarize_windows(
    windows: tuple[WalkForwardWindow, ...],
    outputs: dict[tuple[date, date], Path],
    failures: dict[tuple[date, date], str],
) -> dict[str, Any]:
    """读取每段已完成产物，且不忽略负超额或不可用样本。"""
    records: list[dict[str, Any]] = []
    usable: list[dict[str, Any]] = []
    for window in windows:
        key = (window.start_date, window.end_date)
        record: dict[str, Any] = {
            "start_date": window.start_date.isoformat(),
            "end_date": window.end_date.isoformat(),
        }
        if not window.available:
            record.update({"status": "unavailable", "reason": window.reason})
        elif key in failures:
            record.update({"status": "unavailable", "reason": failures[key]})
        else:
            output = outputs[key]
            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            record.update({
                "status": "completed",
                "artifact_path": str(output),
                "strategy": {name: summary.get(name) for name in ("累计收益", "年化收益", "最大回撤", "夏普比率", "交易次数")},
                "benchmarks": summary.get("benchmarks", {}),
            })
            usable.append(record)
        records.append(record)

    positive_excess: dict[str, dict[str, float]] = {}
    benchmark_ids = {
        benchmark_id
        for record in usable
        for benchmark_id in record["benchmarks"]
    }
    for benchmark_id in sorted(benchmark_ids):
        values = [
            float(record["benchmarks"][benchmark_id]["超额累计收益"])
            for record in usable
            if "超额累计收益" in record["benchmarks"].get(benchmark_id, {})
        ]
        positive_excess[benchmark_id] = {
            "positive_windows": float(sum(value > 0 for value in values)),
            "usable_windows": float(len(values)),
            "positive_excess_ratio": float(sum(value > 0 for value in values) / len(values)) if values else 0.0,
        }
    return {
        "windows": records,
        "completed_windows": len(usable),
        "unavailable_windows": len(records) - len(usable),
        "positive_excess": positive_excess,
    }


def write_walkforward_artifacts(
    summary: dict[str, Any],
    *,
    universe_id: str,
    strategy_id: str,
    start_date: date,
    end_date: date,
    window_years: int,
    reports_dir: Path,
) -> Path:
    """原子写入 P1 稳健性报告及其固定窗口配置。"""
    now = datetime.now().astimezone()
    run_id = f"{now:%Y%m%d_%H%M%S}"
    final_dir = reports_dir / "radar_walkforwards" / "etf" / strategy_id / universe_id / f"{now:%Y-%m}" / run_id
    temporary = final_dir.parent / f"{run_id}.tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    manifest = {
        "asset_type": "etf",
        "universe_id": universe_id,
        "strategy_id": strategy_id,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "window_years": window_years,
        "generated_at": now.isoformat(),
    }
    try:
        (temporary / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        (temporary / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        lines = [
            "# 配置雷达 ETF 稳健性报告",
            "",
            "> 固定窗口结果包含正负超额样本；仅供研究，不构成投资建议。",
            "",
            "## 分段策略结果",
            "",
            "| 区间 | 状态 | 累计收益 | 最大回撤 | 夏普比率 | 交易次数 |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
        for record in summary["windows"]:
            label = f"{record['start_date']} 至 {record['end_date']}"
            if record["status"] == "completed":
                strategy = record["strategy"]
                lines.append(
                    f"| {label} | 已完成 | {_percentage(strategy['累计收益'])} | "
                    f"{_percentage(strategy['最大回撤'])} | {strategy['夏普比率']:.2f} | {strategy['交易次数']:.0f} |"
                )
            else:
                lines.append(f"| {label} | 不可用：{record.get('reason', '未知原因')} | -- | -- | -- | -- |")
        for benchmark_id in sorted({
            benchmark_id
            for record in summary["windows"]
            for benchmark_id in record.get("benchmarks", {})
        }):
            lines.extend([
                "",
                f"## 相对 {benchmark_id} 的表现",
                "",
                "| 区间 | 基准累计收益 | 超额累计收益 | 相对净值最大回撤 |",
                "| --- | ---: | ---: | ---: |",
            ])
            for record in summary["windows"]:
                benchmark = record.get("benchmarks", {}).get(benchmark_id)
                label = f"{record['start_date']} 至 {record['end_date']}"
                if benchmark is None:
                    lines.append(f"| {label} | -- | -- | -- |")
                else:
                    lines.append(
                        f"| {label} | {_percentage(benchmark['累计收益'])} | "
                        f"{_percentage(benchmark['超额累计收益'])} | {_percentage(benchmark['相对净值最大回撤'])} |"
                    )
        (temporary / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(temporary, final_dir)
    except Exception:  # 文件写入边界必须清理临时目录后重新抛出
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return final_dir


def _add_years(value: date, years: int) -> date:
    """保留月日；闰日窗口在非闰年落在 2 月 28 日。"""
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, month=2, day=28)


def _percentage(value: Any) -> str:
    """格式化产物中的收益或回撤数值。"""
    return f"{float(value):.2%}"
