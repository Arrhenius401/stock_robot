"""回测策略实体。"""

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_validator

from report.signal import Signal

ID_PATTERN = re.compile(r"^[a-z0-9_]+$")


@dataclass(frozen=True, slots=True)
class MacdValues:
    """MACD 结果值对象。"""

    dif: float
    dea: float
    bar: float


class BacktestStrategy(BaseModel):
    """单股回测策略配置。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    version: int
    signal_source: Literal["technical_score"]
    thresholds: dict[Signal, float]
    target_positions: dict[Signal, float]
    execution: Literal["next_open"]
    warmup_days: int
    cost_profile: str

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if not ID_PATTERN.fullmatch(value):
            raise ValueError("id 仅允许小写字母、数字和下划线")
        return value

    @field_validator("name", "cost_profile")
    @classmethod
    def _validate_non_empty_text(cls, value: str, info: ValidationInfo) -> str:
        if not value.strip():
            raise ValueError(f"{info.field_name} 不能为空")
        return value

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: int) -> int:
        if value < 1:
            raise ValueError("version 必须大于等于 1")
        return value

    @field_validator("warmup_days")
    @classmethod
    def _validate_warmup_days(cls, value: int) -> int:
        if value < 60:
            raise ValueError("warmup_days 必须不少于 60")
        return value

    @field_validator("thresholds")
    @classmethod
    def _validate_thresholds(cls, value: dict[Signal, float]) -> dict[Signal, float]:
        expected = {"attack", "watch"}
        if set(value) != expected:
            raise ValueError("thresholds 必须且只能包含 attack、watch")
        attack = cls._require_float(value["attack"], "thresholds.attack")
        watch = cls._require_float(value["watch"], "thresholds.watch")
        if not (0 < watch < attack <= 10):
            raise ValueError("thresholds 必须满足 0 < watch < attack <= 10")
        return {"attack": attack, "watch": watch}

    @field_validator("target_positions")
    @classmethod
    def _validate_target_positions(
        cls, value: dict[Signal, float]
    ) -> dict[Signal, float]:
        expected = {"attack", "watch", "defend"}
        if set(value) != expected:
            raise ValueError("target_positions 必须且只能包含 attack、watch、defend")
        attack = cls._require_unit_float(value["attack"], "target_positions.attack")
        watch = cls._require_unit_float(value["watch"], "target_positions.watch")
        defend = cls._require_unit_float(value["defend"], "target_positions.defend")
        if not (attack >= watch >= defend):
            raise ValueError("target_positions 必须满足 attack >= watch >= defend")
        return {"attack": attack, "watch": watch, "defend": defend}

    @staticmethod
    def _require_float(value: float, field_name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{field_name} 必须是数字")
        return float(value)

    @staticmethod
    def _require_unit_float(value: float, field_name: str) -> float:
        numeric = BacktestStrategy._require_float(value, field_name)
        if not 0.0 <= numeric <= 1.0:
            raise ValueError(f"{field_name} 必须在 0 到 1 之间")
        return numeric
