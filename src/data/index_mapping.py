"""指数代码 → 名称 → 类别映射表"""
from pathlib import Path
import csv
from dataclasses import dataclass


@dataclass
class IndexMappingEntry:
    symbol: str
    name: str
    index_style: str    # "broad" | "sector" | "overseas"
    market: str = "a-shares"


class IndexMapping:
    """从 CSV 加载指数映射表，支持查表获取名称和类别"""

    def __init__(self, csv_path: str | Path | None = None):
        if csv_path is None:
            csv_path = Path(__file__).parent.parent.parent / "data" / "index_mapping.csv"
        self._csv_path = Path(csv_path)
        self._mapping: dict[str, IndexMappingEntry] = {}
        self._load()

    def _load(self):
        if not self._csv_path.exists():
            raise FileNotFoundError(f"指数映射表不存在: {self._csv_path}")
        with open(self._csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                symbol = row["symbol"]
                self._mapping[symbol] = IndexMappingEntry(
                    symbol=symbol,
                    name=row["name"],
                    index_style=row["index_style"],
                    market=row.get("market", "a-shares"),
                )

    def lookup(self, symbol: str) -> IndexMappingEntry | None:
        return self._mapping.get(symbol)

    def __len__(self) -> int:
        return len(self._mapping)
