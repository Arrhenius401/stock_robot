"""美股估值：亏损时区分“不适用”与“数据缺失”。"""
from datetime import date

from analysis.valuation import ValuationAnalyzer
from data.schemas import (
    AnalysisContext,
    DataSufficiency,
    DimensionSufficiency,
    FinancialData,
    SufficiencyLevel,
    ValuationData,
)


def _context(net_profit: float) -> AnalysisContext:
    financials = [
        FinancialData(symbol="LOSS", fiscal_quarter=date(2026, month, 30),
                      revenue=10, net_profit=net_profit)
        for month in (3, 6, 9, 12)
    ]
    return AnalysisContext(
        symbol="LOSS", name="示例公司", market="us",
        valuation_data=ValuationData(
            symbol="LOSS", date=date(2026, 11, 13), pe_ttm=None, pb=1.2,
        ),
        financial_data=financials,
        sufficiency=DataSufficiency(valuation=DimensionSufficiency(
            level=SufficiencyLevel.SUFFICIENT, sample_count=4,
        )),
    )


def test_us_negative_ttm_marks_pe_not_applicable():
    result = ValuationAnalyzer().analyze(_context(-1.0))
    assert result.status == "partial"
    assert any("最近四个财季净利润为负，PE 不适用" in note
               for note in result.metrics["valuation_notes"])
    assert "亏损" in result.summary


def test_us_missing_pe_without_loss_stays_missing():
    result = ValuationAnalyzer().analyze(_context(1.0))
    assert any("PE(TTM)：暂无数据" in note for note in result.metrics["valuation_notes"])
    assert "亏损" not in result.summary
