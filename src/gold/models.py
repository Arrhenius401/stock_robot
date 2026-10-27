"""黄金事实契约；日期、口径及原始来源必须随数据传递。"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

SourceKey = Literal[
    "domestic", "international", "real_yield", "dollar", "fx",
    "china_reserves", "holdings", "global_reserves",
]


class GoldBar(BaseModel):
    trade_date: date
    open: float
    high: float
    low: float
    close: float

    @model_validator(mode="after")
    def validate_prices(self) -> GoldBar:
        values = (self.open, self.high, self.low, self.close)
        if not all(math.isfinite(value) and value > 0 for value in values):
            raise ValueError("黄金价格必须为有限正数")
        if self.high < max(self.open, self.close, self.low) or self.low > min(self.open, self.close):
            raise ValueError("黄金日线高低价关系无效")
        return self


class GoldObservation(BaseModel):
    observation_date: date
    value: float

    @model_validator(mode="after")
    def validate_value(self) -> GoldObservation:
        if not math.isfinite(self.value):
            raise ValueError("观察值必须有限；缺失值不能填零")
        return self


class GoldSeries(BaseModel):
    key: SourceKey
    name: str
    source: str
    source_url: str
    unit: str
    frequency: Literal["daily", "monthly"] = "daily"
    observations: list[GoldObservation] = Field(default_factory=list)
    bars: list[GoldBar] = Field(default_factory=list)
    fetched_at: AwareDatetime
    published_at: AwareDatetime | None = None
    status: Literal["ok", "stale", "unavailable"] = "ok"
    error: str | None = None
    parser: str | None = None
    notes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def as_of(self) -> date | None:
        days = [point.observation_date for point in self.observations]
        days.extend(bar.trade_date for bar in self.bars)
        return max(days) if days else None


class GoldCollection(BaseModel):
    generated_at: AwareDatetime
    series: dict[str, GoldSeries] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class GoldRules(BaseModel):
    """已确认的初始研究规则，配置变更必须保留版本。"""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    version: str = "gold-v1"
    source_timeout_seconds: float = Field(default=30, gt=0, le=30)
    total_timeout_seconds: float = Field(default=60, gt=0, le=60)
    max_workers: int = Field(default=3, ge=1, le=3)
    daily_cache_seconds: int = Field(default=21600, gt=0)
    closed_cache_seconds: int = Field(default=86400, gt=0)
    monthly_cache_seconds: int = Field(default=86400, gt=0)
    historical_cache_seconds: int = Field(default=2592000, gt=0)
    daily_max_age_days: int = Field(default=10, gt=0)
    fx_max_age_days: int = Field(default=14, gt=0)
    reserves_max_age_days: int = Field(default=90, gt=0)
    core_weight: int = Field(default=2, gt=0)
    reference_weight: int = Field(default=1, gt=0)
    main_window: int = Field(default=120, gt=0)
    marginal_window: int = Field(default=60, gt=0)
    reserve_main_months: int = Field(default=6, gt=0)
    reserve_marginal_months: int = Field(default=3, gt=0)
    rate_threshold_bp: float = Field(default=25, gt=0)
    dollar_threshold_pct: float = Field(default=2, gt=0)
    holdings_threshold_pct: float = Field(default=2, gt=0)
    rate_marginal_threshold_bp: float | None = Field(default=None, gt=0)
    dollar_marginal_threshold_pct: float | None = Field(default=None, gt=0)
    holdings_marginal_threshold_pct: float | None = Field(default=None, gt=0)
    significant_high_percentile: float = Field(default=80, gt=0, lt=100)
    significant_low_percentile: float = Field(default=20, gt=0, lt=100)
    min_change_rank_samples: int = Field(default=1000, gt=0)
    ma_fast: int = Field(default=20, ge=2)
    ma_slow: int = Field(default=60, ge=2)
    ma_slope_window: int = Field(default=20, gt=0)
    flat_slope_pct: float = Field(default=0.5, ge=0)
    rsi_period: int = Field(default=14, gt=0)
    atr_period: int = Field(default=14, gt=0)
    vol_window: int = Field(default=20, ge=2)
    annual_days: int = Field(default=252, ge=2)
    min_vol_rank_samples: int = Field(default=252, gt=0)
    rsi_hot: float = Field(default=70, gt=0, lt=100)
    rsi_exit: float = Field(default=65, gt=0, lt=100)
    rsi_entry_min: float = Field(default=40, ge=0, lt=100)
    rsi_entry_max: float = Field(default=65, gt=0, le=100)
    rsi_oversold: float = Field(default=30, ge=0, lt=100)
    atr_hot: float = Field(default=2, gt=0)
    atr_exit: float = Field(default=1, gt=0)
    vol_high_percentile: float = Field(default=80, gt=0, lt=100)
    price_high_percentile: float = Field(default=85, gt=0, lt=100)
    min_price_3y_samples: int = Field(default=600, gt=0)
    min_price_5y_samples: int = Field(default=1000, gt=0)
    lookback_years: int = Field(default=5, ge=5)

    @model_validator(mode="after")
    def validate_order(self) -> GoldRules:
        if self.main_window <= self.marginal_window or self.ma_slow <= self.ma_fast:
            raise ValueError("主窗口和慢均线必须长于对应短窗口")
        if not self.rsi_entry_min <= self.rsi_entry_max <= self.rsi_exit < self.rsi_hot:
            raise ValueError("RSI 买入、退出与过热门槛顺序无效")
        if self.atr_hot <= self.atr_exit:
            raise ValueError("ATR 风险退出门槛必须低于触发门槛")
        if self.significant_low_percentile >= self.significant_high_percentile:
            raise ValueError("历史变化低分位门槛必须小于高分位门槛")
        if self.reserve_main_months <= self.reserve_marginal_months:
            raise ValueError("储备主观察窗口必须长于边际窗口")
        return self


def local_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))
