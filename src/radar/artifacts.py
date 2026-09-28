"""雷达回测的可审计产物写入。"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

from radar.backtest import RadarBacktestResult


def write_radar_backtest_artifacts(
    result: RadarBacktestResult,
    *,
    universe_id: str,
    universe_version: int,
    strategy_id: str,
    strategy_fingerprint: str = "",
    start_date: date | None = None,
    end_date: date | None = None,
    benchmarks: list[dict[str, str]] | None = None,
    cost_rate: float = 0.0005,
    strategy_snapshot: dict[str, Any] | None = None,
    cost_profile: dict[str, Any] | None = None,
    data_coverage: dict[str, Any] | None = None,
    data_providers: dict[str, Any] | None = None,
    reports_dir: Path | None = None,
) -> Path:
    """原子写入报告、摘要、净值、成交和运行清单。"""
    root = reports_dir or Path.cwd() / "reports"
    now = datetime.now().astimezone()
    run_id = f"{now:%Y%m%d_%H%M%S}"
    final_dir = root / "radar_backtests" / "etf" / strategy_id / universe_id / f"{now:%Y-%m}" / run_id
    temporary = final_dir.parent / f"{run_id}.tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    try:
        summary = {
            **result.metrics, "benchmarks": result.benchmark_metrics, "warnings": list(result.warnings),
            "strategy_id": strategy_id,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
        }
        manifest = {
            "universe_id": universe_id, "universe_version": universe_version,
            "strategy_id": strategy_id, "strategy_fingerprint": strategy_fingerprint,
            "strategy": strategy_snapshot or {}, "asset_type": "etf",
            "generated_at": now.isoformat(),
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
            "benchmarks": benchmarks or [], "cost_rate": cost_rate,
            "cost_profile": cost_profile or {"commission_rate": cost_rate},
            "data_coverage": data_coverage or {}, "data_providers": data_providers or {},
            "warnings": list(result.warnings),
            "rebalance_dates": [value.isoformat() for value in result.rebalance_dates],
        }
        (temporary / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        (temporary / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        result.equity_curve.to_csv(temporary / "equity_curve.csv")
        result.trades.to_csv(temporary / "trades.csv", index=False)
        report_metrics = "\n".join(f"- {key}: {value:.4f}" for key, value in result.metrics.items())
        report_warnings = "\n".join(f"- ⚠ {warning}" for warning in result.warnings)
        warning_section = f"\n## 运行警告\n\n{report_warnings}\n" if report_warnings else ""
        (temporary / "report.md").write_text("# 配置雷达 ETF 回测\n\n> 仅供研究，不构成投资建议。\n\n" + report_metrics + warning_section, encoding="utf-8")
        os.replace(temporary, final_dir)
    except Exception:  # 产物写入边界需清理临时目录后重新抛出
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return final_dir


def find_reusable_radar_backtest(
    reports_dir: Path,
    *,
    universe_id: str,
    strategy_id: str,
    strategy_fingerprint: str,
    start_date: date,
    end_date: date,
    cost_profile: Mapping[str, Any] | None = None,
    benchmarks: list[dict[str, str]] | None = None,
) -> Path | None:
    """返回同一池、策略与区间的完整产物，避免重复运行相同研究。"""
    root = reports_dir / "radar_backtests" / "etf" / strategy_id / universe_id
    candidates: list[Path] = []
    for manifest_path in root.glob("*/*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        run_dir = manifest_path.parent
        if not all((run_dir / name).is_file() for name in ("report.md", "summary.json", "equity_curve.csv", "trades.csv")):
            continue
        if (
            manifest.get("strategy_fingerprint") == strategy_fingerprint
            and manifest.get("start_date") == start_date.isoformat()
            and manifest.get("end_date") == end_date.isoformat()
            and len(manifest.get("benchmarks", [])) == 3
        ):
            if cost_profile is not None and manifest.get("cost_profile") != dict(cost_profile):
                continue
            if benchmarks is not None and manifest.get("benchmarks") != benchmarks:
                continue
            candidates.append(run_dir)
    return max(candidates, key=lambda item: item.name) if candidates else None
