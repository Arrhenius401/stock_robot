"""雷达回测的可审计产物写入。"""

from __future__ import annotations

import json
import os
import shutil
from datetime import date, datetime
from pathlib import Path

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
    benchmark: str | None = None,
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
        summary = {**result.metrics, "strategy_id": strategy_id, "start_date": start_date.isoformat() if start_date else None, "end_date": end_date.isoformat() if end_date else None}
        manifest = {"universe_id": universe_id, "universe_version": universe_version, "strategy_id": strategy_id, "strategy_fingerprint": strategy_fingerprint, "asset_type": "etf", "generated_at": now.isoformat(), "start_date": start_date.isoformat() if start_date else None, "end_date": end_date.isoformat() if end_date else None, "benchmark": benchmark, "cost_rate": 0.0005, "rebalance_dates": [value.isoformat() for value in result.rebalance_dates]}
        (temporary / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        (temporary / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        result.equity_curve.to_csv(temporary / "equity_curve.csv")
        result.trades.to_csv(temporary / "trades.csv", index=False)
        (temporary / "report.md").write_text("# 配置雷达 ETF 回测\n\n> 仅供研究，不构成投资建议。\n\n" + "\n".join(f"- {key}: {value:.4f}" for key, value in result.metrics.items()) + "\n", encoding="utf-8")
        os.replace(temporary, final_dir)
    except Exception:  # 产物写入边界需清理临时目录后重新抛出
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return final_dir
