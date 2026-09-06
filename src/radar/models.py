"""配置雷达的领域模型。"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_SYMBOL = re.compile(r"^\d{6}$")


class RadarInstrument(BaseModel):
    """一个经过人工审核、允许进入标的池的交易标的。"""

    symbol: str
    name: str = Field(min_length=1)
    asset_type: Literal["etf", "stock"]
    category: str = Field(min_length=1)
    market: str = Field(min_length=1)
    exposure_region: str = Field(min_length=1)
    effective_from: date
    effective_until: date | None = None
    min_avg_amount: float = Field(ge=0)
    leveraged: bool = False
    inverse: bool = False
    single_stock: bool = False

    @model_validator(mode="after")
    def validate_instrument(self) -> RadarInstrument:
        """校验代码、有效期和首期 ETF 的风险边界。"""
        if not _SYMBOL.fullmatch(self.symbol):
            raise ValueError("symbol 必须是 6 位数字代码")
        if self.effective_until is not None and self.effective_until < self.effective_from:
            raise ValueError("effective_until 不能早于 effective_from")
        if self.asset_type == "etf" and (self.leveraged or self.inverse or self.single_stock):
            raise ValueError("首期 ETF 池不允许杠杆、反向或单股 ETF")
        return self


class RadarUniverse(BaseModel):
    """一个同类资产、同一市场边界内的版本化标的池。"""

    id: str
    name: str = Field(min_length=1)
    version: int = Field(ge=1)
    asset_type: Literal["etf", "stock"]
    score_profile: str
    description: str = Field(min_length=1)
    instruments: list[RadarInstrument] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_universe(self) -> RadarUniverse:
        """确保一个池只包含匹配类型且不重复的标的。"""
        if not _IDENTIFIER.fullmatch(self.id):
            raise ValueError("id 必须是小写字母、数字和下划线组成的标识符")
        if not _IDENTIFIER.fullmatch(self.score_profile):
            raise ValueError("score_profile 必须是小写字母、数字和下划线组成的标识符")
        symbols: set[str] = set()
        for instrument in self.instruments:
            if instrument.asset_type != self.asset_type:
                raise ValueError("标的 asset_type 必须与标的池一致")
            if instrument.symbol in symbols:
                raise ValueError(f"标的代码重复: {instrument.symbol}")
            symbols.add(instrument.symbol)
        return self


class RadarStrategy(BaseModel):
    """可复现 ETF 回测所需的评分与交易参数。"""

    id: str
    name: str = Field(min_length=1)
    version: int = Field(ge=1)
    asset_type: Literal["etf", "stock"]
    weights: dict[str, float]
    windows: dict[str, int]
    rebalance: Literal["monthly"]
    execution: Literal["next_open"]
    cost_profile: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_strategy(self) -> RadarStrategy:
        """阻止无效权重进入回测，避免产生不可解释的结果。"""
        if not _IDENTIFIER.fullmatch(self.id):
            raise ValueError("策略 id 必须是小写字母、数字和下划线组成的标识符")
        if set(self.weights) != {"trend", "drawdown", "volatility", "liquidity"}:
            raise ValueError("策略 weights 必须包含四个 ETF 因子")
        if any(value < 0 for value in self.weights.values()) or sum(self.weights.values()) <= 0:
            raise ValueError("策略 weights 必须为非负且总和大于零")
        if any(value <= 0 for value in self.windows.values()):
            raise ValueError("策略 windows 必须为正整数")
        return self

    @property
    def fingerprint(self) -> str:
        """返回规范化配置的短 SHA-256 指纹，供产物审计。"""
        payload = self.model_dump(mode="json")
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class SnapshotItem(BaseModel):
    """一个快照中某标的的可展示观测。"""

    symbol: str
    name: str
    category: str
    status: Literal["fresh", "stale", "failed"]
    observed_at: datetime | None = None
    source_run_id: str | None = None
    close: float | None = None
    amount: float | None = None
    score: float | None = None
    rank: int | None = None
    grade: Literal["偏好", "观察", "谨慎", "unavailable"] = "unavailable"
    error_summary: str | None = None
