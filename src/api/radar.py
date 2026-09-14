"""配置雷达的快照读取与手动刷新 API。"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Any, cast

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.report_library import ReportLibraryError, get_report_detail, list_reports
from radar.backtest_cache import BacktestCacheError, derive_backtest_window
from radar.benchmarks import (
    RadarBenchmarkError,
    fetch_benchmark_closes,
    load_benchmarks,
)
from radar.data import (
    AkShareETFDataProvider,
    FallbackETFDataProvider,
    OfficialExchangeETFDataProvider,
    RadarDataError,
    RequestPacer,
    SinaETFDataProvider,
    TencentETFDataProvider,
)
from radar.performance import calculate_instrument_performance
from radar.refresh import RadarRefresher
from radar.score_profile import ScoreProfileConfigError, ScoreProfileRepository
from radar.store import RadarStore
from radar.universe import UniverseConfigError, UniverseRepository
from utils.config import Config


class RefreshRequest(BaseModel):
    """启动一次指定池刷新的请求体。"""

    universe_id: str
    full: bool = False
    as_of: date | None = None


class BacktestRequest(BaseModel):
    """启动一段 ETF 池回测的请求体。"""

    universe_id: str
    start_date: date
    end_date: date
    strategy: str = "core_rotation_v1"


_RESEARCH_NOTICE = "研究评分，不构成投资建议。"


def create_radar_router() -> APIRouter:
    """创建仅依赖本地状态的雷达路由。"""
    router = APIRouter(prefix="/api/v1/radar", tags=["radar"])
    tasks: dict[str, dict[str, Any]] = {}
    lock = threading.Lock()

    def services() -> tuple[UniverseRepository, RadarStore, RadarRefresher]:
        config = Config()
        root = Path(__file__).parents[2]
        repository = UniverseRepository(root / "config" / "radar_universes")
        store = RadarStore(config.config_dir / "radar.db")
        provider = FallbackETFDataProvider((
            AkShareETFDataProvider(pacer=RequestPacer(config.get("radar.minimum_interval_seconds", 1.0))),
            TencentETFDataProvider(),
            SinaETFDataProvider(),
            OfficialExchangeETFDataProvider(),
        ))
        return repository, store, RadarRefresher(repository, provider, store)

    def backtest_payload(root: Path, report: Any) -> dict[str, Any]:
        """补充池级产物的成本与警告，供前端明确标注其策略属性。"""
        try:
            detail = get_report_detail(root, report.id)
            manifest_path = root / Path(report.path).parent / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ReportLibraryError) as exc:
            raise HTTPException(status_code=422, detail="回测产物已损坏或不完整") from exc
        return {
            **detail.to_dict(),
            "research_notice": _RESEARCH_NOTICE,
            "cost_profile": manifest.get("cost_profile", {}),
            "warnings": detail.summary.get("warnings", []) if detail.summary else [],
        }

    @router.get("/universes")
    def list_universes():
        repository, _, _ = services()
        return [item.model_dump(mode="json") for item in repository.load_all()]

    @router.get("/score-profiles/{profile_id}")
    def get_score_profile(profile_id: str):
        """暴露版本化评分依据，供 Web 详情页解释当前分数。"""
        root = Path(__file__).parents[2]
        try:
            profile = ScoreProfileRepository(root / "config" / "radar_score_profiles").get(profile_id)
        except ScoreProfileConfigError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return profile.model_dump(mode="json")

    @router.get("/snapshots/latest")
    def latest_snapshot(universe_id: str):
        _, store, _ = services()
        snapshot = store.latest_completed(universe_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="尚无完成快照")
        return {**snapshot, "research_notice": _RESEARCH_NOTICE}

    @router.get("/snapshots/{run_id}")
    def get_snapshot(run_id: str):
        _, store, _ = services()
        snapshot = store.get_snapshot(run_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="快照不存在或尚未完成")
        return {**snapshot, "research_notice": _RESEARCH_NOTICE}

    @router.get("/backtests/latest")
    def latest_backtest(universe_id: str):
        """读取指定标的池最近一次完整回测产物，作为“成立以来”的缓存源。"""
        root = Path(__file__).parents[2] / "reports"
        reports = list_reports(root, report_type="backtest", query=universe_id)
        radar_reports = [
            report for report in reports
            if report.path.startswith("radar_backtests/") and report.symbol == universe_id
        ]
        if not radar_reports:
            raise HTTPException(status_code=404, detail="尚无该标的池的回测产物")
        try:
            return derive_backtest_window(backtest_payload(root, radar_reports[0]), None)
        except BacktestCacheError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/backtests")
    def get_backtest(universe_id: str, end_date: date, start_date: date | None = None):
        """从覆盖所选区间的完整产物派生窗口，不重复运行策略。"""
        root = Path(__file__).parents[2] / "reports"
        reports = list_reports(root, report_type="backtest", query=universe_id)
        matching = [
            report for report in reports
            if report.path.startswith("radar_backtests/")
            and report.symbol == universe_id
            and report.end_date == end_date.isoformat()
            and (start_date is None or (report.start_date is not None and report.start_date <= start_date.isoformat()))
        ]
        if not matching:
            raise HTTPException(status_code=404, detail="尚无覆盖该区间的回测产物")
        matching.sort(key=lambda report: report.start_date or "9999-12-31")
        for report in matching:
            try:
                result = derive_backtest_window(backtest_payload(root, report), start_date)
            except BacktestCacheError:
                continue
            result["selection"] = {
                "requested_start_date": start_date.isoformat() if start_date else None,
                "source_start_date": report.start_date,
                "source_end_date": report.end_date,
            }
            return result
        raise HTTPException(status_code=404, detail="尚无覆盖该区间的有效回测缓存")

    @router.get("/performance")
    def instrument_performance(universe_id: str, symbol: str, end_date: date, start_date: date | None = None):
        """读取当前 ETF 自身历史表现；不运行或复用池级轮动策略。"""
        if start_date is not None and start_date >= end_date:
            raise HTTPException(status_code=422, detail="表现开始日必须早于结束日")
        repository, _, _ = services()
        try:
            universe = repository.active_on(universe_id, end_date)
        except UniverseConfigError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if symbol not in {item.symbol for item in universe.instruments}:
            raise HTTPException(status_code=404, detail="标的不属于指定 ETF 池")
        effective_start = start_date or date(2005, 1, 1)
        config = Config()
        provider = FallbackETFDataProvider((
            AkShareETFDataProvider(pacer=RequestPacer(config.get("radar.minimum_interval_seconds", 1.0))),
            TencentETFDataProvider(), SinaETFDataProvider(), OfficialExchangeETFDataProvider(),
        ))
        root = Path(__file__).parents[2]
        try:
            history = provider.fetch_daily(symbol, effective_start, end_date)
            catalog = load_benchmarks(root / "config" / "radar_benchmarks.yaml")
            benchmarks = {item.id: fetch_benchmark_closes(item, effective_start, end_date) for item in catalog.benchmarks}
            result = calculate_instrument_performance(history, benchmarks)
        except (RadarDataError, RadarBenchmarkError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"标的历史表现不可用: {exc}") from exc
        return {
            "symbol": symbol,
            "start_date": str(result.equity_curve.index.min()),
            "end_date": str(result.equity_curve.index.max()),
            "metrics": result.metrics,
            "benchmarks": result.benchmark_metrics,
            "equity_curve": {
                "columns": ["date", *result.equity_curve.columns],
                "rows": [{"date": pd.Timestamp(cast(Any, index)).date().isoformat(), **{key: float(value) for key, value in row.items()}} for index, row in result.equity_curve.iterrows()],
            },
            "research_notice": _RESEARCH_NOTICE,
        }

    @router.post("/backtests", status_code=202)
    def start_backtest(body: BacktestRequest):
        """后台运行指定区间回测；相同完整产物由 CLI 自行复用。"""
        if body.start_date >= body.end_date:
            raise HTTPException(status_code=422, detail="回测开始日必须早于结束日")
        with lock:
            if any(item["status"] == "running" for item in tasks.values()):
                raise HTTPException(status_code=409, detail="已有雷达任务在运行")
            task_id = uuid.uuid4().hex
            tasks[task_id] = {"status": "running", "universe_id": body.universe_id}

        def run() -> None:
            root = Path(__file__).parents[2]
            command = [
                sys.executable, "-m", "stock_robot.cli", "radar", "backtest",
                "--universe", body.universe_id, "--start", body.start_date.isoformat(),
                "--end", body.end_date.isoformat(), "--strategy", body.strategy,
            ]
            try:
                completed = subprocess.run(
                    command, cwd=root, capture_output=True, text=True, timeout=600, check=False,
                )
                if completed.returncode:
                    detail = completed.stderr.strip() or completed.stdout.strip() or "回测执行失败"
                    tasks[task_id] = {"status": "failed", "universe_id": body.universe_id, "error": detail}
                else:
                    tasks[task_id] = {"status": "completed", "universe_id": body.universe_id}
            except (OSError, subprocess.TimeoutExpired) as exc:
                tasks[task_id] = {"status": "failed", "universe_id": body.universe_id, "error": str(exc)}

        threading.Thread(target=run, daemon=True).start()
        return {"task_id": task_id, "status": "running"}

    @router.get("/backtests/tasks/{task_id}")
    def backtest_status(task_id: str):
        task = tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="回测任务不存在")
        return task

    @router.post("/refresh", status_code=202)
    def refresh(body: RefreshRequest):
        with lock:
            if any(item["status"] == "running" for item in tasks.values()):
                raise HTTPException(status_code=409, detail="已有雷达刷新任务在运行")
            task_id = uuid.uuid4().hex
            tasks[task_id] = {"status": "running", "universe_id": body.universe_id}

        def run() -> None:
            try:
                _, _, refresher = services()
                run_id = refresher.refresh(body.universe_id, full=body.full, as_of=body.as_of)
                tasks[task_id] = {"status": "completed", "universe_id": body.universe_id, "run_id": run_id}
            except Exception as exc:  # noqa: BLE001 — 后台刷新隔离，错误需反馈至可轮询任务
                tasks[task_id] = {"status": "failed", "universe_id": body.universe_id, "error": str(exc)}

        threading.Thread(target=run, daemon=True).start()
        return {"task_id": task_id, "status": "running"}

    @router.get("/refresh/{task_id}")
    def refresh_status(task_id: str):
        task = tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="刷新任务不存在")
        return task

    return router
