"""指数目录、名称解析与人工核验的 ETF 跟踪关系。"""
import csv
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, cast

from utils.paths import resource_root

IndexStyle = Literal["broad", "sector", "overseas", "strategy"]
_DATA = resource_root() / "data"


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
    identity: str = ""
    return_type: str = "price"
    series_code: str = ""
    capability: str = "unknown"
    verified_at: str | None = None


class IndexMapping:
    """从 CSV 加载目录；旧四列格式仍可读取。"""

    def __init__(self, csv_path: str | Path | None = None, *, snapshot_path: str | Path | None = None):
        self._csv_path = Path(csv_path) if csv_path is not None else _DATA / "index_mapping.csv"
        self._mapping: dict[str, IndexMappingEntry] = {}
        self.catalog_status: dict[str, bool | float | None] = {
            'hsi_loaded': False, 'hsi_aliases_loaded': False, 'hsi_updated_at': None,
        }
        self._load()
        if snapshot_path is None and csv_path is None:
            base = Path.cwd() / ".stock_robot"
            for source in ("hsi", "cni", "csi1", "csi2", "sw"):
                candidate = base / f"index-catalog-{source}.json"
                if candidate.exists():
                    try:
                        self._load_snapshot(candidate)
                    except (OSError, ValueError, TypeError, KeyError) as error:
                        logging.getLogger(__name__).warning("指数目录快照不可用：%s", error)
        if snapshot_path is not None:
            try:
                self._load_snapshot(Path(snapshot_path))
            except (OSError, ValueError, TypeError, KeyError) as error:
                logging.getLogger(__name__).warning("指数目录快照不可用：%s", error)

    def _load_snapshot(self, path: Path) -> None:
        from data.index_catalog_sources import (
            load_catalog_aliases,
            load_catalog_snapshot,
        )

        additions: dict[str, IndexMappingEntry] = {}
        items = load_catalog_snapshot(path)
        hsi = all(item.provider == 'hsi' and item.market == 'hk' for item in items)
        aliases = load_catalog_aliases(path, items) if hsi else None
        for item in items:
            if item.provider == "hsi" and item.market != "hk":
                continue
            if item.provider not in ("hsi", "cni", "csi", "sw"):
                continue
            symbol = item.identity.upper()
            if symbol in self._mapping:
                existing = self._mapping[symbol]
                if existing.name == item.name and existing.provider == item.provider:
                    continue
                raise ValueError(f"指数身份重复：{symbol}")
            if symbol in additions:
                raise ValueError(f"指数身份重复：{symbol}")
            existing = self._mapping.get(item.code.upper())
            if (item.market == "unknown" and existing is not None
                    and existing.market == "a-shares" and existing.provider == item.provider
                    and item.name.casefold() in {value.casefold() for value in (existing.name, *existing.aliases)}):
                # 仅在现有已核验标的与编制方、代码、名称均一致时复用原路由。
                additions[symbol] = replace(existing, identity=item.identity)
                continue
            additions[symbol] = IndexMappingEntry(
                symbol=symbol, name=item.name, index_style="overseas" if item.market == "hk" else "broad",
                market=item.market, provider=item.provider, price_symbol=item.code, identity=item.identity,
                return_type=item.return_type, series_code=item.series_code,
                aliases=aliases.get(item.identity, ()) if aliases is not None else (),
            )
        self._mapping.update(additions)
        if hsi:
            self.catalog_status.update(hsi_loaded=True, hsi_aliases_loaded=aliases is not None,
                                       hsi_updated_at=path.stat().st_mtime)

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
            key in value.casefold() for value in (entry.symbol, entry.identity, entry.name, *entry.aliases)
        ))

    def resolve_candidates(self, query: str) -> tuple[IndexMappingEntry, ...]:
        import re

        from utils.symbols import index_match_key, normalize_index_query

        normalized = normalize_index_query(query)
        key = index_match_key(query)
        def base_key(value: str) -> str:
            normalized_name = normalize_index_query(value)
            cleaned = re.sub(r"(?:净股息|净收益|总股息|总收益|全收益|毛股息)(?:累计)?(?:指数)?|\b(?:NTRI|TRI|NET TOTAL RETURN|TOTAL RETURN)\b", "", normalized_name)
            cleaned = re.sub(r"[()]", "", cleaned).strip().removesuffix("指数")
            return index_match_key(cleaned)

        def family_key(entry: IndexMappingEntry) -> tuple[str, str]:
            # 其他编制方的 series_code 可能表示整个目录，不能推断收益版本系列。
            return (entry.provider, entry.series_code if entry.provider == "hsi" and entry.series_code
                    else entry.identity or entry.symbol)

        query_base = base_key(query)
        if not key:
            return ()
        values = lambda entry: (entry.symbol, entry.identity, entry.name, *entry.aliases)
        matches = tuple(entry for entry in self.entries() if any(
            key == index_match_key(value) for value in values(entry) if value
        ))
        if not matches:
            # 所有版本均参与基名匹配，避免仅有总收益条目的其他系列被价格系列遮蔽。
            families = {family_key(entry) for entry in self.entries()
                        if query_base == base_key(entry.name)}
            matches = tuple(entry for entry in self.entries() if family_key(entry) in families)
        if not matches:
            matches = tuple(entry for entry in self.entries() if any(
                key in index_match_key(value) for value in values(entry) if value
            ))
        # 仅在目录中存在官方条目时将旧价格身份折叠为官方身份。
        legacy_hk = {"HSI": "00001.00", "HSCEI": "00014.00", "HSTECH": "02083.00"}
        distinct: dict[str, IndexMappingEntry] = {}
        for entry in matches:
            code = legacy_hk.get(entry.symbol)
            official = self.lookup(f"HSI:{code}") if code else None
            chosen = official or entry
            distinct.setdefault(chosen.symbol, chosen)
        matches = tuple(distinct.values())
        families = {family_key(entry) for entry in matches}
        if len(families) == 1 and len(matches) > 1:
            version = ("net_total_return" if any(word in normalized for word in
                       ("净收益", "净股息", "NTRI", "NET")) else
                       "gross_total_return" if any(word in normalized for word in
                       ("总收益", "全收益", "TRI", "TOTAL RETURN")) else "price")
            preferred = tuple(entry for entry in matches if entry.return_type == version)
            if preferred:
                matches = preferred
        return matches

    def resolve(self, query: str) -> IndexMappingEntry | None:
        matches = self.resolve_candidates(query)
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
