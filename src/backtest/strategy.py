"""策略仓库与配置指纹。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from pydantic import ValidationError

from backtest.models import BacktestStrategy


class StrategyConfigError(Exception):
    """策略配置异常。"""


class StrategyRepository:
    """从目录加载回测策略 YAML。"""

    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def load_all(self) -> list[BacktestStrategy]:
        if not self.directory.exists():
            raise StrategyConfigError(f"策略目录不存在: {self.directory}")

        strategies: list[BacktestStrategy] = []
        seen: dict[str, Path] = {}
        for path in sorted(self.directory.glob("*.yaml"), key=lambda item: item.name):
            strategy = self._load_strategy(path)
            if strategy.id in seen:
                first = seen[strategy.id]
                raise StrategyConfigError(
                    f"策略 ID 重复: {strategy.id}（{first.name} 与 {path.name}）"
                )
            seen[strategy.id] = path
            strategies.append(strategy)
        return strategies

    def get(self, strategy_id: str) -> BacktestStrategy:
        for strategy in self.load_all():
            if strategy.id == strategy_id:
                return strategy
        raise StrategyConfigError(f"未找到策略: {strategy_id}")

    @staticmethod
    def _load_strategy(path: Path) -> BacktestStrategy:
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
        except OSError as exc:
            raise StrategyConfigError(f"读取策略文件失败: {path}") from exc
        except yaml.YAMLError as exc:
            raise StrategyConfigError(f"解析策略文件失败: {path}") from exc

        if not isinstance(raw, dict):
            raise StrategyConfigError(f"策略文件内容必须是映射: {path}")

        try:
            return BacktestStrategy.model_validate(raw)
        except ValidationError as exc:
            raise StrategyConfigError(f"策略配置无效: {path}") from exc


def strategy_fingerprint(strategy: BacktestStrategy) -> str:
    """对标准化策略内容生成 8 位短哈希。"""

    payload = json.dumps(
        strategy.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]
