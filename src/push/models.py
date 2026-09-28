"""订阅模型 — 推送订阅的数据结构"""
import re
from typing import Literal

from pydantic import BaseModel, Field, StrictBool, field_validator

Channel = Literal["email"]
SymbolKind = Literal["stock", "index", "auto"]
IndexStyle = Literal["broad", "sector", "overseas"]

TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class SubscriptionSymbol(BaseModel):
    """订阅标的 — 显式类型解决 000001（上证指数 vs 平安银行）代码歧义"""
    symbol: str = Field(min_length=1, strict=True)
    kind: SymbolKind = "auto"
    index_style: IndexStyle | None = None
    display_name: str = ""


class Subscription(BaseModel):
    """一个每日推送订阅：标的集合 + 渠道 + 时间"""
    id: int | None = None
    name: str = Field(min_length=1, strict=True)
    symbols: list[SubscriptionSymbol] = Field(min_length=1)
    channel: Channel
    time: str = Field(strict=True)
    enabled: StrictBool = True
    created_at: str = ""
    updated_at: str = ""

    @field_validator("symbols", mode="before")
    @classmethod
    def _validate_symbols(cls, v) -> list[SubscriptionSymbol]:
        # mode=before：先于 list[SubscriptionSymbol] 类型校验转换，
        # 使字符串项等价 {"symbol": s, "kind": "auto"}，兼容 API 简写
        items: list[SubscriptionSymbol] = []
        if not isinstance(v, list):
            raise ValueError("symbols 必须是数组")  # noqa: TRY004 — Pydantic v2 不包装 TypeError
        for raw in v:
            if isinstance(raw, str):
                symbol = raw.strip()
                if symbol:
                    items.append(SubscriptionSymbol(symbol=symbol))
            elif isinstance(raw, SubscriptionSymbol):
                items.append(raw)
            elif isinstance(raw, dict):
                symbol_value = raw.get("symbol")
                if not isinstance(symbol_value, str):
                    raise ValueError("symbol 必须是字符串")  # noqa: TRY004 — Pydantic v2 不包装 TypeError
                symbol = symbol_value.strip()
                if symbol:
                    items.append(SubscriptionSymbol(
                        symbol=symbol,
                        kind=raw.get("kind", "auto"),
                        index_style=raw.get("index_style"),
                        display_name=str(raw.get("display_name") or ""),
                    ))
            else:
                raise ValueError("symbols 项必须是代码或对象")  # noqa: TRY004 — Pydantic v2 不包装 TypeError
        if not items:
            raise ValueError("symbols 不能为空")
        return items

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name 不能为空")
        return normalized

    @field_validator("time")
    @classmethod
    def _validate_time(cls, v: str) -> str:
        if not TIME_PATTERN.match(v):
            raise ValueError("time 格式必须为 HH:MM")
        return v
