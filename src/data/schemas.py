from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal
from pydantic import BaseModel, Field


class SufficiencyLevel(StrEnum):
    SUFFICIENT = "sufficient"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"


class DimensionSufficiency(BaseModel):
    """单个维度的数据充足状态"""
    level: SufficiencyLevel
    reason: str = ""
    sample_count: int = 0
    score_weight: float = 1.0  # sufficient=1.0, partial=0.5, insufficient=0.0


class DataSufficiency(BaseModel):
    """五个维度的充足状态汇总"""
    price: DimensionSufficiency
    financial: DimensionSufficiency
    valuation: DimensionSufficiency
    industry: DimensionSufficiency
    sentiment: DimensionSufficiency


class DailyValuationPoint(BaseModel):
    """日频估值单点"""
    trade_date: date
    close: float
    pe: float | None = None
    pb: float | None = None
    ps: float | None = None


class EnrichedValuation(BaseModel):
    """充实后的估值数据 — 日频 PE/PB/PS 序列"""
    daily_points: list[DailyValuationPoint] = Field(default_factory=list)
    pe_percentile: float | None = None
    pb_percentile: float | None = None
    pe_zone: str = ""           # "低估" / "中性" / "高估"
    pe_median: float | None = None
    pe_high: float | None = None
    pe_low: float | None = None


class PeerComparison(BaseModel):
    """同行对比单家公司"""
    symbol: str
    name: str = ""
    market_cap: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None
    gross_margin: float | None = None
    net_margin: float | None = None
    roe: float | None = None


class EnrichedIndustry(BaseModel):
    """充实后的行业数据"""
    peer_count: int = 0
    top_peers: list[PeerComparison] = Field(default_factory=list)
    industry_median_pe: float | None = None
    industry_median_pb: float | None = None
    industry_median_gross_margin: float | None = None
    industry_median_net_margin: float | None = None
    target_pe_premium: float | None = None      # 正数=溢价，负数=折价
    target_market_cap_rank: int | None = None


class RawSentimentItem(BaseModel):
    """舆情原始条目"""
    title: str
    source: str  # "news" 或 "announcement"
    publish_date: date
    content: str = ""


class RawSentimentData(BaseModel):
    """采集层输出的舆情原始数据"""
    symbol: str
    fetch_date: date
    items: list[RawSentimentItem] = Field(default_factory=list)


class SentimentItem(BaseModel):
    """LLM 标注后的舆情条目"""
    title: str
    summary: str
    tendency: str  # "positive" / "neutral" / "negative"
    severity: str  # "minor" / "moderate" / "major"
    event_type: str = ""
    source: str = ""


class EnrichedSentiment(BaseModel):
    """充实后的舆情数据"""
    total_count: int = 0
    positive_count: int = 0
    neutral_count: int = 0
    negative_count: int = 0
    major_events: list[SentimentItem] = Field(default_factory=list)
    all_items: list[SentimentItem] = Field(default_factory=list)


class FinancialData(BaseModel):
    """单期财务数据"""
    symbol: str
    fiscal_quarter: date
    revenue: float | None = None
    net_profit: float | None = None
    deducted_net_profit: float | None = None  # 扣非净利润
    total_assets: float | None = None
    total_equity: float | None = None
    operating_cash_flow: float | None = None
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


class PeerBasicInfo(BaseModel):
    """同行公司基础信息"""
    symbol: str
    name: str = ""
    market_cap: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None


class IndustryData(BaseModel):
    """行业分类与同行业公司"""
    symbol: str
    industry: str
    sector: str
    peers: list[str] = Field(default_factory=list)
    # 新增：头部同行详细数据
    top_peers: list[PeerBasicInfo] = Field(default_factory=list)


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

    # 新增：量化打分
    score: float | None = None
    score_detail: str = ""
    risk_flags: list[str] = Field(default_factory=list)

    # 新增 — 行业特定说明
    industry_note: str = ""


class AnalysisContext(BaseModel):
    """分析上下文 — 管道中传递的完整数据容器"""
    symbol: str
    name: str
    market: str = "a-shares"
    financial_data: list[FinancialData] | None = None
    price_data: list[PriceData] | None = None
    valuation_data: ValuationData | None = None  # 只存当日单时点，日频序列走 enriched_valuation
    industry_data: IndustryData | None = None
    news_data: NewsData | None = None
    collected_at: datetime = Field(default_factory=datetime.now)

    # 采集层 — 舆情原始数据
    raw_sentiment: RawSentimentData | None = None

    # 充实层产出 — 若对应维度完全拉取失败则为 None
    sufficiency: DataSufficiency | None = None
    enriched_valuation: EnrichedValuation | None = None
    enriched_industry: EnrichedIndustry | None = None
    enriched_sentiment: EnrichedSentiment | None = None

    # 新增 — 行业分类信息
    sw_industry: str = ""
    style_category: str = ""
