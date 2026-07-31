"""IndustryClassifier 单元测试"""
from pathlib import Path
import pytest
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from data.industry_classifier import IndustryClassifier


class TestIndustryClassifier:
    @pytest.fixture
    def classifier(self):
        csv_path = Path(__file__).parent.parent.parent / "data" / "industry_mapping.csv"
        return IndustryClassifier(csv_path)

    def test_lookup_known_symbol(self, classifier):
        result = classifier.lookup("000001")
        assert result.symbol == "000001"

    def test_lookup_unknown_symbol_falls_back(self, classifier):
        result = classifier.lookup("999999")
        assert result.sw_level1 == "综合"
        assert result.style_category == "高端制造"

    def test_symbol_count_positive(self, classifier):
        assert classifier.symbol_count > 0
