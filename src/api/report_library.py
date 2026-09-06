"""持久化报告库：只读枚举 reports 目录中的已识别产物。"""
from __future__ import annotations

import base64
import binascii
import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

ReportType = Literal["stock", "index", "backtest"]
MAX_TRADE_ROWS = 1000


class ReportLibraryError(Exception):
    """报告库可转换为 HTTP 状态码的业务错误。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ReportSummary:
    id: str
    type: ReportType
    title: str
    symbol: str
    path: str
    generated_at: float
    strategy_id: str | None = None
    strategy_version: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    has_equity_curve: bool = False
    has_trades: bool = False
    legacy: bool = False
    heading: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReportDetail:
    report: ReportSummary
    markdown: str
    summary: dict[str, Any] | None = None
    equity_curve: dict[str, Any] | None = None
    trades: dict[str, Any] | None = None
    missing_artifacts: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _encode_id(relative_path: Path) -> str:
    raw = relative_path.as_posix().encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_id(report_id: str) -> Path:
    try:
        padded = report_id + "=" * (-len(report_id) % 4)
        value = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (UnicodeDecodeError, ValueError, binascii.Error) as exc:
        raise ReportLibraryError("报告不存在", 404) from exc
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ReportLibraryError("报告不存在", 404)
    return candidate


def _safe_relative(reports_root: Path, path: Path) -> Path | None:
    root = reports_root.resolve()
    resolved = path.resolve()
    try:
        return resolved.relative_to(root)
    except ValueError:
        return None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ReportLibraryError("报告文件已损坏或不完整", 422) from exc
    except OSError as exc:
        raise ReportLibraryError("报告不存在", 404) from exc


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReportLibraryError("报告文件已损坏或不完整", 422) from exc
    if not isinstance(payload, dict):
        raise ReportLibraryError("报告文件已损坏或不完整", 422)
    return payload


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return _read_json(path)


def _read_csv(path: Path, max_rows: int = MAX_TRADE_ROWS) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)
            columns = list(reader.fieldnames or [])
            rows: list[dict[str, Any]] = []
            for row in reader:
                rows.append({column: (row.get(column) or None) for column in columns})
                if len(rows) >= max_rows:
                    break
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise ReportLibraryError("报告文件已损坏或不完整", 422) from exc
    return {"columns": columns, "rows": rows}


def _first_heading(markdown: str) -> str:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _mtime_day(path: Path) -> float:
    try:
        value = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    except OSError:
        return 0.0
    return value.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _generated_at_from_path(
        relative: Path, fallback: Path, *, allow_month: bool = True,
        fallback_day: bool = False) -> float:
    candidates = [relative.stem, *relative.parts]
    formats = [("%Y%m%d_%H%M%S", 15), ("%Y%m%d", 8)]
    if allow_month:
        formats.append(("%Y-%m", 7))
    for candidate in candidates:
        for fmt, length in formats:
            for start in range(max(len(candidate) - length + 1, 0)):
                fragment = candidate[start:start + length]
                try:
                    return datetime.strptime(fragment, fmt).astimezone().timestamp()
                except ValueError:
                    continue
    if fallback_day:
        return _mtime_day(fallback)
    return _mtime(fallback)


def _summary_for_markdown(
        reports_root: Path, path: Path, report_type: Literal["stock", "index"],
        *, legacy: bool = False) -> ReportSummary | None:
    relative = _safe_relative(reports_root, path)
    if relative is None or path.suffix.lower() != ".md":
        return None
    symbol = relative.parts[1] if not legacy else relative.parts[0]
    kind = "个股" if report_type == "stock" else "指数"
    title = f"{symbol} {kind}分析报告"
    heading = _first_heading(_read_text(path))
    return ReportSummary(
        id=_encode_id(relative),
        type=report_type,
        title=title,
        symbol=symbol,
        path=relative.as_posix(),
        generated_at=_generated_at_from_path(relative, path),
        legacy=legacy,
        heading=heading or None,
    )


def _backtest_summary(reports_root: Path, report_path: Path) -> ReportSummary | None:
    relative = _safe_relative(reports_root, report_path)
    if relative is None or len(relative.parts) < 6 or relative.name != "report.md":
        return None
    run_dir = report_path.parent
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        return None
    summary = _read_json(summary_path)
    manifest = _read_optional_json(run_dir / "manifest.json")
    strategy = manifest.get("strategy")
    strategy_name = strategy.get("name") if isinstance(strategy, dict) else None
    strategy_id = str(summary.get("strategy_id") or relative.parts[1])
    symbol = str(summary.get("symbol") or relative.parts[2])
    title_strategy = str(strategy_name or strategy_id)
    return ReportSummary(
        id=_encode_id(relative),
        type="backtest",
        title=f"{symbol} {title_strategy}回测报告",
        symbol=symbol,
        path=relative.as_posix(),
        generated_at=_generated_at_from_path(
            relative, report_path, allow_month=False, fallback_day=True),
        strategy_id=strategy_id,
        strategy_version=(str(summary["strategy_version"])
                          if summary.get("strategy_version") is not None else None),
        start_date=(str(summary["start_date"])
                    if summary.get("start_date") is not None else None),
        end_date=(str(summary["end_date"])
                  if summary.get("end_date") is not None else None),
        has_equity_curve=(run_dir / "equity_curve.csv").exists(),
        has_trades=(run_dir / "trades.csv").exists(),
    )


def _radar_backtest_summary(reports_root: Path, report_path: Path) -> ReportSummary | None:
    """识别完整的雷达回测产物，不影响既有单标的回测目录。"""
    relative = _safe_relative(reports_root, report_path)
    if relative is None or len(relative.parts) != 7 or relative.parts[0] != "radar_backtests":
        return None
    run_dir = report_path.parent
    if not (run_dir / "summary.json").exists():
        return None
    manifest = _read_optional_json(run_dir / "manifest.json")
    strategy_id = str(manifest.get("strategy_id") or relative.parts[2])
    universe_id = str(manifest.get("universe_id") or relative.parts[3])
    return ReportSummary(
        id=_encode_id(relative), type="backtest", title=f"{universe_id} {strategy_id}雷达回测报告",
        symbol=universe_id, path=relative.as_posix(),
        generated_at=_generated_at_from_path(relative, report_path, allow_month=False, fallback_day=True),
        strategy_id=strategy_id,
        strategy_version=str(manifest["strategy_fingerprint"]) if manifest.get("strategy_fingerprint") else None,
        start_date=str(manifest["start_date"]) if manifest.get("start_date") else None,
        end_date=str(manifest["end_date"]) if manifest.get("end_date") else None,
        has_equity_curve=(run_dir / "equity_curve.csv").exists(), has_trades=(run_dir / "trades.csv").exists(),
    )


def _matches(summary: ReportSummary, query: str | None) -> bool:
    if not query:
        return True
    needle = query.casefold()
    fields = [
        summary.title,
        summary.symbol,
        summary.path,
        summary.strategy_id or "",
        summary.strategy_version or "",
        summary.start_date or "",
        summary.end_date or "",
        summary.heading or "",
    ]
    return any(needle in field.casefold() for field in fields)


def list_reports(
        reports_root: Path,
        report_type: ReportType | None = None,
        query: str | None = None) -> list[ReportSummary]:
    if not reports_root.exists():
        return []

    items: list[ReportSummary] = []
    if report_type in (None, "stock"):
        for path in sorted((reports_root / "stock").glob("*/*/*.md")):
            summary = _summary_for_markdown(reports_root, path, "stock")
            if summary is not None:
                items.append(summary)
    if report_type in (None, "index"):
        for path in sorted((reports_root / "index").glob("*/*/*.md")):
            summary = _summary_for_markdown(reports_root, path, "index")
            if summary is not None:
                items.append(summary)
    if report_type in (None, "backtest"):
        for path in sorted((reports_root / "backtests").glob("*/*/*/*/report.md")):
            summary = _backtest_summary(reports_root, path)
            if summary is not None:
                items.append(summary)
        for path in sorted((reports_root / "radar_backtests").glob("*/*/*/*/*/report.md")):
            summary = _radar_backtest_summary(reports_root, path)
            if summary is not None:
                items.append(summary)
    if report_type in (None, "stock"):
        for path in sorted(reports_root.glob("[0-9][0-9][0-9][0-9][0-9][0-9]/*/*.md")):
            summary = _summary_for_markdown(reports_root, path, "stock", legacy=True)
            if summary is not None:
                items.append(summary)

    filtered = [item for item in items if _matches(item, query)]
    type_order = {"stock": 0, "index": 1, "backtest": 2}
    return sorted(
        filtered,
        key=lambda item: (-item.generated_at, item.legacy, type_order[item.type], item.path),
    )


def _summary_from_relative(reports_root: Path, relative: Path) -> ReportSummary:
    path = reports_root / relative
    if len(relative.parts) >= 4 and relative.parts[0] in ("stock", "index"):
        report_type: Literal["stock", "index"] = (
            "stock" if relative.parts[0] == "stock" else "index")
        summary = _summary_for_markdown(reports_root, path, report_type)
    elif len(relative.parts) >= 3 and relative.parts[0].isdigit():
        summary = _summary_for_markdown(reports_root, path, "stock", legacy=True)
    elif len(relative.parts) >= 6 and relative.parts[0] == "backtests":
        summary = _backtest_summary(reports_root, path)
    elif len(relative.parts) == 7 and relative.parts[0] == "radar_backtests":
        summary = _radar_backtest_summary(reports_root, path)
    else:
        summary = None
    if summary is None:
        raise ReportLibraryError("报告不存在", 404)
    return summary


def get_report_detail(reports_root: Path, report_id: str) -> ReportDetail:
    relative = _decode_id(report_id)
    path = reports_root / relative
    if _safe_relative(reports_root, path) is None:
        raise ReportLibraryError("报告不存在", 404)
    summary = _summary_from_relative(reports_root, relative)
    markdown = _read_text(path)
    if summary.type != "backtest":
        return ReportDetail(report=summary, markdown=markdown)

    run_dir = path.parent
    payload = _read_json(run_dir / "summary.json")
    missing: list[str] = []
    if (run_dir / "equity_curve.csv").exists():
        equity_curve = _read_csv(run_dir / "equity_curve.csv")
    else:
        equity_curve = {"columns": [], "rows": []}
        missing.append("equity_curve.csv")
    if (run_dir / "trades.csv").exists():
        trades = _read_csv(run_dir / "trades.csv")
    else:
        trades = {"columns": [], "rows": []}
        missing.append("trades.csv")
    return ReportDetail(
        report=summary,
        markdown=markdown,
        summary=payload,
        equity_curve=equity_curve,
        trades=trades,
        missing_artifacts=missing,
    )


def resolve_download_path(reports_root: Path, report_id: str) -> Path:
    relative = _decode_id(report_id)
    path = reports_root / relative
    safe_relative = _safe_relative(reports_root, path)
    if safe_relative is None:
        raise ReportLibraryError("报告不存在", 404)
    summary = _summary_from_relative(reports_root, safe_relative)
    if summary.path != safe_relative.as_posix() or path.suffix.lower() != ".md":
        raise ReportLibraryError("报告不存在", 404)
    return path.resolve()
