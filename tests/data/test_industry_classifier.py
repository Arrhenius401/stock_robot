"""IndustryClassifier 单元测试"""
import csv
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

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

    @pytest.mark.parametrize(
        ("symbol", "level1", "level2", "style"),
        [
            ("000001", "银行", "股份制银行Ⅱ", "大金融"),
            ("000007", "商贸零售", "一般零售", "可选消费"),
            ("000008", "机械设备", "轨交设备Ⅱ", "高端制造"),
            ("000016", "家用电器", "白色家电", "可选消费"),
            ("000021", "电子", "消费电子", "TMT科技"),
            ("601398", "银行", "国有大型银行Ⅱ", "大金融"),
        ],
    )
    def test_official_snapshot_classification(self, classifier, symbol, level1, level2, style):
        """覆盖原缺失记录、多期归属和银行二级分类。"""
        result = classifier.lookup(symbol)
        assert (result.sw_level1, result.sw_level2, result.style_category) == (level1, level2, style)
        assert result.is_verified

    def test_snapshot_has_only_a_share_complete_classifications(self):
        """申万表只保留完整核验的 A 股；港股通资格由独立快照表达。"""
        root = Path(__file__).resolve().parents[2]
        with (root / "data/industry_mapping.csv").open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            assert reader.fieldnames == [
                "symbol", "sw_level1", "sw_level2", "style_category", "mapping_status",
            ]
            rows = list(reader)
        with (root / "src/analysis/config/申万_大类_映射.yaml").open(encoding="utf-8") as stream:
            style_map = yaml.safe_load(stream)
        symbols = [row["symbol"] for row in rows]
        a_share = [row for row in rows if re.fullmatch(r"\d{6}", row["symbol"])]
        assert len(symbols) == len(set(symbols))
        assert len(a_share) == 5534
        assert len(rows) == 5534
        assert [row["symbol"] for row in a_share] == sorted(row["symbol"] for row in a_share)
        assert all(row["sw_level1"] and row["sw_level2"] for row in a_share)
        assert all(row["mapping_status"] == "verified" for row in a_share)
        assert all(row["style_category"] == style_map[row["sw_level1"]] for row in a_share)
        assert not any(row["symbol"].endswith(".HK") for row in rows)
        snapshot_path = root / "data/hk_stock_connect.snapshot.json"
        assert snapshot_path.exists()
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        assert snapshot["source"] in {
            "ak.stock_hk_ggt_components_em", "sse.hkexsc.eligible",
        }
        if snapshot["source"] == "sse.hkexsc.eligible":
            assert snapshot["data_date"]
        assert snapshot["total"] == len(snapshot["members"]) == 666

    @pytest.mark.parametrize("symbol", ["00700.HK", "09988.HK", "00005.HK"])
    def test_snapshot_does_not_invent_hk_classifications(self, classifier, symbol):
        assert not classifier.lookup(symbol).is_verified
