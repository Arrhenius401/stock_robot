"""指数目录、名称解析与人工核验的 ETF 跟踪关系。"""
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

IndexStyle = Literal["broad", "sector", "overseas", "strategy"]
_DATA = Path(__file__).parent.parent.parent / "data"


@dataclass
class IndexMappingEntry:
    symbol: str
    name: str
    index_style: IndexStyle
    market: str = "a-shares"
    provider: str = ""
    strategy_kind: str = ""
    aliases: tuple[str, ...] = ()
    source_url: str = ""
    price_symbol: str = ""
    base_index: str = "000300"


class IndexMapping:
    """从 CSV 加载目录；旧四列格式仍可读取。"""

    def __init__(self, csv_path: str | Path | None = None):
        self._csv_path = Path(csv_path) if csv_path is not None else _DATA / "index_mapping.csv"
        self._mapping: dict[str, IndexMappingEntry] = {}
        self._load()

    def _load(self):
        with self._csv_path.open(encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                symbol = row["symbol"].upper()
                style = row["index_style"]
                if style not in ("broad", "sector", "overseas", "strategy"):
                    raise ValueError(f"指数类别无效：{symbol} {style}")
                if symbol in self._mapping:
                    raise ValueError(f"指数代码重复：{symbol}")
                self._mapping[symbol] = IndexMappingEntry(
                    symbol=symbol, name=row["name"], index_style=cast(IndexStyle, style),
                    market=row.get("market") or "a-shares",
                    provider=row.get("provider") or "",
                    strategy_kind=row.get("strategy_kind") or "",
                    aliases=tuple(a.strip() for a in (row.get("aliases") or "").split("|") if a.strip()),
                    source_url=row.get("source_url") or "",
                    price_symbol=row.get("price_symbol") or symbol,
                    base_index=row.get("base_index") or "000300",
                )

    def lookup(self, symbol: str) -> IndexMappingEntry | None:
        return self._mapping.get(symbol.upper())

    def entries(self) -> tuple[IndexMappingEntry, ...]:
        return tuple(self._mapping.values())

    def search(self, query: str = "") -> tuple[IndexMappingEntry, ...]:
        key = query.strip().casefold()
        return tuple(entry for entry in self.entries() if not key or any(
            key in value.casefold() for value in (entry.symbol, entry.name, *entry.aliases)
        ))

    def resolve(self, query: str) -> IndexMappingEntry | None:
        from utils.symbols import normalize_index_symbol, validate_index_symbol

        raw = query.strip()
        if not raw:
            return None
        if validate_index_symbol(raw):
            return self.lookup(normalize_index_symbol(raw))
        key = raw.casefold()
        matches = tuple(entry for entry in self.entries() if any(
            key == value.casefold() for value in (entry.name, *entry.aliases)
        )) or self.search(raw)
        if len(matches) > 1:
            choices = "、".join(f"{entry.name}（{entry.symbol}）" for entry in matches)
            raise ValueError(f"匹配到多个指数，请选择具体代码：{choices}")
        return matches[0] if matches else None

    def __len__(self) -> int:
        return len(self._mapping)


@dataclass
class ETFMappingEntry:
    symbol: str
    name: str
    index_symbol: str
    source_url: str = ""


class ETFIndexMapping:
    """人工核验的 ETF 跟踪目录，不把基金代码当作指数代码。"""

    def __init__(self, csv_path: str | Path | None = None):
        path = Path(csv_path) if csv_path is not None else _DATA / "etf_index_mapping.csv"
        with path.open(encoding="utf-8-sig", newline="") as file:
            self._mapping = {row["symbol"]: ETFMappingEntry(**row) for row in csv.DictReader(file)}

    def lookup(self, symbol: str) -> ETFMappingEntry | None:
        from utils.symbols import normalize_symbol

        return self._mapping.get(normalize_symbol(symbol))

    def entries(self) -> tuple[ETFMappingEntry, ...]:
        return tuple(self._mapping.values())
