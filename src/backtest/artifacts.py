"""回测产物写入 — 运行目录、五类文件与 Markdown 报告。"""

import importlib.metadata
import json
import logging
import os
import shutil
from datetime import date, datetime
from pathlib import Path

from backtest.models import BacktestResult
from backtest.strategy import strategy_fingerprint

logger = logging.getLogger(__name__)

# 核心指标表在报告中的展示名（年化收益在报告中命名为"策略年化"，供验收断言使用）
_METRIC_LABELS = {
    "累计收益": "累计收益",
    "年化收益": "策略年化",
    "最大回撤": "最大回撤",
    "年化波动率": "年化波动率",
    "夏普比率": "夏普比率",
    "交易次数": "交易次数",
    "超额收益": "超额收益",
}

# 最近交易表最多展示笔数
MAX_RECENT_TRADES = 10


def write_backtest_artifacts(
    result: BacktestResult,
    reports_dir: Path = Path.cwd() / "reports",  # noqa: B008 — 接口约定默认 reports 目录为调用方 cwd 下
) -> Path:
    """将回测结果写入可复现产物目录，返回运行目录。

    目录层级：reports_dir/backtests/<策略ID>/<股票代码>/<YYYY-MM>/<运行ID>/。
    运行 ID 形如 20260829_103045_v1_ab12cd34（时间戳 + 策略版本 + 配置短哈希）。
    先在同级临时目录写完五个文件，再原子重命名到最终目录；
    任一步骤失败则清理临时目录并重抛，不留半成品目录。
    """
    run_at = datetime.now().astimezone()
    fingerprint = strategy_fingerprint(result.strategy)
    run_id = f"{run_at:%Y%m%d_%H%M%S}_v{result.strategy.version}_{fingerprint}"
    month = run_at.strftime("%Y-%m")

    final_dir = (
        Path(reports_dir)
        / "backtests"
        / result.strategy.id
        / result.request.symbol
        / month
        / run_id
    )
    tmp_dir = final_dir.parent / f"{run_id}.tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        _write_manifest(tmp_dir / "manifest.json", result, fingerprint, _app_version())
        _write_summary(tmp_dir / "summary.json", result, run_id)
        _write_equity_curve(tmp_dir / "equity_curve.csv", result)
        _write_trades(tmp_dir / "trades.csv", result)
        _write_report(tmp_dir / "report.md", result)
        os.replace(tmp_dir, final_dir)
    except Exception:
        # 任一产物写入失败都清理临时目录并重抛，保证不留半成品目录
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    return final_dir


def _app_version() -> str:
    """读取实现版本；包未安装（如源码直跑）时回退到项目声明版本。"""
    try:
        return importlib.metadata.version("stock-robot")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


def _write_manifest(
    path: Path, result: BacktestResult, fingerprint: str, version: str
) -> None:
    """运行快照：请求参数、实际数据日期、完整策略、短哈希、成本、基准、警告与版本。"""
    manifest = {
        "request": result.request.model_dump(mode="json"),
        "data_start": result.data_start.isoformat(),
        "data_end": result.data_end.isoformat(),
        "strategy": result.strategy.model_dump(mode="json"),
        "fingerprint": fingerprint,
        "costs": result.costs,
        "benchmark": result.benchmark.model_dump(mode="json"),
        "warnings": result.warnings,
        "version": version,
    }
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _write_summary(path: Path, result: BacktestResult, run_id: str) -> None:
    """稳定扁平摘要：不放任何 DataFrame 原始对象，指标键名与运行器保持一致。"""
    summary = {
        "symbol": result.request.symbol,
        "strategy_id": result.strategy.id,
        "strategy_version": result.strategy.version,
        "run_id": run_id,
        "start_date": result.request.start_date.isoformat(),
        "end_date": result.request.end_date.isoformat(),
        **result.metrics,
        "warnings_count": len(result.warnings),
    }
    path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _write_equity_curve(path: Path, result: BacktestResult) -> None:
    """净值曲线 CSV：日期统一为 ISO 字符串，其余列原样导出。"""
    frame = result.equity_curve.copy()
    frame.insert(0, "净值日期", [ts.date().isoformat() for ts in frame.index])
    frame.to_csv(path, index=False, encoding="utf-8")


def _write_trades(path: Path, result: BacktestResult) -> None:
    """成交明细 CSV：signal_date/trade_date 转 ISO 字符串。"""
    frame = result.trades.copy()
    for col in ("signal_date", "trade_date"):
        frame[col] = frame[col].map(_date_to_iso)
    frame.to_csv(path, index=False, encoding="utf-8")


def _date_to_iso(value: object) -> str:
    """date 值转 ISO 字符串；非 date 值（如字符串）原样字符串化。"""
    return value.isoformat() if isinstance(value, date) else str(value)


def _write_report(path: Path, result: BacktestResult) -> None:
    """Markdown 报告：免责声明、策略/基准/成本、核心指标与最近交易。"""
    costs = result.costs
    lines = [
        f"# {result.request.symbol} 回测报告",
        "",
        "> 研究免责声明：本报告仅用于个人学习和研究目的，不构成任何投资建议。",
        "> 历史表现不代表未来收益，回测未考虑涨跌停无法成交、停牌等实际交易约束。",
        "",
        "## 配置",
        (
            f"- 策略：{result.strategy.id}（{result.strategy.name}）"
            f"v{result.strategy.version}，指纹 {strategy_fingerprint(result.strategy)}"
        ),
        f"- 基准：{result.benchmark.id}（{result.benchmark.name}，{result.benchmark.symbol}）",
        f"- 初始资金：{result.request.initial_cash:.2f} 元",
        (
            f"- 成本：佣金 {costs['commission_rate']:.4%}"
            f"（单笔最低 {costs['minimum_commission']:.2f} 元）、"
            f"印花税 {costs['stamp_duty_rate']:.4%}（仅卖出）、"
            f"过户费 {costs['transfer_fee_rate']:.4%}、滑点 {costs['slippage_rate']:.4%}"
        ),
        f"- 数据区间：{result.data_start.isoformat()} 至 {result.data_end.isoformat()}",
        "",
        "## 核心指标",
        "| 指标 | 数值 |",
        "| --- | --- |",
    ]
    for key, value in result.metrics.items():
        label = _METRIC_LABELS.get(key, key)
        lines.append(f"| {label} | {value:.4f} |")

    lines += ["", "## 最近交易"]
    recent = result.trades.tail(MAX_RECENT_TRADES)
    if recent.empty:
        lines.append("（正式区间内无成交）")
    else:
        lines.append("| 信号日 | 成交日 | 原因 | 方向 | 价格 | 数量 | 费用 |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for _, row in recent.iterrows():
            lines.append(
                f"| {_date_to_iso(row['signal_date'])} | {_date_to_iso(row['trade_date'])} | "
                f"{row['reason']} | {row['direction']} | {row['price']:.2f} | "
                f"{row['quantity']:.0f} | {row['fees']:.2f} |"
            )

    if result.warnings:
        lines += ["", "## 警告"]
        lines.extend(f"- {warning}" for warning in result.warnings)

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
