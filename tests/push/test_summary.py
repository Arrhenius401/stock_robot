from data.schemas import AnalysisContext, AnalysisResult
from push.summary import build_index_full, build_index_summary, build_stock_summary
from report.signal import SignalConfig


def _results():
    return [
        AnalysisResult(dimension="financial", status="ok", score=8.0,
                       summary="财务稳健", metrics={"roe": 0.12}),
        AnalysisResult(dimension="technical", status="ok", score=5.0,
                       summary="均线缠绕", metrics={}),
    ]


def _ctx():
    from datetime import date

    from data.schemas import PriceData
    return AnalysisContext(
        symbol="600519", name="贵州茅台",
        price_data=[PriceData(symbol="600519", trade_date=date(2026, 8, 19),
                              open=1, high=2, low=1, close=1500.0, volume=10000,
                              change_pct=2.35)],
    )


def _signal_cfg():
    return SignalConfig()


class TestStockSummary:
    def test_contains_key_info(self):
        text = build_stock_summary("600519", "贵州茅台", _results(), _ctx(), _signal_cfg())
        assert "贵州茅台" in text
        assert "600519" in text
        assert "1500.0" in text
        assert "+2.35%" in text
        assert "操作信号" in text
        assert "综合得分" in text
        assert "财务健康" in text

    def test_signal_level_by_score(self):
        high = build_stock_summary("600519", "贵州茅台",
                                   [AnalysisResult(dimension="financial", status="ok",
                                                   score=9.0, summary="s", metrics={})],
                                   _ctx(), _signal_cfg())
        assert "进攻" in high


class TestIndexSummary:
    def _report(self):
        from datetime import date

        from data.schemas import IndexReport
        return IndexReport(
            code="000300", name="沪深300", date=date(2026, 8, 19),
            overview={"latest_close": 3800.0},
            section_technical={"趋势": "多头排列"}, section_valuation={},
            section_capital={}, section_macro=None, section_sentiment={},
            tag_technical="bull", tag_valuation="neutral", tag_capital="positive",
            tag_macro="na", tag_sentiment="neutral",
            composite_comment="市场情绪回暖", position_coeff=None,
            risk_list=["外围波动"], visible_sections={"technical"},
        )

    def test_summary_contains_tags_and_comment(self):
        text = build_index_summary(self._report())
        assert "沪深300" in text
        assert "技术：bull" in text
        assert "综合点评：市场情绪回暖" in text
        assert "外围波动" in text

    def test_full_contains_sections(self):
        text = build_index_full(self._report())
        assert "技术面" in text
        assert "多头排列" in text
        assert "沪深300" in text


class TestStockFull:
    def test_build_report_reused(self):
        from push.summary import build_stock_full
        text = build_stock_full("600519", "贵州茅台", _results(), {"bulk": "解读"},
                                _ctx(), _signal_cfg())
        assert "贵州茅台" in text
        assert "解读" in text
