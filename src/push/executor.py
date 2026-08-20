"""串行推送执行器 — 逐标的生成报告并推送，单标的失败隔离"""
import logging
import re

from data.index_mapping import IndexMapping
from data.schemas import AnalysisTarget
from push.backends import get_backend
from push.models import Subscription
from push.summary import (
    build_index_full,
    build_index_summary,
    build_stock_full,
    build_stock_summary,
)
from report.signal import load_signal_config
from utils.symbols import (
    normalize_index_symbol,
    normalize_symbol,
    resolve_name,
    validate_symbol,
)

logger = logging.getLogger(__name__)

OVERSEAS_PATTERN = re.compile(r"^[A-Z]{2,10}$")


class PushExecutor:
    """执行订阅推送：股票走 Pipeline，指数走 IndexPipeline，串行即推"""

    def __init__(self, core, store, config):
        self._core = core
        self._store = store
        self._config = config
        self._mapping = IndexMapping()

    def _classify(self, raw: str) -> tuple[str, str, object | None]:
        """返回 (normalized, kind, entry)；entry 为指数映射条目或 None"""
        if OVERSEAS_PATTERN.match(raw.strip().upper()):
            return raw.strip().upper(), "index", None
        idx_norm = normalize_index_symbol(raw)
        entry = self._mapping.lookup(idx_norm)
        if entry is not None:
            return idx_norm, "index", entry
        if validate_symbol(raw):
            return normalize_symbol(raw), "stock", None
        return "", "invalid", None

    def run_subscription(self, sub: Subscription) -> dict:
        """逐标的串行推送；单标的失败隔离并记录执行结果"""
        failures: list[str] = []
        ok = 0
        backend = get_backend(sub.channel, self._config)
        for raw in sub.symbols:
            try:
                self._push_one(raw, sub.channel, backend)
                ok += 1
            except Exception as e:  # noqa: BLE001 — 单标的失败不影响整体
                logger.error("推送 %s 失败: %s", raw, e)
                failures.append(f"{raw}: {e}")
        self._store.record_run(sub.id or 0, len(sub.symbols), ok, failures)
        return {"total": len(sub.symbols), "ok": ok, "failures": failures}

    def _push_one(self, raw: str, channel: str, backend) -> None:
        normalized, kind, entry = self._classify(raw)
        if kind == "invalid":
            raise ValueError(f"无效的代码: {raw}")
        if kind == "stock":
            self._push_stock(normalized, channel, backend)
        else:
            self._push_index(normalized, entry, channel, backend)

    def _push_stock(self, symbol: str, channel: str, backend) -> None:
        name = resolve_name(symbol) or symbol
        results, commentary, ctx = self._core.pipeline.run(symbol, name)
        signal_cfg = load_signal_config(self._config)
        if channel == "email":
            content = build_stock_full(symbol, name, results, commentary, ctx, signal_cfg)
            title = f"[Stock Robot] {name} 分析报告"
        else:
            content = build_stock_summary(symbol, name, results, ctx, signal_cfg)
            title = f"{name} 分析报告"
        backend.send(title=title, content=content, content_type="markdown")

    def _push_index(self, symbol: str, entry, channel: str, backend) -> None:
        if entry is not None:
            name, market, style = entry.name, entry.market, entry.index_style
        elif OVERSEAS_PATTERN.match(symbol):
            name, market, style = symbol, "overseas", "overseas"
        else:
            name, market, style = symbol, "a-shares", "broad"
        target = AnalysisTarget(
            target_type="index", symbol=symbol, name=name,
            market=market, index_style=style,
        )
        result = self._core.index_pipeline.run([target])
        if not result.reports:
            raise RuntimeError(f"指数分析无结果: {result.errors}")
        report = result.reports[0]
        if channel == "email":
            content = build_index_full(report)
        else:
            content = build_index_summary(report)
        backend.send(title=f"{report.name} 指数报告", content=content,
                     content_type="markdown")
