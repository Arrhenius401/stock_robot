"""配置雷达的快照读取与手动刷新 API。"""

from __future__ import annotations

import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.report_library import ReportLibraryError, get_report_detail, list_reports
from radar.data import (
    AkShareETFDataProvider,
    FallbackETFDataProvider,
    OfficialExchangeETFDataProvider,
    RequestPacer,
    SinaETFDataProvider,
    TencentETFDataProvider,
)
from radar.refresh import RadarRefresher
from radar.score_profile import ScoreProfileConfigError, ScoreProfileRepository
from radar.store import RadarStore
from radar.universe import UniverseRepository
from utils.config import Config


class RefreshRequest(BaseModel):
    """启动一次指定池刷新的请求体。"""

    universe_id: str
    full: bool = False
    as_of: date | None = None


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
        """读取指定标的池最近一次完整回测产物，不在请求时重新计算。"""
        root = Path(__file__).parents[2] / "reports"
        reports = list_reports(root, report_type="backtest", query=universe_id)
        radar_reports = [
            report for report in reports
            if report.path.startswith("radar_backtests/") and report.symbol == universe_id
        ]
        if not radar_reports:
            raise HTTPException(status_code=404, detail="尚无该标的池的回测产物")
        try:
            detail = get_report_detail(root, radar_reports[0].id)
        except ReportLibraryError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        return {**detail.to_dict(), "research_notice": _RESEARCH_NOTICE}

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
