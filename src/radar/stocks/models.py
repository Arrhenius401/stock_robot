"""独立股票定义和真实空态。"""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

STOCK_UNAVAILABLE_REASON = "选股规则和采集流程尚未配置"


class StockPool(BaseModel):
    """定义不保存成员或未经确认的参数。"""
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1)
    asset_type: Literal["stock"] = "stock"
    description: str = Field(min_length=1)
    display_order: int = Field(ge=0)
    enabled: bool = True
    rule_status: Literal["unconfigured"] = "unconfigured"
    rule_version: None = None


class StockMembers(BaseModel):
    """不使用研究样本充当成员。"""
    pool_id: str
    revision: Literal[0] = 0
    items: list[dict[str, Any]] = Field(default_factory=list)
    industries: list[dict[str, Any]] = Field(default_factory=list)
    status: Literal["empty"] = "empty"
    unavailable_reason: str = STOCK_UNAVAILABLE_REASON
