"""行业分类器 — 从本地 CSV 查表获取申万一级行业和投资风格大类"""
import csv
from pathlib import Path


class IndustryClassification:
    """行业分类结果"""
    def __init__(self, symbol: str, sw_level1: str, sw_level2: str, style_category: str,
                 mapping_status: str = "verified"):
        self.symbol = symbol
        self.sw_level1 = sw_level1
        self.sw_level2 = sw_level2
        self.style_category = style_category
        self.mapping_status = mapping_status

    @property
    def is_verified(self) -> bool:
        """是否为已核验的申万分类。"""
        return self.mapping_status == "verified" and bool(self.sw_level1)

    def __repr__(self):
        return f"IndustryClassification(symbol={self.symbol}, sw={self.sw_level1}, style={self.style_category})"


class IndustryClassifier:
    """从本地 CSV 查询股票行业分类"""

    def __init__(self, csv_path: str | Path | None = None):
        if csv_path is None:
            csv_path = Path(__file__).parent.parent.parent / "data" / "industry_mapping.csv"
        self._csv_path = Path(csv_path)
        self._mapping: dict[str, IndustryClassification] = {}
        self._load()

    def _load(self):
        if not self._csv_path.exists():
            raise FileNotFoundError(f"行业映射表不存在: {self._csv_path}")
        with open(self._csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                symbol = row["symbol"]
                mapping_status = row.get("mapping_status")
                if not mapping_status:
                    is_placeholder = (
                        row["sw_level1"] == "综合"
                        and not row.get("sw_level2", "")
                        and row["style_category"] == "高端制造"
                    )
                    mapping_status = "missing" if is_placeholder else "verified"
                self._mapping[symbol] = IndustryClassification(
                    symbol=symbol,
                    sw_level1=row["sw_level1"],
                    sw_level2=row.get("sw_level2", ""),
                    style_category=row["style_category"],
                    mapping_status=mapping_status,
                )

    def lookup(self, symbol: str) -> IndustryClassification:
        """查询股票行业分类。未命中时返回显式缺失分类。"""
        if symbol in self._mapping:
            return self._mapping[symbol]
        return IndustryClassification(
            symbol=symbol,
            sw_level1="",
            sw_level2="",
            style_category="",
            mapping_status="missing",
        )

    @property
    def symbol_count(self) -> int:
        return len(self._mapping)
