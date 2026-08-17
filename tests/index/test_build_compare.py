"""指数横向对比测试"""
from datetime import datetime
from typing import Literal, cast

import pytest

from src.data.schemas import (
    AnalysisTarget,
    IndexAnalysisContext,
    IndexReport,
    IndexValuationData,
)
from src.index.build_compare import CompareTable, IndexCompareReportBuilder


@pytest.fixture
def compare_data():
    reports = []
    contexts = []
    for code, name, style in [
        ("000300", "沪深300", "broad"),
        ("000905", "中证500", "broad"),
        ("399006", "创业板指", "broad"),
    ]:
        target = AnalysisTarget(
            target_type="index", symbol=code,
            name=name, market="a-shares",
            index_style=cast(Literal["broad", "sector", "overseas"], style),
        )
        ctx = IndexAnalysisContext(target=target)
        ctx.valuation_data = IndexValuationData(
            symbol=code, date=datetime.now().astimezone().astimezone().date(), pe_ttm=12.0, pe_percentile=50.0,
            valuation_valid=True
        )
        contexts.append(ctx)
        reports.append(IndexReport(
            code=code, name=name, date=datetime.now().astimezone().astimezone().date(),
            overview={"latest_close": 4000.0, "change_pct": 0.5},
            section_technical={}, section_valuation={},
            section_capital={}, section_macro={}, section_sentiment={},
            tag_technical="bull", tag_valuation="neutral",
            tag_capital="positive", tag_macro="neutral", tag_sentiment="neutral",
            composite_comment="谨慎看多", position_coeff=0.6,
            risk_list=[], visible_sections=set(),
        ))
    return contexts, reports


class TestIndexCompareReportBuilder:
    def test_build_compare_table(self, compare_data):
        contexts, reports = compare_data
        builder = IndexCompareReportBuilder()
        table = builder.build(contexts, reports)
        assert isinstance(table, CompareTable)
        assert len(table.rows) == 3
        assert table.rows[0]["name"] == "沪深300"
