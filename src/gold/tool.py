"""将统一黄金服务暴露给现有 Agent 工具系统。"""
import asyncio
import logging
from typing import Any

from agent.tools import ToolResult

logger = logging.getLogger(__name__)


class AnalyzeGoldTool:
    def __init__(self, service: Any):
        self.name = "analyze_gold"
        self.description = "生成黄金价格综合研报，覆盖国内与国际金价、长期配置及短期技术判断。仅分析金价，不分析黄金股票、ETF或首饰。"
        self.parameters = {"type": "object", "properties": {}, "additionalProperties": False}
        self.tags = ["pipeline", "gold", "analysis", "黄金"]
        self.source = "pipeline"
        self._service = service

    async def execute(self, **kwargs) -> ToolResult:
        try:
            payload = await asyncio.to_thread(self._service.run)
            return ToolResult(status="success", data=payload, metadata={"source": "gold", "symbol": "GOLD"})
        except Exception as exc:  # noqa: BLE001, RUF100 — 工具执行隔离，单项故障不终止 Agent
            logger.exception("黄金工具执行失败")
            return ToolResult(status="error", error=str(exc), metadata={"source": "gold"})
