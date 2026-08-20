"""订阅模型 — 推送订阅的数据结构"""
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Channel = Literal["email", "wecom"]

TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class Subscription(BaseModel):
    """一个每日推送订阅：标的集合 + 渠道 + 时间"""
    id: int | None = None
    name: str
    symbols: list[str] = Field(min_length=1)
    channel: Channel
    time: str
    enabled: bool = True
    created_at: str = ""

    @field_validator("symbols")
    @classmethod
    def _validate_symbols(cls, v: list[str]) -> list[str]:
        cleaned = [s.strip() for s in v if s and s.strip()]
        if not cleaned:
            raise ValueError("symbols 不能为空")
        return cleaned

    @field_validator("time")
    @classmethod
    def _validate_time(cls, v: str) -> str:
        if not TIME_PATTERN.match(v):
            raise ValueError("time 格式必须为 HH:MM")
        return v
