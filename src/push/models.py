"""订阅模型 — 推送订阅的数据结构"""
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Channel = Literal["email", "wecom"]
SymbolKind = Literal["stock", "index", "auto"]
IndexStyle = Literal["broad", "sector", "overseas"]

TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class SubscriptionSymbol(BaseModel):
    """订阅标的 — 显式类型解决 000001（上证指数 vs 平安银行）代码歧义"""
    symbol: str
    kind: SymbolKind = "auto"
    index_style: IndexStyle | None = None


class Subscription(BaseModel):
    """一个每日推送订阅：标的集合 + 渠道 + 时间"""
    id: int | None = None
    name: str
    symbols: list[SubscriptionSymbol] = Field(min_length=1)
    channel: Channel
    time: str
    enabled: bool = True
    created_at: str = ""

    @field_validator("symbols", mode="before")
    @classmethod
    def _validate_symbols(cls, v) -> list[SubscriptionSymbol]:
        # mode=before：先于 list[SubscriptionSymbol] 类型校验转换，
        # 使字符串项等价 {"symbol": s, "kind": "auto"}，兼容 API 简写
        items: list[SubscriptionSymbol] = []
        for raw in v:
            if isinstance(raw, str):
                symbol = raw.strip()
                if symbol:
                    items.append(SubscriptionSymbol(symbol=symbol))
            elif isinstance(raw, SubscriptionSymbol):
                items.append(raw)
            elif isinstance(raw, dict):
                symbol = str(raw.get("symbol", "")).strip()
                if symbol:
                    items.append(SubscriptionSymbol(
                        symbol=symbol,
                        kind=raw.get("kind", "auto"),
                        index_style=raw.get("index_style"),
                    ))
        if not items:
            raise ValueError("symbols 不能为空")
        return items

    @field_validator("time")
    @classmethod
    def _validate_time(cls, v: str) -> str:
        if not TIME_PATTERN.match(v):
            raise ValueError("time 格式必须为 HH:MM")
        return v
