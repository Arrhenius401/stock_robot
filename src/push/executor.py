"""串行推送执行器 — 逐标的生成报告并推送，单标的失败隔离"""
import logging
import re
from typing import Literal, cast

from data.index_mapping import IndexMapping
from data.schemas import AnalysisTarget
from push.backends import get_backend
from push.models import Subscription, SubscriptionSymbol
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
    validate_index_symbol,
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

    def _classify(self, item: SubscriptionSymbol) -> tuple[str, str, object | None]:
        """返回 (normalized, kind, entry)；entry 为指数映射条目或 None

        kind=stock/index 为显式类型（解决 000001 指数/股票歧义），
        kind=auto 沿用自动判定：海外大写→指数、映射命中→指数、否则股票。
        """
        raw = item.symbol
        if item.kind == "stock":
            if validate_symbol(raw):
                return normalize_symbol(raw), "stock", None
            raise ValueError(f"无效的股票代码: {raw}")
        if item.kind == "index":
            if OVERSEAS_PATTERN.match(raw.strip().upper()):
                symbol = raw.strip().upper()
                return symbol, "index", self._mapping.lookup(symbol)
            if validate_index_symbol(raw):
                idx_norm = normalize_index_symbol(raw)
                # 查映射表取名称/市场；未收录的 A 股指数由 _push_index 兜底
                return idx_norm, "index", self._mapping.lookup(idx_norm)
            raise ValueError(f"无效的指数代码: {raw}")
        if OVERSEAS_PATTERN.match(raw.strip().upper()):
            symbol = raw.strip().upper()
            return symbol, "index", self._mapping.lookup(symbol)
        idx_norm = normalize_index_symbol(raw)
        entry = self._mapping.lookup(idx_norm)
        if entry is not None:
            return idx_norm, "index", entry
        if validate_symbol(raw):
            return normalize_symbol(raw), "stock", None
        raise ValueError(f"无效的代码: {raw}")

    def run_subscription(self, sub: Subscription, run_id: int | None = None) -> dict:
        """逐标的串行推送；运行记录在首次执行前持久化。"""
        if run_id is None:
            run_id = self._store.queue_run(sub, "manual")
        run = self._store.get_run(run_id)
        if run is None:
            raise ValueError("运行记录不存在")
        sub = Subscription.model_validate(run["subscription_snapshot"])
        failures: list[str] = []
        ok = 0
        self._store.start_run(run_id)
        try:
            backend = get_backend(sub.channel, self._config)
            for item in sub.symbols:
                raw = item.symbol
                self._store.set_current(run_id, raw)
                try:
                    self._push_one(item, sub.channel, backend)
                    ok += 1
                    self._store.advance_run(run_id, success=True)
                except Exception as exc:  # noqa: BLE001 — 单标的失败不影响整体
                    logger.error("推送 %s 失败: %s", raw, exc)
                    failure = f"{raw}: {exc}"
                    failures.append(failure)
                    self._store.advance_run(run_id, success=False, failure=failure)
        except Exception as exc:
            logger.exception("订阅 %s 运行失败", sub.id)
            failures.append(f"推送初始化失败: {exc}")
            self._store.finish_run(run_id, error=failures[-1])
            return {"run_id": run_id, "total": len(sub.symbols), "ok": ok, "failures": failures}
        self._store.finish_run(run_id)
        return {"run_id": run_id, "total": len(sub.symbols), "ok": ok, "failures": failures}

    def _push_one(self, item: SubscriptionSymbol, channel: str, backend) -> None:
        normalized, kind, entry = self._classify(item)
        if kind == "stock":
            self._push_stock(normalized, channel, backend, item.display_name)
        else:
            self._push_index(normalized, entry, item.index_style, channel, backend)

    def _push_stock(self, symbol: str, channel: str, backend, display_name: str = "") -> None:
        name = display_name or resolve_name(symbol) or symbol
        results, commentary, ctx = self._core.pipeline.run(symbol, name)
        signal_cfg = load_signal_config(self._config)
        if channel == "email":
            content = build_stock_full(symbol, name, results, commentary, ctx, signal_cfg)
            title = f"[Stock Robot] {name} 分析报告"
        else:
            content = build_stock_summary(symbol, name, results, ctx, signal_cfg)
            title = f"{name} 分析报告"
        backend.send(title=title, content=content, content_type="markdown")

    def _push_index(self, symbol: str, entry, explicit_style: str | None,
                    channel: str, backend) -> None:
        # style 优先级：显式 index_style > 映射条目 > 海外默认 overseas > broad
        if explicit_style is not None:
            style = explicit_style
            if entry is not None:
                name, market = entry.name, entry.market
            elif OVERSEAS_PATTERN.match(symbol):
                name, market = symbol, "overseas"
            else:
                name, market = symbol, "a-shares"
        elif entry is not None:
            name, market, style = entry.name, entry.market, entry.index_style
        elif OVERSEAS_PATTERN.match(symbol):
            name, market, style = symbol, "overseas", "overseas"
        else:
            name, market, style = symbol, "a-shares", "broad"
        target = AnalysisTarget(
            target_type="index", symbol=symbol, name=name,
            market=market,
            index_style=cast(Literal["broad", "sector", "overseas"], style),
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
