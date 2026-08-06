"""Registry 按 category 筛选测试"""
import pytest
from src.core.registry import Registry
from src.index.analysis.technical import IndexTechnicalAnalyzer
from src.analysis.technical import TechnicalAnalyzer


class TestRegistryCategoryFilter:
    def test_get_analysis_modules_by_index_dimension(self):
        reg = Registry()
        reg.register_analysis_module(TechnicalAnalyzer())
        reg.register_analysis_module(IndexTechnicalAnalyzer())

        all_modules = reg.get_analysis_modules()
        assert len(all_modules) == 2

        index_modules = [m for m in all_modules
                         if m.dimension.startswith("index_")]
        assert len(index_modules) == 1
