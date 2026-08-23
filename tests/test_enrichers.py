"""充实器单元测试"""
from datetime import date, datetime, timedelta

from data.enrichers.financial_enricher import FinancialEnricher
from data.enrichers.industry_enricher import IndustryEnricher
from data.enrichers.price_enricher import PriceEnricher
from data.enrichers.sentiment_enricher import SentimentEnricher
from data.enrichers.valuation_enricher import ValuationEnricher
from data.schemas import (
    AnalysisContext,
    FinancialData,
    IndustryData,
    PeerBasicInfo,
    PriceData,
    RawSentimentData,
    RawSentimentItem,
    SufficiencyLevel,
    ValuationData,
)


def make_price_data(n: int) -> list[PriceData]:
    """生成 n 条行情数据"""
    return [
        PriceData(
            symbol="000001",
            trade_date=date(2026, 1, 1) + timedelta(days=i),
            open=10.0,
            high=10.5,
            low=9.8,
            close=10.2,
            volume=1000000,
        )
        for i in range(n)
    ]


class TestPriceEnricher:
    def test_sufficient_60_plus(self):
        ctx = AnalysisContext(symbol="000001", name="测试", price_data=make_price_data(120))
        enricher = PriceEnricher()
        result = enricher.enrich(ctx)
        assert result.sufficiency.price.level == SufficiencyLevel.SUFFICIENT
        assert result.sufficiency.price.score_weight == 1.0
        assert result.sufficiency.price.sample_count == 120

    def test_partial_20_to_59(self):
        ctx = AnalysisContext(symbol="000001", name="测试", price_data=make_price_data(40))
        enricher = PriceEnricher()
        result = enricher.enrich(ctx)
        assert result.sufficiency.price.level == SufficiencyLevel.PARTIAL
        assert result.sufficiency.price.score_weight == 0.5

    def test_insufficient_below_20(self):
        ctx = AnalysisContext(symbol="000001", name="测试", price_data=make_price_data(10))
        enricher = PriceEnricher()
        result = enricher.enrich(ctx)
        assert result.sufficiency.price.level == SufficiencyLevel.INSUFFICIENT
        assert result.sufficiency.price.score_weight == 0.0

    def test_none_price_data_is_insufficient(self):
        ctx = AnalysisContext(symbol="000001", name="测试", price_data=None)
        enricher = PriceEnricher()
        result = enricher.enrich(ctx)
        assert result.sufficiency.price.level == SufficiencyLevel.INSUFFICIENT


def make_financial_data(n: int) -> list[FinancialData]:
    return [
        FinancialData(
            symbol="000001",
            fiscal_quarter=date(2025, 12 - i * 3, 1) if (12 - i * 3) > 0 else date(2025, 12, 1),
            revenue=100e8, net_profit=10e8, deducted_net_profit=9e8,
            total_assets=500e8, total_equity=50e8, operating_cash_flow=8e8,
            roe=0.12, gross_margin=0.45,
        )
        for i in range(n)
    ]


class TestFinancialEnricher:
    def test_sufficient_4_plus_complete(self):
        ctx = AnalysisContext(symbol="000001", name="测试",
                              financial_data=make_financial_data(6))
        ctx = PriceEnricher().enrich(ctx)
        ctx = FinancialEnricher().enrich(ctx)
        assert ctx.sufficiency.financial.level == SufficiencyLevel.SUFFICIENT
        assert ctx.sufficiency.financial.score_weight == 1.0
        assert ctx.sufficiency.financial.sample_count == 6

    def test_partial_2_to_3(self):
        ctx = AnalysisContext(symbol="000001", name="测试",
                              financial_data=make_financial_data(2))
        ctx = PriceEnricher().enrich(ctx)
        ctx = FinancialEnricher().enrich(ctx)
        assert ctx.sufficiency.financial.level == SufficiencyLevel.PARTIAL
        assert ctx.sufficiency.financial.score_weight == 0.5

    def test_insufficient_less_than_2(self):
        ctx = AnalysisContext(symbol="000001", name="测试",
                              financial_data=make_financial_data(1))
        ctx = PriceEnricher().enrich(ctx)
        ctx = FinancialEnricher().enrich(ctx)
        assert ctx.sufficiency.financial.level == SufficiencyLevel.INSUFFICIENT
        assert ctx.sufficiency.financial.score_weight == 0.0

    def test_insufficient_missing_profits(self):
        data = make_financial_data(4)
        for d in data:
            d.net_profit = None
        ctx = AnalysisContext(symbol="000001", name="测试", financial_data=data)
        ctx = PriceEnricher().enrich(ctx)
        ctx = FinancialEnricher().enrich(ctx)
        assert ctx.sufficiency.financial.level == SufficiencyLevel.INSUFFICIENT


def make_price_series(n: int, close: float = 10.0) -> list[PriceData]:
    return [
        PriceData(symbol="000001", trade_date=date(2025, 7, 1) + timedelta(days=i),
                  open=close-0.1, high=close+0.1, low=close-0.2, close=close,
                  volume=1000000)
        for i in range(n)
    ]


class TestValuationEnricher:
    def test_insufficient_when_price_partial(self):
        """行情数据不足 60 条时，估值标记为 insufficient"""
        # 29 条：价格维度仍为 partial（20-59），但有效估值点数 29 < 30 → 估值 insufficient
        prices = make_price_series(29)
        financials = make_financial_data(4)
        ctx = AnalysisContext(symbol="000001", name="测试",
                              price_data=prices, financial_data=financials)
        ctx = PriceEnricher().enrich(ctx)
        ctx = FinancialEnricher().enrich(ctx)
        ctx = ValuationEnricher().enrich(ctx)
        assert ctx.sufficiency.valuation.level == SufficiencyLevel.INSUFFICIENT

    def test_insufficient_no_financial(self):
        """无财务数据时标记 insufficient"""
        prices = make_price_series(200)
        ctx = AnalysisContext(symbol="000001", name="测试",
                              price_data=prices, financial_data=None)
        ctx = PriceEnricher().enrich(ctx)
        ctx = ValuationEnricher().enrich(ctx)
        assert ctx.sufficiency.valuation.level == SufficiencyLevel.INSUFFICIENT

    def test_sufficient_with_valid_data(self):
        """有足够的行情和财务数据时正常生成估值序列"""
        prices = make_price_series(200, close=10.0)
        financials = make_financial_data(4)
        ctx = AnalysisContext(symbol="000001", name="测试",
                              price_data=prices, financial_data=financials)
        # 模拟 valuation_data（当前单时点估值，用于总股本回退逻辑）
        ctx.valuation_data = ValuationData(symbol="000001", date=datetime.now().astimezone().astimezone().date(),
                                           pe_ttm=7.5, pb=0.85, ps_ttm=1.2)
        ctx = PriceEnricher().enrich(ctx)
        ctx = FinancialEnricher().enrich(ctx)
        ctx = ValuationEnricher().enrich(ctx)
        assert ctx.sufficiency.valuation.level == SufficiencyLevel.SUFFICIENT
        assert ctx.enriched_valuation is not None
        assert len(ctx.enriched_valuation.daily_points) > 0
        assert ctx.enriched_valuation.pe_percentile is not None


class TestIndustryEnricher:
    def test_sufficient_with_5_peers(self):
        top_peers = [
            PeerBasicInfo(symbol=f"60000{i}", name=f"公司{i}", market_cap=1000e8)
            for i in range(5)
        ]
        ind_data = IndustryData(symbol="000001", industry="银行", sector="金融",
                                peers=[p.symbol for p in top_peers], top_peers=top_peers)
        ctx = AnalysisContext(symbol="000001", name="测试", industry_data=ind_data)
        ctx = PriceEnricher().enrich(ctx)
        ctx = IndustryEnricher().enrich(ctx)
        # 5 家同行 < 8，所以是 partial，不是 insufficient
        assert ctx.sufficiency.industry.level != SufficiencyLevel.INSUFFICIENT
        assert ctx.enriched_industry is not None
        assert ctx.enriched_industry.peer_count == 5

    def test_insufficient_no_industry(self):
        ctx = AnalysisContext(symbol="000001", name="测试", industry_data=None)
        ctx = PriceEnricher().enrich(ctx)
        ctx = IndustryEnricher().enrich(ctx)
        assert ctx.sufficiency.industry.level == SufficiencyLevel.INSUFFICIENT


def make_raw_sentiment(n: int) -> RawSentimentData:
    items = [
        RawSentimentItem(
            title=f"测试标题 {i}", source="news", publish_date=datetime.now().astimezone().astimezone().date(),
            content=f"测试内容 {i}",
        )
        for i in range(n)
    ]
    return RawSentimentData(symbol="000001", fetch_date=datetime.now().astimezone().astimezone().date(), items=items)


class TestSentimentEnricher:
    def test_sufficient_10_plus(self):
        ctx = AnalysisContext(symbol="000001", name="测试",
                              raw_sentiment=make_raw_sentiment(15))
        ctx = PriceEnricher().enrich(ctx)
        ctx = SentimentEnricher().enrich(ctx)
        assert ctx.sufficiency.sentiment.level == SufficiencyLevel.SUFFICIENT
        assert ctx.sufficiency.sentiment.score_weight == 1.0

    def test_partial_3_to_9(self):
        ctx = AnalysisContext(symbol="000001", name="测试",
                              raw_sentiment=make_raw_sentiment(5))
        ctx = PriceEnricher().enrich(ctx)
        ctx = SentimentEnricher().enrich(ctx)
        assert ctx.sufficiency.sentiment.level == SufficiencyLevel.PARTIAL
        assert ctx.sufficiency.sentiment.score_weight == 0.5

    def test_insufficient_below_3(self):
        ctx = AnalysisContext(symbol="000001", name="测试",
                              raw_sentiment=make_raw_sentiment(1))
        ctx = PriceEnricher().enrich(ctx)
        ctx = SentimentEnricher().enrich(ctx)
        assert ctx.sufficiency.sentiment.level == SufficiencyLevel.INSUFFICIENT
        assert ctx.sufficiency.sentiment.score_weight == 0.0

    def test_insufficient_no_data(self):
        ctx = AnalysisContext(symbol="000001", name="测试", raw_sentiment=None)
        ctx = PriceEnricher().enrich(ctx)
        ctx = SentimentEnricher().enrich(ctx)
        assert ctx.sufficiency.sentiment.level == SufficiencyLevel.INSUFFICIENT


class TestValuationAnchoring:
    def test_anchor_shares_from_measured_pe(self, mocker):
        """实测 PE 存在时反推总股本锚点，历史序列用锚点重算"""
        prices = make_price_series(200, close=10.0)
        financials = make_financial_data(4)
        # 使 TTM 净利润 = 40 亿：最近4期各 10 亿（make_financial_data 结构见文件头部）
        for f in financials:
            f.net_profit = 10e8
            f.total_equity = 200e8
        # 锚定路径只做离线估算偏差告警，不应触发 get_total_shares 网络链
        get_total_shares = mocker.patch(
            "data.enrichers.valuation_enricher.get_total_shares")
        ctx = AnalysisContext(symbol="000001", name="测试",
                              price_data=prices, financial_data=financials)
        ctx.valuation_data = ValuationData(
            symbol="000001", date=datetime.now().astimezone().date(),
            pe_ttm=8.0, pb=1.6, ps_ttm=None)
        ctx = PriceEnricher().enrich(ctx)
        ctx = FinancialEnricher().enrich(ctx)
        ctx = ValuationEnricher().enrich(ctx)
        # 锚点：shares = 8.0 * 40亿 / 10.0 = 32 亿股
        # 序列首点 PE = 32亿 * 10.0 / 40亿 = 8.0
        assert ctx.enriched_valuation is not None
        assert ctx.enriched_valuation.daily_points[0].pe == 8.0
        assert ctx.enriched_valuation.pe_percentile is not None
        # 实测锚定 → 标记为已校验
        assert ctx.enriched_valuation.validated is True
        # 锚定路径不调用网络三级链
        get_total_shares.assert_not_called()

    def test_no_measured_pe_uses_chain_shares(self, mocker):
        """无实测 PE 时用 get_total_shares 链估算，标记未经校验"""
        prices = make_price_series(200, close=10.0)
        financials = make_financial_data(4)
        for f in financials:
            f.net_profit = 10e8
            f.total_equity = 200e8
        ctx = AnalysisContext(symbol="000001", name="测试",
                              price_data=prices, financial_data=financials)
        # valuation_data 为空（快照失败）
        mocker.patch("data.enrichers.valuation_enricher.get_total_shares",
                     return_value=32e8)
        ctx = PriceEnricher().enrich(ctx)
        ctx = FinancialEnricher().enrich(ctx)
        ctx = ValuationEnricher().enrich(ctx)
        assert ctx.enriched_valuation is not None
        assert ctx.enriched_valuation.daily_points[0].pe == 8.0
        # 未经校验标记（enriched_valuation 增加字段 validated: bool = False）
        assert ctx.enriched_valuation.validated is False
