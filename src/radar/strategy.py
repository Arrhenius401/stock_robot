"""配置雷达策略配置的加载。"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from radar.models import RadarStrategy


class StrategyConfigError(Exception):
    """策略配置不合法。"""


class StrategyRepository:
    """加载版本化策略 YAML，避免 CLI 将未校验字典传入回测。"""

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def get(self, strategy_id: str) -> RadarStrategy:
        """按策略 ID 获取唯一策略。"""
        path = self.directory / f"{strategy_id}.yaml"
        try:
            with path.open(encoding="utf-8") as file:
                raw = yaml.safe_load(file)
        except OSError as exc:
            raise StrategyConfigError(f"读取策略失败: {strategy_id}") from exc
        except yaml.YAMLError as exc:
            raise StrategyConfigError(f"解析策略失败: {strategy_id}") from exc
        if not isinstance(raw, dict):
            raise StrategyConfigError(f"策略文件内容必须是映射: {strategy_id}")
        try:
            strategy = RadarStrategy.model_validate(raw)
        except ValidationError as exc:
            raise StrategyConfigError(f"策略配置无效: {strategy_id}") from exc
        if strategy.id != strategy_id:
            raise StrategyConfigError(f"策略文件名与策略 ID 不一致: {strategy_id}")
        return strategy
