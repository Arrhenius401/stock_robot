"""IndustryClassifier 单元测试"""
import sys
from pathlib import Path

import pytest

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

    def test_lookup_unknown_symbol_is_explicitly_missing(self, classifier):
        result = classifier.lookup("999999")
        assert result.sw_level1 == ""
        assert result.style_category == ""
        assert result.mapping_status == "missing"
        assert result.is_verified is False

    def test_lookup_legacy_placeholder_is_explicitly_missing(self, tmp_path):
        csv_path = tmp_path / "industry_mapping.csv"
        csv_path.write_text(
            "symbol,sw_level1,sw_level2,style_category\n"
            "002714,综合,,高端制造\n",
            encoding="utf-8",
        )

        result = IndustryClassifier(csv_path).lookup("002714")

        assert result.mapping_status == "missing"
        assert result.is_verified is False

    def test_symbol_count_positive(self, classifier):
        assert classifier.symbol_count > 0
