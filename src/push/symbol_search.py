"""订阅标的搜索：本地指数映射与可降级的股票名称目录。"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from data.index_mapping import IndexMapping
from utils.symbols import normalize_symbol, validate_symbol

logger = logging.getLogger(__name__)
_CACHE_SECONDS = 24 * 60 * 60
_RETRY_SECONDS = 10 * 60
_CATALOGS: dict[Path, StockNameCatalog] = {}
_CATALOGS_LOCK = threading.Lock()
_ALIASES = {
    "HSI": ("恒指",),
    "HSCEI": ("国企指数",),
    "SPX": ("标普", "标普500"),
    "IXIC": ("纳指", "纳斯达克"),
    "DJI": ("道指", "道琼斯"),
}


class StockNameCatalog:
    """进程内缓存一次批量名称表，并保存可跨进程使用的快照。"""

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._names: dict[str, str] | None = None
        self._fetched_at = 0.0
        self._retry_after = 0.0
        self._refreshing = False
        self._refresh_done = threading.Event()

    def names(self) -> dict[str, str] | None:
        started = False
        with self._lock:
            if self._names is None:
                self._load_snapshot()
            now = time.time()
            if (now - self._fetched_at >= _CACHE_SECONDS
                    and now >= self._retry_after and not self._refreshing):
                self._refreshing = True
                self._refresh_done.clear()
                threading.Thread(target=self._refresh, args=(now,), daemon=True).start()
                started = True
            cached = self._names
        if cached is None and started:
            # 首次无快照时短暂等待；网络慢时立刻给出代码回退。
            self._refresh_done.wait(timeout=0.5)
            with self._lock:
                return self._names
        return cached

    def _load_snapshot(self) -> None:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            stocks = payload["stocks"]
            if not isinstance(stocks, dict):
                raise TypeError("股票名称快照格式无效")
            loaded = {str(code): str(name) for code, name in stocks.items()
                      if len(str(code)) == 6 and validate_symbol(str(code)) and str(name).strip()}
            if not loaded:
                raise ValueError("股票名称快照为空")
            self._names = loaded
            self._fetched_at = float(payload["fetched_at"])
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            logger.warning("股票名称快照不可用: %s", exc)

    def _refresh(self, now: float) -> None:
        try:
            from utils.symbols import _ak_code_name

            frame: Any = _ak_code_name()
            names = {
                str(row["code"]): str(row["name"]).strip()
                for _, row in frame.iterrows()
                if len(str(row["code"])) == 6 and validate_symbol(str(row["code"]))
                and str(row["name"]).strip()
            }
            if not names:
                raise ValueError("股票名称表为空")
            with self._lock:
                self._names = names
                self._fetched_at = now
            self._save_snapshot(names, now)
        except Exception as exc:  # noqa: BLE001 — AkShare 网络与数据格式异常不可预测
            logger.warning("股票名称表获取失败，使用快照或代码回退: %s", exc)
            with self._lock:
                self._retry_after = now + _RETRY_SECONDS
        finally:
            with self._lock:
                self._refreshing = False
            self._refresh_done.set()

    def _save_snapshot(self, names: dict[str, str], fetched_at: float) -> None:
        temporary = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps({"fetched_at": fetched_at, "stocks": names},
                                            ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, self._path)
        except OSError as exc:
            logger.warning("股票名称快照写入失败: %s", exc)


def _catalog(config_dir: Path) -> StockNameCatalog:
    path = (config_dir / "push_stock_names.json").resolve()
    with _CATALOGS_LOCK:
        if path not in _CATALOGS:
            _CATALOGS[path] = StockNameCatalog(path)
        return _CATALOGS[path]


def cached_stock_names(config_dir: Path) -> dict[str, str] | None:
    """复用搜索目录；首次无快照时最多等待半秒。"""
    return _catalog(config_dir).names()


def _match_rank(query: str, symbol: str, name: str, aliases: tuple[str, ...] = ()) -> int | None:
    q = query.casefold()
    code = symbol.casefold()
    labels = (name.casefold(), *(alias.casefold() for alias in aliases))
    if code.startswith(q):
        return 0
    if any(label.startswith(q) for label in labels):
        return 1
    if any(q in label for label in labels):
        return 2
    return None


def search_symbols(query: str, limit: int, config_dir: Path) -> dict[str, Any]:
    """返回指数与股票候选；未知海外字母代码没有回退候选。"""
    q = query.strip()
    if not q:
        return {"candidates": [], "stocks_available": False}
    names = _catalog(config_dir).names()
    candidates: list[tuple[int, dict[str, Any]]] = []
    for entry in IndexMapping().entries():
        rank = _match_rank(q, entry.symbol, entry.name, _ALIASES.get(entry.symbol, ()))
        if rank is not None:
            candidates.append((rank, {"symbol": entry.symbol, "display_name": entry.name,
                                      "kind": "index", "index_style": entry.index_style,
                                      "market": entry.market}))
    if names is not None:
        for symbol, name in names.items():
            rank = _match_rank(q, symbol, name)
            if rank is not None:
                candidates.append((rank, {"symbol": symbol, "display_name": name,
                                          "kind": "stock", "index_style": None,
                                          "market": "a-shares"}))
    elif len(q) == 6 and q.isdigit() and validate_symbol(q):
        candidates.append((0, {"symbol": normalize_symbol(q), "display_name": "",
                               "kind": "stock", "index_style": None, "market": "a-shares"}))
    candidates.sort(key=lambda item: (item[0], item[1]["symbol"], item[1]["kind"]))
    return {"candidates": [item for _, item in candidates[:limit]], "stocks_available": names is not None}
