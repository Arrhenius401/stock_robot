from datetime import date, datetime
from typing import Any, Literal
from pydantic import BaseModel, Field


class FinancialData(BaseModel):
    """单期财务数据"""
    symbol: str
    fiscal_quarter: date
    revenue: float
    net_profit: float
    total_assets: float
    total_equity: float
    operating_cash_flow: float
    roe: float | None = None
    gross_margin: float | None = None


class PriceData(BaseModel):
    """日线行情数据"""
    symbol: str
    trade_date: date
    open: float = Field(ge=0)
    high: float = Field(ge=0)
    low: float = Field(ge=0)
    close: float = Field(ge=0)
    volume: int = Field(ge=0)


class ValuationData(BaseModel):
    """估值指标"""
    symbol: str
    date: date
    pe_ttm: float | None = None
    pb: float | None = None
    ps_ttm: float | None = None


class IndustryData(BaseModel):
    """行业分类与同行业公司"""
    symbol: str
    industry: str
    sector: str
    peers: list[str] = Field(default_factory=list)


class NewsData(BaseModel):
    """新闻舆情数据"""
    symbol: str
    date: date
    headlines: list[str] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    """分析模块输出 — 统一结构"""
    dimension: Literal["financial", "technical", "valuation", "industry", "sentiment"]
    status: Literal["ok", "partial", "unavailable"]
    summary: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    charts: list[str] = Field(default_factory=list)


class AnalysisContext(BaseModel):
    """分析上下文 — 管道中传递的完整数据容器"""
    symbol: str
    name: str
    market: str = "a-shares"
    financial_data: list[FinancialData] | None = None
    price_data: list[PriceData] | None = None
    valuation_data: ValuationData | list[ValuationData] | None = None
    industry_data: IndustryData | None = None
    news_data: NewsData | None = None
    collected_at: datetime = Field(default_factory=datetime.now)
